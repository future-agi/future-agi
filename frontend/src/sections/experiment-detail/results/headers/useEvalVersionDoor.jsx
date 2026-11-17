import React, { useCallback, useMemo, useState } from "react";
import { useSnackbar } from "notistack";
import axios, { endpoints } from "src/utils/axios";
import { getSafeActionErrorMessage } from "src/utils/errorUtils";
import EvalVersionChangesDialog from "./EvalVersionChangesDialog";
import { olderRunsOf } from "./evalVersionText";

/**
 * W1-3 (EV-03): the results page's eval-version doors. `seeChanges(group)`
 * opens See changes for the runs in view on an older version; "Use v4 on
 * #2A" (and a refused delta's "Re-score #2A on v4", `rescoreOn`) asks the
 * one re-score door (useRescore's request, with the server's dry-run plan
 * in its confirm) to re-score that run with the version, as a child run;
 * "Use v4 in new runs" pins each experiment's card to it (the same
 * edit_and_run_user_eval the eval editor saves with, `run: false`);
 * `compareAnyway` opts the page into deltas across versions
 * (`mix_versions=1`).
 * @param {{runsById: object, requestRescore: Function, patch: Function,
 *   datasetId: string}} p
 */
export function useEvalVersionDoor({
  runsById,
  requestRescore,
  patch,
  datasetId,
}) {
  const { enqueueSnackbar } = useSnackbar();
  const [open, setOpen] = useState(null);
  const labels = useMemo(
    () =>
      Object.fromEntries(
        Object.entries(runsById ?? {}).map(([id, r]) => [id, r?.label]),
      ),
    [runsById],
  );
  const seeChanges = useCallback(
    (group) =>
      setOpen({ def: group.def, runs: olderRunsOf(group.def, labels) }),
    [labels],
  );
  const compareAnyway = useCallback(() => patch({ mix_versions: 1 }), [patch]);
  const rescoreOnVersion = useCallback(
    (def, runId, version) => {
      const run = runsById?.[runId];
      const uem = def?.uem_ids?.[runId];
      if (!def || !run?.experiment_id || !uem) return;
      requestRescore({
        posts: [
          {
            url: endpoints.develop.experiment.reRunExperimentCell(
              run.experiment_id,
            ),
            body: {
              user_eval_metric_ids: [uem],
              run_ids: [runId],
              eval_versions: { [def.key]: version },
            },
          },
        ],
        evalNames: [def.name ?? def.key],
        experimentId: run.experiment_id,
        title: () => `Re-score ${run.label} with ${def.name} ${version}?`,
        confirmLabel: `Use ${version}`,
      });
    },
    [runsById, requestRescore],
  );
  const onUse = useCallback(
    (r, latest) => {
      const def = open?.def;
      setOpen(null);
      rescoreOnVersion(def, r.runId, latest);
    },
    [open, rescoreOnVersion],
  );
  const rescoreOn = useCallback(
    (group, runId, version) => rescoreOnVersion(group.def, runId, version),
    [rescoreOnVersion],
  );
  // One card per experiment of the runs on the older version.
  const pinTargets = useMemo(() => {
    const def = open?.def;
    const seen = new Map();
    (open?.runs ?? []).forEach((r) => {
      const run = runsById?.[r.runId];
      const uem = def?.uem_ids?.[r.runId];
      if (!run?.experiment_id || !uem || seen.has(run.experiment_id)) return;
      seen.set(run.experiment_id, {
        experimentId: run.experiment_id,
        uem,
        label: String(run.label ?? "").match(/^#\d+/)?.[0] ?? null,
      });
    });
    return [...seen.values()];
  }, [open, runsById]);
  const onPin = useCallback(
    async (target) => {
      const def = open?.def;
      const latest = def?.latest_version;
      if (!def || !latest || !datasetId) return;
      try {
        const res = await axios.post(
          endpoints.develop.eval.editEval(datasetId, target.uem),
          {
            experiment_id: target.experimentId,
            run: false,
            config: { version_pin: latest },
          },
        );
        setOpen(null);
        enqueueSnackbar(
          res.data?.result?.message ??
            `New runs score ${def.name} with ${latest}.`,
          { variant: "success" },
        );
      } catch (err) {
        enqueueSnackbar(
          getSafeActionErrorMessage(err, "Could not pin the version"),
          { variant: "error" },
        );
      }
    },
    [open, datasetId, enqueueSnackbar],
  );
  const dialog = (
    <EvalVersionChangesDialog
      open={Boolean(open)}
      def={open?.def}
      runs={open?.runs ?? []}
      onClose={() => setOpen(null)}
      onUse={onUse}
      pinTargets={pinTargets}
      onPin={datasetId ? onPin : undefined}
      onOpenRow={(runId, rowId) => {
        setOpen(null);
        patch({ row: rowId, run: runId }, { push: true });
      }}
    />
  );
  return { seeChanges, compareAnyway, rescoreOn, dialog };
}
