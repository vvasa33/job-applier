export type AutonomyLevel = "observe" | "assist" | "supervised";

export type AgentStatus = {
  desired: "running" | "stopped";
  phase: string;
  alive: boolean;
  pid: number | null;
  started_at: string | null;
  heartbeat_at: string | null;
  last_discovery_at: string | null;
  next_discovery_at: string | null;
  current: {
    job_id: number | null;
    application_id: number | null;
    company: string | null;
    title: string | null;
    step: string | null;
  };
  message: string | null;
  autonomy_level: AutonomyLevel;
  discovery_interval_hours: number;
  waiting_for_user: number;
  submitted_today: number;
  failing: number;
  gave_up: number;
  sources_configured: number;
  sources_error: string | null;
  limits: {
    daily_applications: number;
    applications_today: number;
    daily_auto_submits: number;
    apply_interval_seconds: number;
    max_failures: number;
  };
};

export type AgentActivity = {
  id: number;
  created_at: string;
  level: "info" | "warning" | "error" | string;
  kind: string;
  message: string;
  job_id: number | null;
  application_id: number | null;
};

export function fetchAgent(signal?: AbortSignal): Promise<AgentStatus> {
  return readJson("/api/agent", { signal }) as Promise<AgentStatus>;
}

export function fetchActivity(limit = 100, signal?: AbortSignal): Promise<AgentActivity[]> {
  return readJson(`/api/agent/activity?limit=${limit}`, { signal }) as Promise<AgentActivity[]>;
}

export function agentCommand(command: "start" | "stop" | "discover"): Promise<AgentStatus> {
  return readJson(`/api/agent/${command}`, { method: "POST" }) as Promise<AgentStatus>;
}

export function retryApplication(applicationId: number): Promise<AgentStatus> {
  return readJson(`/api/agent/applications/${applicationId}/retry`, { method: "POST" }) as Promise<AgentStatus>;
}

export function updateAgentSettings(settings: {
  autonomy_level?: AutonomyLevel;
  discovery_interval_hours?: number;
}): Promise<AgentStatus> {
  return readJson("/api/agent/settings", { method: "PUT", body: JSON.stringify(settings) }) as Promise<AgentStatus>;
}

async function readJson(path: string, init: RequestInit): Promise<unknown> {
  const response = await fetch(path, {
    ...init,
    headers: { Accept: "application/json", "Content-Type": "application/json" },
  });
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // The status code is enough when the body is not JSON.
    }
    throw new Error(detail);
  }
  return response.json() as Promise<unknown>;
}
