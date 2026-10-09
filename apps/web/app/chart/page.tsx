"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { IRange, UTCTimestamp } from "lightweight-charts";
import type { ExclusionView, EventsView } from "@cryptosos/client";
import { getEvents } from "@cryptosos/client";

import ChartPane, { type PaneHandles, type PaneLine } from "./panes";
import CommandPalette, { type PaletteAction } from "./palette";
import { useChartLayout, type OverlayId } from "./store";
import {
  TIMEFRAMES,
  hoverRequests,
  overlayRequests,
  useChartWindows,
  useRegistry,
  type IndicatorRequest,
} from "./data";
import { eventToMarker, envelopeToHoverCard, findEventAtTime, type EventMarkerSpec } from "./events";
import { API_BASE } from "@/lib/api";

/**
 * Chart workspace (spec sections 12, M2 design note): header controls,
 * candle stack with overlay lines and Volume/RSI sub-panes, crosshair sync,
 * hover metrics row, quality badge, exclusion shading, lazy history, and a
 * Ctrl/Cmd+K palette. Every number on screen comes from a server response;
 * the UI never computes indicators locally (PD-5).
 */

const EMPTY_EXCLUSIONS: ExclusionView[] = [];
const RSI_LEVELS = [
  { price: 70, title: "70" },
  { price: 30, title: "30" },
];
const QUALITY_BADGE: Record<string, string> = {
  GREEN: "badge-green",
  YELLOW: "badge-yellow",
  ORANGE: "badge-orange",
  RED: "badge-red",
};
const OVERLAY_LABELS: Record<OverlayId, string> = {
  sma: "SMA",
  ema: "EMA",
  vwap: "VWAP",
  bb: "Bollinger Bands",
};

function fmtPrice(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  return value.toLocaleString("en-US", { maximumFractionDigits: 4 });
}

function fmtCompact(value: number): string {
  return value.toLocaleString("en-US", { notation: "compact", maximumFractionDigits: 2 });
}

function fmtMetric(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  return value.toLocaleString("en-US", { maximumFractionDigits: 3 });
}

function formatTime(time: number): string {
  return `${new Date(time * 1000).toISOString().replace("T", " ").slice(0, 16)}Z`;
}

export default function ChartPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const layout = useChartLayout();
  const registry = useRegistry();
  const [hydrated, setHydrated] = useState(false);
  const [hoverTime, setHoverTime] = useState<number | null>(null);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [displayOpen, setDisplayOpen] = useState(false);

  // The persisted layout rehydrates after mount; the first paint must match
  // the server render (loading text), so gate the workspace on hydration.
  useEffect(() => {
    if (useChartLayout.persist.hasHydrated()) {
      setHydrated(true);
      return undefined;
    }
    return useChartLayout.persist.onFinishHydration(() => setHydrated(true));
  }, []);

  const instruments = useMemo(() => registry.data ?? [], [registry.data]);
  const instrumentId = useMemo(() => {
    if (layout.instrumentId && instruments.some((item) => item.instrument_id === layout.instrumentId)) {
      return layout.instrumentId;
    }
    return instruments[0]?.instrument_id ?? null;
  }, [layout.instrumentId, instruments]);

  const rsiEnabled = layout.subPanes.some((pane) => pane.id === "rsi" && pane.enabled);
  const specs = useMemo<IndicatorRequest[]>(
    () => [
      ...hoverRequests(layout.atrLength),
      ...(rsiEnabled
        ? [{ key: "pane:rsi", name: "rsi", params: { length: layout.rsiLength } }]
        : []),
      ...overlayRequests(layout.overlays, layout.smaLength, layout.emaLength),
    ],
    [
      layout.atrLength,
      rsiEnabled,
      layout.rsiLength,
      layout.overlays,
      layout.smaLength,
      layout.emaLength,
    ],
  );

  const data = useChartWindows(instrumentId, layout.timeframe, specs);

  // Events layer (T4.4): on-demand detector run, default ON once data exists.
  const eventsQuery = useQuery({
    queryKey: ["events", instrumentId, layout.timeframe],
    queryFn: () =>
      getEvents({ instrument_id: instrumentId ?? "", tf: layout.timeframe }, API_BASE),
    enabled: layout.eventsEnabled && instrumentId != null,
    staleTime: 60_000,
    retry: 1,
  });

  const eventMarkers = useMemo<EventMarkerSpec[]>(() => {
    if (!layout.eventsEnabled) return [];
    return (eventsQuery.data?.events ?? [])
      .map(eventToMarker)
      .filter((spec): spec is EventMarkerSpec => spec != null);
  }, [layout.eventsEnabled, eventsQuery.data]);

  const handlesRef = useRef<PaneHandles>({});
  const rangeRef = useRef<IRange<number> | null>(null);
  const viewRangeRef = useRef<IRange<number> | null>(null);

  // A new (instrument, timeframe) view drops viewport and crosshair state.
  useEffect(() => {
    rangeRef.current = null;
    viewRangeRef.current = null;
    setHoverTime(null);
  }, [instrumentId, layout.timeframe]);

  const anchors = useMemo(() => {
    const closes = new Map<number, number>();
    const volumes = new Map<number, number>();
    for (const candle of data.candles) {
      closes.set(candle.time, candle.c);
      volumes.set(candle.time, candle.v);
    }
    const rsi = new Map<number, number>();
    for (const point of data.series.get("pane:rsi")?.[0]?.points ?? []) {
      if (point.value != null) rsi.set(point.time, point.value);
    }
    return {
      main: (time: number) => closes.get(time) ?? null,
      volume: (time: number) => volumes.get(time) ?? null,
      rsi: (time: number) => rsi.get(time) ?? null,
    };
  }, [data.candles, data.series]);

  // Crosshair relay: every pane reports its move, all panes show it.
  useEffect(() => {
    for (const [paneId, handle] of Object.entries(handlesRef.current)) {
      const series = handle.anchorSeries();
      if (hoverTime == null || !series) {
        handle.chart.clearCrosshairPosition();
        continue;
      }
      const price = anchors[paneId as keyof typeof anchors]?.(hoverTime);
      if (price == null) {
        handle.chart.clearCrosshairPosition();
        continue;
      }
      handle.chart.setCrosshairPosition(price, hoverTime as UTCTimestamp, series);
    }
  }, [hoverTime, anchors]);

  const handleHover = useCallback((time: number | null) => setHoverTime(time), []);

  const retryAll = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ["candles"] });
    void queryClient.invalidateQueries({ queryKey: ["indicators"] });
    void queryClient.invalidateQueries({ queryKey: ["instruments"] });
  }, [queryClient]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen((open) => !open);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const mainLines = useMemo<PaneLine[]>(() => {
    const pointsOf = (key: string) => data.series.get(key)?.[0]?.points ?? [];
    const out: PaneLine[] = [];
    if (layout.overlays.sma) {
      const points = pointsOf("overlay:sma");
      if (points.length) out.push({ id: "sma", points, color: "#facc15" });
    }
    if (layout.overlays.ema) {
      const points = pointsOf("overlay:ema");
      if (points.length) out.push({ id: "ema", points, color: "#60a5fa" });
    }
    if (layout.overlays.vwap) {
      const points = pointsOf("overlay:vwap");
      if (points.length) out.push({ id: "vwap", points, color: "#c084fc" });
    }
    if (layout.overlays.bb) {
      const bucket = data.series.get("overlay:bb") ?? [];
      const upper = bucket.find((entry) => entry.name === "upper")?.points;
      const middle = bucket.find((entry) => entry.name === "middle")?.points;
      const lower = bucket.find((entry) => entry.name === "lower")?.points;
      if (upper?.length) out.push({ id: "bb-upper", points: upper, color: "#38bdf8" });
      if (middle?.length) out.push({ id: "bb-middle", points: middle, color: "#94a3b8", dashed: true });
      if (lower?.length) out.push({ id: "bb-lower", points: lower, color: "#38bdf8" });
    }
    return out;
  }, [data.series, layout.overlays]);

  const volumeLines = useMemo<PaneLine[]>(() => {
    const points = data.series.get("hover:rvol")?.[0]?.points ?? [];
    return points.length ? [{ id: "rvol", points, color: "#4ade80", overlay: true }] : [];
  }, [data.series]);

  const rsiLines = useMemo<PaneLine[]>(() => {
    const points = data.series.get("pane:rsi")?.[0]?.points ?? [];
    return points.length
      ? [{ id: "rsi", points, color: "#f87171", fixedRange: { min: 0, max: 100 } }]
      : [];
  }, [data.series]);

  const exclusions = data.quality?.exclusions ?? EMPTY_EXCLUSIONS;
  const quality = data.quality;
  const timeVisible = layout.timeframe !== "1D";

  const paletteActions = useMemo<PaletteAction[]>(() => {
    const actions: PaletteAction[] = [];
    for (const instrument of instruments) {
      actions.push({
        id: `instrument:${instrument.instrument_id}`,
        group: "Instrument",
        label: `${instrument.asset}/${instrument.quote} · ${instrument.market_type}`,
        run: () => layout.setInstrument(instrument.instrument_id),
      });
    }
    for (const timeframe of TIMEFRAMES) {
      actions.push({
        id: `tf:${timeframe}`,
        group: "Timeframe",
        label: `Timeframe ${timeframe}`,
        run: () => layout.setTimeframe(timeframe),
      });
    }
    for (const overlayId of Object.keys(OVERLAY_LABELS) as OverlayId[]) {
      actions.push({
        id: `overlay:${overlayId}`,
        group: "Overlay",
        label: `Toggle ${OVERLAY_LABELS[overlayId]}`,
        run: () => layout.toggleOverlay(overlayId),
      });
    }
    for (const pane of layout.subPanes) {
      const name = pane.id === "volume" ? "Volume pane" : "RSI pane";
      actions.push({
        id: `pane:${pane.id}:toggle`,
        group: "Pane",
        label: `Toggle ${name}`,
        run: () => layout.togglePane(pane.id),
      });
      actions.push({
        id: `pane:${pane.id}:collapse`,
        group: "Pane",
        label: `${name} — ${pane.collapsed ? "expand" : "collapse"}`,
        run: () => layout.setPaneCollapsed(pane.id, !pane.collapsed),
      });
      actions.push({
        id: `pane:${pane.id}:up`,
        group: "Pane",
        label: `${name} — move up`,
        run: () => layout.movePane(pane.id, -1),
      });
      actions.push({
        id: `pane:${pane.id}:down`,
        group: "Pane",
        label: `${name} — move down`,
        run: () => layout.movePane(pane.id, 1),
      });
    }
    actions.push({
      id: "layout:reset",
      group: "Layout",
      label: "Reset layout to defaults",
      run: () => layout.resetLayout(),
    });
    actions.push({
      id: "nav:data",
      group: "Navigate",
      label: "Data Health",
      run: () => router.push("/data"),
    });
    return actions;
  }, [instruments, layout, router]);

  const hoverRow = hoverTime != null ? data.hover.get(hoverTime) : undefined;
  const hoverEvent = hoverTime != null ? findEventAtTime(eventMarkers, hoverTime) : null;
  const hoverEventCard = hoverEvent ? envelopeToHoverCard(hoverEvent) : null;
  const qualityTooltip = quality
    ? [
        `${quality.state} · ${(quality.completeness * 100).toFixed(1)}% complete`,
        ...exclusions.map(
          (item) => `${item.kind}: ${item.exclusion_start} → ${item.exclusion_end} (${item.status})`,
        ),
      ].join("\n")
    : undefined;

  const workspace = (
    <>
      <div className="chart-stack">
        <ChartPane
          id="main"
          height={440}
          timeVisible={timeVisible}
          showTimeScale
          candles={data.candles}
          candleBars
          lines={mainLines}
          exclusions={exclusions}
          eventMarkers={layout.eventsEnabled ? eventMarkers : undefined}
          restore={data.restore}
          publishRange
          handles={handlesRef.current}
          rangeRef={rangeRef}
          viewRangeRef={viewRangeRef}
          onHover={handleHover}
          onNearLeftEdge={data.loadOlder}
        />
        {layout.subPanes.map((pane) => {
          if (!pane.enabled) return null;
          if (pane.collapsed) {
            return (
              <div key={pane.id} className="chart-pane-collapsed">
                {pane.id === "volume" ? "VOLUME" : "RSI"} — collapsed (expand from Display)
              </div>
            );
          }
          const common = {
            height: pane.id === "volume" ? 110 : 140,
            timeVisible,
            showTimeScale: false,
            candles: data.candles,
            exclusions,
            restore: data.restore,
            publishRange: false,
            handles: handlesRef.current,
            rangeRef,
            viewRangeRef,
            onHover: handleHover,
            onNearLeftEdge: data.loadOlder,
          } as const;
          return pane.id === "volume" ? (
            <ChartPane key="volume" id="volume" {...common} histogram lines={volumeLines} />
          ) : (
            <ChartPane
              key="rsi"
              id="rsi"
              {...common}
              lines={rsiLines}
              priceLevels={RSI_LEVELS}
            />
          );
        })}
      </div>
      <div className="chart-hover">
        <span className="muted">
          {hoverTime != null ? formatTime(hoverTime) : "Hover a bar for OHLCV + metrics"}
        </span>
        {hoverRow && (
          <>
            <span>O <b>{fmtPrice(hoverRow.o)}</b></span>
            <span>H <b>{fmtPrice(hoverRow.h)}</b></span>
            <span>L <b>{fmtPrice(hoverRow.l)}</b></span>
            <span>C <b>{fmtPrice(hoverRow.c)}</b></span>
            <span>V <b>{fmtCompact(hoverRow.v)}</b></span>
            <span>RVOL <b>{fmtMetric(hoverRow.rvol)}</b></span>
            <span>CPOS <b>{fmtMetric(hoverRow.cpos)}</b></span>
            <span>WICK <b>{fmtMetric(hoverRow.upperWick)}</b></span>
            <span>ATR <b>{fmtMetric(hoverRow.atr)}</b></span>
            <span>EMA100 <b>{fmtMetric(hoverRow.ema100)}</b></span>
          </>
        )}
      </div>
      {hoverEventCard && (
        <div className="event-hover-card" role="status" aria-label="Event details">
          <div className="event-card-header">
            <b>{hoverEventCard.eventType}</b> · {hoverEventCard.direction} · {formatTime(Math.floor(Date.parse(hoverEventCard.time) / 1000))}
          </div>
          <div className="event-card-row">
            <span>Ref <b>{fmtPrice(hoverEventCard.refPrice)}</b></span>
            <span>Inval <b>{fmtPrice(hoverEventCard.invalidationPrice)}</b></span>
            <span>RVOL <b>{fmtMetric(hoverEventCard.rvol)}</b></span>
            <span>CPOS <b>{fmtMetric(hoverEventCard.cpos)}</b></span>
            {hoverEventCard.archetype && <span>Arch <b>{hoverEventCard.archetype}</b></span>}
          </div>
          {hoverEventCard.triggerRules.length > 0 && (
            <div className="event-card-rules">
              {hoverEventCard.triggerRules.map((rule) => (
                <div key={rule.name} className={`event-rule ${rule.passed ? "passed" : "failed"}`}>
                  {rule.passed ? "✓" : "✗"} {rule.name}{" "}
                  {rule.value != null && <span className="muted">= {fmtMetric(rule.value)}</span>}
                  {rule.threshold != null && <span className="muted"> / {fmtMetric(rule.threshold)}</span>}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </>
  );

  return (
    <div className="chart-page">
      <div className="chart-header">
        <div className="btn-row">
          <label className="muted" htmlFor="chart-instrument">
            Instrument
          </label>
          <select
            id="chart-instrument"
            className="chart-select"
            value={instrumentId ?? ""}
            disabled={instruments.length === 0}
            onChange={(event) => layout.setInstrument(event.target.value)}
          >
            {instruments.map((instrument) => (
              <option key={instrument.instrument_id} value={instrument.instrument_id}>
                {instrument.asset}/{instrument.quote} · {instrument.market_type} · {instrument.venue}
              </option>
            ))}
          </select>
          <label className="muted" htmlFor="chart-tf">
            TF
          </label>
          <select
            id="chart-tf"
            className="chart-select"
            value={layout.timeframe}
            onChange={(event) => layout.setTimeframe(event.target.value)}
          >
            {TIMEFRAMES.map((timeframe) => (
              <option key={timeframe} value={timeframe}>
                {timeframe}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="btn"
            aria-pressed={layout.eventsEnabled}
            onClick={() => layout.toggleEvents()}
          >
            Events {layout.eventsEnabled ? "on" : "off"}
          </button>
          <button
            type="button"
            className="btn"
            aria-expanded={displayOpen}
            onClick={() => setDisplayOpen((open) => !open)}
          >
            Display
          </button>
          <button type="button" className="btn" onClick={() => setPaletteOpen(true)}>
            ⌘K
          </button>
          <button type="button" className="btn" onClick={layout.resetLayout}>
            Reset layout
          </button>
          <span className="muted">
            {data.candles.length} bars
            {data.datasetVersion ? ` · dataset ${data.datasetVersion}` : ""}
          </span>
          {quality && (
            <span className={`badge ${QUALITY_BADGE[quality.state] ?? ""}`} title={qualityTooltip}>
              {quality.state} · {(quality.completeness * 100).toFixed(1)}%
            </span>
          )}
        </div>
        {displayOpen && (
          <>
            <div className="menu-backdrop" onClick={() => setDisplayOpen(false)} role="presentation" />
            <div className="chart-menu" role="dialog" aria-label="Chart display options">
              <div className="chart-menu-section">
                <div className="chart-menu-title">Overlays</div>
                {(Object.keys(OVERLAY_LABELS) as OverlayId[]).map((overlayId) => (
                  <label key={overlayId} className="chart-menu-item">
                    <input
                      type="checkbox"
                      checked={layout.overlays[overlayId]}
                      onChange={() => layout.toggleOverlay(overlayId)}
                    />
                    {OVERLAY_LABELS[overlayId]}
                    {(overlayId === "sma" || overlayId === "ema") && (
                      <input
                        className="chart-num"
                        type="number"
                        min={1}
                        aria-label={`${OVERLAY_LABELS[overlayId]} length`}
                        value={overlayId === "sma" ? layout.smaLength : layout.emaLength}
                        onChange={(event) =>
                          layout.setLength(
                            overlayId === "sma" ? "smaLength" : "emaLength",
                            Math.max(1, Number(event.target.value) || 1),
                          )
                        }
                      />
                    )}
                    {overlayId === "bb" && <span className="muted">20, 2 defaults</span>}
                  </label>
                ))}
              </div>
              <div className="chart-menu-section">
                <div className="chart-menu-title">Panes</div>
                {layout.subPanes.map((pane, index) => (
                  <div key={pane.id} className="chart-menu-item">
                    <label>
                      <input
                        type="checkbox"
                        checked={pane.enabled}
                        onChange={() => layout.togglePane(pane.id)}
                      />
                      {pane.id === "volume" ? "Volume (+ RVOL line)" : "RSI"}
                    </label>
                    {pane.id === "rsi" && (
                      <input
                        className="chart-num"
                        type="number"
                        min={1}
                        aria-label="RSI length"
                        value={layout.rsiLength}
                        onChange={(event) =>
                          layout.setLength("rsiLength", Math.max(1, Number(event.target.value) || 1))
                        }
                      />
                    )}
                    <button
                      type="button"
                      className="btn"
                      onClick={() => layout.setPaneCollapsed(pane.id, !pane.collapsed)}
                    >
                      {pane.collapsed ? "Expand" : "Collapse"}
                    </button>
                    <button
                      type="button"
                      className="btn"
                      disabled={index === 0}
                      onClick={() => layout.movePane(pane.id, -1)}
                      aria-label={`Move ${pane.id} pane up`}
                    >
                      ↑
                    </button>
                    <button
                      type="button"
                      className="btn"
                      disabled={index === layout.subPanes.length - 1}
                      onClick={() => layout.movePane(pane.id, 1)}
                      aria-label={`Move ${pane.id} pane down`}
                    >
                      ↓
                    </button>
                  </div>
                ))}
              </div>
              <div className="chart-menu-section">
                <div className="chart-menu-title">Hover metrics</div>
                <label className="chart-menu-item">
                  ATR length
                  <input
                    className="chart-num"
                    type="number"
                    min={1}
                    aria-label="ATR length"
                    value={layout.atrLength}
                    onChange={(event) =>
                      layout.setLength("atrLength", Math.max(1, Number(event.target.value) || 1))
                    }
                  />
                </label>
                <div className="muted chart-menu-note">
                  rvol/cpos/upper_wick use documented defaults; regime EMA uses length 100.
                </div>
              </div>
            </div>
          </>
        )}
      </div>

      {data.error && (
        <div className="banner banner-error">
          <span>Failed to load candles: {data.error}</span>
          <button type="button" className="btn" onClick={retryAll}>
            Retry
          </button>
        </div>
      )}
      {data.staleError && (
        <div className="banner banner-stale">
          <span>Refresh failed, showing the loaded window: {data.staleError}</span>
          <button type="button" className="btn" onClick={retryAll}>
            Retry
          </button>
        </div>
      )}
      {data.indicatorError && (
        <div className="banner banner-error">
          <span>Indicator request failed: {data.indicatorError}</span>
          <button type="button" className="btn" onClick={retryAll}>
            Retry
          </button>
        </div>
      )}
      {exclusions.length > 0 && (
        <div className="banner banner-partial">
          <span>
            {exclusions.length} exclusion interval(s) shaded on the chart (hover a band for details)
          </span>
        </div>
      )}
      {data.loadingOlder && (
        <div className="banner">
          <span className="muted">Loading older bars…</span>
        </div>
      )}
      {registry.isError && (
        <div className="banner banner-error">
          <span>Instrument registry unavailable: {registry.error?.message ?? "unknown error"}</span>
          <button type="button" className="btn" onClick={() => void registry.refetch()}>
            Retry
          </button>
        </div>
      )}

      {!hydrated ? (
        <p className="muted">Loading layout…</p>
      ) : registry.isLoading ? (
        <p className="muted">Loading instrument registry…</p>
      ) : registry.isError ? null : instruments.length === 0 ? (
        <div className="panel">
          <p className="muted">
            No instruments registered yet. Ingest data first, then return to the chart.{" "}
            <Link href="/data">Open Data Health →</Link>
          </p>
        </div>
      ) : data.loading ? (
        <p className="muted">Loading candles…</p>
      ) : data.error ? null : data.candles.length === 0 ? (
        <p className="muted">No closed bars in this window.</p>
      ) : (
        workspace
      )}

      <CommandPalette
        open={paletteOpen}
        onClose={() => setPaletteOpen(false)}
        actions={paletteActions}
      />
    </div>
  );
}
