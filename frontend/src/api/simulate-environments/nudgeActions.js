import { useCallback } from "react";

import { useAnswerNudge, useStopNudge } from "./versions";

// Rebuild, Skip and Stop for the chat card and the Agents panel, and what
// each nudge shows of them. Each action clears the other's outcome first.
export function useNudgeActions(envId) {
  const answer = useAnswerNudge(envId);
  const stop = useStopNudge(envId);
  const { mutate: answerMutate, reset: resetAnswer } = answer;
  const { mutate: stopMutate, reset: resetStop } = stop;
  const rebuild = useCallback(
    (nudgeId) => {
      resetStop();
      answerMutate({ nudgeId, answer: "yes" });
    },
    [answerMutate, resetStop],
  );
  const skip = useCallback(
    (nudgeId) => {
      resetStop();
      answerMutate({ nudgeId, answer: "no" });
    },
    [answerMutate, resetStop],
  );
  const stopById = useCallback(
    (nudgeId) => {
      resetAnswer();
      stopMutate({ nudgeId });
    },
    [stopMutate, resetAnswer],
  );
  const reset = useCallback(() => {
    resetAnswer();
    resetStop();
  }, [resetAnswer, resetStop]);
  const answeringId = answer.isPending ? answer.variables?.nudgeId : null;
  const rebuilding = answer.variables?.answer === "yes";
  const stoppingId = stop.isPending ? stop.variables?.nudgeId : null;
  // The nudge whose own answer or Stop landed takes focus on its state line.
  const settledId =
    (answer.isSuccess && answer.variables?.nudgeId) ||
    (stop.isSuccess && stop.variables?.nudgeId) ||
    null;
  const failed = [answer, stop].find((one) => one.isError);
  const failedId = failed?.variables?.nudgeId ?? null;
  const error = failed?.error;
  const flagsFor = useCallback(
    (nudgeId) => ({
      answering: answeringId === nudgeId,
      rebuilding,
      stopping: stoppingId === nudgeId,
      settled: settledId === nudgeId,
      error: failedId === nudgeId ? error : undefined,
    }),
    [answeringId, rebuilding, stoppingId, settledId, failedId, error],
  );
  return {
    rebuild,
    skip,
    stop: stopById,
    reset,
    flagsFor,
    stopping: stop.isPending,
  };
}
