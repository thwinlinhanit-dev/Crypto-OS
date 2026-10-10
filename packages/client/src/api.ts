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
export type StatsRequest = components["schemas"]["StatsRequest"];
export type StatsResultView = components["schemas"]["StatsResultView"];
export type EvidenceView = components["schemas"]["EvidenceView"];
export type FamilyCreate = components["schemas"]["FamilyCreate"];
export type FamilyPatch = components["schemas"]["FamilyPatch"];
export type FamilyView = components["schemas"]["FamilyView"];
export type VariantView = components["schemas"]["VariantView"];
export type ExperimentCreate = components["schemas"]["ExperimentCreate"];
export type ExperimentPatch = components["schemas"]["ExperimentPatch"];
export type ExperimentView = components["schemas"]["ExperimentView"];
export type LineageView = components["schemas"]["LineageView"];
export type VerifyView = components["schemas"]["VerifyView"];
export type StrategyCreate = components["schemas"]["StrategyCreate"];
export type StrategyView = components["schemas"]["StrategyView"];
export type BacktestCreate = components["schemas"]["BacktestCreate"];
export type BacktestView = components["schemas"]["BacktestView"];
export type TradeView = components["schemas"]["TradeView"];
export type ValidationCreate = components["schemas"]["ValidationCreate"];
export type BaselineCreate = components["schemas"]["BaselineCreate"];
export type RunView = components["schemas"]["RunView"];
export type WatchlistCreate = components["schemas"]["WatchlistCreate"];
export type WatchlistView = components["schemas"]["WatchlistView"];
export type HypothesisView = components["schemas"]["HypothesisView"];
export type HypothesisCreate = components["schemas"]["HypothesisCreate"];
export type HypothesisPatch = components["schemas"]["HypothesisPatch"];
export type NoteView = components["schemas"]["NoteView"];
export type NoteCreate = components["schemas"]["NoteCreate"];
export type NotePatch = components["schemas"]["NotePatch"];

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

export async function postStats(
  body: StatsRequest,
  baseUrl: string = DEFAULT_API_BASE,
): Promise<StatsResultView> {
  return (await postJson("/v1/stats", baseUrl, body)) as StatsResultView;
}

export async function getStats(
  refId: string,
  baseUrl: string = DEFAULT_API_BASE,
): Promise<StatsResultView> {
  return (await readJson(`/v1/stats/${encodeURIComponent(refId)}`, baseUrl)) as StatsResultView;
}

export async function getEventEvidence(
  eventId: string,
  baseUrl: string = DEFAULT_API_BASE,
): Promise<EvidenceView> {
  return (await readJson(
    `/v1/events/${encodeURIComponent(eventId)}`,
    baseUrl,
  )) as EvidenceView;
}

export async function postHypothesis(
  body: HypothesisCreate,
  baseUrl: string = DEFAULT_API_BASE,
): Promise<HypothesisView> {
  return (await postJson("/v1/hypotheses", baseUrl, body)) as HypothesisView;
}

export async function getHypotheses(
  query: Query = {},
  baseUrl: string = DEFAULT_API_BASE,
): Promise<HypothesisView[]> {
  return (await readJson(`/v1/hypotheses${queryString(query)}`, baseUrl)) as HypothesisView[];
}

export async function getHypothesis(
  hypId: string,
  baseUrl: string = DEFAULT_API_BASE,
): Promise<HypothesisView> {
  return (await readJson(
    `/v1/hypotheses/${encodeURIComponent(hypId)}`,
    baseUrl,
  )) as HypothesisView;
}

export async function patchHypothesis(
  hypId: string,
  body: HypothesisPatch,
  baseUrl: string = DEFAULT_API_BASE,
): Promise<HypothesisView> {
  return (await callJson(`/v1/hypotheses/${encodeURIComponent(hypId)}`, baseUrl, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  })) as HypothesisView;
}

export async function openHoldout(
  hypId: string,
  baseUrl: string = DEFAULT_API_BASE,
): Promise<HypothesisView> {
  return (await postJson(
    `/v1/hypotheses/${encodeURIComponent(hypId)}/open-holdout`,
    baseUrl,
    {},
  )) as HypothesisView;
}

export async function postFamily(
  body: FamilyCreate,
  baseUrl: string = DEFAULT_API_BASE,
): Promise<FamilyView> {
  return (await postJson("/v1/families", baseUrl, body)) as FamilyView;
}

export async function getFamilies(baseUrl: string = DEFAULT_API_BASE): Promise<FamilyView[]> {
  return (await readJson("/v1/families", baseUrl)) as FamilyView[];
}

export async function getFamily(
  familyId: string,
  baseUrl: string = DEFAULT_API_BASE,
): Promise<FamilyView> {
  return (await readJson(`/v1/families/${encodeURIComponent(familyId)}`, baseUrl)) as FamilyView;
}

export async function patchFamily(
  familyId: string,
  body: FamilyPatch,
  baseUrl: string = DEFAULT_API_BASE,
): Promise<FamilyView> {
  return (await callJson(`/v1/families/${encodeURIComponent(familyId)}`, baseUrl, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  })) as FamilyView;
}

export async function deleteFamily(
  familyId: string,
  baseUrl: string = DEFAULT_API_BASE,
): Promise<void> {
  const response = await fetch(`${baseUrl}/v1/families/${encodeURIComponent(familyId)}`, {
    method: "DELETE",
    cache: "no-store",
  });
  if (!response.ok) throw new Error(`HTTP_${response.status}: ${familyId}`);
}

export async function getFamilyVariants(
  familyId: string,
  baseUrl: string = DEFAULT_API_BASE,
): Promise<VariantView[]> {
  return (await readJson(
    `/v1/families/${encodeURIComponent(familyId)}/variants`,
    baseUrl,
  )) as VariantView[];
}

export async function postExperiment(
  body: ExperimentCreate,
  baseUrl: string = DEFAULT_API_BASE,
): Promise<ExperimentView> {
  return (await postJson("/v1/experiments", baseUrl, body)) as ExperimentView;
}

export async function getExperiments(
  baseUrl: string = DEFAULT_API_BASE,
): Promise<ExperimentView[]> {
  return (await readJson("/v1/experiments", baseUrl)) as ExperimentView[];
}

export async function getExperiment(
  experimentId: string,
  baseUrl: string = DEFAULT_API_BASE,
): Promise<ExperimentView> {
  return (await readJson(
    `/v1/experiments/${encodeURIComponent(experimentId)}`,
    baseUrl,
  )) as ExperimentView;
}

export async function patchExperiment(
  experimentId: string,
  body: ExperimentPatch,
  baseUrl: string = DEFAULT_API_BASE,
): Promise<ExperimentView> {
  return (await callJson(`/v1/experiments/${encodeURIComponent(experimentId)}`, baseUrl, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  })) as ExperimentView;
}

export async function freezeExperiment(
  experimentId: string,
  baseUrl: string = DEFAULT_API_BASE,
): Promise<ExperimentView> {
  return (await postJson(
    `/v1/experiments/${encodeURIComponent(experimentId)}/freeze`,
    baseUrl,
    {},
  )) as ExperimentView;
}

export async function verifyExperiment(
  experimentId: string,
  baseUrl: string = DEFAULT_API_BASE,
): Promise<VerifyView> {
  return (await readJson(
    `/v1/experiments/${encodeURIComponent(experimentId)}/verify`,
    baseUrl,
  )) as VerifyView;
}

export async function getExperimentLineage(
  experimentId: string,
  baseUrl: string = DEFAULT_API_BASE,
): Promise<LineageView> {
  return (await readJson(
    `/v1/experiments/${encodeURIComponent(experimentId)}/lineage`,
    baseUrl,
  )) as LineageView;
}

export async function postStrategy(
  body: StrategyCreate,
  baseUrl: string = DEFAULT_API_BASE,
): Promise<StrategyView> {
  return (await postJson("/v1/strategies", baseUrl, body)) as StrategyView;
}

export async function getStrategies(baseUrl: string = DEFAULT_API_BASE): Promise<StrategyView[]> {
  return (await readJson("/v1/strategies", baseUrl)) as StrategyView[];
}

export async function getStrategy(
  strategyId: string,
  version: string,
  baseUrl: string = DEFAULT_API_BASE,
): Promise<StrategyView> {
  return (await readJson(
    `/v1/strategies/${encodeURIComponent(strategyId)}/${encodeURIComponent(version)}`,
    baseUrl,
  )) as StrategyView;
}

export async function postBacktest(
  body: BacktestCreate,
  baseUrl: string = DEFAULT_API_BASE,
): Promise<BacktestView> {
  return (await postJson("/v1/backtests", baseUrl, body)) as BacktestView;
}

export async function getBacktests(baseUrl: string = DEFAULT_API_BASE): Promise<BacktestView[]> {
  return (await readJson("/v1/backtests", baseUrl)) as BacktestView[];
}

export async function getBacktest(
  backtestId: string,
  baseUrl: string = DEFAULT_API_BASE,
): Promise<BacktestView> {
  return (await readJson(
    `/v1/backtests/${encodeURIComponent(backtestId)}`,
    baseUrl,
  )) as BacktestView;
}

export async function getBacktestTrades(
  backtestId: string,
  baseUrl: string = DEFAULT_API_BASE,
): Promise<TradeView[]> {
  return (await readJson(
    `/v1/backtests/${encodeURIComponent(backtestId)}/trades`,
    baseUrl,
  )) as TradeView[];
}

export async function getBacktestMetrics(
  backtestId: string,
  baseUrl: string = DEFAULT_API_BASE,
): Promise<Record<string, unknown>> {
  return (await readJson(
    `/v1/backtests/${encodeURIComponent(backtestId)}/metrics`,
    baseUrl,
  )) as Record<string, unknown>;
}

export async function getBacktestSkipped(
  backtestId: string,
  baseUrl: string = DEFAULT_API_BASE,
): Promise<Record<string, unknown>[]> {
  return (await readJson(
    `/v1/backtests/${encodeURIComponent(backtestId)}/skipped`,
    baseUrl,
  )) as Record<string, unknown>[];
}

export async function getBacktestEquity(
  backtestId: string,
  baseUrl: string = DEFAULT_API_BASE,
): Promise<Record<string, unknown>[]> {
  return (await readJson(
    `/v1/backtests/${encodeURIComponent(backtestId)}/equity`,
    baseUrl,
  )) as Record<string, unknown>[];
}

export async function postValidation(
  body: ValidationCreate,
  baseUrl: string = DEFAULT_API_BASE,
): Promise<RunView[]> {
  return (await postJson("/v1/validation", baseUrl, body)) as RunView[];
}

export async function getValidation(
  runId: string,
  baseUrl: string = DEFAULT_API_BASE,
): Promise<RunView> {
  return (await readJson(
    `/v1/validation/${encodeURIComponent(runId)}`,
    baseUrl,
  )) as RunView;
}

export async function postRandomBaseline(
  body: BaselineCreate,
  baseUrl: string = DEFAULT_API_BASE,
): Promise<RunView> {
  return (await postJson("/v1/baselines/random", baseUrl, body)) as RunView;
}

export async function postWatchlist(
  body: WatchlistCreate,
  baseUrl: string = DEFAULT_API_BASE,
): Promise<WatchlistView> {
  return (await postJson("/v1/watchlists", baseUrl, body)) as WatchlistView;
}

export async function getWatchlists(
  baseUrl: string = DEFAULT_API_BASE,
): Promise<WatchlistView[]> {
  return (await readJson("/v1/watchlists", baseUrl)) as WatchlistView[];
}

export async function deleteWatchlist(
  watchlistId: string,
  baseUrl: string = DEFAULT_API_BASE,
): Promise<void> {
  const response = await fetch(`${baseUrl}/v1/watchlists/${encodeURIComponent(watchlistId)}`, {
    method: "DELETE",
    cache: "no-store",
  });
  if (!response.ok) throw new Error(`HTTP_${response.status}: ${watchlistId}`);
}

export async function getScanner(
  params: {
    watchlist?: string;
    tf?: string;
    min_grade?: string;
    as_of?: string;
  },
  baseUrl: string = DEFAULT_API_BASE,
): Promise<Record<string, unknown>> {
  const query = new URLSearchParams();
  if (params.watchlist) query.set("watchlist", params.watchlist);
  if (params.tf) query.set("tf", params.tf);
  if (params.min_grade) query.set("min_grade", params.min_grade);
  if (params.as_of) query.set("as_of", params.as_of);
  const suffix = query.toString();
  return (await readJson(`/v1/scanner${suffix ? `?${suffix}` : ""}`, baseUrl)) as Record<
    string,
    unknown
  >;
}

export async function getMtf(
  instrumentId: string,
  baseUrl: string = DEFAULT_API_BASE,
): Promise<Record<string, unknown>> {
  return (await readJson(`/v1/mtf?instrument_id=${encodeURIComponent(instrumentId)}`, baseUrl)) as Record<
    string,
    unknown
  >;
}

export async function postNote(
  body: NoteCreate,
  baseUrl: string = DEFAULT_API_BASE,
): Promise<NoteView> {
  return (await postJson("/v1/notes", baseUrl, body)) as NoteView;
}

export async function getNotes(baseUrl: string = DEFAULT_API_BASE): Promise<NoteView[]> {
  return (await readJson("/v1/notes", baseUrl)) as NoteView[];
}

export async function getNote(
  noteId: string,
  baseUrl: string = DEFAULT_API_BASE,
): Promise<NoteView> {
  return (await readJson(`/v1/notes/${encodeURIComponent(noteId)}`, baseUrl)) as NoteView;
}

export async function patchNote(
  noteId: string,
  body: NotePatch,
  baseUrl: string = DEFAULT_API_BASE,
): Promise<NoteView> {
  return (await callJson(`/v1/notes/${encodeURIComponent(noteId)}`, baseUrl, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  })) as NoteView;
}

export async function deleteNote(noteId: string, baseUrl: string = DEFAULT_API_BASE): Promise<void> {
  const response = await fetch(`${baseUrl}/v1/notes/${encodeURIComponent(noteId)}`, {
    method: "DELETE",
    cache: "no-store",
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as
      | { error?: { code?: string; message?: string } }
      | null;
    const error = body?.error;
    throw new Error(
      `${error?.code ?? `HTTP_${response.status}`}: ${error?.message ?? noteId}`,
    );
  }
}
