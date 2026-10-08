"use client";

import { useCallback, useEffect, useState } from "react";

import { DEFAULT_API_BASE, getDefaults, getStatus } from "@cryptosos/client";
import type { DefaultsResponse, StatusResponse } from "@cryptosos/client";

export default function HomePage() {
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [defaults, setDefaults] = useState<DefaultsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [nextStatus, nextDefaults] = await Promise.all([
        getStatus(DEFAULT_API_BASE),
        getDefaults(DEFAULT_API_BASE),
      ]);
      setStatus(nextStatus);
      setDefaults(nextDefaults);
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), 5000);
    return () => clearInterval(timer);
  }, [refresh]);

  return (
    <>
      <section className="panel">
        <h1>API status</h1>
        {error ? (
          <p className="bad">
            {error} — start the API with <code>make dev-api</code>.
          </p>
        ) : null}
        {status ? (
          <dl>
            <dt>service</dt>
            <dd>{status.service}</dd>
            <dt>status</dt>
            <dd className={status.status === "ok" ? "ok" : "bad"}>{status.status}</dd>
            <dt>database</dt>
            <dd className={status.database === "up" ? "ok" : "bad"}>{status.database}</dd>
            <dt>api_version</dt>
            <dd>{status.api_version}</dd>
            <dt>service_version</dt>
            <dd>{status.service_version}</dd>
            <dt>engine_version</dt>
            <dd>{status.engine_version}</dd>
            <dt>code_version</dt>
            <dd>{status.code_version}</dd>
            <dt>dataset_schema_version</dt>
            <dd>{status.dataset_schema_version}</dd>
            <dt>spec</dt>
            <dd>{status.spec}</dd>
          </dl>
        ) : null}
      </section>

      <section className="panel">
        <h1>Documented defaults (PD-11)</h1>
        {defaults ? (
          <>
            <p className="muted">source: {defaults.source}</p>
            <pre>{JSON.stringify(defaults.defaults, null, 2)}</pre>
          </>
        ) : null}
      </section>
    </>
  );
}
