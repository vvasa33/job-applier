export type JobSummary = {
  id: number;
  title: string;
  company: string;
  locations: string[];
  location_class: string;
  workplace: string;
  sources: string[];
  first_seen_at: string;
  is_internship: boolean | null;
  status: string;
  application_status: string | null;
  ats_type: string | null;
};

export type JobDetail = JobSummary & {
  normalized_title: string;
  normalized_company: string;
  cs_relevance: string;
  term: string | null;
  description_text: string | null;
  apply_url: string | null;
  canonical_apply_url: string | null;
  requisition_id: string | null;
  posted_at: string | null;
  closed_at: string | null;
  status_reason: string | null;
  dedup_key: string;
  possible_duplicate_of: number | null;
  source_records: {
    ats_type: string;
    board_key: string;
    external_id: string;
    url: string;
    title: string;
    first_seen_at: string;
    last_seen_at: string;
  }[];
  requirements: { kind: string; value: string; verified: boolean }[];
  ai_cost_usd: number;
};

export type JobList = { total: number; companies: string[]; jobs: JobSummary[] };

export type Dashboard = {
  job_count: number;
  internship_count: number;
  remote_count: number;
  application_count: number;
  by_status: Record<string, number>;
  recent: JobSummary[];
};

export type JobFilters = {
  q: string;
  internship: string;
  location: string;
  workplace: string;
  company: string;
  status: string;
  application_status: string;
};

export const EMPTY_FILTERS: JobFilters = {
  q: "",
  internship: "",
  location: "",
  workplace: "",
  company: "",
  status: "",
  application_status: "",
};

async function readJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, { signal, headers: { Accept: "application/json" } });
  if (!response.ok) {
    throw new Error(response.status === 404 ? "missing" : `HTTP ${response.status}`);
  }
  return (await response.json()) as T;
}

export function fetchDashboard(signal?: AbortSignal): Promise<Dashboard> {
  return readJson("/api/dashboard", signal);
}

export function fetchJobs(filters: JobFilters, signal?: AbortSignal): Promise<JobList> {
  const params = new URLSearchParams();
  if (filters.q.trim()) params.set("q", filters.q.trim());
  if (filters.internship) params.set("internship", filters.internship);
  if (filters.location.trim()) params.set("location", filters.location.trim());
  if (filters.workplace) params.set("workplace", filters.workplace);
  if (filters.company) params.set("company", filters.company);
  if (filters.status) params.set("status", filters.status);
  if (filters.application_status === "none") params.set("unapplied", "true");
  else if (filters.application_status) params.set("application_status", filters.application_status);
  const query = params.toString();
  return readJson(`/api/jobs${query ? `?${query}` : ""}`, signal);
}

export function fetchJob(id: string, signal?: AbortSignal): Promise<JobDetail> {
  return readJson(`/api/jobs/${id}`, signal);
}
