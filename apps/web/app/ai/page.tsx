/**
 * M9 — the AI Copilot chat page (spec §9, design note m9_ai section 6).
 *
 * The five modes (Research, Quant, Data, Critic, Writer), each with its
 * system prompt + allowed tool subset (spec §9). Every number the model
 * emits must carry a {{ref:<ref_id>#<json_path>}} placeholder; unresolved
 * placeholders are flagged here (the M9 AC: "numbers without ref
 * flagged"), and the executed tool calls are listed with their refs
 * (PD-5).
 */

"use client";

import { useCallback, useState } from "react";
import { getAiTools, postAiChat } from "@cryptosos/client";

import { API_BASE } from "@/lib/api";

const MODES = ["Research", "Quant", "Data", "Critic", "Writer"] as const;
type Mode = (typeof MODES)[number];

const MODE_NOTES: Record<Mode, string> = {
  Research: "Research questions from stored results; may create hypotheses, experiments, notes.",
  Quant: "Backtest + validation answers from stored results; data_scope and n_eff shown.",
  Data: "Data-health and ingest questions; read-only tools.",
  Critic: "Critiques the conclusion; must list at least three ways it could be wrong.",
  Writer: "Composes the report: n_eff, intervals, data_scope, OOS, multiple-testing, limitations.",
};

interface Turn {
  role: "user" | "assistant";
  content: string;
  toolCalls?: Array<{ name: string; result_ref: string }>;
  unresolved?: string[];
}

function messageOf(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause);
}

export default function AiPage() {
  const [mode, setMode] = useState<Mode>("Research");
  const [input, setInput] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toolsOpen, setToolsOpen] = useState(false);

  const send = useCallback(async () => {
    const prompt = input.trim();
    if (prompt === "" || busy) return;
    setBusy(true);
    setError(null);
    setInput("");
    const messages = [
      ...turns.map((turn) => ({ role: turn.role, content: turn.content })),
      { role: "user", content: prompt },
    ];
    setTurns((current) => [...current, { role: "user", content: prompt }]);
    try {
      const result = await postAiChat({ messages, mode }, API_BASE);
      setTurns((current) => [
        ...current,
        {
          role: "assistant",
          content: result.text,
          toolCalls: result.toolCalls,
          unresolved: result.unresolvedPlaceholders,
        },
      ]);
    } catch (cause) {
      setError(messageOf(cause));
    } finally {
      setBusy(false);
    }
  }, [busy, input, mode, turns]);

  return (
    <div className="panel">
      <h2>AI Copilot</h2>
      <p className="muted">
        Modes: system prompt + allowed tool subset (spec 9). Numbers are emitted only as
        placeholders resolved server-side from stored tool results (PD-5); an unresolvable
        placeholder is flagged, never rendered raw.
      </p>

      <div className="btn-row" role="group" aria-label="Copilot mode">
        {MODES.map((option) => (
          <button
            key={option}
            type="button"
            className={`btn ${mode === option ? "primary" : ""}`}
            aria-pressed={mode === option}
            onClick={() => setMode(option)}
          >
            {option}
          </button>
        ))}
      </div>
      <p className="muted small">{MODE_NOTES[mode]}</p>

      <button
        type="button"
        className="btn link"
        onClick={() => setToolsOpen((open) => !open)}
        aria-expanded={toolsOpen}
      >
        {toolsOpen ? "Hide" : "Show"} tool registry
      </button>
      {toolsOpen && <ToolRegistry />}

      <div className="chat-log" role="log" aria-live="polite">
        {turns.length === 0 ? (
          <p className="muted">
            Ask a research question. The Copilot calls the stored tools and answers with
            referenced numbers only.
          </p>
        ) : (
          turns.map((turn, index) => (
            <div key={index} className={`chat-turn ${turn.role}`}>
              <span className="chat-role">{turn.role}</span>
              <p className="chat-content">{turn.content}</p>
              {turn.unresolved && turn.unresolved.length > 0 && (
                <p className="banner banner-warn" role="alert">
                  Unresolved placeholders (flagged, not rendered raw):{" "}
                  {turn.unresolved.join(", ")}
                </p>
              )}
              {turn.toolCalls && turn.toolCalls.length > 0 && (
                <ul className="tool-calls">
                  {turn.toolCalls.map((call, callIndex) => (
                    <li key={callIndex}>
                      <code>{call.name}</code> → <code>{call.result_ref}</code>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ))
        )}
      </div>

      {error != null && (
        <div className="banner banner-error" role="alert">
          <span>Chat failed: {error}</span>
        </div>
      )}

      <div className="chat-composer">
        <textarea
          className="chat-input"
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder={`Ask the ${mode} mode…`}
          aria-label="Message"
          rows={3}
        />
        <button type="button" className="btn primary" onClick={() => void send()} disabled={busy}>
          {busy ? "Thinking…" : "Send"}
        </button>
      </div>
    </div>
  );
}

function ToolRegistry() {
  const [tools, setTools] = useState<Array<Record<string, unknown>> | null>(null);
  const [failed, setFailed] = useState(false);

  const load = useCallback(() => {
    void getAiTools(undefined, API_BASE)
      .then((response) => setTools(response.tools))
      .catch(() => setFailed(true));
  }, []);

  if (tools == null && !failed) {
    // load once on first render (the panel is opt-in via the toggle)
    queueMicrotask(load);
    return <p className="muted">Loading the registry…</p>;
  }
  if (failed) return <p className="muted">Registry unavailable.</p>;
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th>tool</th>
            <th>tier</th>
            <th>holdout-gated</th>
            <th>touches frozen</th>
          </tr>
        </thead>
        <tbody>
          {(tools ?? []).map((tool) => (
            <tr key={String(tool.name)}>
              <td>
                <code>{String(tool.name)}</code>
              </td>
              <td>{String(tool.tier)}</td>
              <td>{tool.requires_holdout ? "yes" : "-"}</td>
              <td>{tool.touches_frozen ? "yes" : "-"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted small">
        No order-related tool exists (spec 9); the registry is closed.
      </p>
    </div>
  );
}
