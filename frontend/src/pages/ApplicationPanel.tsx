import { useEffect, useState } from "react";

import {
  ApplicationRequestError,
  fetchApplication,
  openApplication,
  reviewApplication,
  transitionApplication,
  type ApplicationDetail,
  type ReviewAction,
} from "../api/applications";
import { formatDateTime, label } from "../format";
import { Intervention } from "./Intervention";

export function ApplicationPanel({ jobId, onStatus }: { jobId: number; onStatus?: (status: string | null) => void }) {
  const [state, setState] = useState<
    { phase: "loading" } | { phase: "error" } | { phase: "empty" } | { phase: "ready"; application: ApplicationDetail }
  >({ phase: "loading" });
  const [reason, setReason] = useState("");
  const [versionId, setVersionId] = useState("");
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    void fetchApplication(jobId, controller.signal)
      .then((application) => {
        if (controller.signal.aborted) return;
        setState(application ? { phase: "ready", application } : { phase: "empty" });
        onStatus?.(application ? application.status : null);
      })
      .catch(() => {
        if (!controller.signal.aborted) setState({ phase: "error" });
      });
    return () => controller.abort();
  }, [jobId, onStatus]);

  async function start() {
    setPending(true);
    setMessage(null);
    try {
      const application = await openApplication(jobId, reason.trim() || "Application opened.");
      setReason("");
      setState({ phase: "ready", application });
      onStatus?.(application.status);
    } catch (error) {
      if (error instanceof ApplicationRequestError && error.status === 409) {
        const application = await fetchApplication(jobId);
        if (application) {
          setState({ phase: "ready", application });
          onStatus?.(application.status);
        }
        setMessage(error.message);
      } else {
        setMessage(error instanceof Error ? error.message : "The application could not be opened.");
      }
    } finally {
      setPending(false);
    }
  }

  async function review(action: ReviewAction, fieldId: string, value?: string) {
    if (state.phase !== "ready") return;
    setPending(true);
    setMessage(null);
    try {
      const application = await reviewApplication(state.application.id, action, fieldId, value);
      setState({ phase: "ready", application });
      onStatus?.(application.status);
      if (application.status === "withdrawn") setMessage("The application was stopped.");
      else if (application.status === "waiting_for_user") setMessage("Saved. The next question is ready.");
      else setMessage("Saved. The application continued and was not submitted.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "The answer could not be saved.");
    } finally {
      setPending(false);
    }
  }

  async function move(to: string, resumeVersionId: number | null) {
    if (state.phase !== "ready") return;
    setPending(true);
    setMessage(null);
    try {
      const application = await transitionApplication(state.application.id, to, reason, resumeVersionId);
      setReason("");
      setState({ phase: "ready", application });
      onStatus?.(application.status);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "The status could not be changed.");
    } finally {
      setPending(false);
    }
  }

  if (state.phase === "loading") {
    return <div className="mt-10 h-24 rounded-xl bg-[var(--line)]" aria-hidden="true" />;
  }
  if (state.phase === "error") {
    return <p className="mt-10 text-[var(--muted)]">The application could not be loaded.</p>;
  }

  return (
    <section className="mt-10 max-w-3xl">
      <h2 className="text-lg font-medium">Application</h2>
      {state.phase === "empty" ? (
        <div className="mt-3">
          <p className="text-sm text-[var(--muted)]">No application is tracked for this job.</p>
          <label className="mt-4 grid gap-1 text-sm">
            <span className="text-[var(--muted)]">Note</span>
            <input
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              className={controlClass}
              placeholder="Optional reason for opening it"
            />
          </label>
          <button type="button" className={buttonClass} disabled={pending} onClick={() => void start()}>
            Track this job
          </button>
        </div>
      ) : (
        <ApplicationBody
          application={state.application}
          reason={reason}
          versionId={versionId}
          pending={pending}
          onReason={setReason}
          onVersion={setVersionId}
          onReview={(action, fieldId, value) => void review(action, fieldId, value)}
          onMove={(to, resumeVersionId) => void move(to, resumeVersionId)}
        />
      )}
      {message ? <p className="mt-3 text-sm text-[var(--muted)]">{message}</p> : null}
    </section>
  );
}

function ApplicationBody({
  application,
  reason,
  versionId,
  pending,
  onReason,
  onVersion,
  onReview,
  onMove,
}: {
  application: ApplicationDetail;
  reason: string;
  versionId: string;
  pending: boolean;
  onReason: (value: string) => void;
  onVersion: (value: string) => void;
  onReview: (action: ReviewAction, fieldId: string, value?: string) => void;
  onMove: (to: string, resumeVersionId: number | null) => void;
}) {
  const needsVersion = application.allowed_transitions.includes("ready_to_apply");
  const selectedVersion = versionId || (application.resume_version ? String(application.resume_version.id) : "");
  const stamps: [string, string | null][] = [
    ["Opened", application.opened_at],
    ["Status changed", application.status_changed_at],
    ["Applying started", application.started_at],
    ["Submitted", application.submitted_at],
  ];

  return (
    <div className="mt-3">
      <p className="text-sm">
        Status <span className="capitalize font-medium">{label(application.status)}</span>
      </p>
      <dl className="mt-4 divide-y divide-[var(--line)] border-y border-[var(--line)]">
        {stamps.map(([name, value]) => (
          <div key={name} className="grid gap-1 py-3 md:grid-cols-[12rem_1fr]">
            <dt className="text-sm text-[var(--muted)]">{name}</dt>
            <dd className="text-sm">{formatDateTime(value)}</dd>
          </div>
        ))}
        <div className="grid gap-1 py-3 md:grid-cols-[12rem_1fr]">
          <dt className="text-sm text-[var(--muted)]">Resume version</dt>
          <dd className="break-all font-mono text-sm">
            {application.resume_version
              ? `#${application.resume_version.id} ${application.resume_version.sha256.slice(0, 12)}`
              : "None attached"}
          </dd>
        </div>
      </dl>

      {application.status === "waiting_for_user" ? (
        <div className="mt-6">
          <Intervention application={application} busy={pending} onReview={onReview} />
        </div>
      ) : null}

      {application.allowed_transitions.length > 0 ? (
        <form
          className="mt-6 grid gap-3"
          onSubmit={(event) => {
            event.preventDefault();
          }}
        >
          <label className="grid gap-1 text-sm">
            <span className="text-[var(--muted)]">Reason</span>
            <input
              value={reason}
              onChange={(event) => onReason(event.target.value)}
              className={controlClass}
              placeholder="Why this status is changing"
            />
          </label>
          {needsVersion ? (
            <label className="grid gap-1 text-sm">
              <span className="text-[var(--muted)]">Resume version for ready to apply</span>
              {application.resume_versions.length === 0 ? (
                <span>Ready to apply stays unavailable until this application has a tailored resume.</span>
              ) : (
                <select value={selectedVersion} onChange={(event) => onVersion(event.target.value)} className={controlClass}>
                  <option value="">Choose a resume version</option>
                  {application.resume_versions.map((version) => (
                    <option key={version.id} value={version.id}>
                      Version {version.id} · {version.sha256.slice(0, 12)}
                    </option>
                  ))}
                </select>
              )}
            </label>
          ) : null}
          <div className="flex flex-wrap gap-2">
            {application.allowed_transitions.map((status) => {
              const blocked = status === "ready_to_apply" && !selectedVersion;
              return (
                <button
                  key={status}
                  type="button"
                  className={buttonClass}
                  disabled={pending || !reason.trim() || blocked}
                  onClick={() =>
                    onMove(status, status === "ready_to_apply" ? Number(selectedVersion) : null)
                  }
                >
                  Move to {label(status)}
                </button>
              );
            })}
          </div>
        </form>
      ) : (
        <p className="mt-4 text-sm text-[var(--muted)]">This status does not lead anywhere else.</p>
      )}

      <h3 className="mt-8 text-base font-medium">History</h3>
      <ol className="mt-3 divide-y divide-[var(--line)] border-y border-[var(--line)]">
        {application.history.map((event) => (
          <li key={event.id} className="py-3 text-sm">
            <p>
              {sentence(event.to_status ? label(event.to_status) : label(event.event_type))}
              {event.from_status ? `, from ${label(event.from_status)}` : ""}
            </p>
            <p className="mt-1 text-[var(--muted)]">
              {formatDateTime(event.created_at)} · {event.actor}
              {event.resume_version_id ? ` · resume #${event.resume_version_id}` : ""}
            </p>
            {event.reason ? <p className="mt-1">{event.reason}</p> : null}
          </li>
        ))}
      </ol>
    </div>
  );
}

function sentence(value: string): string {
  return value ? value.slice(0, 1).toUpperCase() + value.slice(1) : value;
}

const controlClass = "h-10 rounded-xl border border-[var(--line)] bg-[var(--panel)] px-3 text-sm text-[var(--fg)]";
const buttonClass =
  "mt-2 inline-flex h-10 items-center rounded-xl border border-[var(--line)] bg-[var(--panel)] px-3 text-sm disabled:opacity-50";
