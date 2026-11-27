import { useInfiniteQuery, useQuery } from "@tanstack/react-query";

import axios, { endpoints } from "src/utils/axios";

// The backend's own limits (ask_selection.MAX_CALLS and MAX_QUESTIONS,
// ask_engine.MAX_QUESTION_LENGTH). The backend refuses past them anyway; these
// let the page say so before sending.
export const MAX_ASK_CALLS = 200;
export const MAX_ASK_QUESTIONS = 5;
export const MAX_ASK_QUESTION_LENGTH = 2000;

// An ask still grading is read again this often, until it finishes or stops.
const RUNNING_POLL_MS = 5000;

export const runAsksKey = (executionId) => ["simulation-run-asks", executionId];

const running = (ask) => ask?.status === "running";

// The page shows a refusal where it happened, so the app-wide toast stays quiet.
const QUIET = { errorHandled: true };

/**
 * This run's asks, newest first, a page (20) at a time: `fetchNextPage` reads
 * the older ones. While any shown ask is running, the shown pages are read again.
 */
export function useRunAsks(executionId, { enabled = true } = {}) {
  return useInfiniteQuery({
    queryKey: runAsksKey(executionId),
    queryFn: ({ pageParam }) =>
      axios
        .get(endpoints.runResultsV3.asks(executionId), {
          params: { page: pageParam },
        })
        .then((response) => response.data),
    initialPageParam: 1,
    getNextPageParam: (last) =>
      last.current_page < last.total_pages ? last.current_page + 1 : undefined,
    enabled: !!executionId && enabled,
    refetchInterval: (query) =>
      query.state.data?.pages?.some((page) => page.results.some(running))
        ? RUNNING_POLL_MS
        : false,
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
 * backend refuses when the calls or the cost changed since. Its `launchKey`
 * makes a repeat of this launch return the ask it started.
 */
export const launchAsk = (executionId, body, check) =>
  axios
    .post(endpoints.runResultsV3.asks(executionId), {
      ...body,
      accepted: {
        requested: check.requested,
        credits_estimate: check.credits_estimate ?? null,
      },
      launch_key: check.launchKey,
    })
    .then((response) => response.data);
