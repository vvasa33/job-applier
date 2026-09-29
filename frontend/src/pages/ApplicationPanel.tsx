import { useEffect, useState } from "react";

import {
  ApplicationRequestError,
  fetchApplication,
  openApplication,
  reviewApplication,
  reviewOutcome,
  transitionApplication,
  type ApplicationDetail,
  type ReviewAction,
} from "../api/applications";
import { formatDateTime, formatUsd, label } from "../format";
import { Chip, Notice, Skeleton, Timeline, TimelineItem, buttonClass, controlClass, statusTone } from "../ui";
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
      setMessage(reviewOutcome(application));
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
    return <Skeleton className="mt-10 h-28" />;
  }
  if (state.phase === "error") {
    return (
      <div className="mt-10">
        <Notice tone="out">The application could not be loaded.</Notice>
      </div>
    );
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
          <button type="button" className={`${buttonClass} mt-2`} disabled={pending} onClick={() => void start()}>
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
  const visited = new Set(
    application.history.map((event) => event.to_status).filter((status): status is string => Boolean(status)),
  );
  visited.add(application.status);

  return (
    <div className="mt-3 grid gap-6">
      <div className="flex flex-wrap items-center gap-2">
        <Chip tone={statusTone(application.status)}>{label(application.status)}</Chip>
        <span className="text-sm tabular-nums text-[var(--muted)]">{formatUsd(application.ai_cost_usd)} estimated</span>
      </div>
      <ol className="flex gap-2 overflow-x-auto pb-1" aria-label="Application progress">
        {PIPELINE.map((status) => {
          const current = status === application.status;
          const seen = visited.has(status);
          return (
            <li
              key={status}
              aria-current={current ? "step" : undefined}
              className={
                current
                  ? "shrink-0 rounded-xl bg-[var(--accent)] px-3 py-2 text-xs text-[var(--accent-fg)]"
                  : seen
                    ? "shrink-0 rounded-xl bg-[var(--fit-bg)] px-3 py-2 text-xs text-[var(--fit)]"
                    : "shrink-0 rounded-xl border border-[var(--line)] px-3 py-2 text-xs text-[var(--muted)]"
              }
            >
              {label(status)}
            </li>
          );
        })}
      </ol>
      <ResumeChanges application={application} />

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

      <section className="grid gap-3">
        <h3 className="text-base font-medium">Activity</h3>
        {application.history.length === 0 ? (
          <p className="text-sm text-[var(--muted)]">No events yet.</p>
        ) : (
          <Timeline>
            {[...application.history].reverse().map((event) => (
              <TimelineItem
                key={event.id}
                time={`${formatDateTime(event.created_at)} · ${event.actor}`}
                title={sentence(event.to_status ? label(event.to_status) : label(event.event_type))}
                tone={statusTone(event.to_status)}
              >
                {event.from_status ? <p className="text-sm text-[var(--muted)]">From {label(event.from_status)}</p> : null}
                {event.resume_version_id ? <p className="text-sm text-[var(--muted)]">Resume #{event.resume_version_id}</p> : null}
                {event.reason ? <p className="text-sm">{event.reason}</p> : null}
              </TimelineItem>
            ))}
          </Timeline>
        )}
      </section>
    </div>
  );
}

const PIPELINE = ["found", "matched", "saved", "tailoring", "ready_to_apply", "applying", "waiting_for_user", "submitted"];

function ResumeChanges({ application }: { application: ApplicationDetail }) {
  if (application.resume_versions.length === 0) {
    return <p className="text-sm text-[var(--muted)]">No tailored resume yet.</p>;
  }
  return (
    <section className="grid gap-3">
      <h3 className="text-base font-medium">Resume changes</h3>
      <ul className="grid gap-3">
        {application.resume_versions.map((version) => {
          const attached = application.resume_version?.id === version.id;
          return (
            <li key={version.id} className="grid gap-2 rounded-xl border border-[var(--line)] bg-[var(--panel)] p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="font-medium">Version {version.id}</p>
                {attached ? <Chip tone="fit">Attached</Chip> : null}
              </div>
              <p className="text-sm text-[var(--muted)]">
                {formatDateTime(version.created_at)} · {formatUsd(version.ai_cost_usd)} to tailor
              </p>
              <p className="break-all font-mono text-xs text-[var(--muted)]">{version.sha256}</p>
              <dl className="grid gap-1 text-sm">
                {version.tex_path ? <Path name="TeX" value={version.tex_path} /> : null}
                {version.pdf_path ? <Path name="PDF" value={version.pdf_path} /> : null}
                {version.diff_path ? <Path name="Change record" value={version.diff_path} /> : null}
              </dl>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function Path({ name, value }: { name: string; value: string }) {
  return (
    <div className="grid gap-0.5">
      <dt className="text-xs text-[var(--muted)]">{name}</dt>
      <dd className="break-all font-mono text-xs">{value}</dd>
    </div>
  );
}

function sentence(value: string): string {
  return value ? value.slice(0, 1).toUpperCase() + value.slice(1) : value;
}
