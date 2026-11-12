// Confirms and the rename dialog of the leaderboard. One hook,
// useLeaderboardActions, owns them so the run menu (grid) and the selection
// bar (toolbar) say the same thing and call the same endpoints.
import React, { useCallback, useState } from "react";
import { Button, Typography } from "@mui/material";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { create } from "zustand";
import axios, { endpoints } from "src/utils/axios";
import { getRequestErrorMessage } from "src/utils/errorUtils";
import CustomDialog from "../Common/CustomDialog/CustomDialog";
import RenameExperimentDialog from "./RenameExperimentDialog";
import {
  useDownloadExperimentCsv,
  useRenameExperiment,
} from "src/api/develop/experiment-list";
import { datasetRunsKey } from "src/api/develop/runs";
import { useRunExperimentStore } from "../states";
import { notify } from "src/sections/common/ux/toast";
import { copyText as copyToClipboard } from "src/sections/common/ux/CopyButton";
import ConfirmDialog from "src/sections/common/ux/ConfirmDialog";
import { getRunStatus } from "src/sections/common/Runs/runStatus";
import {
  leaderboardRerunBody,
  leaderboardRerunFailedBody,
  startedRunsOf,
  startedToast,
} from "src/sections/common/Runs/rerunCopyModel";
import { experimentUrl } from "./leaderboardModel";
import { useRescore } from "src/sections/common/Runs/useRescore";
import { backoffLine } from "src/sections/common/Runs/errorCause";

/**
 * Experiments hidden while their delete request is in flight (keyed by
 * dataset), so the toolbar, the grid and the answer line drop them
 * together; a failed delete shows them again.
 */
export const usePendingDeletes = create((set) => ({
  byDataset: {},
  hide: (datasetId, ids) =>
    set((s) => ({
      byDataset: {
        ...s.byDataset,
        [datasetId]: [...new Set([...(s.byDataset[datasetId] ?? []), ...ids])],
      },
    })),
  show: (datasetId, ids) =>
    set((s) => ({
      byDataset: {
        ...s.byDataset,
        [datasetId]: (s.byDataset[datasetId] ?? []).filter(
          (x) => !ids.includes(x),
        ),
      },
    })),
}));

const RERUN_TEXT = {
  // W1-1 kept runs: Re-run and Re-run failed rows start new runs.
  all: {
    title: (n) => `Re-run ${n}?`,
    body: (runs) => leaderboardRerunBody(runs),
    button: "Re-run",
  },
  failed: {
    title: (n) => `Re-run failed rows of ${n}?`,
    // W1-7: rate-limited rows wait the delay their error names.
    body: (runs) =>
      [leaderboardRerunFailedBody(), backoffLine(runs)]
        .filter(Boolean)
        .join(" "),
    button: "Re-run failed rows",
  },
};

/**
 * L7c g5 (lb:delete): what the delete confirm says: what goes, a run still
 * running is stopped, and that it cannot be undone. Pure.
 */
export function deleteConfirmBody(req) {
  const n = Number(req?.runCount) || 0;
  const runs = `${n} ${n === 1 ? "run" : "runs"} and ${n === 1 ? "its" : "their"} results`;
  const running = (req?.running ?? []).map((r) => r.label).filter(Boolean);
  const stop = running.length
    ? `${running.join(", ")} ${running.length === 1 ? "is" : "are"} still running and will be stopped. `
    : "";
  return `${stop}${runs} are deleted. This cannot be undone.`;
}

const runsText = (runs) =>
  runs.length === 1 ? runs[0].label : `${runs.length} runs`;

/**
 * options-truth L7b G4 (action:leaderboard:group-rerun-all-runs): a group
 * rerun that skips runs Future AGI cannot start again says so: `Re-run 4
 * of 7 runs?` and `#3A, #3B and #3C ran outside Future AGI and are not
 * rerun.` Pure.
 */
export function groupRerunText(req) {
  const runs = req?.runs ?? [];
  const skipped = req?.skipped ?? [];
  const text = RERUN_TEXT[req?.mode] ?? RERUN_TEXT.all;
  if (!skipped.length)
    return { title: text.title(runsText(runs)), body: text.body(runs) };
  const total = runs.length + skipped.length;
  const names = skipped.map((r) => r.label).filter(Boolean);
  const list =
    names.length > 1
      ? `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`
      : names[0] ?? `${skipped.length} runs`;
  return {
    title: text.title(`${runs.length} of ${total} runs`),
    body: `${text.body(runs)} ${list} ran outside Future AGI and ${skipped.length === 1 ? "is" : "are"} not rerun.`,
  };
}

/**
 * Actions shared by the run menu and the selection bar, with their dialogs.
 * Returns callbacks and a `dialogs` node to render once.
 */
export function useLeaderboardActions(
  datasetId,
  { onDone, metricDefs, pinnedRunId = null, runs = [] } = {},
) {
  const ctx = React.useRef({});
  ctx.current = { pinnedRunId, runs };
  const labelOf = (id) =>
    ctx.current.runs.find((r) => r.run_id === id)?.label ?? null;
  const [rerun, setRerun] = useState(null);
  const [del, setDel] = useState(null);
  const [rename, setRename] = useState(null);
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const renameExperiment = useRenameExperiment(datasetId, {
    onSuccess: () => setRename(null),
  });
  const download = useDownloadExperimentCsv();
  const invalidate = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: datasetRunsKey(datasetId) });
    queryClient.invalidateQueries({ queryKey: ["datasets", "list"] });
    queryClient.invalidateQueries({ queryKey: ["experiment-list"] });
  }, [queryClient, datasetId]);

  // EL#6: pin / unpin toast with Undo (puts the previous baseline back).
  const [pinPending, setPinPending] = useState(false);
  const putBaseline = (runId) =>
    runId
      ? axios.put(endpoints.develop.datasetRuns.baseline(datasetId), {
          run_id: runId,
        })
      : axios.delete(endpoints.develop.datasetRuns.baseline(datasetId));
  const changeBaseline = useCallback(
    async (runId, opts = {}) => {
      const previousId = opts.previousId ?? ctx.current.pinnedRunId ?? null;
      const label = opts.label ?? labelOf(runId);
      const previousLabel = opts.previousLabel ?? labelOf(previousId);
      if (runId && runId === previousId) return;
      setPinPending(true);
      try {
        const res = await putBaseline(runId);
        invalidate();
        const shown = runId
          ? `${res?.data?.result?.label ?? label ?? "Run"} pinned as baseline`
          : "Baseline unpinned. Deltas use the earliest run.";
        notify(shown, {
          variant: "success",
          key: "leaderboard-baseline",
          undo: {
            onUndo: async () => {
              await putBaseline(previousId ?? null);
              invalidate();
              notify(
                previousId
                  ? `${previousLabel ?? "Previous run"} is the baseline again`
                  : "Baseline unpinned again",
                { key: "leaderboard-baseline" },
              );
            },
          },
        });
      } catch (e) {
        notify(getRequestErrorMessage(e, "Could not change the baseline"), {
          variant: "error",
        });
      } finally {
        setPinPending(false);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [datasetId, invalidate],
  );

  // L7c g5 (lb:delete): Delete asks first, then deletes at once. The soft
  // delete's Undo (a 6 s toast window) was never shown to restore anything
  // in a walk, so it is gone: the confirm names what goes (and stops a run
  // still running) and says it cannot be undone; nothing is promised back.
  const hide = usePendingDeletes((st) => st.hide);
  const show = usePendingDeletes((st) => st.show);
  const [deleting, setDeleting] = useState(false);
  const deleteNow = useCallback(
    async (req) => {
      const ids = req?.ids ?? [];
      if (!ids.length) return;
      const what =
        ids.length === 1 && req.name
          ? `“${req.name}”`
          : `${ids.length} experiments`;
      setDeleting(true);
      hide(datasetId, ids);
      try {
        // Dev's delete route answers DELETE only (L2).
        await axios.delete(endpoints.develop.experiment.delete, {
          data: { experiment_ids: ids },
        });
        setDel(null);
        onDone?.();
        invalidate();
        notify(
          `Deleted ${what} and ${req.runCount} ${req.runCount === 1 ? "run" : "runs"}`,
          { variant: "success", key: `leaderboard-delete-${ids.join(",")}` },
        );
      } catch (e) {
        show(datasetId, ids);
        notify(getRequestErrorMessage(e, "Could not delete the experiment"), {
          variant: "error",
        });
      } finally {
        setDeleting(false);
      }
    },
    [datasetId, hide, show, invalidate, onDone],
  );
  const requestDelete = useCallback((req) => {
    if (!req?.ids?.length) return;
    const ids = new Set(req.ids);
    const running = ctx.current.runs.filter(
      (r) => ids.has(r.experiment_id) && getRunStatus(r).active,
    );
    setDel({ ...req, running });
  }, []);

  const duplicate = useCallback((experimentId) => {
    // L5 prefills the drawer from the experiment (create mode, "(copy)").
    useRunExperimentStore
      .getState()
      .initiateCreateMode({ duplicateFromExperimentId: experimentId });
  }, []);

  // W1-2: Re-score goes through the one re-score door (useRescore): a
  // confirm with the judge's cost, then a new run per run (#2A·2) with the
  // rows that flipped. It sends the dataset evals the runs carry.
  const rescore = useRescore({ datasetId, onDone });
  const requestRerun = useCallback(
    (req) => {
      if (req.mode !== "rescore") {
        setRerun(req);
        return;
      }
      const keys = new Set(req.runs.flatMap((r) => r.metric_keys ?? []));
      const evals = (metricDefs ?? []).filter((d) => d.user_eval_id);
      const own = evals.filter((d) => keys.has(d.key));
      const picked = own.length ? own : evals;
      // L7c g5 (rs:leaderboard-row-menu): the runs' own evals (Task
      // completion, Tool-call accuracy) have no judge to call again; the
      // confirm names them as kept, so it says exactly what is re-scored.
      const runOnly = (metricDefs ?? []).filter(
        (d) => d.kind === "eval" && !d.user_eval_id && keys.has(d.key),
      );
      rescore.request({
        runs: req.runs,
        evalIdsOf: () => picked.map((d) => d.user_eval_id),
        evalNames: picked.map((d) => d.name ?? d.key),
        runOnlyEvals: runOnly.map((d) => d.name ?? d.key),
      });
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [metricDefs, rescore.request],
  );

  // options-truth G6: Rerun all rows reruns exactly the runs it names
  // (rerun-cells `source_ids`), so only #2C goes Queued, not its siblings.
  const [scopedPending, setScopedPending] = useState(false);
  const rerunRunsScoped = async ({ runs: list = [], mode }) => {
    const byExp = new Map();
    list.forEach((r) => {
      if (!byExp.has(r.experiment_id)) byExp.set(r.experiment_id, []);
      byExp.get(r.experiment_id).push(r.run_id);
    });
    setScopedPending(true);
    try {
      const responses = await Promise.all(
        [...byExp.entries()].map(([expId, ids]) =>
          axios.post(endpoints.develop.experiment.reRunExperimentCell(expId), {
            source_ids: ids,
            ...(mode === "failed" && { failed_only: true }),
          }),
        ),
      );
      invalidate();
      setRerun(null);
      onDone?.();
      // W1-1 kept runs: the toast names the new runs a rerun started, with
      // Open; when none started it says the server's words.
      const started = responses.map(startedRunsOf);
      const labels = started.flatMap((x) => x.labels);
      const first = started.find((x) => x.runIds.length);
      const firstExp = first ? [...byExp.keys()][started.indexOf(first)] : null;
      const said = responses
        .map((res) => res?.data?.result?.message ?? res?.data?.message)
        .find(Boolean);
      notify(
        labels.length
          ? startedToast(
              labels,
              list.map((r) => r.label),
            )
          : mode === "failed" && said
            ? said
            : `Re-running ${runsText(list)}`,
        {
          variant: labels.length || mode !== "failed" ? "success" : "info",
          key: "leaderboard-rerun",
          ...(first && {
            action: () => (
              <Button
                size="small"
                color="inherit"
                onClick={() =>
                  navigate(
                    experimentUrl(datasetId, firstExp, {
                      runId: first.runIds[0],
                    }),
                  )
                }
              >
                Open
              </Button>
            ),
          }),
        },
      );
    } catch (e) {
      notify(getRequestErrorMessage(e, "Could not start the rerun"), {
        variant: "error",
      });
    } finally {
      setScopedPending(false);
    }
  };

  const copyText = useCallback((text, message) => {
    copyToClipboard(text).then((okCopy) =>
      notify(okCopy ? message : "Could not copy. Select the text instead.", {
        key: "leaderboard-copy",
        variant: okCopy ? "success" : "error",
      }),
    );
  }, []);

  const dialogs = (
    <>
      {rescore.dialog}
      <CustomDialog
        open={!!rerun}
        onClose={() => setRerun(null)}
        title={rerun ? groupRerunText(rerun).title : ""}
        actionButton={rerun ? RERUN_TEXT[rerun.mode].button : ""}
        loading={scopedPending}
        onClickAction={() => rerun && rerunRunsScoped(rerun)}
      >
        <Typography typography="s1" color="text.secondary">
          {rerun ? groupRerunText(rerun).body : ""}
        </Typography>
      </CustomDialog>
      <ConfirmDialog
        open={!!del}
        onClose={() => {
          if (!deleting) setDel(null);
        }}
        title={
          del
            ? del.ids.length === 1 && del.name
              ? `Delete “${del.name}”${del.running?.length ? " while it runs" : ""}?`
              : `Delete ${del.ids.length} experiments${del.running?.length ? " while they run" : ""}?`
            : ""
        }
        body={del ? deleteConfirmBody(del) : ""}
        confirmLabel={del?.running?.length ? "Stop and delete" : "Delete"}
        cancelLabel="Keep"
        busy={deleting}
        onConfirm={() => deleteNow(del)}
      />
      <RenameExperimentDialog
        target={rename}
        datasetId={datasetId}
        onClose={() => setRename(null)}
        loading={renameExperiment.isPending}
        onSave={(v) => renameExperiment.mutateAsync(v)}
      />
    </>
  );

  return {
    dialogs,
    requestRerun,
    requestDelete,
    openRename: setRename,
    downloadCsv: download.mutate,
    pinBaseline: changeBaseline,
    unpinBaseline: (opts) => changeBaseline(null, opts),
    pinPending,
    duplicate,
    copyText,
  };
}
