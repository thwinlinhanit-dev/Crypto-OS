/**
 * M10 — the perp sub-pane data (T10.1/D-074): funding, open interest and
 * liquidations, fetched from the persisted perp rows (PD-5: every number
 * comes from a stored result, never recomputed in the browser).
 *
 * Rendered under the candles for perp instruments only; a spot instrument
 * never shows these panes (PD-10).
 */

import { API_BASE } from "@/lib/api";

export interface FundingPoint {
  time: number;
  value: number;
}

export interface OiPoint {
  time: number;
  value: number;
}

export interface LiquidationPoint {
  time: number;
  side: "LONG" | "SHORT";
  qty: number;
  price: number;
}

export interface PerpData {
  funding: FundingPoint[];
  openInterest: OiPoint[];
  liquidations: LiquidationPoint[];
}

const EMPTY: PerpData = {
  funding: [],
  openInterest: [],
  liquidations: [],
};

function toMs(iso: string): number {
  const parsed = Date.parse(iso);
  return Number.isNaN(parsed) ? 0 : parsed;
}

/** Fetch the perp rows for one instrument (all three feeds). */
export async function fetchPerpData(
  instrumentId: string,
  options?: { signal?: AbortSignal },
): Promise<PerpData> {
  const [funding, oi, liquidations] = await Promise.all([
    fetch(`${API_BASE}/v1/funding/${instrumentId}?limit=500`, {
      signal: options?.signal,
      cache: "no-store",
    }).then((response) => {
      if (!response.ok) throw new Error(`HTTP_${response.status}`);
      return response.json() as Promise<{ funding: Array<{ ts: string; rate: number }> }>;
    }),
    fetch(`${API_BASE}/v1/oi/${instrumentId}?tf=1D&limit=500`, {
      signal: options?.signal,
      cache: "no-store",
    }).then((response) => {
      if (!response.ok) throw new Error(`HTTP_${response.status}`);
      return response.json() as Promise<{
        open_interest: Array<{ bar_time: string; oi: number }>;
      }>;
    }),
    fetch(`${API_BASE}/v1/liquidations/${instrumentId}?limit=500`, {
      signal: options?.signal,
      cache: "no-store",
    }).then((response) => {
      if (!response.ok) throw new Error(`HTTP_${response.status}`);
      return response.json() as Promise<{
        liquidations: Array<{
          ts: string;
          side: "LONG" | "SHORT";
          qty: number;
          price: number;
        }>;
      }>;
    }),
  ]);
  return {
    funding: funding.funding.map((row) => ({
      time: toMs(row.ts),
      value: row.rate * 100, // bps: the funding rate as basis points
    })),
    openInterest: oi.open_interest.map((row) => ({
      time: toMs(row.bar_time),
      value: row.oi,
    })),
    liquidations: liquidations.liquidations.map((row) => ({
      time: toMs(row.ts),
      side: row.side,
      qty: row.qty,
      price: row.price,
    })),
  };
}

/** A spot instrument has no perp data: the panes never render (PD-10). */
export function emptyPerpData(): PerpData {
  return { ...EMPTY, funding: [], openInterest: [], liquidations: [] };
}
