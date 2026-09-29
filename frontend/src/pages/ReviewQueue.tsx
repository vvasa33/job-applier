import { useEffect, useState } from "react";

import {
  fetchApplicationById,
  fetchReviewQueue,
  reviewApplication,
  type ApplicationDetail,
  type ReviewAction,
  type ReviewItem,
} from "../api/applications";
import { formatDateTime } from "../format";
import { Intervention } from "./Intervention";

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
      if (application.status === "withdrawn") setMessage("The application was stopped.");
      else if (application.status === "waiting_for_user") setMessage("Saved. The next question is ready.");
      else setMessage("Saved. The application continued and was not submitted.");
      await reload(application.status === "waiting_for_user" ? application.id : null);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "The answer could not be saved.");
    } finally {
      setBusy(false);
    }
  }

  if (loadError && items === null) {
    return <p className="mt-10 text-[var(--muted)]">The review queue could not be loaded.</p>;
  }
  if (items === null) {
    return <div className="mt-10 h-24 rounded-xl bg-[var(--line)]" aria-hidden="true" />;
  }

  return (
    <div className="mt-10">
      <h1 className="text-3xl font-medium tracking-tight">Review</h1>
      <p className="mt-2 max-w-[62ch] text-[var(--muted)]">
        Applications waiting for an answer. The question, suggestion, and page image are shown here.
      </p>
      {items.length === 0 ? (
        <p className="mt-8 text-[var(--muted)]">No applications are waiting for an answer.</p>
      ) : (
        <div className="mt-8 grid gap-8 lg:grid-cols-[18rem_1fr]">
          <ul className="divide-y divide-[var(--line)] border-y border-[var(--line)]">
            {items.map((item) => {
              const selected = item.application_id === selectedId;
              return (
                <li key={item.application_id}>
                  <button
                    type="button"
                    className={selected ? "w-full bg-[var(--panel)] py-3 text-left" : "w-full py-3 text-left"}
                    aria-current={selected ? "true" : undefined}
                    onClick={() => {
                      setMessage(null);
                      setSelectedId(item.application_id);
                    }}
                  >
                    <span className="grid gap-1">
                      <span className="font-medium">{item.title}</span>
                      <span className="text-sm text-[var(--muted)]">{item.company}</span>
                      <span className="text-sm">
                        {item.question_label || item.waiting_reason || "Waiting for an answer"}
                        {item.question_count > 1 ? ` · ${item.question_count} questions` : ""}
                      </span>
                      <span className="text-sm text-[var(--muted)]">{formatDateTime(item.waiting_since)}</span>
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
