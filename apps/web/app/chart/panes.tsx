"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { MutableRefObject } from "react";
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  LineStyle,
  createChart,
} from "lightweight-charts";
import type {
  IChartApi,
  ISeriesApi,
  IRange,
  MouseEventParams,
  SeriesType,
  UTCTimestamp,
} from "lightweight-charts";

import type { ExclusionView } from "@cryptosos/client";

import type { CandlePoint, SeriesPoint } from "./data";
import { HISTORY_EDGE, HISTORY_FIT_RATIO, isoToSeconds } from "./data";
import { applyEventMarkers, type EventMarkerSpec, type EventMarkersHandle } from "./events";

/**
 * One lightweight-charts pane (design section 6/7). Every pane is its own
 * chart instance; the main pane owns the visible-range state and publishes
 * it to the sub-panes, all panes report crosshair moves upward, and
 * exclusion bands are a DOM overlay (D-014) painted with
 * ``timeScale().timeToCoordinate`` on top of the canvas.
 */

const UP_BAR = "rgba(74, 222, 128, 0.55)";
const DOWN_BAR = "rgba(248, 113, 113, 0.55)";
const CANDLE_KEY = "__candles";
const HIST_KEY = "__volume";

export interface PaneLine {
  id: string;
  points: SeriesPoint[];
  color: string;
  lineWidth?: 1 | 2 | 3;
  dashed?: boolean;
  overlay?: boolean;
  fixedRange?: { min: number; max: number };
}

export interface PaneHandle {
  chart: IChartApi;
  anchorSeries: () => ISeriesApi<SeriesType> | undefined;
}

export type PaneHandles = Record<string, PaneHandle>;

export interface ChartPaneProps {
  id: string;
  height: number;
  timeVisible: boolean;
  showTimeScale: boolean;
  candles: CandlePoint[];
  candleBars?: boolean;
  histogram?: boolean;
  lines: PaneLine[];
  priceLevels?: { price: number; title: string }[];
  exclusions: ExclusionView[];
  eventMarkers?: EventMarkerSpec[];
  restore: number;
  publishRange: boolean;
  handles: PaneHandles;
  rangeRef: MutableRefObject<IRange<number> | null>;
  viewRangeRef: MutableRefObject<IRange<number> | null>;
  onHover: (time: number | null) => void;
  onNearLeftEdge: () => void;
}

function lineData(points: SeriesPoint[]) {
  return points.map((point) =>
    point.value == null
      ? { time: point.time as UTCTimestamp }
      : { time: point.time as UTCTimestamp, value: point.value },
  );
}

function paneChartOptions(
  timeVisible: boolean,
  showTimeScale: boolean,
): Parameters<typeof createChart>[1] {
  return {
    autoSize: true,
    layout: {
      background: { type: ColorType.Solid, color: "transparent" },
      textColor: "#8b97a5",
      fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
      fontSize: 10,
    },
    grid: {
      vertLines: { color: "#171d24" },
      horzLines: { color: "#171d24" },
    },
    crosshair: {
      mode: CrosshairMode.Normal,
      vertLine: {
        color: "#8b97a5",
        width: 1,
        style: LineStyle.Dotted,
        labelBackgroundColor: "#232a32",
      },
      horzLine: {
        color: "#8b97a5",
        width: 1,
        style: LineStyle.Dotted,
        labelBackgroundColor: "#232a32",
      },
    },
    rightPriceScale: { borderColor: "#232a32" },
    timeScale: {
      borderColor: "#232a32",
      visible: showTimeScale,
      timeVisible,
      secondsVisible: false,
    },
  };
}

/** Exclusion shading (D-014): a DOM layer above the canvas, per pane. */
function Bands({
  chart,
  exclusions,
  refreshKey,
}: {
  chart: IChartApi | null;
  exclusions: ExclusionView[];
  refreshKey: number;
}) {
  const holderRef = useRef<HTMLDivElement>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!chart) return undefined;
    const handler = () => setTick((value) => value + 1);
    chart.timeScale().subscribeVisibleLogicalRangeChange(handler);
    window.addEventListener("resize", handler);
    return () => {
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(handler);
      window.removeEventListener("resize", handler);
    };
  }, [chart]);

  const rects = useMemo(() => {
    if (!chart || exclusions.length === 0) return [];
    const holder = holderRef.current;
    if (!holder) return [];
    const scale = chart.timeScale();
    const visible = scale.getVisibleRange();
    const width = holder.clientWidth;
    if (!visible || width <= 0) return [];
    const from = Number(visible.from);
    const to = Number(visible.to);
    const clampX = (time: number): number | null => {
      const coordinate = scale.timeToCoordinate(time as UTCTimestamp);
      if (coordinate != null) return coordinate;
      if (time <= from) return 0;
      if (time >= to) return width;
      return null;
    };
    const out: { key: string; left: number; width: number; label: string }[] = [];
    for (const exclusion of exclusions) {
      const startX = clampX(isoToSeconds(exclusion.exclusion_start));
      const endX = clampX(isoToSeconds(exclusion.exclusion_end));
      if (startX == null || endX == null) continue;
      const left = Math.max(0, Math.min(startX, width));
      const right = Math.max(0, Math.min(endX, width));
      if (right <= 0 || left >= width) continue;
      out.push({
        key: exclusion.gap_id,
        left,
        width: Math.max(1, right - left),
        label: `${exclusion.kind} · ${exclusion.exclusion_start} → ${exclusion.exclusion_end} · ${exclusion.status}`,
      });
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chart, exclusions, tick, refreshKey]);

  return (
    <div className="chart-bands" ref={holderRef} aria-hidden="true">
      {rects.map((rect) => (
        <div key={rect.key} className="chart-band" title={rect.label} style={{ left: rect.left, width: rect.width }} />
      ))}
    </div>
  );
}

export default function ChartPane({
  id,
  height,
  timeVisible,
  showTimeScale,
  candles,
  candleBars,
  histogram,
  lines,
  priceLevels,
  exclusions,
  eventMarkers,
  restore,
  publishRange,
  handles,
  rangeRef,
  viewRangeRef,
  onHover,
  onNearLeftEdge,
}: ChartPaneProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef(new Map<string, ISeriesApi<SeriesType>>());
  const anchorRef = useRef<ISeriesApi<SeriesType> | undefined>(undefined);
  const onHoverRef = useRef(onHover);
  const onEdgeRef = useRef(onNearLeftEdge);
  const countRef = useRef(candles.length);
  const [chartReady, setChartReady] = useState<IChartApi | null>(null);
  onHoverRef.current = onHover;
  onEdgeRef.current = onNearLeftEdge;
  useEffect(() => {
    countRef.current = candles.length;
  }, [candles.length]);

  // Chart lifetime: create once, publish a stable handle, tear down on unmount.
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return undefined;
    const chart = createChart(container, paneChartOptions(timeVisible, showTimeScale));
    chartRef.current = chart;
    seriesRef.current.clear();

    const moveHandler = (param: MouseEventParams) => {
      const time = param.time;
      onHoverRef.current(typeof time === "number" ? time : null);
    };
    chart.subscribeCrosshairMove(moveHandler);

    if (publishRange) {
      chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
        if (!range) return;
        rangeRef.current = range;
        // Time range pins the viewport across history prepends: logical
        // indices shift left when older bars are inserted.
        const visible = chart.timeScale().getVisibleRange();
        if (visible) viewRangeRef.current = { from: Number(visible.from), to: Number(visible.to) };
        const total = countRef.current;
        const zoomed = total > 0 && range.to - range.from < total * HISTORY_FIT_RATIO;
        if (range.from < HISTORY_EDGE && zoomed) onEdgeRef.current();
      });
    }

    const handle: PaneHandle = { chart, anchorSeries: () => anchorRef.current };
    handles[id] = handle;
    setChartReady(chart);

    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current.clear();
      anchorRef.current = undefined;
      delete handles[id];
      setChartReady(null);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, publishRange]);

  // Reconcile the series set and push data; runs on every candles/lines change.
  const seriesKey = useMemo(
    () =>
      [
        candleBars ? "candles" : "",
        histogram ? "volume" : "",
        ...lines.map((line) => `${line.id}|${line.color}|${line.dashed ? 1 : 0}|${line.overlay ? 1 : 0}|${line.fixedRange ? "f" : ""}`),
      ].join(";"),
    [candleBars, histogram, lines],
  );

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const desired = new Set(lines.map((line) => `line:${line.id}`));
    if (candleBars) desired.add(CANDLE_KEY);
    if (histogram) desired.add(HIST_KEY);
    for (const [key, series] of Array.from(seriesRef.current.entries())) {
      if (desired.has(key)) continue;
      chart.removeSeries(series);
      seriesRef.current.delete(key);
    }

    if (candleBars && !seriesRef.current.has(CANDLE_KEY)) {
      seriesRef.current.set(
        CANDLE_KEY,
        chart.addSeries(CandlestickSeries, {
          upColor: "#4ade80",
          downColor: "#f87171",
          borderUpColor: "#4ade80",
          borderDownColor: "#f87171",
          wickUpColor: "#4ade80",
          wickDownColor: "#f87171",
          priceLineVisible: false,
        }),
      );
    }
    if (histogram && !seriesRef.current.has(HIST_KEY)) {
      const volume = chart.addSeries(HistogramSeries, {
        priceLineVisible: false,
        priceFormat: { type: "volume" },
      });
      volume.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
      seriesRef.current.set(HIST_KEY, volume);
    }
    for (const line of lines) {
      const key = `line:${line.id}`;
      if (seriesRef.current.has(key)) continue;
      const range = line.fixedRange;
      seriesRef.current.set(
        key,
        chart.addSeries(LineSeries, {
          color: line.color,
          lineWidth: line.lineWidth ?? 1,
          lineStyle: line.dashed ? LineStyle.Dashed : LineStyle.Solid,
          priceScaleId: line.overlay ? "" : "right",
          priceLineVisible: false,
          crosshairMarkerVisible: false,
          ...(range
            ? {
                autoscaleInfoProvider: () => ({
                  priceRange: { minValue: range.min, maxValue: range.max },
                }),
              }
            : {}),
        }),
      );
    }

    const candleData = candles.map((candle) => ({
      time: candle.time as UTCTimestamp,
      open: candle.o,
      high: candle.h,
      low: candle.l,
      close: candle.c,
    }));
    const volumeData = candles.map((candle) => ({
      time: candle.time as UTCTimestamp,
      value: candle.v,
      color: candle.c >= candle.o ? UP_BAR : DOWN_BAR,
    }));
    for (const [key, series] of seriesRef.current.entries()) {
      if (key === CANDLE_KEY) {
        series.setData(candleData as never);
      } else if (key === HIST_KEY) {
        series.setData(volumeData as never);
      } else {
        const line = lines.find((entry) => `line:${entry.id}` === key);
        if (line) series.setData(lineData(line.points) as never);
      }
    }

    anchorRef.current = seriesRef.current.get(CANDLE_KEY) ?? seriesRef.current.get(HIST_KEY) ?? seriesRef.current.values().next().value;
  }, [candles, seriesKey, lines, candleBars, histogram]);

  // Event markers (M4 T4.4): lightweight-charts v5 createSeriesMarkers plugin
  // on the candle series; recreated when the series instance is rebuilt.
  const markersRef = useRef<EventMarkersHandle | null>(null);
  useEffect(() => {
    markersRef.current = null;
  }, [seriesKey]);
  useEffect(() => {
    if (!candleBars || !eventMarkers) return;
    const series = seriesRef.current.get(CANDLE_KEY);
    if (!series) return;
    markersRef.current = applyEventMarkers(series, markersRef.current, eventMarkers);
  }, [candleBars, eventMarkers, seriesKey]);

  // Static price levels (RSI 70/30) attach once per line-series instance.
  const levelsAttached = useRef(new WeakSet<ISeriesApi<SeriesType>>());
  useEffect(() => {
    if (!priceLevels || priceLevels.length === 0) return;
    const lineKey = [...seriesRef.current.keys()].find((key) => key.startsWith("line:"));
    const target = lineKey ? seriesRef.current.get(lineKey) : undefined;
    if (!target || levelsAttached.current.has(target)) return;
    for (const level of priceLevels) {
      target.createPriceLine({
        price: level.price,
        color: "#8b97a5",
        lineStyle: LineStyle.Dashed,
        lineWidth: 1,
        axisLabelVisible: true,
        title: level.title,
      });
    }
    levelsAttached.current.add(target);
  }, [seriesKey, priceLevels]);

  // Keep the visible range: the main pane restores its time range after a
  // history prepend (logical indices shift); sub-panes follow the main
  // pane's logical range whenever their data lands or changes.
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || candles.length === 0) return;
    if (publishRange) {
      if (restore <= 0) return;
      const view = viewRangeRef.current;
      if (view) {
        chart.timeScale().setVisibleRange({
          from: view.from as UTCTimestamp,
          to: view.to as UTCTimestamp,
        });
      }
      return;
    }
    const range = rangeRef.current;
    if (range) chart.timeScale().setVisibleLogicalRange(range);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candles.length, restore, seriesKey, publishRange]);

  return (
    <div className="chart-pane" style={{ height }}>
      <div ref={containerRef} className="chart-pane-canvas" />
      <Bands chart={chartReady} exclusions={exclusions} refreshKey={candles.length + restore} />
    </div>
  );
}
