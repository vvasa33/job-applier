import { useEffect, useState } from "react";

import { fetchDashboard, type Dashboard as DashboardData, type JobSummary } from "../api/jobs";
import { formatDate, label, place, workplaceLabel } from "../format";

export function Dashboard({ onOpen }: { onOpen: (path: string) => void }) {
  const [state, setState] = useState<{ phase: "loading" } | { phase: "error" } | { phase: "ready"; data: DashboardData }>(
    { phase: "loading" },
  );

  useEffect(() => {
    const controller = new AbortController();
    void fetchDashboard(controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setState({ phase: "ready", data });
      })
      .catch(() => {
        if (!controller.signal.aborted) setState({ phase: "error" });
      });
    return () => controller.abort();
  }, []);

  if (state.phase === "loading") return <Skeleton />;
  if (state.phase === "error") {
    return <p className="mt-10 text-[var(--muted)]">The job list could not be loaded. Check that the local API is running.</p>;
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
          <h2 className="text-lg font-medium">Recently discovered</h2>
          <button type="button" className="text-sm text-[var(--accent)]" onClick={() => onOpen("/jobs")}>
            All jobs
          </button>
        </div>
        {data.recent.length === 0 ? (
          <p className="mt-4 text-[var(--muted)]">No jobs stored yet. A discovery run will fill this list.</p>
        ) : (
          <ul className="mt-4 divide-y divide-[var(--line)] border-y border-[var(--line)]">
            {data.recent.map((job) => (
              <li key={job.id}>
                <button type="button" className="w-full py-3 text-left" onClick={() => onOpen(`/jobs/${job.id}`)}>
                  <JobLine job={job} />
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="mt-12">
        <h2 className="text-lg font-medium">By status</h2>
        <ul className="mt-4 grid gap-2 sm:grid-cols-2">
          {Object.entries(data.by_status)
            .filter(([, count]) => count > 0)
            .map(([status, count]) => (
              <li key={status} className="flex items-center justify-between rounded-xl border border-[var(--line)] px-4 py-3">
                <span className="capitalize">{label(status)}</span>
                <span className="font-mono text-sm">{count}</span>
              </li>
            ))}
        </ul>
        {Object.values(data.by_status).every((count) => count === 0) ? (
          <p className="mt-4 text-[var(--muted)]">Nothing to group until jobs are stored.</p>
        ) : null}
      </section>
    </div>
  );
}

export function JobLine({ job }: { job: JobSummary }) {
  return (
    <span className="grid gap-1">
      <span className="font-medium">{job.title}</span>
      <span className="text-sm text-[var(--muted)]">
        {job.company} · {place(job)} · {workplaceLabel(job.workplace)} · {formatDate(job.first_seen_at)}
      </span>
    </span>
  );
}

function Skeleton() {
  return (
    <div className="mt-10 space-y-3" aria-hidden="true">
      <div className="h-8 w-40 rounded-xl bg-[var(--line)]" />
      <div className="h-20 rounded-xl bg-[var(--line)]" />
    </div>
  );
}
