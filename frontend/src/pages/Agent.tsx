import { type ReactNode, useCallback, useEffect, useState } from "react";

import {
  agentCommand,
  fetchActivity,
  fetchAgent,
  retryApplication,
  updateAgentSettings,
  type AgentActivity,
  type AgentStatus,
  type AutonomyLevel,
} from "../api/agent";
import { formatDateTime, label } from "../format";

const POLL_MS = 3000;

const PHASES: Record<string, string> = {
  stopped: "Stopped",
  starting: "Starting",
  idle: "Idle",
  discovering: "Discovering jobs",
  preparing: "Tailoring a resume",
  applying: "Applying",
  waiting_for_user: "Waiting for you",
  stopping: "Stopping",
};

const AUTONOMY: { value: AutonomyLevel; title: string; detail: string }[] = [
  { value: "observe", title: "Observe", detail: "Discover and score jobs only. Nothing is opened or submitted." },
  {
    value: "assist",
    title: "Assist",
    detail: "Prepare and fill applications. Every submit waits for your confirmation in Review.",
  },
  {
    value: "supervised",
    title: "Supervised",
    detail:
      "Submit on its own only when every answer came from stored facts, the resume was tailored cleanly, the site has 10 confirmed submissions, and the daily auto-submit limit is not reached. Otherwise it asks.",
  },
];

const INTERVALS = [1, 3, 6, 12, 24];

export function Agent({ onOpen }: { onOpen: (path: string) => void }) {
  const [status, setStatus] = useState<AgentStatus | null>(null);
  const [activity, setActivity] = useState<AgentActivity[]>([]);
  const [loadError, setLoadError] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const refresh = useCallback(async (signal?: AbortSignal) => {
    const [nextStatus, nextActivity] = await Promise.all([fetchAgent(signal), fetchActivity(100, signal)]);
    setStatus(nextStatus);
    setActivity(nextActivity);
    setLoadError(false);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void refresh(controller.signal).catch(() => {
      if (!controller.signal.aborted) setLoadError(true);
    });
    const timer = window.setInterval(() => {
      void refresh().catch(() => setLoadError(true));
    }, POLL_MS);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [refresh]);

  async function run(action: () => Promise<AgentStatus>, done: string) {
    setBusy(true);
    setMessage(null);
    try {
      setStatus(await action());
      setMessage(done);
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "The request failed.");
    } finally {
      setBusy(false);
    }
  }

  if (status === null) {
    return loadError ? (
      <p className="mt-10 text-[var(--muted)]">The agent status could not be loaded.</p>
    ) : (
      <div className="mt-10 h-24 rounded-xl bg-[var(--line)]" aria-hidden="true" />
    );
  }

  const running = status.desired === "running";
  const limits = status.limits;

  return (
    <div className="mt-10 grid gap-10">
      <section className="grid gap-4">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-3xl font-medium tracking-tight">Agent</h1>
            <p className="mt-2 flex items-center gap-2 text-[var(--muted)]">
              <span
                aria-hidden="true"
                className={
                  status.alive && running
                    ? "h-2.5 w-2.5 rounded-full bg-[var(--accent)]"
                    : "h-2.5 w-2.5 rounded-full border border-[var(--muted)]"
                }
              />
              {status.alive ? PHASES[status.phase] ?? label(status.phase) : "Worker not running"}
              {status.alive && !running && status.phase !== "stopped" ? " · stopping after the current step" : ""}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            {running ? (
              <button
                type="button"
                className={buttonClass}
                disabled={busy}
                onClick={() => void run(() => agentCommand("stop"), "Stop requested. The current step finishes first.")}
              >
                Stop
              </button>
            ) : (
              <button
                type="button"
                className={primaryClass}
                disabled={busy}
                onClick={() => void run(() => agentCommand("start"), "Start requested.")}
              >
                Start
              </button>
            )}
            <button
              type="button"
              className={buttonClass}
              disabled={busy}
              onClick={() => void run(() => agentCommand("discover"), "Discovery will run when the agent is next free.")}
            >
              Discover now
            </button>
          </div>
        </div>
        {!status.alive ? (
          <p className="max-w-[70ch] rounded-xl border border-[var(--line)] bg-[var(--panel)] px-4 py-3 text-sm">
            {status.message}
          </p>
        ) : null}
        {message ? <p className="text-sm">{message}</p> : null}
      </section>

      {status.alive && status.current.step ? (
        <section className="grid gap-1">
          <h2 className="text-sm font-medium">Now</h2>
          <p>{status.current.step}</p>
          {status.current.job_id !== null ? (
            <button
              type="button"
              className="justify-self-start text-sm text-[var(--accent)]"
              onClick={() => onOpen(`/jobs/${status.current.job_id}`)}
            >
              {status.current.title ?? "Open the job"}
              {status.current.company ? ` at ${status.current.company}` : ""}
            </button>
          ) : null}
        </section>
      ) : null}

      <section className="grid grid-cols-2 gap-x-6 gap-y-5 border-y border-[var(--line)] py-5 md:grid-cols-4">
        <Stat title="Waiting for you" value={String(status.waiting_for_user)}>
          {status.waiting_for_user > 0 ? (
            <button type="button" className="text-sm text-[var(--accent)]" onClick={() => onOpen("/review")}>
              Open Review
            </button>
          ) : null}
        </Stat>
        <Stat
          title="Applications, last 24 hours"
          value={`${limits.applications_today} of ${limits.daily_applications}`}
        />
        <Stat title="Submitted, last 24 hours" value={String(status.submitted_today)} />
        <Stat
          title="Retrying / gave up"
          value={`${status.failing} / ${status.gave_up}`}
          note={`Each step is tried at most ${limits.max_failures} times.`}
        />
      </section>

      <section className="grid gap-6 md:grid-cols-2">
        <div className="grid content-start gap-3">
          <h2 className="text-sm font-medium">Autonomy</h2>
          <div className="grid gap-2" role="radiogroup" aria-label="Autonomy">
            {AUTONOMY.map((option) => {
              const selected = option.value === status.autonomy_level;
              return (
                <button
                  key={option.value}
                  type="button"
                  role="radio"
                  aria-checked={selected}
                  disabled={busy || selected}
                  className={
                    selected
                      ? "rounded-xl border border-[var(--accent)] bg-[var(--panel)] px-4 py-3 text-left"
                      : "rounded-xl border border-[var(--line)] px-4 py-3 text-left disabled:opacity-50"
                  }
                  onClick={() =>
                    void run(
                      () => updateAgentSettings({ autonomy_level: option.value }),
                      `Autonomy set to ${option.title.toLowerCase()}.`,
                    )
                  }
                >
                  <span className="font-medium">{option.title}</span>
                  <span className="mt-1 block text-sm text-[var(--muted)]">{option.detail}</span>
                </button>
              );
            })}
          </div>
        </div>
        <div className="grid content-start gap-4">
          <div className="grid gap-2">
            <h2 className="text-sm font-medium">Discovery</h2>
            <label className="grid gap-1 text-sm">
              <span className="text-[var(--muted)]">Check job boards every</span>
              <select
                className={controlClass}
                value={status.discovery_interval_hours}
                disabled={busy}
                onChange={(event) =>
                  void run(
                    () => updateAgentSettings({ discovery_interval_hours: Number(event.target.value) }),
                    "Discovery interval saved.",
                  )
                }
              >
                {[...new Set([...INTERVALS, status.discovery_interval_hours])]
                  .sort((a, b) => a - b)
                  .map((hours) => (
                    <option key={hours} value={hours}>
                      {hours === 1 ? "1 hour" : `${hours} hours`}
                    </option>
                  ))}
              </select>
            </label>
            <p className="text-sm text-[var(--muted)]">
              Last run {formatDateTime(status.last_discovery_at)}. Next{" "}
              {status.next_discovery_at ? formatDateTime(status.next_discovery_at) : "as soon as the agent starts"}.
            </p>
            <p className="text-sm text-[var(--muted)]">
              {status.sources_error
                ? status.sources_error
                : status.sources_configured === 0
                  ? "No job boards are configured. Add them to sources.toml in the data directory."
                  : `${status.sources_configured} job ${status.sources_configured === 1 ? "board" : "boards"} configured.`}
            </p>
          </div>
          <div className="grid gap-1 text-sm text-[var(--muted)]">
            <h2 className="font-medium text-[var(--fg)]">Limits</h2>
            <p>At most {limits.daily_applications} new applications per 24 hours.</p>
            <p>At least {Math.round(limits.apply_interval_seconds / 60)} minutes between new applications.</p>
            <p>At most {limits.daily_auto_submits} supervised auto-submits per 24 hours.</p>
          </div>
        </div>
      </section>

      <section className="grid gap-3">
        <h2 className="text-sm font-medium">Activity</h2>
        {activity.length === 0 ? (
          <p className="text-sm text-[var(--muted)]">Nothing has happened yet.</p>
        ) : (
          <ol className="divide-y divide-[var(--line)] border-y border-[var(--line)]">
            {activity.map((entry) => (
              <li key={entry.id} className="grid gap-1 py-3 md:grid-cols-[11rem_1fr]">
                <span className="text-sm text-[var(--muted)]">{formatDateTime(entry.created_at)}</span>
                <span className="grid gap-1">
                  <span className={entry.level === "info" ? "" : "font-medium"}>
                    {entry.level === "error" ? "Error: " : entry.level === "warning" ? "Warning: " : ""}
                    {entry.message}
                  </span>
                  <span className="flex flex-wrap gap-4 text-sm">
                    {entry.job_id !== null ? (
                      <button type="button" className="text-[var(--accent)]" onClick={() => onOpen(`/jobs/${entry.job_id}`)}>
                        Open the job
                      </button>
                    ) : null}
                    {entry.kind.endsWith("_gave_up") && entry.application_id !== null ? (
                      <button
                        type="button"
                        className="text-[var(--accent)]"
                        disabled={busy}
                        onClick={() =>
                          void run(
                            () => retryApplication(entry.application_id as number),
                            "The agent will try this application again.",
                          )
                        }
                      >
                        Try again
                      </button>
                    ) : null}
                  </span>
                </span>
              </li>
            ))}
          </ol>
        )}
      </section>
    </div>
  );
}

function Stat({
  title,
  value,
  note,
  children,
}: {
  title: string;
  value: string;
  note?: string;
  children?: ReactNode;
}) {
  return (
    <div className="grid content-start gap-1">
      <span className="text-sm text-[var(--muted)]">{title}</span>
      <span className="text-2xl font-medium tabular-nums tracking-tight">{value}</span>
      {note ? <span className="text-sm text-[var(--muted)]">{note}</span> : null}
      {children}
    </div>
  );
}

const controlClass = "h-10 rounded-xl border border-[var(--line)] bg-[var(--panel)] px-3 text-sm text-[var(--fg)]";
const buttonClass =
  "inline-flex h-10 items-center rounded-xl border border-[var(--line)] bg-[var(--panel)] px-3 text-sm disabled:opacity-50";
const primaryClass =
  "inline-flex h-10 items-center rounded-xl bg-[var(--accent)] px-3 text-sm text-[var(--accent-fg)] disabled:opacity-50";
