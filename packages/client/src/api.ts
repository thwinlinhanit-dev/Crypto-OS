import type { components } from "./schema";

export type StatusResponse = components["schemas"]["StatusResponse"];
export type DefaultsResponse = components["schemas"]["DefaultsResponse"];
export type InstrumentView = components["schemas"]["InstrumentView"];
export type HealthView = components["schemas"]["HealthView"];
export type GapView = components["schemas"]["GapView"];
export type QualityView = components["schemas"]["QualityView"];
export type DatasetManifestView = components["schemas"]["DatasetManifestView"];
export type CandleQualityView = components["schemas"]["CandleQualityView"];
export type ExclusionView = components["schemas"]["ExclusionView"];
export type WindowView = components["schemas"]["WindowView"];
export type CandlesView = components["schemas"]["CandlesView"];
export type SeriesView = components["schemas"]["SeriesView"];
export type IndicatorsView = components["schemas"]["IndicatorsView"];
export type JobView = components["schemas"]["JobView"];
export type JobQueued = components["schemas"]["JobQueued"];
export type IngestRequest = components["schemas"]["IngestRequest"];
export type RepairRequest = components["schemas"]["RepairRequest"];
export type EventEnvelopeView = components["schemas"]["EventEnvelopeView"];
export type EventsView = components["schemas"]["EventsView"];

/** Where the API listens by default; override per environment. */
export const DEFAULT_API_BASE = "http://127.0.0.1:8000";

type Query = Record<string, string | number | undefined>;

function queryString(query: Query): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined) params.set(key, String(value));
  }
  const encoded = params.toString();
  return encoded ? `?${encoded}` : "";
}

async function callJson(path: string, baseUrl: string, init?: RequestInit): Promise<unknown> {
  const response = await fetch(`${baseUrl}${path}`, { cache: "no-store", ...init });
  if (!response.ok) {
    // The envelope is {error:{code,message,detail}}; fall back to the status.
    const body = (await response.json().catch(() => null)) as
      | { error?: { code?: string; message?: string } }
      | null;
    const error = body?.error;
    throw new Error(
      `${error?.code ?? `HTTP_${response.status}`}: ${error?.message ?? path}`,
    );
  }
  return response.json();
}

async function readJson(path: string, baseUrl: string): Promise<unknown> {
  return callJson(path, baseUrl);
}

async function postJson(path: string, baseUrl: string, body: unknown): Promise<unknown> {
  return callJson(path, baseUrl, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function getStatus(baseUrl: string = DEFAULT_API_BASE): Promise<StatusResponse> {
  return (await readJson("/v1/status", baseUrl)) as StatusResponse;
}

export async function getDefaults(baseUrl: string = DEFAULT_API_BASE): Promise<DefaultsResponse> {
  return (await readJson("/v1/config/defaults", baseUrl)) as DefaultsResponse;
}

export async function getInstruments(
  query: Query = {},
  baseUrl: string = DEFAULT_API_BASE,
): Promise<InstrumentView[]> {
  return (await readJson(`/v1/instruments${queryString(query)}`, baseUrl)) as InstrumentView[];
}

export async function getDataHealth(
  query: Query = {},
  baseUrl: string = DEFAULT_API_BASE,
): Promise<HealthView> {
  return (await readJson(`/v1/data/health${queryString(query)}`, baseUrl)) as HealthView;
}

export async function getDataGaps(
  query: Query = {},
  baseUrl: string = DEFAULT_API_BASE,
): Promise<GapView[]> {
  return (await readJson(`/v1/data/gaps${queryString(query)}`, baseUrl)) as GapView[];
}

export async function getJob(
  jobId: string,
  baseUrl: string = DEFAULT_API_BASE,
): Promise<JobView> {
  return (await readJson(`/v1/jobs/${encodeURIComponent(jobId)}`, baseUrl)) as JobView;
}

export async function getDataset(
  datasetVersion: string,
  baseUrl: string = DEFAULT_API_BASE,
): Promise<DatasetManifestView> {
  return (await readJson(
    `/v1/datasets/${encodeURIComponent(datasetVersion)}`,
    baseUrl,
  )) as DatasetManifestView;
}

export async function getCandles(
  query: Query,
  baseUrl: string = DEFAULT_API_BASE,
): Promise<CandlesView> {
  return (await readJson(`/v1/candles${queryString(query)}`, baseUrl)) as CandlesView;
}

export async function getIndicators(
  query: Query,
  baseUrl: string = DEFAULT_API_BASE,
): Promise<IndicatorsView> {
  return (await readJson(`/v1/indicators${queryString(query)}`, baseUrl)) as IndicatorsView;
}

export async function getEvents(
  query: Query,
  baseUrl: string = DEFAULT_API_BASE,
): Promise<EventsView> {
  return (await readJson(`/v1/events${queryString(query)}`, baseUrl)) as EventsView;
}

export async function postIngest(
  body: IngestRequest,
  baseUrl: string = DEFAULT_API_BASE,
): Promise<JobQueued> {
  return (await postJson("/v1/data/ingest", baseUrl, body)) as JobQueued;
}

export async function postRepair(
  body: RepairRequest,
  baseUrl: string = DEFAULT_API_BASE,
): Promise<JobQueued> {
  return (await postJson("/v1/data/repair", baseUrl, body)) as JobQueued;
}
