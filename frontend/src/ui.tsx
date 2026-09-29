import type { ReactNode } from "react";

import type { JobSummary } from "./api/jobs";
import { label, place, workplaceLabel } from "./format";

export type Tone = "fit" | "weak" | "out" | "neutral";

const TONE_CLASS: Record<Tone, string> = {
  fit: "border-transparent bg-[var(--fit-bg)] text-[var(--fit)]",
  weak: "border-transparent bg-[var(--weak-bg)] text-[var(--weak)]",
  out: "border-transparent bg-[var(--out-bg)] text-[var(--out)]",
  neutral: "border-[var(--line)] bg-[var(--panel)] text-[var(--fg)]",
};

export function Chip({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  return (
    <span className={`inline-flex items-center rounded-xl border px-2 py-1 text-xs ${TONE_CLASS[tone]}`}>{children}</span>
  );
}

export function statusTone(status: string | null): Tone {
  if (status === null) return "neutral";
  if (["shortlisted", "application_created", "submitted", "interview", "offer", "ready_to_apply"].includes(status)) {
    return "fit";
  }
  if (["scored_low", "candidate", "waiting_for_user", "applying", "tailoring"].includes(status)) return "weak";
  if (["filtered_out", "ineligible", "dismissed", "closed", "rejected", "withdrawn"].includes(status)) return "out";
  return "neutral";
}

type Signal = { key: string; text: string; tone: Tone };

export function matchSignals(
  job: Pick<JobSummary, "is_internship" | "location_class" | "workplace" | "status">,
  extra?: { cs?: string; verified?: number; total?: number },
): Signal[] {
  const internship: Signal =
    job.is_internship === true
      ? { key: "role", text: "Internship", tone: "fit" }
      : job.is_internship === false
        ? { key: "role", text: "Not an internship", tone: "out" }
        : { key: "role", text: "Role unconfirmed", tone: "neutral" };

  const locationText: Record<string, string> = {
    dmv: "DMV",
    us_remote: "US remote",
    us_other: "US, outside DMV",
    non_us: "Outside the US",
    unknown: "Location unknown",
  };
  const locationTone: Tone =
    job.location_class === "dmv" || job.location_class === "us_remote"
      ? "fit"
      : job.location_class === "non_us"
        ? "out"
        : job.location_class === "us_other"
          ? "weak"
          : "neutral";

  const signals: Signal[] = [
    internship,
    { key: "place", text: locationText[job.location_class] ?? label(job.location_class), tone: locationTone },
    { key: "workplace", text: workplaceLabel(job.workplace), tone: "neutral" },
    { key: "status", text: label(job.status), tone: statusTone(job.status) },
  ];
  if (extra?.cs) {
    signals.push({
      key: "cs",
      text: extra.cs === "relevant" ? "CS relevant" : extra.cs === "not_relevant" ? "Not CS relevant" : "CS relevance unknown",
      tone: extra.cs === "relevant" ? "fit" : extra.cs === "not_relevant" ? "out" : "neutral",
    });
  }
  if (extra && extra.total !== undefined && extra.total > 0) {
    signals.push({
      key: "reqs",
      text: `${extra.verified ?? 0} of ${extra.total} requirements verified`,
      tone: extra.verified === extra.total ? "fit" : "neutral",
    });
  }
  return signals;
}

export function MatchSignals({
  job,
  cs,
  verified,
  total,
}: {
  job: Pick<JobSummary, "is_internship" | "location_class" | "workplace" | "status">;
  cs?: string;
  verified?: number;
  total?: number;
}) {
  const signals = matchSignals(job, { cs, verified, total });
  return (
    <ul className="flex flex-wrap gap-2" aria-label="Match">
      {signals.map((signal) => (
        <li key={signal.key}>
          <Chip tone={signal.tone}>{signal.text}</Chip>
        </li>
      ))}
    </ul>
  );
}

export function JobCard({
  job,
  onOpen,
}: {
  job: JobSummary;
  onOpen: (path: string) => void;
}) {
  const application = job.application_status ? label(job.application_status) : "No application";
  return (
    <button
      type="button"
      onClick={() => onOpen(`/jobs/${job.id}`)}
      className="grid w-full gap-3 rounded-xl border border-[var(--line)] bg-[var(--panel)] p-4 text-left shadow-[var(--shadow)]"
    >
      <span className="flex items-start justify-between gap-3">
        <span className="grid gap-0.5">
          <span className="text-base font-medium tracking-tight">{job.title}</span>
          <span className="text-sm text-[var(--muted)]">{job.company}</span>
        </span>
        <Chip tone={statusTone(job.application_status)}>{application}</Chip>
      </span>
      <span className="text-sm">{place(job)}</span>
      <MatchSignals job={job} />
      <span className="text-xs text-[var(--muted)]">
        {job.sources.length > 0 ? job.sources.join(", ") : "Unknown source"}
      </span>
    </button>
  );
}

export function Timeline({ children }: { children: ReactNode }) {
  return <ol className="grid gap-0 border-l border-[var(--line)] pl-4">{children}</ol>;
}

export function TimelineItem({
  time,
  title,
  tone = "neutral",
  children,
}: {
  time: string;
  title: string;
  tone?: Tone;
  children?: ReactNode;
}) {
  const dot = tone === "out" ? "bg-[var(--out)]" : tone === "weak" ? "bg-[var(--weak)]" : tone === "fit" ? "bg-[var(--fit)]" : "bg-[var(--line)]";
  return (
    <li className="relative grid gap-1 pb-5">
      <span aria-hidden="true" className={`absolute -left-[1.3rem] top-1.5 h-2 w-2 rounded-full ${dot}`} />
      <span className="text-xs text-[var(--muted)]">{time}</span>
      <span className="text-sm font-medium">{title}</span>
      {children}
    </li>
  );
}

export function Notice({ children, tone = "neutral" }: { children: ReactNode; tone?: Tone }) {
  return (
    <p role="status" className={`rounded-xl border px-4 py-3 text-sm ${TONE_CLASS[tone]}`}>
      {children}
    </p>
  );
}

export function Skeleton({ className }: { className: string }) {
  return <div className={`skeleton rounded-xl bg-[var(--line)] ${className}`} aria-hidden="true" />;
}

export const controlClass = "h-10 rounded-xl border border-[var(--line)] bg-[var(--panel)] px-3 text-sm text-[var(--fg)]";
export const buttonClass =
  "inline-flex h-10 items-center rounded-xl border border-[var(--line)] bg-[var(--panel)] px-3 text-sm disabled:opacity-50";
export const primaryClass =
  "inline-flex h-10 items-center rounded-xl bg-[var(--accent)] px-3 text-sm font-medium text-[var(--accent-fg)] disabled:opacity-50";
