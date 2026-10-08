"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

/**
 * Command palette (design section 7): Ctrl/Cmd+K over every direct jump the
 * chart workspace offers — instrument, timeframe, overlay, pane, layout and
 * navigation. Pure client-side actions; nothing here resolves parameters.
 */

export interface PaletteAction {
  id: string;
  group: string;
  label: string;
  run: () => void;
}

export default function CommandPalette({
  open,
  onClose,
  actions,
}: {
  open: boolean;
  onClose: () => void;
  actions: PaletteAction[];
}) {
  const [filter, setFilter] = useState("");
  const [cursor, setCursor] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);

  useEffect(() => {
    if (!open) return undefined;
    setFilter("");
    setCursor(0);
    inputRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const matches = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    if (!needle) return actions;
    return actions.filter(
      (action) =>
        action.label.toLowerCase().includes(needle) ||
        action.group.toLowerCase().includes(needle),
    );
  }, [actions, filter]);

  useEffect(() => {
    setCursor((value) => Math.min(value, Math.max(0, matches.length - 1)));
  }, [matches.length]);

  useEffect(() => {
    const node = listRef.current?.children[cursor];
    if (node instanceof HTMLElement) node.scrollIntoView({ block: "nearest" });
  }, [cursor]);

  if (!open) return null;

  const move = (delta: number) => {
    setCursor((value) => Math.max(0, Math.min(matches.length - 1, value + delta)));
  };
  const commit = (index: number) => {
    const action = matches[index];
    if (!action) return;
    action.run();
    onClose();
  };

  return (
    <div className="palette-backdrop" role="presentation" onMouseDown={onClose}>
      <div
        className="palette"
        role="dialog"
        aria-label="Command palette"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <input
          ref={inputRef}
          className="palette-input"
          value={filter}
          placeholder="Jump to… (instrument, timeframe, overlay, pane)"
          onChange={(event) => setFilter(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "ArrowDown") {
              event.preventDefault();
              move(1);
            } else if (event.key === "ArrowUp") {
              event.preventDefault();
              move(-1);
            } else if (event.key === "Enter") {
              event.preventDefault();
              commit(cursor);
            }
          }}
        />
        <ul className="palette-list" ref={listRef}>
          {matches.length === 0 && <li className="palette-empty">No matches</li>}
          {(() => {
            const rows: ReactNode[] = [];
            let group = "";
            matches.forEach((action, index) => {
              if (action.group !== group) {
                group = action.group;
                rows.push(
                  <li key={`${action.id}-group`} className="palette-group">
                    {group}
                  </li>,
                );
              }
              rows.push(
                <li
                  key={action.id}
                  className={index === cursor ? "palette-item is-active" : "palette-item"}
                  onMouseEnter={() => setCursor(index)}
                  onClick={() => commit(index)}
                >
                  {action.label}
                </li>,
              );
            });
            return rows;
          })()}
        </ul>
        <div className="palette-footer muted">↑↓ move · Enter run · Esc close</div>
      </div>
    </div>
  );
}
