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
};

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
  const response = await fetch(path, {
    ...init,
    headers: { Accept: "application/json", "Content-Type": "application/json" },
  });
  if (response.status === 404 && missingIsNull) return null as unknown as ApplicationDetail;
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
  return (await response.json()) as ApplicationDetail;
}
