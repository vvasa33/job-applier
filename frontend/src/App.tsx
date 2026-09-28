import { ArrowClockwise, CheckCircle, Moon, Sun, WarningCircle } from "@phosphor-icons/react";
import { useCallback, useEffect, useState } from "react";

import { checkHealth, type HealthResult } from "./api/health";

type LoadState = { phase: "loading" } | ({ phase: "ready" } & HealthResult);

function readDark(): boolean {
  return document.documentElement.classList.contains("dark");
}

export function App() {
  const [themeDark, setThemeDark] = useState(readDark);
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<LoadState>({ phase: "loading" });

  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      void checkHealth(controller.signal).then((result) => {
        if (!controller.signal.aborted) {
          setState({ phase: "ready", ...result });
        }
      });
    }, 0);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [attempt]);

  const toggleTheme = useCallback(() => {
    const next = !themeDark;
    document.documentElement.classList.toggle("dark", next);
    document.documentElement.style.colorScheme = next ? "dark" : "light";
    localStorage.setItem("jobhunter-theme", next ? "dark" : "light");
    setThemeDark(next);
  }, [themeDark]);

  const headline =
    state.phase === "loading"
      ? "Checking the local API"
      : state.kind === "ok"
        ? "The API is reachable"
        : state.kind === "degraded"
          ? "The API answered"
          : "The API is not reachable";

  const detail =
    state.phase === "loading"
      ? "Waiting for a response from the backend on this machine."
      : state.kind === "ok"
        ? "SQLite answered a connectivity check. This screen only confirms the backend is up."
        : state.kind === "degraded"
          ? "The process is up, but SQLite did not answer the connectivity check."
          : "Nothing answered on this machine. Start the backend, then try again.";

  return (
    <main className="mx-auto flex min-h-[100dvh] max-w-3xl flex-col px-6 py-10 md:px-10 md:py-14">
      <header className="flex h-12 items-center justify-between">
        <p className="text-[15px] font-medium tracking-tight">Job Hunter</p>
        <button
          type="button"
          onClick={toggleTheme}
          className="inline-flex h-10 items-center gap-2 rounded-xl border border-[var(--line)] bg-[var(--panel)] px-3 text-sm text-[var(--fg)] transition-transform active:scale-[0.98]"
        >
          {themeDark ? <Sun size={18} weight="regular" /> : <Moon size={18} weight="regular" />}
          {themeDark ? "Light" : "Dark"}
        </button>
      </header>

      <section className="mt-16 max-w-[38rem] md:mt-24">
        <StatusMark state={state} />
        <h1 className="mt-6 text-4xl font-medium tracking-tight text-balance md:text-5xl">{headline}</h1>
        <p className="mt-4 max-w-[58ch] text-base leading-relaxed text-[var(--muted)]">{detail}</p>

        {state.phase === "loading" ? <Skeleton /> : null}
        {state.phase === "ready" && state.kind !== "unreachable" ? <Facts report={state.report} /> : null}
        {state.phase === "ready" && state.kind === "unreachable" ? (
          <button
            type="button"
            onClick={() => {
              setState({ phase: "loading" });
              setAttempt((value) => value + 1);
            }}
            className="mt-8 inline-flex h-11 items-center gap-2 rounded-xl bg-[var(--accent)] px-4 text-sm font-medium text-[var(--accent-fg)] transition-transform active:scale-[0.98]"
          >
            <ArrowClockwise size={18} weight="bold" />
            Retry
          </button>
        ) : null}
      </section>
    </main>
  );
}

function StatusMark({ state }: { state: LoadState }) {
  if (state.phase === "loading") {
    return <div className="h-8 w-8 rounded-full border border-[var(--line)] bg-[var(--panel)]" aria-hidden="true" />;
  }
  if (state.kind === "ok") {
    return <CheckCircle size={32} weight="regular" color="var(--accent)" aria-hidden="true" />;
  }
  return <WarningCircle size={32} weight="regular" color="var(--fg)" aria-hidden="true" />;
}

function Skeleton() {
  return (
    <div className="mt-10 space-y-3" aria-hidden="true">
      <div className="h-4 w-40 rounded-xl bg-[var(--line)]" />
      <div className="h-4 w-56 rounded-xl bg-[var(--line)]" />
      <div className="h-4 w-28 rounded-xl bg-[var(--line)]" />
    </div>
  );
}

function Facts({ report }: { report: { database: string; version: string } }) {
  const rows = [
    ["Database", report.database],
    ["Version", report.version],
    ["Endpoint", "/health"],
  ] as const;

  return (
    <dl className="mt-10 max-w-sm rounded-xl border border-[var(--line)] bg-[var(--panel)] shadow-[var(--shadow)]">
      {rows.map(([label, value]) => (
        <div key={label} className="grid grid-cols-[8rem_1fr] gap-4 border-b border-[var(--line)] px-4 py-3 last:border-b-0">
          <dt className="text-sm text-[var(--muted)]">{label}</dt>
          <dd className="font-mono text-sm">{value}</dd>
        </div>
      ))}
    </dl>
  );
}
