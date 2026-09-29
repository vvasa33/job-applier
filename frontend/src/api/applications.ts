export type ResumeVersion = {
  id: number;
  sha256: string;
  tex_path: string | null;
  pdf_path: string | null;
  diff_path: string | null;
  created_at: string;
};

export type ApplicationEvent = {
  id: number;
  actor: string;
  event_type: string;
  from_status: string | null;
  to_status: string | null;
  reason: string | null;
  resume_version_id: number | null;
  created_at: string;
};

export type PendingField = {
  field_id: string;
  label: string;
  action: string;
  proposed_value: string | null;
  reasoning: string;
  required: boolean;
  confidence: number;
  type: string;
  options: string[];
};

export type ReviewItem = {
  application_id: number;
  job_id: number;
  company: string;
  title: string;
  waiting_since: string;
  question_count: number;
  question_label: string | null;
  waiting_reason: string | null;
  has_screenshot: boolean;
  pause_kind: PauseKind | null;
  resume_queued: boolean;
};

export type PauseKind = "questions" | "confirm_submit" | "needs_look" | "verify_submit";

export type ReviewAction =
  | "approve"
  | "edit"
  | "skip"
  | "stop"
  | "continue"
  | "confirm_submit"
  | "confirm_submitted";

export type ApplicationDetail = {
  id: number;
  job_id: number;
  status: string;
  allowed_transitions: string[];
  opened_at: string;
  status_changed_at: string;
  started_at: string | null;
  submitted_at: string | null;
  resume_version: ResumeVersion | null;
  resume_versions: ResumeVersion[];
  history: ApplicationEvent[];
  pending_fields: PendingField[];
  company: string;
  title: string;
  page_url: string | null;
  page_title: string | null;
  has_screenshot: boolean;
  waiting_reason: string | null;
  pause_kind: PauseKind | null;
  resume_queued: boolean;
  submit_attempted: boolean;
  agent_owns_browser: boolean;
};

export function reviewOutcome(application: ApplicationDetail): string {
  if (application.status === "withdrawn") return "The application was stopped.";
  if (application.status === "submitted") return "Submitted. The confirmation is saved in the history.";
  if (application.status !== "waiting_for_user") return "Saved. The application continued.";
  if (application.resume_queued) return "Saved. The agent will continue this application in its browser.";
  if (application.pause_kind === "confirm_submit") return "Every question is answered. Confirm to submit.";
  if (application.pause_kind === "verify_submit") return "Check whether the submission went through.";
  if (application.pause_kind === "needs_look") return "Saved. The page needs a look before it can continue.";
  return "Saved. The next question is ready.";
}

export class ApplicationRequestError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export function fetchApplication(jobId: number, signal?: AbortSignal): Promise<ApplicationDetail | null> {
  return request(`/api/jobs/${jobId}/application`, { signal }, true);
}

export function openApplication(jobId: number, reason: string): Promise<ApplicationDetail> {
  return request(`/api/jobs/${jobId}/application`, {
    method: "POST",
    body: JSON.stringify({ reason }),
  });
}

export function fetchApplicationById(applicationId: number, signal?: AbortSignal): Promise<ApplicationDetail> {
  return request(`/api/applications/${applicationId}`, { signal });
}

export function fetchReviewQueue(signal?: AbortSignal): Promise<ReviewItem[]> {
  return requestList(`/api/review`, { signal });
}

export function reviewApplication(
  applicationId: number,
  action: ReviewAction,
  fieldId: string,
  value?: string,
): Promise<ApplicationDetail> {
  return request(`/api/applications/${applicationId}/review`, {
    method: "POST",
    body: JSON.stringify({ action, field_id: fieldId, value: value ?? null }),
  });
}

export function resumeApplication(applicationId: number, answers: Record<string, string>): Promise<ApplicationDetail> {
  return request(`/api/applications/${applicationId}/answers`, {
    method: "POST",
    body: JSON.stringify({ answers }),
  });
}

export function transitionApplication(
  applicationId: number,
  to: string,
  reason: string,
  resumeVersionId: number | null,
): Promise<ApplicationDetail> {
  return request(`/api/applications/${applicationId}/transitions`, {
    method: "POST",
    body: JSON.stringify({
      to,
      reason,
      resume_version_id: resumeVersionId,
    }),
  });
}

async function request(
  path: string,
  init: RequestInit,
  missingIsNull = false,
): Promise<ApplicationDetail> {
  return (await readJson(path, init, missingIsNull)) as ApplicationDetail;
}

async function requestList(path: string, init: RequestInit): Promise<ReviewItem[]> {
  return (await readJson(path, init, false)) as ReviewItem[];
}

async function readJson(path: string, init: RequestInit, missingIsNull: boolean): Promise<unknown> {
  const response = await fetch(path, {
    ...init,
    headers: { Accept: "application/json", "Content-Type": "application/json" },
  });
  if (response.status === 404 && missingIsNull) return null;
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // The status code is enough when the body is not JSON.
    }
    throw new ApplicationRequestError(response.status, detail);
  }
  return response.json() as Promise<unknown>;
}
