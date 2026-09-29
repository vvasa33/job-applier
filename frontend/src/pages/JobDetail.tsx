import { useEffect, useState } from "react";

import { fetchJob, type JobDetail as Detail } from "../api/jobs";
import { formatDate, label, place, workplaceLabel } from "../format";
import { ApplicationPanel } from "./ApplicationPanel";

export function JobDetail({ id, onOpen }: { id: string; onOpen: (path: string) => void }) {
  const [state, setState] = useState<
    { phase: "loading" } | { phase: "missing" } | { phase: "error" } | { phase: "ready"; job: Detail }
  >({ phase: "loading" });

  useEffect(() => {
    const controller = new AbortController();
    void fetchJob(id, controller.signal)
      .then((job) => {
        if (!controller.signal.aborted) setState({ phase: "ready", job });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setState({ phase: error instanceof Error && error.message === "missing" ? "missing" : "error" });
      });
    return () => controller.abort();
  }, [id]);

  if (state.phase === "loading") {
    return <div className="mt-10 h-24 rounded-xl bg-[var(--line)]" aria-hidden="true" />;
  }
  if (state.phase === "missing") {
    return (
      <div className="mt-10">
        <p>That job is not in the local database.</p>
        <button type="button" className="mt-4 text-sm text-[var(--accent)]" onClick={() => onOpen("/jobs")}>
          Back to jobs
        </button>
      </div>
    );
  }
  if (state.phase === "error") {
    return <p className="mt-10 text-[var(--muted)]">The job could not be loaded. Check that the local API is running.</p>;
  }

  const job = state.job;
  return <JobArticle key={job.id} job={job} onOpen={onOpen} />;
}

function JobArticle({ job, onOpen }: { job: Detail; onOpen: (path: string) => void }) {
  const [shown, setShown] = useState(job);
  const facts: [string, string][] = [
    ["Company", shown.company],
    ["Normalized company", shown.normalized_company],
    ["Normalized title", shown.normalized_title],
    ["Location", place(shown)],
    ["Location class", label(shown.location_class)],
    ["Workplace", workplaceLabel(shown.workplace)],
    ["Internship", shown.is_internship === null ? "Unknown" : shown.is_internship ? "Yes" : "No"],
    ["CS relevance", label(shown.cs_relevance)],
    ["Term", shown.term ?? "Not recorded"],
    ["Status", label(shown.status)],
    ["Status reason", shown.status_reason ?? "None"],
    ["Application", shown.application_status ? label(shown.application_status) : "No application"],
    ["Source", shown.sources.join(", ") || "Unknown"],
    ["Requisition", shown.requisition_id ?? "None"],
    ["Discovered", formatDate(shown.first_seen_at)],
    ["Posted", formatDate(shown.posted_at)],
    ["Closed", formatDate(shown.closed_at)],
    ["Dedup key", shown.dedup_key],
  ];

  return (
    <article className="mt-10">
      <button type="button" className="text-sm text-[var(--accent)]" onClick={() => onOpen("/jobs")}>
        Back to jobs
      </button>
      <h1 className="mt-4 text-3xl font-medium tracking-tight">{shown.title}</h1>
      <p className="mt-2 text-[var(--muted)]">{shown.company}</p>
      {shown.apply_url ? (
        <a
          href={shown.apply_url}
          className="mt-6 inline-flex h-11 items-center rounded-xl bg-[var(--accent)] px-4 text-sm font-medium text-[var(--accent-fg)]"
          target="_blank"
          rel="noreferrer"
        >
          Open application
        </a>
      ) : null}
      <dl className="mt-8 max-w-3xl divide-y divide-[var(--line)] border-y border-[var(--line)]">
        {facts.map(([name, value]) => (
          <div key={name} className="grid gap-1 py-3 md:grid-cols-[12rem_1fr]">
            <dt className="text-sm text-[var(--muted)]">{name}</dt>
            <dd className="break-words font-mono text-sm">{value}</dd>
          </div>
        ))}
      </dl>
      <ApplicationPanel
        jobId={shown.id}
        onStatus={(status) => {
          if (status === shown.application_status) return;
          void fetchJob(String(shown.id)).then(setShown).catch(() => undefined);
        }}
      />
      <section className="mt-10 max-w-3xl">
        <h2 className="text-lg font-medium">Description</h2>
        <p className="mt-3 whitespace-pre-wrap leading-relaxed text-[var(--fg)]">
          {shown.description_text ?? "No description was stored."}
        </p>
      </section>
      <section className="mt-10 max-w-3xl">
        <h2 className="text-lg font-medium">Sources</h2>
        {shown.source_records.length === 0 ? <p className="mt-3 text-[var(--muted)]">No source rows.</p> : null}
        <ul className="mt-3 divide-y divide-[var(--line)] border-y border-[var(--line)]">
          {shown.source_records.map((source) => (
            <li key={`${source.ats_type}-${source.external_id}`} className="py-3 text-sm">
              <p className="font-medium capitalize">{source.ats_type}</p>
              <p className="mt-1 text-[var(--muted)]">
                {source.board_key} · {source.external_id}
              </p>
              <a href={source.url} className="mt-1 block break-all text-[var(--accent)]" target="_blank" rel="noreferrer">
                {source.url}
              </a>
              <p className="mt-1 text-[var(--muted)]">
                First seen {formatDate(source.first_seen_at)}, last seen {formatDate(source.last_seen_at)}
              </p>
            </li>
          ))}
        </ul>
      </section>
      {shown.requirements.length > 0 ? (
        <section className="mt-10 max-w-3xl">
          <h2 className="text-lg font-medium">Requirements</h2>
          <ul className="mt-3 divide-y divide-[var(--line)] border-y border-[var(--line)]">
            {shown.requirements.map((item) => (
              <li key={item.kind} className="py-3 text-sm">
                <span className="capitalize">{label(item.kind)}</span>: {item.value}
                {item.verified ? " (verified)" : ""}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      {shown.possible_duplicate_of ? (
        <p className="mt-8 text-sm">
          Possible duplicate of{" "}
          <button type="button" className="text-[var(--accent)]" onClick={() => onOpen(`/jobs/${shown.possible_duplicate_of}`)}>
            job {shown.possible_duplicate_of}
          </button>
        </p>
      ) : null}
    </article>
  );
}
