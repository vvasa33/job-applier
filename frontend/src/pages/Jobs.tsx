import { useEffect, useState, type ReactNode } from "react";

import { fetchJobs, type JobFilters, type JobList, type JobSummary } from "../api/jobs";
import { APPLICATION_STATUSES, formatDate, JOB_STATUSES, label, place, workplaceLabel } from "../format";

export function Jobs({
  filters,
  onChange,
  onOpen,
}: {
  filters: JobFilters;
  onChange: (next: JobFilters) => void;
  onOpen: (path: string) => void;
}) {
  const [state, setState] = useState<{ phase: "loading" } | { phase: "error" } | { phase: "ready"; data: JobList }>({
    phase: "loading",
  });

  useEffect(() => {
    const controller = new AbortController();
    void fetchJobs(filters, controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setState({ phase: "ready", data });
      })
      .catch(() => {
        if (!controller.signal.aborted) setState({ phase: "error" });
      });
    return () => controller.abort();
  }, [filters]);

  return (
    <div className="mt-10">
      <h1 className="text-3xl font-medium tracking-tight">Jobs</h1>
      <p className="mt-2 text-[var(--muted)]">Search the postings already stored locally.</p>
      <Filters filters={filters} companies={state.phase === "ready" ? state.data.companies : []} onChange={onChange} />
      {state.phase === "loading" ? <ListSkeleton /> : null}
      {state.phase === "error" ? (
        <p className="mt-8 text-[var(--muted)]">The job list could not be loaded. Check that the local API is running.</p>
      ) : null}
      {state.phase === "ready" && state.data.jobs.length === 0 ? (
        <p className="mt-8 text-[var(--muted)]">
          {state.data.total === 0 && !hasFilter(filters)
            ? "No jobs stored yet. A discovery run will fill this list."
            : "No stored jobs match these filters."}
        </p>
      ) : null}
      {state.phase === "ready" && state.data.jobs.length > 0 ? (
        <>
          <p className="mt-6 text-sm text-[var(--muted)]">{state.data.total} matching</p>
          <ul className="mt-2 divide-y divide-[var(--line)] border-y border-[var(--line)]">
            {state.data.jobs.map((job) => (
              <li key={job.id}>
                <button type="button" className="w-full py-4 text-left" onClick={() => onOpen(`/jobs/${job.id}`)}>
                  <JobCard job={job} />
                </button>
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </div>
  );
}

function JobCard({ job }: { job: JobSummary }) {
  return (
    <span className="grid gap-2">
      <span className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="text-lg font-medium">{job.title}</span>
        <span className="text-sm capitalize text-[var(--muted)]">{label(job.application_status ?? "no application")}</span>
      </span>
      <span className="text-sm text-[var(--muted)]">{job.company}</span>
      <span className="text-sm">
        {place(job)} · {workplaceLabel(job.workplace)}
      </span>
      <span className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-[var(--muted)]">
        <span>{job.sources.length > 0 ? job.sources.join(", ") : "Unknown source"}</span>
        <span>Discovered {formatDate(job.first_seen_at)}</span>
        <span className="capitalize">{label(job.status)}</span>
      </span>
    </span>
  );
}

function Filters({
  filters,
  companies,
  onChange,
}: {
  filters: JobFilters;
  companies: string[];
  onChange: (next: JobFilters) => void;
}) {
  const set = (key: keyof JobFilters, value: string) => onChange({ ...filters, [key]: value });
  return (
    <form className="mt-6 grid gap-3 md:grid-cols-2" onSubmit={(event) => event.preventDefault()}>
      <Field label="Title">
        <input
          value={filters.q}
          onChange={(event) => set("q", event.target.value)}
          className={controlClass}
          placeholder="Search titles"
        />
      </Field>
      <Field label="Company">
        <select value={filters.company} onChange={(event) => set("company", event.target.value)} className={controlClass}>
          <option value="">Any company</option>
          {companies.map((company) => (
            <option key={company} value={company}>
              {company}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Location">
        <input
          value={filters.location}
          onChange={(event) => set("location", event.target.value)}
          className={controlClass}
          placeholder="City, region, or class"
        />
      </Field>
      <Field label="Workplace">
        <select value={filters.workplace} onChange={(event) => set("workplace", event.target.value)} className={controlClass}>
          <option value="">Any workplace</option>
          <option value="remote">Remote</option>
          <option value="hybrid">Hybrid</option>
          <option value="onsite">On-site</option>
        </select>
      </Field>
      <Field label="Internship">
        <select value={filters.internship} onChange={(event) => set("internship", event.target.value)} className={controlClass}>
          <option value="">Any role</option>
          <option value="true">Internships</option>
          <option value="false">Not internships</option>
        </select>
      </Field>
      <Field label="Status">
        <select value={filters.status} onChange={(event) => set("status", event.target.value)} className={controlClass}>
          <option value="">Any status</option>
          {JOB_STATUSES.map((status) => (
            <option key={status} value={status}>
              {label(status)}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Application">
        <select
          value={filters.application_status}
          onChange={(event) => set("application_status", event.target.value)}
          className={controlClass}
        >
          <option value="">Any application</option>
          {APPLICATION_STATUSES.map((status) => (
            <option key={status} value={status}>
              {status === "none" ? "No application" : label(status)}
            </option>
          ))}
        </select>
      </Field>
    </form>
  );
}

function Field({ label: name, children }: { label: string; children: ReactNode }) {
  return (
    <label className="grid gap-1 text-sm">
      <span className="text-[var(--muted)]">{name}</span>
      {children}
    </label>
  );
}

const controlClass =
  "h-10 rounded-xl border border-[var(--line)] bg-[var(--panel)] px-3 text-sm text-[var(--fg)]";

function hasFilter(filters: JobFilters): boolean {
  return Object.values(filters).some((value) => value.trim() !== "");
}

function ListSkeleton() {
  return (
    <div className="mt-8 space-y-3" aria-hidden="true">
      <div className="h-16 rounded-xl bg-[var(--line)]" />
      <div className="h-16 rounded-xl bg-[var(--line)]" />
    </div>
  );
}
