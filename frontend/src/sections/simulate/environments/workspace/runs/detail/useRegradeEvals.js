import { enqueueSnackbar } from "notistack";
import { useRunNewEvals } from "src/api/simulate-environments/runEvals";
import { refusalText } from "../../evals/refusalText";

// A failed or unanswered request may still have started grading.
export const RUN_FALLBACK = "Grading may not have started. Try again.";

/**
 * Grade chosen evals again on one finished run, without rerunning its calls.
 *
 * Every place that re-runs an eval on a run page goes through here, so the
 * request, its messages and its refusals live in one place.
 *
 * `envId` is the environment the run belongs to; the re-grade route is under it.
 *
 * `regrade(configs, { onSuccess })` calls `onSuccess(dispatched)` once the
 * server answers: `dispatched` is false when it accepted the request but
 * couldn't start the grading job. A refusal shows the server's sentence and
 * calls nothing, so a confirm dialog stays open for a retry.
 */
export function useRegradeEvals({ envId, executionId }) {
  const runEvals = useRunNewEvals();

  const regrade = (configs, { onSuccess } = {}) => {
    const evalConfigIds = configs.map((c) => c.id);
    runEvals.mutate(
      { id: envId, executionId, evalConfigIds },
      {
        onSuccess: (result) => {
          // Only the run-test route sends this flag (false when grading never
          // started and the old scores were put back). This route answers that
          // with a 503 instead, so its bodies carry no flag and read as started.
          if (result?.dispatched === false) {
            enqueueSnackbar(RUN_FALLBACK, { variant: "warning" });
            onSuccess?.(false);
            return;
          }
          const k = evalConfigIds.length;
          enqueueSnackbar(
            `Grading ${k} evaluation${k === 1 ? "" : "s"}. This run updates when grading finishes.`,
            { variant: "success" },
          );
          onSuccess?.(true);
        },
        onError: (e) => {
          enqueueSnackbar(refusalText(e, RUN_FALLBACK), { variant: "error" });
        },
      },
    );
  };

  return { regrade, isPending: runEvals.isPending };
}
