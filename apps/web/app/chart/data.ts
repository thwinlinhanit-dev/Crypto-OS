"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQueries, useQuery } from "@tanstack/react-query";
import type { UseQueryResult } from "@tanstack/react-query";

import { getCandles, getIndicators, getInstruments } from "@cryptosos/client";
import type { CandleQualityView, CandlesView, IndicatorsView, InstrumentView } from "@cryptosos/client";

import { API_BASE } from "@/lib/api";

/**
 * /chart data layer (M2 design note section 6). One candle window plus its
 * indicator windows are fetched in parallel per history chunk and merged
 * client-side. Required indicator lengths (OQ-13) are explicit request
 * values the operator can edit; the API echoes what it resolved (PD-11
 * applies to the server, never to the UI).
 */

/** Timeframes the frozen manifest carries (OQ-12: no RESAMPLE manifest). */
export const TIMEFRAMES = ["1D", "4H", "1H"] as const;
export const CANDLE_LIMIT = 5000;
/** Scroll position (logical bars from the left edge) that loads history. */
export const HISTORY_EDGE = 25;
/** A view this wide (fraction of loaded bars) counts as a fit, not a zoom. */
export const HISTORY_FIT_RATIO = 0.9;

export interface WindowRange {
  from?: string;
  to?: string;
}

export interface CandlePoint {
  time: number;
  o: number;
  h: number;
  l: number;
  c: number;
  v: number;
}

export interface SeriesPoint {
  time: number;
  value: number | null;
}

export interface NamedSeries {
  name: string;
  points: SeriesPoint[];
}

export interface HoverRow extends CandlePoint {
  rvol: number | null;
  cpos: number | null;
  upperWick: number | null;
  atr: number | null;
  ema100: number | null;
}

export interface IndicatorRequest {
  key: string;
  name: string;
  params?: Record<string, number>;
}

export function isoToSeconds(iso: string): number {
  return Math.floor(Date.parse(iso) / 1000);
}

/**
 * Hover-row metrics (spec section 12). rvol/cpos/upper_wick resolve from
 * documented defaults; atr/ema carry explicit request lengths (OQ-13).
 */
export function hoverRequests(atrLength: number): IndicatorRequest[] {
  return [
    { key: "hover:rvol", name: "rvol" },
    { key: "hover:cpos", name: "cpos" },
    { key: "hover:upper_wick", name: "upper_wick" },
    { key: "hover:atr", name: "atr", params: { length: atrLength } },
    { key: "regime:ema", name: "ema", params: { length: 100 } },
  ];
}

/** Enabled chart overlays; bb/rvol use their documented defaults. */
export function overlayRequests(
  overlays: Record<string, boolean>,
  smaLength: number,
  emaLength: number,
): IndicatorRequest[] {
  const requests: IndicatorRequest[] = [];
  if (overlays.sma) requests.push({ key: "overlay:sma", name: "sma", params: { length: smaLength } });
  if (overlays.ema) requests.push({ key: "overlay:ema", name: "ema", params: { length: emaLength } });
  if (overlays.vwap) requests.push({ key: "overlay:vwap", name: "vwap" });
  if (overlays.bb) requests.push({ key: "overlay:bb", name: "bb" });
  return requests;
}

export function useRegistry() {
  return useQuery({
    queryKey: ["instruments", "BINANCE", "SPOT"],
    queryFn: () => getInstruments({ venue: "BINANCE", market_type: "SPOT" }, API_BASE),
    staleTime: 300_000,
  });
}

interface ChartQueryBundle {
  windows: WindowRange[];
  candles: CandlePoint[];
  series: Map<string, NamedSeries[]>;
  hover: Map<number, HoverRow>;
  quality: CandleQualityView | null;
  datasetVersion: string | null;
  loading: boolean;
  error: string | null;
  staleError: string | null;
  indicatorError: string | null;
  loadingOlder: boolean;
  noMoreHistory: boolean;
  restore: number;
  loadOlder: () => void;
}

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/**
 * Windowed candle+indicator queries with lazy history (design D-013) and
 * scroll-position restoration (restore token consumed by the panes).
 */
export function useChartWindows(
  instrumentId: string | null,
  tf: string,
  specs: IndicatorRequest[],
): ChartQueryBundle {
  const [windows, setWindows] = useState<WindowRange[]>([{}]);
  const [noMoreHistory, setNoMoreHistory] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [restore, setRestore] = useState(0);

  // A new (instrument, timeframe) is a new universe view: drop every window.
  useEffect(() => {
    setWindows([{}]);
    setNoMoreHistory(false);
    setLoadingOlder(false);
    setRestore(0);
  }, [instrumentId, tf]);

  const queries = useQueries({
    queries: useMemo(() => {
      if (!instrumentId) return [];
      const candles = windows.map((w) => ({
        queryKey: ["candles", instrumentId, tf, w.from ?? null, w.to ?? null, CANDLE_LIMIT] as const,
        queryFn: () =>
          getCandles(
            { instrument_id: instrumentId, tf, from: w.from, to: w.to, limit: CANDLE_LIMIT },
            API_BASE,
          ),
        staleTime: 60_000,
        retry: 1,
      }));
      const indicators = windows.flatMap((w) =>
        specs.map((spec) => ({
          queryKey: [
            "indicators",
            instrumentId,
            tf,
            w.from ?? null,
            w.to ?? null,
            spec.key,
          ] as const,
          queryFn: () =>
            getIndicators(
              {
                instrument_id: instrumentId,
                tf,
                name: spec.name,
                params: spec.params ? JSON.stringify(spec.params) : undefined,
                from: w.from,
                to: w.to,
                limit: CANDLE_LIMIT,
              },
              API_BASE,
            ),
          staleTime: 60_000,
          retry: 1,
        })),
      );
      return [...candles, ...indicators];
    }, [instrumentId, tf, windows, specs]),
  });

  const windowCount = instrumentId ? windows.length : 0;
  const candleResults = queries.slice(0, windowCount) as UseQueryResult<CandlesView>[];
  const indicatorResults = queries.slice(windowCount) as UseQueryResult<IndicatorsView>[];

  // `useQueries` hands back a fresh array on every render, so `queries` itself
  // is an unstable memo dependency: depending on it rebuilt `merged` (and with
  // it `candles`/`series`) each render, which re-pushed series data on every
  // commit and fed the crosshair a new time to relay — an update loop. The
  // content signature below only moves when a query's result actually changes.
  const dataSignature = queries
    .map((result) => `${result.dataUpdatedAt}:${result.isError ? 1 : 0}:${result.data ? 1 : 0}`)
    .join("|");

  const merged = useMemo(() => {
    const candles: CandlePoint[] = [];
    const seen = new Set<number>();
    for (const result of candleResults) {
      const view = result.data;
      if (!view) continue;
      for (let index = 0; index < view.t.length; index += 1) {
        const time = isoToSeconds(view.t[index]);
        if (seen.has(time)) continue;
        seen.add(time);
        candles.push({
          time,
          o: view.o[index],
          h: view.h[index],
          l: view.l[index],
          c: view.c[index],
          v: view.v[index],
        });
      }
    }
    candles.sort((left, right) => left.time - right.time);

    const series = new Map<string, NamedSeries[]>();
    // `indicatorResults` already excludes the candle queries (sliced off the
    // front), so its index 0 is the first indicator of window 0.
    let cursor = 0;
    for (let windowIndex = 0; windowIndex < windows.length; windowIndex += 1) {
      for (const spec of specs) {
        const view = indicatorResults[cursor]?.data;
        cursor += 1;
        if (!view) continue;
        const bucket = series.get(spec.key) ?? [];
        for (const output of view.series) {
          let named = bucket.find((entry) => entry.name === output.key);
          if (!named) {
            named = { name: output.key, points: [] };
            bucket.push(named);
          }
          for (let index = 0; index < view.t.length; index += 1) {
            named.points.push({ time: isoToSeconds(view.t[index]), value: output.values[index] ?? null });
          }
        }
        series.set(spec.key, bucket);
      }
    }

    const valuesAt = (key: string): Map<number, number | null> => {
      const points = series.get(key)?.[0]?.points ?? [];
      const table = new Map<number, number | null>();
      for (const point of points) table.set(point.time, point.value);
      return table;
    };
    const rvol = valuesAt("hover:rvol");
    const cpos = valuesAt("hover:cpos");
    const wick = valuesAt("hover:upper_wick");
    const atr = valuesAt("hover:atr");
    const ema100 = valuesAt("regime:ema");
    const hover = new Map<number, HoverRow>();
    for (const candle of candles) {
      hover.set(candle.time, {
        ...candle,
        rvol: rvol.get(candle.time) ?? null,
        cpos: cpos.get(candle.time) ?? null,
        upperWick: wick.get(candle.time) ?? null,
        atr: atr.get(candle.time) ?? null,
        ema100: ema100.get(candle.time) ?? null,
      });
    }

    const newest = candleResults[candleResults.length - 1]?.data ?? candleResults[0]?.data ?? null;
    return {
      candles,
      series,
      hover,
      quality: newest?.quality ?? null,
      datasetVersion: newest?.dataset_version ?? null,
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dataSignature, windowCount, windows.length, specs]);

  const candleError = candleResults.find((result) => result.isError)?.error ?? null;
  const indicatorError = indicatorResults.find((result) => result.isError)?.error ?? null;
  const hasData = merged.candles.length > 0;
  const loading =
    !hasData &&
    !candleError &&
    (candleResults.length === 0 || candleResults.some((result) => result.fetchStatus === "fetching"));

  // Oldest-window verdict: keep, top up (partial) or stop (history exhausted).
  useEffect(() => {
    if (windows.length <= 1) return;
    const oldest = candleResults[0];
    if (!oldest || oldest.fetchStatus === "fetching") return;
    setLoadingOlder(false);
    if (oldest.isError) {
      setWindows((current) => current.slice(1));
      setNoMoreHistory(true);
      return;
    }
    if (oldest.data && oldest.data.t.length < CANDLE_LIMIT) setNoMoreHistory(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candleResults[0], windows.length]);

  const candlesRef = useRef<CandlePoint[]>([]);
  useEffect(() => {
    candlesRef.current = merged.candles;
  }, [merged]);

  const loadOlder = useCallback(() => {
    if (!instrumentId || loadingOlder || noMoreHistory) return;
    const oldest = candlesRef.current[0];
    if (!oldest) return;
    setLoadingOlder(true);
    setRestore((token) => token + 1);
    setWindows((current) => [{ to: new Date(oldest.time * 1000 - 1).toISOString() }, ...current]);
  }, [instrumentId, loadingOlder, noMoreHistory]);

  return {
    windows,
    candles: merged.candles,
    series: merged.series,
    hover: merged.hover,
    quality: merged.quality,
    datasetVersion: merged.datasetVersion,
    loading,
    error: hasData ? null : candleError ? messageOf(candleError) : null,
    staleError: hasData && candleError ? messageOf(candleError) : null,
    indicatorError: indicatorError ? messageOf(indicatorError) : null,
    loadingOlder,
    noMoreHistory,
    restore,
    loadOlder,
  };
}

export type { InstrumentView };
