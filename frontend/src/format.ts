const DATE = new Intl.DateTimeFormat(undefined, { dateStyle: "medium" });

export function formatDate(value: string | null): string {
  if (!value) return "Not recorded";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "Not recorded";
  return DATE.format(parsed);
}

export function label(value: string): string {
  return value.replaceAll("_", " ");
}

export function place(job: { locations: string[]; location_class: string }): string {
  if (job.locations.length > 0) return job.locations.join("; ");
  return label(job.location_class);
}

export function workplaceLabel(value: string): string {
  if (value === "onsite") return "On-site";
  if (value === "unspecified") return "Not stated";
  return value.slice(0, 1).toUpperCase() + value.slice(1);
}

export const JOB_STATUSES = [
  "discovered",
  "filtered_out",
  "candidate",
  "scored_low",
  "ineligible",
  "shortlisted",
  "dismissed",
  "application_created",
  "closed",
] as const;

export const APPLICATION_STATUSES = [
  "none",
  "found",
  "matched",
  "saved",
  "tailoring",
  "ready_to_apply",
  "applying",
  "waiting_for_user",
  "submitted",
  "rejected",
  "interview",
  "offer",
  "withdrawn",
] as const;

const DATE_TIME = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });

export function formatDateTime(value: string | null): string {
  if (!value) return "Not recorded";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "Not recorded";
  return DATE_TIME.format(parsed);
}
