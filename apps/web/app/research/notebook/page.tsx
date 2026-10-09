"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { deleteNote, getNotes, patchNote, postNote } from "@cryptosos/client";

import { API_BASE } from "@/lib/api";

/**
 * Notebook (spec UI 549, design note m5_stats section 7): note list plus a
 * markdown editor (plain textarea + rendered preview, no new dependencies).
 * Notes link to experiment_id (NULL until M5b); "Save to Notebook" on the
 * Evidence panel writes here.
 */

interface Draft {
  noteId: string | null;
  title: string;
  body: string;
}

const EMPTY: Draft = { noteId: null, title: "", body: "" };

function Markdown({ text }: { text: string }) {
  const lines = text.split("\n");
  return (
    <div className="note-preview">
      {lines.map((line, index) => {
        if (line === "") return <br key={index} />;
        if (line.startsWith("### ")) {
          return <h5 key={index}>{line.slice(4)}</h5>;
        }
        if (line.startsWith("## ")) {
          return <h4 key={index}>{line.slice(3)}</h4>;
        }
        if (line.startsWith("# ")) {
          return <h3 key={index}>{line.slice(2)}</h3>;
        }
        return <p key={index}>{line}</p>;
      })}
    </div>
  );
}

export default function NotebookPage() {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<Draft>(EMPTY);

  const notes = useQuery({
    queryKey: ["notes"],
    queryFn: () => getNotes(API_BASE),
    staleTime: 10_000,
    retry: 1,
  });

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["notes"] });
  };

  const save = useMutation({
    mutationFn: (payload: Draft) =>
      draft.noteId == null
        ? postNote(
            { title: payload.title, body_md: payload.body, experiment_id: null },
            API_BASE,
          )
        : patchNote(
            draft.noteId,
            { title: payload.title, body_md: payload.body },
            API_BASE,
          ),
    onSuccess: (note) => {
      setDraft({ noteId: note.note_id, title: note.title, body: note.body_md });
      refresh();
    },
  });

  const remove = useMutation({
    mutationFn: (noteId: string) => deleteNote(noteId, API_BASE),
    onSuccess: () => {
      setDraft(EMPTY);
      refresh();
    },
  });

  useEffect(() => {
    if (draft.noteId == null) setDraft(EMPTY);
  }, [draft.noteId]);

  return (
    <div className="panel">
      <h2>Notebook</h2>
      <p className="muted">
        Research notes. Notes reference experiments by id (M5b); &quot;Save to Notebook&quot; on
        the Evidence panel writes here.
      </p>

      <div className="notebook-grid">
        <section aria-label="Notes">
          <h4>Notes</h4>
          {notes.isLoading ? (
            <p className="muted">Loading notes.</p>
          ) : notes.isError ? (
            <div className="banner banner-error">
              <span>Failed: {notes.error?.message ?? "unknown error"}</span>
              <button type="button" className="btn" onClick={() => void notes.refetch()}>
                Retry
              </button>
            </div>
          ) : notes.data == null || notes.data.length === 0 ? (
            <p className="muted">No notes yet. Write one on the right.</p>
          ) : (
            <ul className="log-list">
              {notes.data.map((note) => (
                <li key={note.note_id}>
                  <button
                    type="button"
                    className="btn link"
                    onClick={() =>
                      setDraft({
                        noteId: note.note_id,
                        title: note.title,
                        body: note.body_md,
                      })
                    }
                  >
                    {note.title}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section aria-label="Editor">
          <h4>{draft.noteId == null ? "New note" : "Edit note"}</h4>
          <input
            className="chart-select"
            placeholder="Title"
            aria-label="Title"
            value={draft.title}
            onChange={(event) => setDraft({ ...draft, title: event.target.value })}
          />
          <textarea
            className="note-editor"
            aria-label="Markdown body"
            rows={14}
            value={draft.body}
            onChange={(event) => setDraft({ ...draft, body: event.target.value })}
          />
          <div className="btn-row">
            <button
              type="button"
              className="btn"
              disabled={draft.title.trim() === "" || draft.body.trim() === "" || save.isPending}
              onClick={() => save.mutate(draft)}
            >
              {save.isPending ? "Saving." : "Save"}
            </button>
            <button type="button" className="btn" onClick={() => setDraft(EMPTY)}>
              New
            </button>
            {draft.noteId != null && (
              <button
                type="button"
                className="btn"
                disabled={remove.isPending}
                onClick={() => remove.mutate(draft.noteId ?? "")}
              >
                {remove.isPending ? "Deleting." : "Delete"}
              </button>
            )}
          </div>
          {save.isError && (
            <div className="banner banner-error">
              Save failed: {save.error?.message ?? "unknown error"}
            </div>
          )}
          {remove.isError && (
            <div className="banner banner-error">
              Delete failed: {remove.error?.message ?? "unknown error"}
            </div>
          )}
          <Markdown text={draft.body} />
        </section>
      </div>
    </div>
  );
}
