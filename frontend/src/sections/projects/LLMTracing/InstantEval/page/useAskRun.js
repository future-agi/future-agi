import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useInstantEvals } from "../useInstantEval";

// One Instant eval run as the Ask page reads it, at any size. The server
// aggregates: one summary (progress, answer counts, the timeline and every
// group the charts need, as counts) is read every 2 s while the run is live,
// and the same summary serves a run of 100 rows or 100,000. Only a run of up
// to 1,500 rows also reads its rows, for the charts' square-per-row mode;
// the rows table pages through the rows endpoint on its own (useAskRows).
//
//   useAskRun({ projectId, taskId, unit, questions, startedAt, tz }) -> {
//     status: "loading" | "preparing" | "running" | "paused" | "done"
//             | "cancelled" | "stalled" | "failed" | "error",
//     summary,              // GET /tracer/instant-eval/summary/ result
//     records | null,       // every row, when the run has ≤ 1,500
//     questions: [{ configId, question, answerType, labels, ... }],
//     total, answered, failed, pending, notRead, rate, eta, sample,
//     startedAt, elapsedMs, window: [isoStart, isoEnd] | null,
//     readNow(),                 // read the summary now, between polls
//     control(action, options),  // pause | resume | cancel | retry_failed | read_more
//   }

export const SUMMARY_POLL_MS = 2000;
// A slow summary means a busy server: poll it less often, never on top of
// a poll still in flight.
export const SLOW_POLL_MS = 5000;
export const SLOWER_POLL_MS = 10000;

/** How long to wait before the next poll of a live run, by how long the last took. */
export const livePollMs = (lastMs) =>
  lastMs > 3000
    ? SLOWER_POLL_MS
    : lastMs > 1000
      ? SLOW_POLL_MS
      : SUMMARY_POLL_MS;
// A run that stopped before finishing is looked at again now and then, in
// case it wasn't stopped after all.
export const STALLED_POLL_MS = 15000;

const statusOf = (error) =>
  Number(error?.statusCode ?? error?.response?.status ?? 0);

/** Retry a failed read twice, unless the server refused it (4xx). */
export const retryServerErrors = (failureCount, error) => {
  const code = statusOf(error);
  if (code >= 400 && code < 500) return false;
  return failureCount < 2;
};

/**
 * The server has no such run or project for this organization (deleted, or
 * another organization's): retrying won't bring it back.
 */
export const isNotFound = (error) => statusOf(error) === 404;
export const SQUARES_MAX = 1500;
const TICK_MS = 1000;
const LIVE = new Set(["pending", "running"]);

const result = (response) => response?.data?.result;
const finite = (value) =>
  typeof value === "number" && Number.isFinite(value) ? value : null;

export const viewerTimeZone = () => {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
};

export const askRunKeys = {
  summary: (projectId, taskId, tz) => ["ask-summary", projectId, taskId, tz],
  records: (projectId, taskId, tz) => ["ask-records", projectId, taskId, tz],
  rows: (projectId, taskId) => ["ask-rows", projectId, taskId],
};

function toAnswer(entry = {}) {
  const answer =
    typeof entry.answer === "string" && entry.answer ? entry.answer : null;
  return {
    answer,
    confidence: finite(entry.confidence),
    explanation:
      typeof entry.explanation === "string" ? entry.explanation.trim() : "",
    status: entry.status || (answer ? "completed" : "pending"),
    // A user's answer is the most common one across their sessions.
    ...(entry.counts && typeof entry.counts === "object"
      ? { counts: entry.counts }
      : {}),
  };
}

/** One row of the rows endpoint as a record. */
export function toRecord(row, unit) {
  const facets = row.facets || {};
  return {
    id: String(row.id),
    unit,
    time: row.time || null,
    user_id: row.user_id || "",
    end_user_id: row.end_user_id || "",
    label: row.label || "",
    excerpt: row.excerpt || "",
    trace_id: row.trace_id || null,
    ...(typeof row.sessions === "number" ? { sessions: row.sessions } : {}),
    facets: {
      model: facets.model ?? null,
      turns: finite(facets.turns),
      duration_ms: finite(facets.duration_ms),
      error: typeof facets.error === "boolean" ? facets.error : null,
    },
    answers: Object.fromEntries(
      Object.entries(row.answers || {}).map(([configId, entry]) => [
        configId,
        toAnswer(entry),
      ]),
    ),
  };
}

const SEPARATOR = " · ";

/**
 * Question text as the page shows it: without "Instant eval · ", and without
 * a leading "Name · " that every question of the run shares (a run named
 * "Synthetic scale test · …" shows "What did the customer come for?").
 */
export function displayQuestions(texts = []) {
  const stripped = texts.map((text) =>
    (text || "").replace(/^Instant eval · /, ""),
  );
  const prefixOf = (text) => {
    const at = text.indexOf(SEPARATOR);
    return at > 0 ? text.slice(0, at + SEPARATOR.length) : null;
  };
  const prefix = prefixOf(stripped[0] || "");
  const shared =
    prefix &&
    stripped.every(
      (text) => text.startsWith(prefix) && text.length > prefix.length,
    );
  return shared ? stripped.map((text) => text.slice(prefix.length)) : stripped;
}

/** The summary's questions, with what their saved evaluators add. */
export function summaryQuestions(summary, configsById = {}) {
  const items = summary?.questions || [];
  const shown = displayQuestions(
    items.map(
      (item) => item.question || configsById[item.config_id]?.question || "",
    ),
  );
  return items.map((item, index) => {
    const saved = configsById[item.config_id] || {};
    return {
      configId: item.config_id,
      question: shown[index],
      fullQuestion: item.question || saved.question || "",
      answerType: item.answer_type || saved.answerType,
      labels: item.labels?.length ? item.labels : saved.labels || [],
      definitions: saved.definitions || {},
      ...(saved.reads ? { reads: saved.reads } : {}),
      ...(saved.templateId ? { templateId: saved.templateId } : {}),
    };
  });
}

/** The run's window: the view's, or the span of its rows. */
export function summaryWindow(summary) {
  if (!summary) return null;
  const timeline = summary.timeline || {};
  if (Number.isFinite(timeline.from) && Number.isFinite(timeline.to))
    return [
      new Date(timeline.from).toISOString(),
      new Date(timeline.to).toISOString(),
    ];
  if (summary.window?.from && summary.window?.to)
    return [summary.window.from, summary.window.to];
  return null;
}

// Runs that ended and can be asked again.
export const RERUNNABLE_STATES = new Set(["done", "cancelled", "failed"]);

/** The page's status word for a summary. */
export function runStatus(summary) {
  if (!summary) return "loading";
  if (summary.status === "cancelled") return "cancelled";
  if (summary.status === "stalled") return "stalled";
  // Failed before Jev read a row (a run that stopped partway is stalled).
  if (summary.status === "failed") return "failed";
  if (summary.status === "pausing") return "pausing";
  if (summary.status === "paused") return "paused";
  if (LIVE.has(summary.status))
    return summary.preparing ? "preparing" : "running";
  return "done";
}

/** Read a run's summary; a 304 keeps what the page already has. */
export async function fetchSummary({ projectId, taskId, tz, etag, signal }) {
  const response = await axios.get(endpoints.project.instantEvalSummary(), {
    params: { project_id: projectId, eval_task_id: taskId, tz },
    headers: etag ? { "If-None-Match": etag } : undefined,
    validateStatus: (code) => code === 200 || code === 304,
    signal,
  });
  return {
    notModified: response.status === 304,
    etag: response.headers?.etag || null,
    summary: response.status === 304 ? null : result(response),
  };
}

/** Every row of a run of up to 1,500, newest first, for the squares. */
export async function fetchRecords({ projectId, taskId, tz, unit, signal }) {
  const response = await axios.get(endpoints.project.instantEvalRows(), {
    params: {
      project_id: projectId,
      eval_task_id: taskId,
      tz,
      compact: true,
      page_size: SQUARES_MAX,
    },
    signal,
  });
  return (result(response)?.rows || []).map((row) => toRecord(row, unit));
}

/** Pause, resume, cancel, retry the failed rows or read more of a sample. */
export async function controlRun({ projectId, taskId, action, more, confirm }) {
  const response = await axios.post(endpoints.project.instantEvalControl(), {
    project_id: projectId,
    eval_task_id: taskId,
    action,
    ...(more !== undefined ? { more } : {}),
    ...(confirm ? { confirm: true } : {}),
  });
  return result(response);
}

function useClock(running) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!running) return undefined;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), TICK_MS);
    return () => clearInterval(timer);
  }, [running]);
  return now;
}

/**
 * A run of the Ask page. `questions` and `startedAt` describe a run that is
 * still being created (no `taskId` yet).
 */
export function useAskRun({
  projectId,
  taskId,
  unit: unitHint,
  questions: pendingQuestions,
  startedAt: launchedAt,
  tz,
}) {
  const zone = tz || viewerTimeZone();
  const queryClient = useQueryClient();
  const saved = useInstantEvals(projectId, { enabled: Boolean(projectId) });
  const configsById = useMemo(
    () => Object.fromEntries(saved.all.map((item) => [item.configId, item])),
    [saved.all],
  );
  const etag = useRef(null);
  // How often to read the summary again, or false.
  const liveRef = useRef(SUMMARY_POLL_MS);
  // How long the last summary read took.
  const pollMs = useRef(0);

  const summaryKey = askRunKeys.summary(projectId, taskId, zone);
  const summaryQuery = useQuery({
    queryKey: summaryKey,
    enabled: Boolean(projectId && taskId),
    queryFn: async ({ signal }) => {
      const started = Date.now();
      try {
        const read = await fetchSummary({
          projectId,
          taskId,
          tz: zone,
          etag: etag.current,
          signal,
        });
        if (read.notModified) {
          const previous = queryClient.getQueryData(summaryKey);
          if (previous) return previous;
          etag.current = null;
          return (await fetchSummary({ projectId, taskId, tz: zone, signal }))
            .summary;
        }
        etag.current = read.etag;
        return read.summary;
      } finally {
        pollMs.current = Date.now() - started;
      }
    },
    refetchInterval: () => liveRef.current,
    // A 4xx (a deleted run, a bad time zone) is not retried: it won't change.
    retry: retryServerErrors,
    meta: { errorHandled: true },
  });
  const summary = summaryQuery.data || null;
  const status = summary
    ? runStatus(summary)
    : summaryQuery.isError
      ? "error"
      : "loading";
  // The next poll starts this long after the last one finished (the
  // interval restarts with each result), so polls never pile up.
  // A failing read stops the polling until the person retries (or, with a
  // run already on screen, backs off hard): it used to poll every 2 s, three
  // requests at a time, for as long as the page was open.
  liveRef.current = summaryQuery.isError
    ? summary
      ? STALLED_POLL_MS
      : false
    : !summary ||
        status === "running" ||
        status === "preparing" ||
        status === "pausing"
      ? livePollMs(pollMs.current)
      : status === "stalled"
        ? STALLED_POLL_MS
        : false;

  const unit = summary?.unit || unitHint || "sessions";
  const total = summary?.total ?? 0;
  const settled = (summary?.answered ?? 0) + (summary?.failed ?? 0);

  // The squares: every row of a small run, read again as answers land.
  const wantRecords = Boolean(
    summary && !summary.preparing && total > 0 && total <= SQUARES_MAX,
  );
  // While answers land, the squares are read again every twentieth of the
  // run (50 answers of 1,000), not on every poll; once it ends, in full.
  const runLive = status === "running" || status === "preparing";
  const recordsStep = Math.max(1, Math.ceil(total / 20));
  const recordsMark = runLive ? Math.floor(settled / recordsStep) : settled;
  // Each time the run goes live again (Retry, Read more, Resume) it can end
  // where it ended before: five failed rows retried and answered settle at
  // five again. Each stretch reads its squares anew, as the rows table does,
  // rather than reusing the squares cached when the last one ended.
  const [stretch, setStretch] = useState({ live: runLive, count: 0 });
  if (stretch.live !== runLive)
    setStretch({ live: runLive, count: stretch.count + (runLive ? 1 : 0) });
  const recordsQuery = useQuery({
    queryKey: [
      ...askRunKeys.records(projectId, taskId, zone),
      stretch.count,
      recordsMark,
      total,
      runLive,
    ],
    enabled: wantRecords,
    queryFn: ({ signal }) =>
      fetchRecords({ projectId, taskId, tz: zone, unit, signal }),
    placeholderData: (previous) => previous,
    staleTime: Infinity,
    retry: 2,
    meta: { errorHandled: true },
  });
  const records = wantRecords ? recordsQuery.data || null : null;

  const questions = useMemo(
    () =>
      summary
        ? summaryQuestions(summary, configsById)
        : (pendingQuestions || []).map((item) => ({
            configId: item.configId || "",
            question: item.question,
            answerType: item.answerType,
            labels: item.labels || [],
          })),
    [summary, configsById, pendingQuestions],
  );

  const startedAt = launchedAt || summary?.started_at || null;
  const finishedAt = summary?.finished_at || null;
  const live = status === "running" || status === "preparing";
  const now = useClock(live || (!summary && Boolean(launchedAt)));
  const elapsedMs = startedAt
    ? Math.max(
        0,
        (finishedAt && !live ? new Date(finishedAt).getTime() : now) -
          new Date(startedAt).getTime(),
      )
    : null;

  const window = useMemo(() => summaryWindow(summary), [summary]);

  // Read the summary now, joining a read already in flight rather than
  // cancelling it (a slow server would otherwise never answer one).
  const { refetch: refetchSummary } = summaryQuery;
  const readNow = useCallback(
    () => refetchSummary({ cancelRefetch: false }),
    [refetchSummary],
  );

  const control = async (action, options = {}) => {
    const response = await controlRun({
      projectId,
      taskId,
      action,
      ...options,
    });
    etag.current = null;
    liveRef.current = SUMMARY_POLL_MS;
    await summaryQuery.refetch();
    return response;
  };

  return {
    taskId: taskId || null,
    status,
    summary,
    records,
    recordsLoading: wantRecords && !recordsQuery.data,
    questions,
    total,
    answered: summary?.answered ?? 0,
    failed: summary?.failed ?? 0,
    pending: summary?.pending ?? 0,
    notRead: summary?.not_read ?? 0,
    rate: summary?.rate_per_min ?? null,
    eta: summary?.eta_s ?? null,
    sample: summary?.sample || null,
    createdBy: summary?.created_by || null,
    creditsUsed: summary?.credits_used ?? null,
    startedAt,
    elapsedMs,
    window,
    tz: zone,
    unit,
    // The unit the server reports for the run, once it has been read.
    knownUnit: summary?.unit || null,
    reads: questions[0]?.reads || null,
    taskStatus: summary?.status || null,
    error: summaryQuery.isError && !summary ? summaryQuery.error : null,
    refetch: () => {
      etag.current = null;
      summaryQuery.refetch();
      if (wantRecords) recordsQuery.refetch();
    },
    readNow,
    control,
  };
}
