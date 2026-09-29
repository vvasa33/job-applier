import { useEffect, useState } from "react";

import type { ApplicationDetail, ReviewAction } from "../api/applications";

export function Intervention({
  application,
  busy,
  onReview,
}: {
  application: ApplicationDetail;
  busy: boolean;
  onReview: (action: ReviewAction, fieldId: string, value?: string) => void;
}) {
  const question = application.pending_fields[0] ?? null;
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [confirmStop, setConfirmStop] = useState(false);
  const suggestion = question?.proposed_value?.trim() ?? "";

  useEffect(() => {
    setEditing(false);
    setDraft(suggestion);
    setConfirmStop(false);
  }, [application.id, application.status_changed_at, question?.field_id, suggestion]);

  return (
    <article className="grid gap-6">
      <header>
        <p className="text-sm text-[var(--muted)]">{application.company}</p>
        <h2 className="mt-1 text-2xl font-medium tracking-tight">{application.title}</h2>
      </header>

      <PageContext application={application} />

      {question ? (
        <section className="grid gap-3">
          <p className="text-sm text-[var(--muted)]">
            {application.pending_fields.length > 1
              ? `Question 1 of ${application.pending_fields.length}`
              : "Question"}
            {question.required ? " · Required" : ""}
          </p>
          <h3 className="text-xl font-medium">{question.label || question.field_id}</h3>
          {question.options.length > 0 ? (
            <p className="text-sm text-[var(--muted)]">Choices: {question.options.join(", ")}</p>
          ) : null}
          <div className="rounded-xl border border-[var(--line)] bg-[var(--panel)] px-4 py-3">
            <p className="text-sm text-[var(--muted)]">Suggested answer</p>
            <p className="mt-1">{suggestion || "No answer was suggested."}</p>
            <p className="mt-3 text-sm text-[var(--muted)]">Why this was suggested</p>
            <p className="mt-1 text-sm">{question.reasoning || "The system did not explain a suggestion."}</p>
            <p className="mt-3 text-sm">Confidence {Math.round(question.confidence * 100)}%</p>
          </div>

          {editing ? (
            <div className="grid gap-2">
              <label className="grid gap-1 text-sm">
                <span className="text-[var(--muted)]">Your answer</span>
                <input value={draft} onChange={(event) => setDraft(event.target.value)} className={controlClass} />
              </label>
              {question.options.length > 0 ? (
                <div className="flex flex-wrap gap-2">
                  {question.options.map((option) => (
                    <button key={option} type="button" className={buttonClass} onClick={() => setDraft(option)}>
                      {option}
                    </button>
                  ))}
                </div>
              ) : null}
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  className={primaryClass}
                  disabled={busy || !draft.trim()}
                  onClick={() => onReview("edit", question.field_id, draft.trim())}
                >
                  Save answer
                </button>
                <button type="button" className={buttonClass} disabled={busy} onClick={() => setEditing(false)}>
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                className={primaryClass}
                disabled={busy || !suggestion}
                onClick={() => onReview("approve", question.field_id)}
              >
                Approve
              </button>
              <button
                type="button"
                className={buttonClass}
                disabled={busy}
                onClick={() => {
                  setDraft(suggestion);
                  setEditing(true);
                }}
              >
                Edit
              </button>
              <button type="button" className={buttonClass} disabled={busy} onClick={() => onReview("skip", question.field_id)}>
                Skip
              </button>
              <StopButton busy={busy} confirm={confirmStop} onConfirm={setConfirmStop} onStop={() => onReview("stop", "")} />
            </div>
          )}
          <p className="text-sm text-[var(--muted)]">Saving an answer continues this application. It is not submitted.</p>
        </section>
      ) : (
        <section className="grid gap-3">
          <h3 className="text-xl font-medium">The page needs a look</h3>
          <p>{application.waiting_reason || "The application paused before a question could be read."}</p>
          <StopButton busy={busy} confirm={confirmStop} onConfirm={setConfirmStop} onStop={() => onReview("stop", "")} />
        </section>
      )}
    </article>
  );
}

function PageContext({ application }: { application: ApplicationDetail }) {
  return (
    <section className="grid gap-2">
      <h3 className="text-sm font-medium">Page</h3>
      {application.page_title ? <p className="text-sm">{application.page_title}</p> : null}
      {application.page_url ? <p className="break-all text-sm text-[var(--muted)]">{application.page_url}</p> : null}
      {application.has_screenshot ? (
        <img
          src={`/api/applications/${application.id}/screenshot?at=${encodeURIComponent(application.status_changed_at)}`}
          alt="Application page"
          className="max-h-[28rem] w-full rounded-xl border border-[var(--line)] bg-[var(--panel)] object-contain object-top"
        />
      ) : (
        <p className="text-sm text-[var(--muted)]">No page image was captured.</p>
      )}
    </section>
  );
}

function StopButton({
  busy,
  confirm,
  onConfirm,
  onStop,
}: {
  busy: boolean;
  confirm: boolean;
  onConfirm: (value: boolean) => void;
  onStop: () => void;
}) {
  if (!confirm) {
    return (
      <button type="button" className={buttonClass} disabled={busy} onClick={() => onConfirm(true)}>
        Stop application
      </button>
    );
  }
  return (
    <span className="flex flex-wrap gap-2">
      <button type="button" className={buttonClass} disabled={busy} onClick={onStop}>
        Withdraw this application
      </button>
      <button type="button" className={buttonClass} disabled={busy} onClick={() => onConfirm(false)}>
        Keep reviewing
      </button>
    </span>
  );
}

const controlClass = "h-10 rounded-xl border border-[var(--line)] bg-[var(--panel)] px-3 text-sm text-[var(--fg)]";
const buttonClass =
  "inline-flex h-10 items-center rounded-xl border border-[var(--line)] bg-[var(--panel)] px-3 text-sm disabled:opacity-50";
const primaryClass =
  "inline-flex h-10 items-center rounded-xl bg-[var(--accent)] px-3 text-sm text-[var(--accent-fg)] disabled:opacity-50";
