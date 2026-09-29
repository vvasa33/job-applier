import { type ReactNode, useCallback, useEffect, useState } from "react";

import {
  agentCommand,
  fetchActivity,
  fetchAgent,
  retryApplication,
  updateAgentSettings,
  type AgentActivity,
  type AgentStatus,
  type AIStatus,
  type AutonomyLevel,
} from "../api/agent";
import { formatDateTime, formatUsd, label } from "../format";
import { Chip, Notice, Skeleton, Timeline, TimelineItem, buttonClass, controlClass, primaryClass } from "../ui";

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

const PURPOSES: Record<string, string> = {
  semantic_matching: "Job matching",
  job_analysis: "Job analysis",
  question_classification: "Question classification",
  question_suggestion: "Answer suggestions",
  difficult_question: "Difficult questions",
  resume_tailoring: "Resume tailoring",
};

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
      <div className="mt-10">
        <Notice tone="out">The agent status could not be loaded.</Notice>
      </div>
    ) : (
      <div className="mt-10 grid gap-3">
        <Skeleton className="h-16" />
        <Skeleton className="h-24" />
      </div>
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
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <Chip tone={status.alive && running ? "fit" : "neutral"}>
                {status.alive ? PHASES[status.phase] ?? label(status.phase) : "Worker not running"}
              </Chip>
              {status.alive && !running && status.phase !== "stopped" ? (
                <span className="text-sm text-[var(--muted)]">Stopping after the current step</span>
              ) : null}
            </div>
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
        {!status.alive && status.message ? <Notice>{status.message}</Notice> : null}
        {message ? <Notice tone="fit">{message}</Notice> : null}
        {status.ai.exceeded ? (
          <Notice tone="weak">
            Today's AI budget is used up ({formatUsd(status.ai.spent_today_usd)} of {formatUsd(status.ai.budget_usd)}).
            New jobs are matched without AI and no new resumes are tailored until midnight. Applications already in
            progress continue.
          </Notice>
        ) : null}
      </section>

      {status.alive && status.current.step ? (
        <section className="grid gap-2 rounded-xl border border-[var(--line)] bg-[var(--panel)] p-4">
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

      <AICosts
        ai={status.ai}
        busy={busy}
        onBudget={(amount) =>
          void run(() => updateAgentSettings({ daily_llm_budget_usd: amount }), `Daily AI budget set to ${formatUsd(amount)}.`)
        }
      />

      <section className="grid gap-3">
        <h2 className="text-sm font-medium">Activity</h2>
        {activity.length === 0 ? (
          <p className="text-sm text-[var(--muted)]">Nothing has happened yet.</p>
        ) : (
          <Timeline>
            {activity.map((entry) => (
              <TimelineItem
                key={entry.id}
                time={formatDateTime(entry.created_at)}
                title={entry.message}
                tone={entry.level === "error" ? "out" : entry.level === "warning" ? "weak" : "neutral"}
              >
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
              </TimelineItem>
            ))}
          </Timeline>
        )}
      </section>
    </div>
  );
}

function AICosts({ ai, busy, onBudget }: { ai: AIStatus; busy: boolean; onBudget: (amount: number) => void }) {
  const [draft, setDraft] = useState(ai.budget_usd.toFixed(2));
  const amount = Number(draft);
  const valid = draft.trim() !== "" && Number.isFinite(amount) && amount >= 0 && amount <= 1000;
  const purposes = Object.entries(ai.by_purpose).sort((a, b) => b[1] - a[1]);

  return (
    <section className="grid gap-6 md:grid-cols-2">
      <div className="grid content-start gap-3">
        <h2 className="text-sm font-medium">AI spending</h2>
        <p className="text-2xl font-medium tabular-nums tracking-tight">
          {formatUsd(ai.spent_today_usd)} <span className="text-base text-[var(--muted)]">of {formatUsd(ai.budget_usd)} today</span>
        </p>
        <div
          className="h-1.5 w-full overflow-hidden rounded-xl"
          role="meter"
          aria-label="AI budget used today"
          aria-valuemin={0}
          aria-valuemax={ai.budget_usd}
          aria-valuenow={ai.spent_today_usd}
        >
          <div
            className={ai.exceeded ? "h-full rounded-xl bg-[var(--weak)]" : "h-full rounded-xl bg-[var(--accent)]"}
            style={{ width: `${ai.budget_usd <= 0 ? 100 : Math.min(100, (ai.spent_today_usd / ai.budget_usd) * 100)}%` }}
          />
        </div>
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            if (valid) onBudget(Math.round(amount * 100) / 100);
          }}
        >
          <label className="grid gap-1 text-sm">
            <span className="text-[var(--muted)]">Daily budget in US dollars</span>
            <input
              className={`${controlClass} w-32 tabular-nums`}
              inputMode="decimal"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              aria-invalid={!valid}
            />
          </label>
          <button type="submit" className={buttonClass} disabled={busy || !valid || amount === ai.budget_usd}>
            Save budget
          </button>
        </form>
        <p className="text-sm text-[var(--muted)]">
          Costs are estimates from token counts. When the budget is reached, job matching falls back to the rule-based
          filters and new resumes wait; a resume you tailor yourself and applications in progress are never stopped.
        </p>
        <p className="text-sm text-[var(--muted)]">
          {ai.configured
            ? `Classification uses ${ai.cheap_model}. Resume tailoring and difficult questions use ${ai.strong_model}.`
            : "No model key is configured, so nothing is spent. Resumes are copied unchanged."}
        </p>
        {purposes.length > 0 ? (
          <dl className="grid gap-1 text-sm">
            {purposes.map(([purpose, cost]) => (
              <div key={purpose} className="flex justify-between gap-4">
                <dt className="text-[var(--muted)]">{PURPOSES[purpose] ?? label(purpose)}</dt>
                <dd className="tabular-nums">{formatUsd(cost)}</dd>
              </div>
            ))}
          </dl>
        ) : null}
      </div>
      <div className="grid content-start gap-3">
        <h2 className="text-sm font-medium">By day</h2>
        {ai.days.length === 0 ? (
          <p className="text-sm text-[var(--muted)]">No AI calls yet.</p>
        ) : (
          <table className="w-full text-sm tabular-nums">
            <thead className="text-left text-[var(--muted)]">
              <tr>
                <th className="py-1 font-normal">Day</th>
                <th className="py-1 text-right font-normal">Spent</th>
                <th className="py-1 text-right font-normal">Calls</th>
                <th className="py-1 text-right font-normal">Reused</th>
                <th className="py-1 text-right font-normal">Saved</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--line)] border-y border-[var(--line)]">
              {ai.days.map((day) => (
                <tr key={day.day}>
                  <td className="py-2">{day.day}</td>
                  <td className="py-2 text-right">{formatUsd(day.cost_usd)}</td>
                  <td className="py-2 text-right">{day.calls}</td>
                  <td className="py-2 text-right">{day.cached}</td>
                  <td className="py-2 text-right">{formatUsd(day.saved_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </section>
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

