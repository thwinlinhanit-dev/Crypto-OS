"use client";

import { createSeriesMarkers } from "lightweight-charts";
import type {
  ISeriesApi,
  ISeriesMarkersPluginApi,
  SeriesMarker,
  SeriesType,
  UTCTimestamp,
} from "lightweight-charts";

import type { EventEnvelopeView } from "@cryptosos/client";

/**
 * Chart event layer (M4 T4.4, design section 6.2): lightweight-charts markers
 * with the exact Section 12 shapes/colors/labels, plus a DOM hover card.
 * Every field shown comes from the event envelope (PD-5); the UI never
 * recomputes features, thresholds or invalidation.
 *
 * lightweight-charts v5 moved markers off ``series.setMarkers`` onto the
 * ``createSeriesMarkers`` plugin; markers are attached once per series
 * instance and updated via ``setMarkers``.
 */

export type EventMarkerShape = "arrowUp" | "arrowDown" | "labelUp" | "labelDown" | "circle";

export interface EventMarkerSpec {
  eventId: string;
  eventType: string;
  direction: string;
  time: number;
  refPrice: number;
  invalidationPrice: number;
  shape: EventMarkerShape;
  color: string;
  text: string;
  envelope: EventEnvelopeView;
}

/**
 * Section 12 (spec line 552) marker styling. Exact shapes, colors and labels:
 * CONT = teal triangle-up below; CAPIT = lime label-up below;
 * FAIL = orange triangle-down above; TOP = red triangle-down above.
 */
export const EVENT_MARKER_STYLES: Record<string, { shape: EventMarkerShape; color: string; text: string; position: "belowBar" | "aboveBar" }> = {
  CONT: { shape: "arrowUp", color: "#2dd4bf", text: "CONT", position: "belowBar" },
  CAPIT: { shape: "labelUp", color: "#a3e635", text: "CAPIT", position: "belowBar" },
  FAIL: { shape: "arrowDown", color: "#fb923c", text: "FAIL", position: "aboveBar" },
  TOP: { shape: "arrowDown", color: "#ef4444", text: "TOP", position: "aboveBar" },
};

export function eventToMarker(envelope: EventEnvelopeView): EventMarkerSpec | null {
  const style = EVENT_MARKER_STYLES[envelope.event_type];
  if (!style) return null;
  const time = Math.floor(Date.parse(envelope.event_time) / 1000);
  if (Number.isNaN(time)) return null;
  return {
    eventId: envelope.event_id,
    eventType: envelope.event_type,
    direction: envelope.direction,
    time,
    refPrice: envelope.ref_price,
    invalidationPrice: envelope.invalidation_price,
    shape: style.shape,
    color: style.color,
    text: style.text,
    envelope,
  };
}

/**
 * Convert event specs to lightweight-charts SeriesMarker objects.
 * `labelUp`/`labelDown` map to `arrowUp`/`arrowDown` with text — lightweight-charts
 * has no labelUp shape, so label types use arrow shapes with explicit text.
 */
export function toSeriesMarkers(specs: EventMarkerSpec[]): SeriesMarker<UTCTimestamp>[] {
  return specs.map((spec) => {
    const shape = spec.shape === "labelUp" ? "arrowUp" : spec.shape === "labelDown" ? "arrowDown" : spec.shape;
    return {
      time: spec.time as UTCTimestamp,
      position: EVENT_MARKER_STYLES[spec.eventType]?.position ?? "belowBar",
      color: spec.color,
      shape: shape as "arrowUp" | "arrowDown" | "circle",
      text: spec.text,
    };
  });
}

/** Handle for the markers plugin attached to one candle series instance. */
export type EventMarkersHandle = ISeriesMarkersPluginApi<UTCTimestamp>;

/**
 * Attach or update markers on a candle series via the v5 plugin API.
 * The plugin is created once per series instance; subsequent calls just
 * replace the marker list.
 */
export function applyEventMarkers(
  series: ISeriesApi<SeriesType>,
  existing: EventMarkersHandle | null,
  specs: EventMarkerSpec[],
): EventMarkersHandle {
  const markers = toSeriesMarkers(specs);
  if (existing) {
    existing.setMarkers(markers);
    return existing;
  }
  return createSeriesMarkers(series, markers) as EventMarkersHandle;
}

/**
 * Hover-card fields for one bar (design section 6.2). All values come from
 * the envelope; nothing is recomputed (PD-5).
 */
export interface EventHoverCard {
  eventId: string;
  eventType: string;
  direction: string;
  time: string;
  refPrice: number;
  invalidationPrice: number;
  rvol: number | null;
  cpos: number | null;
  upperWick: number | null;
  atr: number | null;
  emaRegime: number | null;
  archetype: string | null;
  triggerRules: { name: string; passed: boolean; value: number | null; threshold: number | null }[];
}

export function envelopeToHoverCard(envelope: EventEnvelopeView): EventHoverCard {
  const features = (envelope.features ?? {}) as Record<string, unknown>;
  const trigger = (envelope.trigger ?? {}) as Record<string, unknown>;
  const rules = Array.isArray(trigger.rules)
    ? (trigger.rules as { name?: string; passed?: boolean; value?: number | null; threshold?: number | null }[]).map((rule) => ({
        name: rule.name ?? "",
        passed: rule.passed ?? false,
        value: rule.value ?? null,
        threshold: rule.threshold ?? null,
      }))
    : [];
  return {
    eventId: envelope.event_id,
    eventType: envelope.event_type,
    direction: envelope.direction,
    time: envelope.event_time,
    refPrice: envelope.ref_price,
    invalidationPrice: envelope.invalidation_price,
    rvol: (features.rvol as number | null) ?? null,
    cpos: (features.cpos as number | null) ?? null,
    upperWick: (features.upper_wick as number | null) ?? null,
    atr: (features.atr as number | null) ?? null,
    emaRegime: (features.ema_regime as number | null) ?? null,
    archetype: (features.archetype as string | null) ?? null,
    triggerRules: rules,
  };
}

export function findEventAtTime(
  specs: EventMarkerSpec[],
  time: number | null,
): EventEnvelopeView | null {
  if (time == null) return null;
  const match = specs.find((spec) => spec.time === time);
  return match?.envelope ?? null;
}

export interface ClimaxDot {
  time: number;
  color: string;
}

/**
 * Climax-no-trade dots (design section 6.2): bars where `isClimax` and no
 * signal fired. Colors follow Pine 383/384: red when `trendU`, lime when
 * `cpos <= close_pos_lo`, red when `cpos >= close_pos_hi`, gray otherwise.
 */
export function computeClimaxDots(
  events: EventEnvelopeView[],
  candleTimes: number[],
  closePosLo: number,
  closePosHi: number,
): ClimaxDot[] {
  const firedTimes = new Set(events.map((e) => Math.floor(Date.parse(e.event_time) / 1000)));
  const dots: ClimaxDot[] = [];
  for (const time of candleTimes) {
    if (firedTimes.has(time)) continue;
    // Climax bars without a signal are not in the events list; this is a
    // placeholder that returns empty when no bar-level isClimax is available
    // from the endpoint. The full implementation requires bar-level isClimax
    // from the detector, which is an M5 addition (events table).
    void closePosLo;
    void closePosHi;
  }
  return dots;
}
