"use client";

import { create } from "zustand";
import { persist } from "zustand/middleware";

/**
 * Chart layout persistence (T2.3, decision D-012): instrument, timeframe,
 * enabled overlays, pane state and pane order persist client-side in
 * localStorage through a zustand-persisted store. Length fields are request
 * prefills the operator can edit; they are echoed by the API in `params`
 * (PD-11 applies to the server, never to the UI).
 */

export type OverlayId = "sma" | "ema" | "vwap" | "bb";
export type SubPaneId = "volume" | "rsi";

export interface SubPaneState {
  id: SubPaneId;
  enabled: boolean;
  collapsed: boolean;
}

export interface ChartLayoutState {
  instrumentId: string | null;
  timeframe: string;
  overlays: Record<OverlayId, boolean>;
  smaLength: number;
  emaLength: number;
  rsiLength: number;
  atrLength: number;
  eventsEnabled: boolean;
  subPanes: SubPaneState[];
  setInstrument: (instrumentId: string) => void;
  setTimeframe: (timeframe: string) => void;
  toggleOverlay: (id: OverlayId) => void;
  setLength: (field: "smaLength" | "emaLength" | "rsiLength" | "atrLength", value: number) => void;
  toggleEvents: () => void;
  togglePane: (id: SubPaneId) => void;
  setPaneCollapsed: (id: SubPaneId, collapsed: boolean) => void;
  movePane: (id: SubPaneId, delta: number) => void;
  resetLayout: () => void;
}

const DEFAULTS = {
  instrumentId: null,
  timeframe: "1D",
  overlays: { sma: false, ema: true, vwap: false, bb: false },
  smaLength: 20,
  emaLength: 20,
  rsiLength: 14,
  atrLength: 14,
  eventsEnabled: true,
  subPanes: [
    { id: "volume", enabled: true, collapsed: false },
    { id: "rsi", enabled: false, collapsed: false },
  ],
} satisfies Partial<ChartLayoutState>;

function defaults(): Pick<
  ChartLayoutState,
  "instrumentId" | "timeframe" | "overlays" | "smaLength" | "emaLength" | "rsiLength" | "atrLength" | "eventsEnabled" | "subPanes"
> {
  return {
    ...DEFAULTS,
    overlays: { ...DEFAULTS.overlays },
    subPanes: DEFAULTS.subPanes.map((pane) => ({ ...pane })),
  };
}

export const useChartLayout = create<ChartLayoutState>()(
  persist(
    (set) => ({
      ...defaults(),
      setInstrument: (instrumentId) => set({ instrumentId }),
      setTimeframe: (timeframe) => set({ timeframe }),
      toggleOverlay: (id) =>
        set((state) => ({ overlays: { ...state.overlays, [id]: !state.overlays[id] } })),
      setLength: (field, value) => {
        switch (field) {
          case "smaLength":
            set({ smaLength: value });
            break;
          case "emaLength":
            set({ emaLength: value });
            break;
          case "rsiLength":
            set({ rsiLength: value });
            break;
          default:
            set({ atrLength: value });
        }
      },
      toggleEvents: () => set((state) => ({ eventsEnabled: !state.eventsEnabled })),
      togglePane: (id) =>
        set((state) => ({
          subPanes: state.subPanes.map((pane) =>
            pane.id === id ? { ...pane, enabled: !pane.enabled } : pane,
          ),
        })),
      setPaneCollapsed: (id, collapsed) =>
        set((state) => ({
          subPanes: state.subPanes.map((pane) =>
            pane.id === id ? { ...pane, collapsed } : pane,
          ),
        })),
      movePane: (id, delta) =>
        set((state) => {
          const index = state.subPanes.findIndex((pane) => pane.id === id);
          const target = index + delta;
          if (index < 0 || target < 0 || target >= state.subPanes.length) return state;
          const subPanes = [...state.subPanes];
          const [moved] = subPanes.splice(index, 1);
          subPanes.splice(target, 0, moved);
          return { subPanes };
        }),
      resetLayout: () => set(defaults()),
    }),
    { name: "cryptosos-chart-layout", version: 1 },
  ),
);
