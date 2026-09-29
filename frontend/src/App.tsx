import { Moon, Sun } from "@phosphor-icons/react";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";

import { fetchReviewQueue } from "./api/applications";
import { EMPTY_FILTERS, type JobFilters } from "./api/jobs";
import { Agent } from "./pages/Agent";
import { Dashboard } from "./pages/Dashboard";
import { JobDetail } from "./pages/JobDetail";
import { Jobs } from "./pages/Jobs";
import { ReviewQueue } from "./pages/ReviewQueue";
import { Notice } from "./ui";

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

  const [waiting, setWaiting] = useState<number | null>(null);

  useEffect(() => {
    let stop = false;
    async function load() {
      try {
        const queue = await fetchReviewQueue();
        if (!stop) setWaiting(queue.length);
      } catch {
        if (!stop) setWaiting(null);
      }
    }
    void load();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void load();
    }, 5000);
    return () => {
      stop = true;
      window.clearInterval(timer);
    };
  }, [path]);

  const url = useMemo(() => new URL(path, "http://localhost"), [path]);
  const jobId = url.pathname.match(/^\/jobs\/(\d+)$/)?.[1];
  const filters = url.pathname === "/jobs" ? readFilters(url.search) : EMPTY_FILTERS;
  const reviewCount = waiting ?? 0;

  return (
    <div className="mx-auto min-h-[100dvh] max-w-5xl px-4 py-4 md:px-10 md:py-6">
      <header className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 border-b border-[var(--line)] py-2 md:h-16 md:flex-nowrap md:py-0">
        <button type="button" className="shrink-0 font-medium tracking-tight" onClick={() => open("/")}>
          Job Hunter
        </button>
        <button
          type="button"
          onClick={toggleTheme}
          aria-label={themeDark ? "Switch to light theme" : "Switch to dark theme"}
          className="inline-flex h-10 shrink-0 items-center gap-2 rounded-xl border border-[var(--line)] bg-[var(--panel)] px-3 text-sm md:order-last"
        >
          {themeDark ? <Sun size={18} /> : <Moon size={18} />}
          <span className="hidden sm:inline">{themeDark ? "Light" : "Dark"}</span>
        </button>
        <nav className="flex w-full min-w-0 items-center gap-1 overflow-x-auto text-sm md:w-auto" aria-label="Primary">
          <NavLink active={url.pathname === "/"} onClick={() => open("/")}>
            Dashboard
          </NavLink>
          <NavLink active={url.pathname.startsWith("/jobs")} onClick={() => open("/jobs")}>
            Jobs
          </NavLink>
          <NavLink active={url.pathname.startsWith("/review")} onClick={() => open("/review")}>
            Review
            {reviewCount > 0 ? (
              <span className="ml-1.5 inline-flex h-5 min-w-5 items-center justify-center rounded-xl bg-[var(--weak-bg)] px-1 text-xs text-[var(--weak)]">
                {reviewCount}
              </span>
            ) : null}
          </NavLink>
          <NavLink active={url.pathname === "/agent"} onClick={() => open("/agent")}>
            Agent
          </NavLink>
        </nav>
      </header>
      {reviewCount > 0 && url.pathname !== "/review" ? (
        <div className="mt-4">
          <Notice tone="weak">
            <button type="button" className="text-left" onClick={() => open("/review")}>
              {reviewCount === 1
                ? "1 application is waiting for you."
                : `${reviewCount} applications are waiting for you.`}
            </button>
          </Notice>
        </div>
      ) : null}
      <main className="enter">
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

function NavLink({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-current={active ? "page" : undefined}
      className={
        active
          ? "inline-flex h-10 shrink-0 items-center rounded-xl bg-[var(--panel)] px-2.5 text-[var(--fg)] md:px-3"
          : "inline-flex h-10 shrink-0 items-center rounded-xl px-2.5 text-[var(--muted)] md:px-3"
      }
      onClick={onClick}
    >
      {children}
    </button>
  );
}
