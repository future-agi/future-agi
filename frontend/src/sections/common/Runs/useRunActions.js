import { useCallback, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { notify } from "src/sections/common/ux/toast";
import { useAuthContext } from "src/auth/hooks";
import { getRequestErrorMessage } from "src/utils/errorUtils";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import {
  rerunCells,
  rerunExperiments,
  uploadRunResults,
} from "src/api/develop/experiment-run-actions";
import { fetchExperimentDetail, readEnvelope } from "src/api/develop/runs";
import {
  RUN_NO_LONGER_RUNNING,
  RUN_RESUME_HELD,
  RUN_STOP_HELD,
  RUN_STOP_NOT_ALONE,
  isRunControlNotEnabled,
  resumeExperimentRuns,
  runControlState,
  stopOneRun,
  stopSelectedRuns,
  useRunControlState,
} from "src/sections/common/ExperimentRunControl/runControlAvailability";
import { confirmRunAction } from "src/sections/develop-detail/Experiment/runActionConfirm";
import {
  rerunExperimentBody,
  rerunRunBody,
  startedRunsOf,
  startedToast,
} from "./rerunCopyModel";
import { rescoreMessage } from "./rescoreCopyModel";
import { canResumeRun, resumeOutcomeOf } from "./runResume";
import { ledgerNounOf } from "./runLedger";
import { getRunStatus } from "./runStatus";

const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/**
 * The toast of a re-score response (L5 G1); W1-2: a child-run answer reads
 * the one re-score sentence (rescoreCopyModel).
 */
export function rescoreToast(result, label = "") {
  if (result && (Array.isArray(result.runs) || result.unchanged))
    return rescoreMessage(result);
  if (result?.unchanged)
    return `Scores unchanged · same eval version (${result.eval_version ?? "v1"})`;
  if (result?.rescored_cells != null)
    return `Re-scored ${result.rescored_cells} cells${label ? ` of ${label}` : ""} · eval ${result.eval_version}`;
  return `Re-scoring ${label}`;
}

/**
 * Actions on one run (canonical Run object: run_id, label, experiment_id,
 * progress{done,total,errors}, PRODUCT-SPEC §3.9). L4, L6 and L8 call these
 * from run chips and menus.
 *
 * Frozen API (+ `rerunRun`, `labels` and `stopInList`, additive):
 *   const { stop, stopInList, resume, rerunAll, rerunRun, rerunFailed,
 *           rescore, simulateUpload, can: { edit }, pending,
 *           labels: { resume } } = useRunActions(run);
 * rerunAll re-runs the run's whole experiment (POST re-run/
 * {experiment_ids}); rerunRun re-runs only this run (rerun-cells by source).
 * Each action returns a promise (the response, or null when cancelled or
 * failed), toasts its outcome and refreshes the experiment and the
 * dataset's runs. Stop and Re-run ask first where a confirm host is
 * mounted (the experiment drawer hosts it on the dataset and experiment
 * pages).
 */
export default function useRunActions(run) {
  const queryClient = useQueryClient();
  const { role } = useAuthContext();
  const [busy, setBusy] = useState(0);
  const experimentId = run?.experiment_id;
  const ordinal = run?.experiment_ordinal ?? "";
  const runId = run?.run_id;
  const label = run?.label || "Run";
  const done = run?.progress?.done ?? 0;
  const total = run?.progress?.total ?? run?.examples ?? 0;
  const errors = run?.progress?.errors ?? 0;
  const left = Math.max(0, total - done);
  // L7c g5 (action:stop-resume): one noun with the run's ledger (rows or
  // tasks), never a third word for the same count.
  const noun = ledgerNounOf(run?.ledger);
  // W1-1: names the new run (#4B·2) from R1 `attempts_count`.
  const rerunRunText = rerunRunBody(run);

  const refresh = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ["experiment", experimentId] });
    queryClient.invalidateQueries({ queryKey: ["develop", "dataset-runs"] });
    // Run chips on the results/compare grid show Stopped/progress at once.
    queryClient.invalidateQueries({ queryKey: ["develop", "run-results"] });
    queryClient.invalidateQueries({ queryKey: ["experiment-list"] });
    queryClient.invalidateQueries({
      queryKey: ["experiment-column-config", experimentId],
    });
  }, [queryClient, experimentId]);

  // What the server said about pausing and resuming single runs ("unknown"
  // until a run-control request answers). Off, a run's own Stop and Resume
  // are held: the doors show them disabled with the reason, and a call from
  // anywhere sends nothing and says the same reason.
  const runControl = useRunControlState();
  const runControlOff = runControl === "off";
  // The reason, as information: the server refused before doing anything.
  const sayHeld = useCallback(
    (reason) => {
      notify(reason, { variant: "info", key: `run-action-${runId}` });
      return null;
    },
    [runId],
  );

  // R3 (CR#36, F6): reversible actions toast with Undo; `undo(res)` runs
  // the inverse request (Stop → Resume within the window).
  const undoOf = useCallback(
    (undo, res) => ({
      ms: 10000,
      onUndo: async () => {
        try {
          const undone = await undo.request(res);
          if (undo.held && undone === null) {
            sayHeld(undo.held);
            return;
          }
          if (undo.settle && !(await undo.settle(undone))) return;
          refresh();
          notify(undo.text, { key: `run-action-${runId}` });
        } catch (error) {
          if (undo.held && isRunControlNotEnabled(error)) {
            sayHeld(undo.held);
            return;
          }
          notify(getRequestErrorMessage(error, `Could not undo on ${label}`), {
            variant: "error",
          });
        }
      },
    }),
    [refresh, label, runId, sayHeld],
  );

  // `held` is the reason to show when run control is off: the request
  // resolved null (it sent nothing) or the server refused it with
  // run_control_not_enabled.
  const call = useCallback(
    async (request, successText, confirm, undo, held) => {
      if (held && runControlState() === "off") return sayHeld(held);
      if (confirm && !(await confirmRunAction(confirm))) return null;
      setBusy((n) => n + 1);
      try {
        const res = await request();
        if (held && res === null) return sayHeld(held);
        refresh();
        const text =
          typeof successText === "function"
            ? await successText(res)
            : successText;
        if (text)
          notify(text, {
            variant: "success",
            key: `run-action-${runId}`,
            undo: undo ? undoOf(undo, res) : undefined,
          });
        return res;
      } catch (error) {
        if (held && isRunControlNotEnabled(error)) return sayHeld(held);
        notify(getRequestErrorMessage(error, `Could not update ${label}`), {
          variant: "error",
        });
        return null;
      } finally {
        setBusy((n) => n - 1);
      }
    },
    [refresh, label, runId, sayHeld, undoOf],
  );

  // A run's Stop asks first, naming the run it stops. The count here can be
  // a poll behind the run; final counts come from a settled response or detail.
  const stopConfirm = useMemo(
    () => ({
      title: `Stop ${label}?`,
      // "Resume later" only once the server said it resumes runs.
      content: `So far about ${done} of ${plural(total, noun)} are done. Every ${noun} done when you stop is kept${runControl === "on" ? "; you can resume the rest later" : ""}.`,
      confirmLabel: "Stop run",
      cancelLabel: "Keep running",
      danger: true,
    }),
    [label, done, total, noun, runControl],
  );
  // Resume acknowledges the workflow signal before its Running state is
  // persisted. A single invalidation can read Stopped and disable polling
  // forever. Keep reading this run until detail catches up, also for Undo.
  const refreshAfterResume = useCallback(async () => {
    for (let attempt = 0; attempt < 60; attempt += 1) {
      try {
        const response = await fetchExperimentDetail(experimentId);
        const current = readEnvelope(response).runs?.find(
          (r) => r.run_id === runId,
        );
        const status = getRunStatus(current);
        if (status.active || (status.terminal && status.key !== "Cancelled")) {
          queryClient.setQueryData(["experiment", experimentId], response);
          refresh();
          return true;
        }
        if (status.key !== "Cancelled") break;
      } catch {
        break;
      }
      if (attempt < 59)
        await new Promise((resolve) => setTimeout(resolve, 1000));
    }
    notify(
      `Resume requested for ${label}. Could not confirm its current state; refresh the run.`,
      { variant: "info", key: `run-action-${runId}` },
    );
    return false;
  }, [experimentId, runId, label, queryClient, refresh]);

  // A paused run's Undo resumes it.
  const stopUndo = useMemo(
    () => ({
      request: () => resumeExperimentRuns(experimentId, [runId]),
      settle: refreshAfterResume,
      text: `${label} running again`,
      held: RUN_RESUME_HELD,
    }),
    [experimentId, runId, label, refreshAfterResume],
  );

  // A final acknowledgement settles directly; only an in-progress stop
  // needs detail reads. Keep this promise alive when row menus unmount.
  const stopOutcome = useCallback(
    async (res) => {
      const key = `run-action-${runId}`;
      const unconfirmed = () =>
        notify(
          `Stop requested for ${label}. Could not confirm its stopped state; check the run before resuming.`,
          { variant: "info", key },
        );
      const settle = (current) => {
        const status = getRunStatus(current);
        if (status.key === "Cancelled") {
          const progress = current.progress;
          const known =
            Number.isFinite(progress?.done) && Number.isFinite(progress?.total);
          const kept = known
            ? `${progress.done} of ${plural(progress.total, noun)} kept`
            : "kept-row count unavailable";
          refresh();
          notify(`Stopped ${label} · ${kept}`, {
            variant: "success",
            key,
            ...(known &&
              canResumeRun(current) && { undo: undoOf(stopUndo, res) }),
          });
          return true;
        }
        if (status.terminal) {
          refresh();
          notify(RUN_NO_LONGER_RUNNING, { variant: "info", key });
          return true;
        }
        return false;
      };
      const ack = res?.data?.result?.runs?.find((r) => r.run_id === runId);
      const ackStatus = String(ack?.status ?? "")
        .trim()
        .toLowerCase();
      // Older responses give final counts without a status. Never let those
      // counts override an explicit in-progress status on newer responses.
      const counted = ack?.status == null && Number.isFinite(ack?.done);
      if (
        settle({
          ...run,
          ...ack,
          status: counted || ackStatus === "paused" ? "Cancelled" : ack?.status,
          progress: ack?.progress ?? {
            done: ack?.done,
            total: ack?.total ?? total,
          },
        })
      )
        return;
      if (ackStatus !== "stopping" && !getRunStatus(ack).active) {
        unconfirmed();
        return;
      }
      notify(
        `Stop requested for ${label}. Waiting for stopped state and kept-row count.`,
        { variant: "info", key },
      );
      for (let attempt = 0; attempt < 60; attempt += 1) {
        let current;
        try {
          const detail = readEnvelope(
            await fetchExperimentDetail(experimentId),
          );
          current = detail.runs?.find((r) => r.run_id === runId);
          if (!current) throw new Error("Run unavailable");
        } catch {
          unconfirmed();
          return;
        }
        if (settle(current)) return;
        await new Promise((resolve) => setTimeout(resolve, 1000));
      }
      notify(
        `Stop is still pending for ${label}. Check the run before resuming.`,
        {
          variant: "info",
          key,
        },
      );
    },
    [experimentId, runId, run, label, total, noun, refresh, stopUndo, undoOf],
  );

  return useMemo(
    () => ({
      // Stops this run only. It never becomes the whole-experiment stop,
      // which would also stop the experiment's other runs.
      stop: () =>
        call(
          () => stopSelectedRuns(experimentId, [runId]),
          stopOutcome,
          stopConfirm,
          undefined,
          RUN_STOP_HELD,
        ),
      // The Experiments list's Stop of this run (its row menu and its
      // status chip), by stopOneRun. Run control on: only this run pauses,
      // with Undo, as `stop`. Off: it stops when it is the experiment's one
      // active run at the click (the runs are read again then); with other
      // runs going nothing stops and the page says why until it is closed.
      stopInList: async () => {
        if (!(await confirmRunAction(stopConfirm))) return null;
        const key = `run-action-${runId}`;
        setBusy((n) => n + 1);
        try {
          const out = await stopOneRun(experimentId, runId, async () => {
            try {
              const detail = readEnvelope(
                await fetchExperimentDetail(experimentId),
              );
              return detail.runs ?? [];
            } catch {
              throw new Error(
                `Could not stop ${label}: the experiment's runs could not be read. Try again.`,
              );
            }
          });
          refresh();
          if (out.refused) {
            notify(out.refused, { variant: "info", key, persist: true });
            return null;
          }
          if (out.ended) {
            notify(RUN_NO_LONGER_RUNNING, { variant: "info", key });
            return null;
          }
          const res = out.response;
          if (!out.whole) {
            await stopOutcome(res);
            return res;
          }
          // The empty-body stop ended the experiment: nothing to resume.
          // Rows whose call was already sent still finish, so no count is
          // final while any are in flight. The count is the read's.
          const kept = out.run?.progress?.done ?? done;
          const of = plural(out.run?.progress?.total ?? total, noun);
          notify(
            res?.data?.result?.rows_in_flight > 0
              ? `Stopped ${label}. Rows already sent will finish.`
              : `Stopped ${label} · ${kept} of ${of} kept`,
            { variant: "success", key },
          );
          return res;
        } catch (error) {
          // Any other refusal, as `stop` shows it; never the empty-body stop.
          // The run may still be going, so the page says so until closed.
          notify(getRequestErrorMessage(error, `Could not update ${label}`), {
            variant: "error",
            persist: true,
          });
          return null;
        } finally {
          setBusy((n) => n - 1);
        }
      },
      // options-truth L7b G4: a run the server refuses to resume (its cost
      // cap stopped it) toasts the refusal, never `resumed` with an Undo.
      resume: async () => {
        if (runControlState() === "off") return sayHeld(RUN_RESUME_HELD);
        setBusy((n) => n + 1);
        try {
          const res = await resumeExperimentRuns(experimentId, [runId]);
          if (res === null) return sayHeld(RUN_RESUME_HELD);
          refresh();
          const { resumed, refused } = resumeOutcomeOf(res?.data?.result);
          if (refused.length || !resumed.length) {
            notify(refused[0] ?? `${label} is not stopped.`, {
              variant: "error",
              key: `run-action-${runId}`,
            });
            return null;
          }
          if (!(await refreshAfterResume())) return res;
          const pending = resumed[0].pending ?? left;
          notify(`${label} resumed · ${plural(pending, noun)} left`, {
            variant: "success",
            key: `run-action-${runId}`,
            undo: {
              ms: 10000,
              onUndo: async () => {
                try {
                  const again = await stopSelectedRuns(experimentId, [runId]);
                  if (again === null) {
                    sayHeld(RUN_STOP_HELD);
                    return;
                  }
                  refresh();
                  await stopOutcome(again);
                } catch (error) {
                  if (isRunControlNotEnabled(error)) {
                    sayHeld(RUN_STOP_HELD);
                    return;
                  }
                  notify(
                    getRequestErrorMessage(error, `Could not undo on ${label}`),
                    { variant: "error" },
                  );
                }
              },
            },
          });
          return res;
        } catch (error) {
          if (isRunControlNotEnabled(error)) return sayHeld(RUN_RESUME_HELD);
          notify(getRequestErrorMessage(error, `Could not resume ${label}`), {
            variant: "error",
          });
          return null;
        } finally {
          setBusy((n) => n - 1);
        }
      },
      // POST experiments/v2/re-run/ {experiment_ids} (never select_all):
      // every run of the experiment runs again.
      // W1-1 kept runs: every rerun starts new runs; the toast names them.
      rerunAll: () =>
        call(
          () => rerunExperiments([experimentId]),
          (res) =>
            startedToast(startedRunsOf(res).labels) ||
            `Re-running experiment #${ordinal}`,
          {
            title: `Re-run experiment #${ordinal}?`,
            content: rerunExperimentBody(),
            confirmLabel: "Re-run all",
          },
        ),
      // Additive: only this run, on every example (rerun-cells by source).
      rerunRun: () =>
        call(
          () => rerunCells(experimentId, { source_ids: [runId] }),
          (res) =>
            startedToast(startedRunsOf(res).labels, [label]) ||
            `Re-running ${label} on ${plural(total, "example")}`,
          {
            title: `Re-run ${label}?`,
            content: rerunRunText,
            confirmLabel: "Re-run",
          },
        ),
      rerunFailed: () =>
        call(
          () =>
            rerunCells(experimentId, {
              source_ids: [runId],
              failed_only: true,
            }),
          (res) =>
            startedToast(startedRunsOf(res).labels, [label]) ||
            (errors
              ? `Retrying ${plural(errors, "failed example")} of ${label}`
              : `Retrying failed examples of ${label}`),
        ),
      rescore: (userEvalMetricIds = []) =>
        call(
          () =>
            rerunCells(experimentId, {
              user_eval_metric_ids: userEvalMetricIds,
            }),
          // L5 G1: the scorer says whether any definition changed.
          (res) => rescoreToast(res?.data?.result, label),
        ),
      // Prototype only: plays every example's result in through E3.
      simulateUpload: () =>
        call(
          () =>
            uploadRunResults(experimentId, runId, {
              results: [],
              complete: true,
            }),
          (res) => {
            const n = res?.data?.result?.accepted;
            return n != null
              ? `${label} · ${plural(n, "result")} received`
              : `Results received for ${label}`;
          },
        ),
      can: {
        edit: Boolean(RolePermission.DATASETS[PERMISSIONS.UPDATE][role]),
      },
      pending: busy > 0,
      labels: { resume: left ? `Resume · ${left} left` : "Resume" },
      // options-truth L7b G4: Resume only where the ledger allows it.
      canResume: canResumeRun(run),
      // Run control off: this run's own Stop and Resume show disabled with
      // these reasons (the experiment page's Stop still stops it). On the
      // Experiments list a Stop is held only while other runs of its
      // experiment are going (`notAlone`).
      runControlOff,
      held: {
        stop: RUN_STOP_HELD,
        resume: RUN_RESUME_HELD,
        notAlone: RUN_STOP_NOT_ALONE,
      },
    }),
    [
      run,
      refresh,
      refreshAfterResume,
      call,
      sayHeld,
      undoOf,
      stopConfirm,
      stopUndo,
      stopOutcome,
      experimentId,
      ordinal,
      runId,
      label,
      role,
      busy,
      done,
      total,
      left,
      errors,
      rerunRunText,
      noun,
      runControl,
      runControlOff,
    ],
  );
}

export { useRunActions };
