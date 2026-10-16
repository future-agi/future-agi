import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { historyRun, useInstantEvals } from "../useInstantEval";

// One Instant eval run as the Ask page reads it: every row it asks about, from
// the moment it starts, each filling in with Jev's answers while they land.
//
//   useAskRun({ projectId, taskId, unit, seedItems }) -> {
//     status: "loading" | "running" | "done" | "error",
//     startedAt, elapsedMs, answered, total,
//     questions: [{ configId, question, answerType, labels }],
//     records: [{ id, unit, time, user_id, end_user_id, label, excerpt,
//                 trace_id, facets: { model, turns, duration_ms, error },
//                 answers: { [configId]: { answer, confidence, explanation,
//                                          status } } }],
//     window: [isoStart, isoEnd] | null,
//   }
//
// A missing answers key means the row hasn't been read yet; an answer of null
// with status "pending" or "running" means Jev is reading it.

export const ANSWERS_POLL_MS = 1500;
const RUN_POLL_MS = 5000;
const TICK_MS = 1000;
const IN_FLIGHT = new Set(["pending", "running"]);
const TERMINAL = new Set(["completed", "failed", "paused", "deleted"]);
const NO_FACETS = { model: null, turns: null, duration_ms: null, error: null };

const result = (response) => response?.data?.result;
const finite = (value) =>
  typeof value === "number" && Number.isFinite(value) ? value : null;

export const askRunKeys = {
  run: (projectId, taskId) => ["ask-run", projectId, taskId],
  answers: (projectId, taskId, unit, configIds) => [
    "ask-run-answers",
    projectId,
    taskId,
    unit,
    configIds,
  ],
};

/** The run's History entry: its questions, unit, reads and progress. */
export async function fetchAskRun({ projectId, taskId, signal }) {
  const response = await axios.get(endpoints.project.instantEvalRuns(), {
    params: { project_id: projectId, task_id: taskId, page_size: 1 },
    signal,
  });
  const run = result(response)?.runs?.[0];
  if (!run) throw new Error("This run could not be found.");
  return run;
}

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

/** One row of the answers endpoint as a record. */
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

// A row handed over from the view, before the answers endpoint has it.
const seedRecord = (item, unit) => ({
  id: String(item.id),
  unit,
  time: item.time || null,
  user_id: item.user_id || "",
  end_user_id: unit === "users" ? String(item.id) : "",
  label: "",
  excerpt: "",
  trace_id: item.trace_id || null,
  facets: NO_FACETS,
  answers: {},
});

const newestFirst = (a, b) =>
  new Date(b.time || 0).getTime() - new Date(a.time || 0).getTime();

/**
 * Every row of the run, newest first: the answers endpoint's rows, plus any
 * handed-over row it hasn't returned yet, so each square shows from the start.
 */
export function mergeRecords(rows = [], seedItems = [], unit) {
  const known = new Set(rows.map((row) => row.id));
  const missing = seedItems
    .filter((item) => item?.id && !known.has(String(item.id)))
    .map((item) => seedRecord(item, unit));
  return [...rows, ...missing].sort(newestFirst);
}

/** Every question of the row has its answer (a failure counts). */
export const isAnswered = (record, configIds) =>
  configIds.length > 0 &&
  configIds.every((configId) => {
    const entry = record.answers?.[configId];
    return Boolean(entry?.answer) && !IN_FLIGHT.has(entry.status);
  });

/** The earliest and latest row times, as the timeline's default window. */
export function windowOf(records = []) {
  const times = records
    .filter((record) => record.time)
    .map((record) => new Date(record.time).getTime())
    .filter(Number.isFinite);
  if (!times.length) return null;
  return [
    new Date(Math.min(...times)).toISOString(),
    new Date(Math.max(...times)).toISOString(),
  ];
}

/** The run's questions, from their saved evaluators (or what the run says). */
export function runQuestions(run, configsById = {}) {
  if (!run) return [];
  return historyRun(run, configsById).items.map((item) => ({
    configId: item.configId,
    question: item.question,
    answerType: item.answerType,
    labels: item.labels || [],
    definitions: item.definitions || {},
    ...(item.reads ? { reads: item.reads } : {}),
    ...(item.templateId ? { templateId: item.templateId } : {}),
  }));
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
 * A run of the Ask page. `seedItems` are the rows handed over when it was
 * asked ({id, time, user_id}); `questions` and `startedAt` describe a run
 * that is still being created (no `taskId` yet); `window` is the view's date
 * window when known.
 */
export function useAskRun({
  projectId,
  taskId,
  unit: unitHint,
  seedItems,
  questions: pendingQuestions,
  startedAt: launchedAt,
  window: viewWindow,
}) {
  const saved = useInstantEvals(projectId, { enabled: Boolean(projectId) });
  const configsById = useMemo(
    () => Object.fromEntries(saved.all.map((item) => [item.configId, item])),
    [saved.all],
  );
  const doneRef = useRef(false);

  const run = useQuery({
    queryKey: askRunKeys.run(projectId, taskId),
    enabled: Boolean(projectId && taskId),
    queryFn: ({ signal }) => fetchAskRun({ projectId, taskId, signal }),
    refetchInterval: () => (doneRef.current ? false : RUN_POLL_MS),
    retry: 1,
    meta: { errorHandled: true },
  });
  const unit = run.data?.unit || unitHint || "sessions";
  const questions = useMemo(
    () =>
      run.data
        ? runQuestions(run.data, configsById)
        : (pendingQuestions || []).map((item) => ({
            configId: item.configId || "",
            question: item.question,
            answerType: item.answerType,
            labels: item.labels || [],
          })),
    [run.data, configsById, pendingQuestions],
  );
  const configIds = useMemo(
    () => questions.map((item) => item.configId).filter(Boolean),
    [questions],
  );

  // Polls never overlap: react-query reuses a read still in flight.
  const answers = useQuery({
    queryKey: askRunKeys.answers(projectId, taskId, unit, configIds.join(",")),
    enabled: Boolean(projectId && taskId && configIds.length),
    queryFn: async ({ signal }) => {
      const response = await axios.get(endpoints.project.instantEvalAnswers(), {
        params: {
          project_id: projectId,
          eval_task_id: taskId,
          unit,
          config_ids: configIds.join(","),
        },
        signal,
      });
      return (result(response)?.rows || [])
        .filter((row) => row?.id)
        .map((row) => toRecord(row, unit));
    },
    refetchInterval: () => (doneRef.current ? false : ANSWERS_POLL_MS),
    retry: 2,
    meta: { errorHandled: true },
  });

  const records = useMemo(
    () => mergeRecords(answers.data || [], seedItems || [], unit),
    [answers.data, seedItems, unit],
  );
  const total = records.length;
  const answered = useMemo(
    () => records.filter((record) => isAnswered(record, configIds)).length,
    [records, configIds],
  );

  // Done once every row has its answers, or the task ended and an answers
  // read after that shows what it left.
  const taskEnded =
    TERMINAL.has(run.data?.status) &&
    answers.dataUpdatedAt > (run.dataUpdatedAt || Infinity);
  const done =
    Boolean(answers.data) && total > 0 && (answered >= total || taskEnded);
  doneRef.current = done;

  const [doneAt, setDoneAt] = useState(null);
  useEffect(() => {
    if (done && !doneAt) setDoneAt(Date.now());
    if (!done && doneAt) setDoneAt(null);
  }, [done, doneAt]);
  // The run's own finish time arrives with the next History read.
  useEffect(() => {
    if (done && !run.data?.finished_at && taskId) run.refetch();
    // Once, when the answers say the run is done.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [done]);

  // A run asked on this page counts from the click, on this browser's
  // clock, so its time never jumps back when the task's own arrives. Others
  // count from the task's creation to its last answer.
  const startedAt = launchedAt || run.data?.created_at || null;
  const running = !done && Boolean(startedAt);
  const now = useClock(running);
  const doneIso = doneAt ? new Date(doneAt).toISOString() : null;
  const finishedAt = done
    ? launchedAt
      ? doneIso
      : run.data?.finished_at || doneIso
    : null;
  const elapsedMs = startedAt
    ? Math.max(
        0,
        (finishedAt ? new Date(finishedAt).getTime() : now) -
          new Date(startedAt).getTime(),
      )
    : null;

  const failed =
    (run.isError && !run.data) || (answers.isError && !answers.data);
  let status = "running";
  if (failed) status = "error";
  else if (done) status = "done";
  else if (!total && !answers.data) status = "loading";

  const windowRange = useMemo(
    () => (viewWindow?.length === 2 ? viewWindow : windowOf(records)),
    [viewWindow, records],
  );

  return {
    status,
    startedAt,
    elapsedMs,
    answered,
    total,
    questions,
    records,
    window: windowRange,
    // Beyond the contract, for the page around the charts.
    unit,
    // The unit the server reports for the run, once it has been read.
    knownUnit: run.data?.unit || null,
    reads: run.data?.reads || null,
    taskStatus: run.data?.status || null,
    error: failed ? run.error || answers.error : null,
    refetch: () => {
      run.refetch();
      answers.refetch();
    },
  };
}
