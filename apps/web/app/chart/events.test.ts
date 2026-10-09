import { describe, expect, it } from "vitest";

import type { EventEnvelopeView } from "@cryptosos/client";

import {
  EVENT_MARKER_STYLES,
  applyEventMarkers,
  eventToMarker,
  toSeriesMarkers,
  type EventMarkerSpec,
} from "./events";

// Minimal envelope factory — every field the API contract pins (PD-5).
function makeEnvelope(overrides: Partial<EventEnvelopeView> = {}): EventEnvelopeView {
  return {
    event_id: "e1",
    detector_id: "rvol_climax_v5",
    dataset_version: "23bce73d",
    instrument_id: "BTCUSDT",
    timeframe: "1D",
    event_type: "CONT",
    event_time: "2024-01-08T00:00:00Z",
    direction: "LONG",
    ref_price: 103.0,
    invalidation_price: 97.0,
    features: { rvol: 10.0, cpos: 0.1, hiN: 105.0, swingLo: 98.0, emaR: 101.0, atr: 2.0 },
    trigger: { rules: [{ name: "rvol_ge", passed: true, value: 10.0, threshold: 2.0 }] },
    ...overrides,
  } as EventEnvelopeView;
}

describe("EVENT_MARKER_STYLES (Section 12)", () => {
  it("pins exact shapes, colors, labels, positions", () => {
    expect(EVENT_MARKER_STYLES.CONT).toEqual({
      shape: "arrowUp",
      color: "#2dd4bf",
      text: "CONT",
      position: "belowBar",
    });
    expect(EVENT_MARKER_STYLES.CAPIT).toEqual({
      shape: "labelUp",
      color: "#a3e635",
      text: "CAPIT",
      position: "belowBar",
    });
    expect(EVENT_MARKER_STYLES.FAIL).toEqual({
      shape: "arrowDown",
      color: "#fb923c",
      text: "FAIL",
      position: "aboveBar",
    });
    expect(EVENT_MARKER_STYLES.TOP).toEqual({
      shape: "arrowDown",
      color: "#ef4444",
      text: "TOP",
      position: "aboveBar",
    });
  });
});

describe("eventToMarker", () => {
  it("maps CONT envelope to correct marker spec", () => {
    const spec = eventToMarker(makeEnvelope({ event_type: "CONT", direction: "LONG" }));
    expect(spec).not.toBeNull();
    expect(spec!.eventId).toBe("e1");
    expect(spec!.eventType).toBe("CONT");
    expect(spec!.shape).toBe("arrowUp");
    expect(spec!.color).toBe("#2dd4bf");
    expect(spec!.text).toBe("CONT");
    expect(spec!.time).toBe(Math.floor(Date.parse("2024-01-08T00:00:00Z") / 1000));
  });

  it("returns null for unknown event_type", () => {
    expect(eventToMarker(makeEnvelope({ event_type: "FAST_RECLAIM" }))).toBeNull();
    expect(eventToMarker(makeEnvelope({ event_type: "SPEC_REQUIRED_PLACEHOLDER" }))).toBeNull();
  });

  it("returns null for invalid event_time", () => {
    expect(eventToMarker(makeEnvelope({ event_time: "not-a-date" }))).toBeNull();
  });
});

describe("toSeriesMarkers", () => {
  it("converts labelUp to arrowUp with text", () => {
    const specs: EventMarkerSpec[] = [
      {
        eventId: "e2",
        eventType: "CAPIT",
        direction: "LONG",
        time: 1704672000,
        refPrice: 88.5,
        invalidationPrice: 74.3,
        shape: "labelUp",
        color: "#a3e635",
        text: "CAPIT",
        envelope: makeEnvelope({ event_type: "CAPIT" }),
      },
    ];
    const markers = toSeriesMarkers(specs);
    expect(markers).toHaveLength(1);
    expect(markers[0]!.shape).toBe("arrowUp");
    expect(markers[0]!.text).toBe("CAPIT");
    expect(markers[0]!.position).toBe("belowBar");
    expect(markers[0]!.color).toBe("#a3e635");
  });

  it("keeps arrowDown for FAIL/TOP aboveBar", () => {
    const specs: EventMarkerSpec[] = [
      {
        eventId: "e3",
        eventType: "FAIL",
        direction: "SHORT",
        time: 1704758400,
        refPrice: 92.0,
        invalidationPrice: 110.97,
        shape: "arrowDown",
        color: "#fb923c",
        text: "FAIL",
        envelope: makeEnvelope({ event_type: "FAIL", direction: "SHORT" }),
      },
    ];
    const markers = toSeriesMarkers(specs);
    expect(markers[0]!.shape).toBe("arrowDown");
    expect(markers[0]!.position).toBe("aboveBar");
  });
});

describe("applyEventMarkers (v5 plugin)", () => {
  it("creates plugin on first call, updates on second", () => {
    // lightweight-charts v5 createSeriesMarkers needs a real series with
    // attachPrimitive; we provide a minimal stub satisfying the internal API.
    const mockSeries = {
      attachPrimitive: (primitive: unknown) => primitive,
      detachPrimitive: () => {},
    } as unknown as never;
    const specs: EventMarkerSpec[] = [
      {
        eventId: "e1",
        eventType: "CONT",
        direction: "LONG",
        time: 1704672000,
        refPrice: 103.0,
        invalidationPrice: 97.0,
        shape: "arrowUp",
        color: "#2dd4bf",
        text: "CONT",
        envelope: makeEnvelope(),
      },
    ];
    // First call with null existing → creates plugin
    const handle1 = applyEventMarkers(mockSeries, null, specs);
    expect(handle1).toBeTruthy();
    // Second call with existing handle → reuses it
    const handle2 = applyEventMarkers(mockSeries, handle1, []);
    expect(handle2).toBe(handle1);
  });
});
