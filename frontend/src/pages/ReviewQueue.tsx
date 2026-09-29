import { useEffect, useState } from "react";

import {
  fetchApplicationById,
  fetchReviewQueue,
  reviewApplication,
  reviewOutcome,
  type ApplicationDetail,
  type ReviewAction,
  type ReviewItem,
} from "../api/applications";
import { formatDateTime } from "../format";
import { Chip, Notice, Skeleton, type Tone } from "../ui";
import { Intervention } from "./Intervention";

const POLL_MS = 5000;

function pauseLabel(item: ReviewItem): string {
  if (item.resume_queued) return "Continuing in the agent's browser";
  if (item.pause_kind === "confirm_submit") return "Ready to submit";
  if (item.pause_kind === "verify_submit") return "Check whether it was submitted";
  return item.question_label || item.waiting_reason || "Waiting for an answer";
}

function pauseTone(item: ReviewItem): Tone {
  if (item.pause_kind === "confirm_submit") return "fit";
  if (item.pause_kind === "verify_submit" || item.pause_kind === "needs_look") return "out";
  return "weak";
}

export function ReviewQueue({ onOpen }: { onOpen: (path: string) => void }) {
  const [items, setItems] = useState<ReviewItem[] | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<ApplicationDetail | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    void fetchReviewQueue(controller.signal)
      .then((queue) => {
        if (controller.signal.aborted) return;
        setItems(queue);
        setSelectedId((current) =>
          current !== null && queue.some((item) => item.application_id === current)
            ? current
            : (queue[0]?.application_id ?? null),
        );
      })
      .catch(() => {
        if (!controller.signal.aborted) setLoadError(true);
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (selectedId === null) {
      setDetail(null);
      return;
    }
    const controller = new AbortController();
    void fetchApplicationById(selectedId, controller.signal)
      .then((application) => {
        if (!controller.signal.aborted) setDetail(application);
      })
      .catch(() => {
        if (!controller.signal.aborted) setMessage("The application could not be loaded.");
      });
    return () => controller.abort();
  }, [selectedId]);

  useEffect(() => {
    if (busy) return;
    const timer = window.setInterval(() => {
      void fetchReviewQueue()
        .then((queue) => {
          setItems(queue);
          setSelectedId((current) =>
            current !== null && queue.some((item) => item.application_id === current)
              ? current
              : (queue[0]?.application_id ?? null),
          );
        })
        .catch(() => undefined);
      if (selectedId !== null) {
        void fetchApplicationById(selectedId)
          .then(setDetail)
          .catch(() => undefined);
      }
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [busy, selectedId]);

  async function reload(preferId: number | null) {
    const queue = await fetchReviewQueue();
    setItems(queue);
    const next =
      preferId !== null && queue.some((item) => item.application_id === preferId)
        ? preferId
        : (queue[0]?.application_id ?? null);
    setSelectedId(next);
    if (next === null) setDetail(null);
    else if (next !== preferId) setDetail(await fetchApplicationById(next));
  }

  async function review(action: ReviewAction, fieldId: string, value?: string) {
    if (detail === null) return;
    setBusy(true);
    setMessage(null);
    try {
      const application = await reviewApplication(detail.id, action, fieldId, value);
      setDetail(application);
      setMessage(reviewOutcome(application));
      await reload(application.status === "waiting_for_user" ? application.id : null);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "The answer could not be saved.");
    } finally {
      setBusy(false);
    }
  }

  if (loadError && items === null) {
    return (
      <div className="mt-10">
        <Notice tone="out">The review queue could not be loaded.</Notice>
      </div>
    );
  }
  if (items === null) {
    return (
      <div className="mt-10 grid gap-3 md:grid-cols-[18rem_1fr]">
        <Skeleton className="h-40" />
        <Skeleton className="h-64" />
      </div>
    );
  }

  return (
    <div className="mt-10">
      <h1 className="text-3xl font-medium tracking-tight">Review</h1>
      <p className="mt-2 max-w-[62ch] text-[var(--muted)]">
        Applications waiting for you: questions, submit confirmations, and pages that need a look. The page image is
        shown with each one. This list refreshes on its own.
      </p>
      {items.length === 0 ? (
        <p className="mt-8 text-[var(--muted)]">No applications are waiting for an answer.</p>
      ) : (
        <div className="mt-8 grid items-start gap-6 lg:grid-cols-[18rem_1fr]">
          <ul className="grid gap-2" aria-label="Applications waiting">
            {items.map((item) => {
              const selected = item.application_id === selectedId;
              return (
                <li key={item.application_id}>
                  <button
                    type="button"
                    className={
                      selected
                        ? "grid w-full gap-2 rounded-xl border border-[var(--accent)] bg-[var(--panel)] p-3 text-left"
                        : "grid w-full gap-2 rounded-xl border border-[var(--line)] p-3 text-left"
                    }
                    aria-current={selected ? "true" : undefined}
                    onClick={() => {
                      setMessage(null);
                      setSelectedId(item.application_id);
                    }}
                  >
                    <span className="font-medium">{item.title}</span>
                    <span className="text-sm text-[var(--muted)]">{item.company}</span>
                    <Chip tone={pauseTone(item)}>{pauseLabel(item)}</Chip>
                    <span className="text-xs text-[var(--muted)]">
                      {formatDateTime(item.waiting_since)}
                      {item.question_count > 1 ? ` · ${item.question_count} questions` : ""}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
          <div>
            {detail && detail.status === "waiting_for_user" ? (
              <Intervention application={detail} busy={busy} onReview={(action, fieldId, value) => void review(action, fieldId, value)} />
            ) : (
              <p className="text-[var(--muted)]">Select an application to review its question.</p>
            )}
            {detail ? (
              <button type="button" className="mt-6 text-sm text-[var(--accent)]" onClick={() => onOpen(`/jobs/${detail.job_id}`)}>
                Open the job
              </button>
            ) : null}
            {message ? <p className="mt-4 text-sm">{message}</p> : null}
          </div>
        </div>
      )}
      {message && items.length === 0 ? <p className="mt-4 text-sm">{message}</p> : null}
    </div>
  );
}
