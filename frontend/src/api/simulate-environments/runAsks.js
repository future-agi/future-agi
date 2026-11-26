import { useQuery } from "@tanstack/react-query";

import axios, { endpoints } from "src/utils/axios";

// The backend's own limits (ask_selection.MAX_CALLS and MAX_QUESTIONS). The
// backend refuses past them anyway; these let the page say so before sending.
export const MAX_ASK_CALLS = 200;
export const MAX_ASK_QUESTIONS = 5;

// An ask still grading is read again this often, until it finishes or stops.
const RUNNING_POLL_MS = 5000;

export const runAsksKey = (executionId) => ["simulation-run-asks", executionId];

const running = (ask) => ask?.status === "running";

// The page shows a refusal where it happened, so the app-wide toast stays quiet.
const QUIET = { errorHandled: true };

/** This run's asks, newest first. */
export function useRunAsks(executionId, { enabled = true } = {}) {
  return useQuery({
    queryKey: runAsksKey(executionId),
    queryFn: () =>
      axios
        .get(endpoints.runResultsV3.asks(executionId))
        .then((response) => response.data),
    enabled: !!executionId && enabled,
    refetchInterval: (query) =>
      query.state.data?.asks?.some(running) ? RUNNING_POLL_MS : false,
    meta: QUIET,
  });
}

/** One ask: its counts per question and a row per call and question. */
export function useRunAsk(executionId, askId) {
  return useQuery({
    queryKey: [...runAsksKey(executionId), askId],
    queryFn: () =>
      axios
        .get(endpoints.runResultsV3.askDetail(executionId, askId))
        .then((response) => response.data),
    enabled: !!executionId && !!askId,
    refetchInterval: (query) =>
      running(query.state.data) ? RUNNING_POLL_MS : false,
    meta: QUIET,
  });
}

/**
 * The request body for a check or a launch, from what the person picked.
 * `selection` is `{ kind: "runs" }` or `{ kind: "calls", callIds }`.
 */
export function askBody(selection, questions) {
  return {
    selection:
      selection.kind === "calls"
        ? { kind: "calls", call_ids: selection.callIds }
        : { kind: selection.kind },
    questions: questions.map((one) => ({
      question: one.question.trim(),
      answer_type: one.answerType,
      ...(one.answerType === "category" ? { labels: labelsOf(one) } : {}),
      kinds: one.kinds,
    })),
  };
}

/** A category question's answers, one per line, blanks and repeats dropped. */
export function labelsOf(question) {
  return [
    ...new Set(
      (question.labels || "")
        .split("\n")
        .map((label) => label.trim())
        .filter(Boolean),
    ),
  ];
}

/** What an ask would read and whether it could launch. Writes nothing. */
export const checkAsk = (executionId, body) =>
  axios
    .post(endpoints.runResultsV3.askCheck(executionId), body)
    .then((response) => response.data);

/**
 * Launches the ask the person accepted. `check` is the check they saw: the
 * backend refuses when the calls or the cost changed since.
 */
export const launchAsk = (executionId, body, check) =>
  axios
    .post(endpoints.runResultsV3.asks(executionId), {
      ...body,
      accepted: {
        requested: check.requested,
        credits_estimate: check.credits_estimate ?? null,
      },
    })
    .then((response) => response.data);
