import { useEffect, useState } from "react";

import { fetchReviewQueue, type ReviewItem } from "../api/applications";
import { fetchDashboard, type Dashboard as DashboardData } from "../api/jobs";
import { formatDateTime, label } from "../format";
import { JobCard, Notice, Skeleton } from "../ui";

export function Dashboard({ onOpen }: { onOpen: (path: string) => void }) {
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<{ phase: "loading" } | { phase: "error" } | { phase: "ready"; data: DashboardData }>(
    { phase: "loading" },
  );
  const [queue, setQueue] = useState<ReviewItem[] | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    void fetchDashboard(controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setState({ phase: "ready", data });
      })
      .catch(() => {
        if (!controller.signal.aborted) setState({ phase: "error" });
      });
    void fetchReviewQueue(controller.signal)
      .then((items) => {
        if (!controller.signal.aborted) setQueue(items);
      })
      .catch(() => {
        if (!controller.signal.aborted) setQueue(null);
      });
    return () => controller.abort();
  }, [attempt]);

  if (state.phase === "loading") {
    return (
      <div className="mt-10 grid gap-3">
        <Skeleton className="h-8 w-40" />
        <Skeleton className="h-24" />
        <Skeleton className="h-36" />
      </div>
    );
  }
  if (state.phase === "error") {
    return (
      <div className="mt-10 grid gap-3">
        <Notice tone="out">The job list could not be loaded. Check that the local API is running.</Notice>
        <button type="button" className="justify-self-start text-sm text-[var(--accent)]" onClick={() => setAttempt((n) => n + 1)}>
          Try again
        </button>
      </div>
    );
  }

  const data = state.data;
  const counts = [
    ["Jobs", data.job_count],
    ["Internships", data.internship_count],
    ["Remote", data.remote_count],
    ["Applications", data.application_count],
  ] as const;

  return (
    <div className="mt-10">
      <h1 className="text-3xl font-medium tracking-tight">Dashboard</h1>
      <p className="mt-2 max-w-[58ch] text-[var(--muted)]">Counts from the jobs stored in the local database.</p>
      <dl className="mt-8 grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-[var(--line)] bg-[var(--line)] md:grid-cols-4">
        {counts.map(([name, value]) => (
          <div key={name} className="bg-[var(--panel)] px-4 py-4">
            <dt className="text-sm text-[var(--muted)]">{name}</dt>
            <dd className="mt-1 font-mono text-2xl">{value}</dd>
          </div>
        ))}
      </dl>

      <section className="mt-12">
        <div className="flex items-baseline justify-between gap-4">
          <h2 className="text-lg font-medium">Waiting for you</h2>
          <button type="button" className="text-sm text-[var(--accent)]" onClick={() => onOpen("/review")}>
            Review
          </button>
        </div>
        {queue === null ? (
          <p className="mt-4 text-sm text-[var(--muted)]">The review queue could not be loaded.</p>
        ) : queue.length === 0 ? (
          <p className="mt-4 text-[var(--muted)]">Nothing is waiting on you.</p>
        ) : (
          <ul className="mt-4 grid gap-3">
            {queue.map((item) => (
              <li key={item.application_id}>
                <button
                  type="button"
                  className="grid w-full gap-1 rounded-xl border border-[var(--line)] bg-[var(--panel)] p-4 text-left"
                  onClick={() => onOpen("/review")}
                >
                  <span className="font-medium">{item.title}</span>
                  <span className="text-sm text-[var(--muted)]">{item.company}</span>
                  <span className="text-sm">{queueLabel(item)}</span>
                  <span className="text-xs text-[var(--muted)]">Waiting since {formatDateTime(item.waiting_since)}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="mt-12">
        <div className="flex items-baseline justify-between gap-4">
          <h2 className="text-lg font-medium">Recently discovered</h2>
          <button type="button" className="text-sm text-[var(--accent)]" onClick={() => onOpen("/jobs")}>
            All jobs
          </button>
        </div>
        {data.recent.length === 0 ? (
          <p className="mt-4 text-[var(--muted)]">No jobs stored yet. A discovery run will fill this list.</p>
        ) : (
          <ul className="mt-4 grid gap-3">
            {data.recent.map((job) => (
              <li key={job.id}>
                <JobCard job={job} onOpen={onOpen} />
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="mt-12">
        <h2 className="text-lg font-medium">By status</h2>
        <StatusMix counts={data.by_status} total={data.job_count} onOpen={onOpen} />
      </section>
    </div>
  );
}

function queueLabel(item: ReviewItem): string {
  if (item.resume_queued) return "Continuing in the agent's browser";
  if (item.pause_kind === "confirm_submit") return "Ready to submit";
  if (item.pause_kind === "verify_submit") return "Check whether it was submitted";
  return item.question_label || item.waiting_reason || "Waiting for an answer";
}

function StatusMix({
  counts,
  total,
  onOpen,
}: {
  counts: Record<string, number>;
  total: number;
  onOpen: (path: string) => void;
}) {
  const rows = Object.entries(counts).filter(([, count]) => count > 0);
  if (rows.length === 0 || total === 0) {
    return <p className="mt-4 text-[var(--muted)]">Nothing to group until jobs are stored.</p>;
  }
  return (
    <ul className="mt-4 grid gap-3">
      {rows.map(([status, count]) => (
        <li key={status}>
          <button
            type="button"
            className="grid w-full gap-2 text-left"
            onClick={() => onOpen(`/jobs?status=${encodeURIComponent(status)}`)}
          >
            <span className="flex items-baseline justify-between gap-4 text-sm">
              <span className="capitalize">{label(status)}</span>
              <span className="tabular-nums text-[var(--muted)]">{count}</span>
            </span>
            <span
              className="block h-1 rounded-xl bg-[var(--accent)]"
              style={{ width: `${Math.max(8, (count / total) * 100)}%` }}
            />
          </button>
        </li>
      ))}
    </ul>
  );
}
