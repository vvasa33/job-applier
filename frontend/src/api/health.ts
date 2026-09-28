export type HealthReport = {
  status: "ok" | "degraded";
  database: "ok" | "unavailable";
  version: string;
};

export type HealthResult =
  | { kind: "ok"; report: HealthReport }
  | { kind: "degraded"; report: HealthReport }
  | { kind: "unreachable" };

export async function checkHealth(signal: AbortSignal): Promise<HealthResult> {
  let response: Response;
  try {
    response = await fetch("/health", {
      signal,
      headers: { Accept: "application/json" },
    });
  } catch {
    return { kind: "unreachable" };
  }

  if (response.status === 200 || response.status === 503) {
    const report = (await response.json()) as HealthReport;
    if (response.status === 200 && report.status === "ok" && report.database === "ok") {
      return { kind: "ok", report };
    }
    return { kind: "degraded", report };
  }

  return { kind: "unreachable" };
}
