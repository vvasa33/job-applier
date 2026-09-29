import { type ReactNode, useEffect, useState } from "react";

import type { ApplicationDetail, ReviewAction } from "../api/applications";
import { buttonClass, controlClass, primaryClass } from "../ui";

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
            <p className="mt-3 text-sm">
              <span className="text-[var(--muted)]">Confidence </span>
              <meter
                min={0}
                max={1}
                value={question.confidence}
                className="align-middle"
                aria-label="Suggestion confidence"
              >
                {Math.round(question.confidence * 100)}%
              </meter>
              <span className="ml-2 tabular-nums">{Math.round(question.confidence * 100)}%</span>
            </p>
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
          <p className="text-sm text-[var(--muted)]">
            Answering continues this application once every question is resolved. Submitting is a separate step.
          </p>
        </section>
      ) : (
        <Pause
          application={application}
          busy={busy}
          stop={
            <StopButton busy={busy} confirm={confirmStop} onConfirm={setConfirmStop} onStop={() => onReview("stop", "")} />
          }
          onReview={onReview}
        />
      )}
    </article>
  );
}

function Pause({
  application,
  busy,
  stop,
  onReview,
}: {
  application: ApplicationDetail;
  busy: boolean;
  stop: ReactNode;
  onReview: (action: ReviewAction, fieldId: string, value?: string) => void;
}) {
  const reason = application.waiting_reason || "The application paused before a question could be read.";
  if (application.resume_queued) {
    return (
      <section className="grid gap-3">
        <h3 className="text-xl font-medium">Continuing</h3>
        <p>
          {application.agent_owns_browser
            ? "Your decision is saved. The agent will pick this application up in its browser shortly."
            : "Your decision is saved. Start the agent to continue this application."}
        </p>
        <div className="flex flex-wrap gap-2">{stop}</div>
      </section>
    );
  }
  if (application.pause_kind === "confirm_submit") {
    return (
      <section className="grid gap-3">
        <h3 className="text-xl font-medium">Ready to submit</h3>
        <p>{reason}</p>
        <p className="text-sm text-[var(--muted)]">
          The review page is shown above. Submitting presses the site&apos;s Submit button once. It is never retried. If an
          answer changes after you confirm, you are asked again.
        </p>
        <div className="flex flex-wrap gap-2">
          <button type="button" className={primaryClass} disabled={busy} onClick={() => onReview("confirm_submit", "")}>
            Submit application
          </button>
          {stop}
        </div>
      </section>
    );
  }
  if (application.pause_kind === "verify_submit") {
    return (
      <section className="grid gap-3">
        <h3 className="text-xl font-medium">Did it go through?</h3>
        <p>{reason}</p>
        <p className="text-sm text-[var(--muted)]">
          Check the site or your email for a confirmation. The agent will not press Submit again for this application.
        </p>
        <div className="flex flex-wrap gap-2">
          <button type="button" className={primaryClass} disabled={busy} onClick={() => onReview("confirm_submitted", "")}>
            It went through
          </button>
          {stop}
        </div>
      </section>
    );
  }
  return (
    <section className="grid gap-3">
      <h3 className="text-xl font-medium">The page needs a look</h3>
      <p>{reason}</p>
      <p className="text-sm text-[var(--muted)]">
        Fix anything on the page in the browser window if needed, then continue. The application restarts from its first
        page and reuses every saved answer.
      </p>
      <div className="flex flex-wrap gap-2">
        <button type="button" className={primaryClass} disabled={busy} onClick={() => onReview("continue", "")}>
          Continue
        </button>
        {stop}
      </div>
    </section>
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
          alt={`Application page for ${application.title} at ${application.company}`}
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

