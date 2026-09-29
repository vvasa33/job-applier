import { Moon, Sun } from "@phosphor-icons/react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { EMPTY_FILTERS, type JobFilters } from "./api/jobs";
import { Agent } from "./pages/Agent";
import { Dashboard } from "./pages/Dashboard";
import { JobDetail } from "./pages/JobDetail";
import { Jobs } from "./pages/Jobs";
import { ReviewQueue } from "./pages/ReviewQueue";

function readDark(): boolean {
  return document.documentElement.classList.contains("dark");
}

function readFilters(search: string): JobFilters {
  const params = new URLSearchParams(search);
  return {
    q: params.get("q") ?? "",
    internship: params.get("internship") ?? "",
    location: params.get("location") ?? "",
    workplace: params.get("workplace") ?? "",
    company: params.get("company") ?? "",
    status: params.get("status") ?? "",
    application_status: params.get("application_status") ?? "",
  };
}

function writeFilters(filters: JobFilters): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value.trim()) params.set(key, value);
  }
  const query = params.toString();
  return query ? `/jobs?${query}` : "/jobs";
}

export function App() {
  const [themeDark, setThemeDark] = useState(readDark);
  const [path, setPath] = useState(`${window.location.pathname}${window.location.search}`);

  useEffect(() => {
    const onPop = () => setPath(`${window.location.pathname}${window.location.search}`);
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const open = useCallback((next: string) => {
    window.history.pushState({}, "", next);
    setPath(next);
  }, []);

  const toggleTheme = useCallback(() => {
    const next = !themeDark;
    document.documentElement.classList.toggle("dark", next);
    document.documentElement.style.colorScheme = next ? "dark" : "light";
    localStorage.setItem("jobhunter-theme", next ? "dark" : "light");
    setThemeDark(next);
  }, [themeDark]);

  const url = useMemo(() => new URL(path, "http://localhost"), [path]);
  const jobId = url.pathname.match(/^\/jobs\/(\d+)$/)?.[1];
  const filters = url.pathname === "/jobs" ? readFilters(url.search) : EMPTY_FILTERS;

  return (
    <div className="mx-auto min-h-[100dvh] max-w-5xl px-6 py-6 md:px-10">
      <header className="flex h-14 items-center justify-between gap-4 border-b border-[var(--line)]">
        <nav className="flex items-center gap-5 text-sm">
          <button type="button" className="font-medium tracking-tight" onClick={() => open("/")}>
            Job Hunter
          </button>
          <button type="button" className={navClass(url.pathname === "/")} onClick={() => open("/")}>
            Dashboard
          </button>
          <button type="button" className={navClass(url.pathname.startsWith("/jobs"))} onClick={() => open("/jobs")}>
            Jobs
          </button>
          <button type="button" className={navClass(url.pathname.startsWith("/review"))} onClick={() => open("/review")}>
            Review
          </button>
          <button type="button" className={navClass(url.pathname === "/agent")} onClick={() => open("/agent")}>
            Agent
          </button>
        </nav>
        <button
          type="button"
          onClick={toggleTheme}
          className="inline-flex h-10 items-center gap-2 rounded-xl border border-[var(--line)] bg-[var(--panel)] px-3 text-sm"
        >
          {themeDark ? <Sun size={18} /> : <Moon size={18} />}
          {themeDark ? "Light" : "Dark"}
        </button>
      </header>
      <main>
        {url.pathname === "/" ? <Dashboard onOpen={open} /> : null}
        {url.pathname === "/jobs" ? (
          <Jobs filters={filters} onChange={(next) => open(writeFilters(next))} onOpen={open} />
        ) : null}
        {jobId ? <JobDetail id={jobId} onOpen={open} /> : null}
        {url.pathname === "/review" ? <ReviewQueue onOpen={open} /> : null}
        {url.pathname === "/agent" ? <Agent onOpen={open} /> : null}
        {!KNOWN_PATHS.has(url.pathname) && !jobId ? (
          <p className="mt-10">
            That page does not exist.{" "}
            <button type="button" className="text-[var(--accent)]" onClick={() => open("/")}>
              Go to the dashboard
            </button>
          </p>
        ) : null}
      </main>
    </div>
  );
}

const KNOWN_PATHS = new Set(["/", "/jobs", "/review", "/agent"]);

function navClass(active: boolean): string {
  return active ? "text-[var(--fg)]" : "text-[var(--muted)]";
}
