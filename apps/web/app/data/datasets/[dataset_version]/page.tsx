"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { getDataset } from "@cryptosos/client";
import type { DatasetManifestView } from "@cryptosos/client";

import { API_BASE } from "@/lib/api";

/**
 * Manifest viewer (design note section 8): exactly the section 4.4 fields, so
 * the manifest answers "what exactly did this use?" (AC2).
 */

function utc(iso: string): string {
  return `${iso.slice(0, 19).replace("T", " ")}Z`;
}

function messageOf(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause);
}

export default function DatasetManifestPage() {
  const params = useParams<{ dataset_version: string }>();
  const datasetVersion = params.dataset_version;
  const [manifest, setManifest] = useState<DatasetManifestView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  const load = useCallback(async () => {
    try {
      setManifest(await getDataset(datasetVersion, API_BASE));
      setError(null);
    } catch (cause) {
      setError(messageOf(cause));
    } finally {
      setLoaded(true);
    }
  }, [datasetVersion]);

  useEffect(() => {
    void load();
  }, [load]);

  if (!loaded) {
    return (
      <section className="panel">
        <h1>Dataset manifest</h1>
        <p className="section-note" aria-busy="true">
          Loading manifest…
        </p>
      </section>
    );
  }

  if (manifest === null && error !== null) {
    return (
      <section className="panel">
        <h1>Dataset manifest</h1>
        <div className="banner banner-error">
          <span>{error}</span>
          <button className="btn" type="button" onClick={() => void load()}>
            Retry
          </button>
        </div>
        <Link className="back-link" href="/data">
          ← Back to Data Health
        </Link>
      </section>
    );
  }

  if (manifest === null) return null;

  return (
    <>
      <section className="panel">
        <h1>Dataset manifest</h1>
        {error !== null ? (
          <div className="banner banner-stale">
            <span>Showing the last good read — {error}</span>
            <button className="btn" type="button" onClick={() => void load()}>
              Retry
            </button>
          </div>
        ) : null}
        <dl>
          <dt>dataset_version</dt>
          <dd className="hash">{manifest.dataset_version}</dd>
          <dt>venue / market_type</dt>
          <dd>
            {manifest.venue} / {manifest.market_type}
          </dd>
          <dt>instruments</dt>
          <dd>{manifest.instruments.join(", ")}</dd>
          <dt>timeframes</dt>
          <dd>{manifest.timeframes.join(", ")}</dd>
          <dt>range</dt>
          <dd>
            {utc(manifest.start_time)} → {utc(manifest.end_time)}
          </dd>
          <dt>created_at</dt>
          <dd>{utc(manifest.created_at)}</dd>
          <dt>dataset_schema_version</dt>
          <dd>{manifest.dataset_schema_version}</dd>
          <dt>bar_semantics_version</dt>
          <dd>{manifest.bar_semantics_version}</dd>
          <dt>timezone_policy_version</dt>
          <dd>{manifest.timezone_policy_version}</dd>
          <dt>instrument_metadata_version</dt>
          <dd>{manifest.instrument_metadata_version}</dd>
        </dl>
        <p style={{ marginTop: "1rem", marginBottom: 0 }}>
          <Link className="back-link" href="/data">
            ← Back to Data Health
          </Link>
        </p>
      </section>

      <section className="panel">
        <h1>Source files</h1>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Path</th>
                <th>content_sha256</th>
                <th>Rows</th>
              </tr>
            </thead>
            <tbody>
              {manifest.source_files.map((file) => (
                <tr key={file.path}>
                  <td className="hash">{file.path}</td>
                  <td className="hash">{file.content_sha256}</td>
                  <td className="numeric">{file.rows.toLocaleString("en-US")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel">
        <h1>Row counts</h1>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Key</th>
                <th>Rows</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(manifest.row_counts).map(([key, rows]) => (
                <tr key={key}>
                  <td>{key}</td>
                  <td className="numeric">{rows.toLocaleString("en-US")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel">
        <h1>Missing ranges</h1>
        {manifest.missing_ranges.length === 0 ? (
          <p className="section-note">None.</p>
        ) : (
          <pre className="json-block">{JSON.stringify(manifest.missing_ranges, null, 2)}</pre>
        )}
      </section>

      <section className="panel">
        <h1>Quality summary</h1>
        <pre className="json-block">{JSON.stringify(manifest.quality_summary, null, 2)}</pre>
      </section>
    </>
  );
}
