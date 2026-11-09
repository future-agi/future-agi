// The New experiment drawer's "selected rows are not saved yet" state (IT-R1
// G5 P1c). Reads the form through `control` so the Data and Evaluations
// steps share it without new context. `Save as vN and continue` saves the
// pending change-set, follows the door rows into the new version through the
// save result's `row_id_map`, pins that version and opens Evaluations.
//
// IT-R2 G5 P2: when the change-set holds only the door rows, the save is
// direct (no modal) with the note `Added 6 failed production sessions`;
// `Edit note` opens the Save version dialog instead. After a save of rows
// imported from production failures, `applyEvals` swaps in the check that
// failed in production (P1, retestEvals.js). S7: the move is to step 3
// (EVALUATIONS), never past it.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useController, useWatch } from "react-hook-form";
import { useQueryClient } from "@tanstack/react-query";
import { useSaveDatasetVersion } from "src/api/develop/dataset-versions";
import { notify } from "src/sections/common/ux/toast";
import { isSurfaceShown } from "src/launch/launchSwitch";
import { useRunExperimentStore } from "../../states";
import { EXPERIMENT_STEP } from "../../Experiment/constants";
import { useExperimentForm } from "../../Experiment/ExperimentFormContext";
import { runCount } from "../../Experiment/runPlan";
import { useDevelopDetailContext } from "../../Context/DevelopDetailContext";
import { followSavedRows } from "../DataGrid/newRowsReveal";
import { useChangeSet, useChangeSetStore } from "./changeSetStore";
import { useOpenSaveVersion } from "./PendingChangesGate";
import { selectedRowValues } from "./evalCoverage";
import { doorNote, productionOfSelection, retestEvals } from "./retestEvals";
import {
  remapRowIds,
  unsavedSelection,
  unsavedSentence,
} from "./unsavedSelection";

const EMPTY = Object.freeze([]);
const plural = (n, w) =>
  `${n.toLocaleString("en-US")} ${w}${n === 1 ? "" : "s"}`;

/**
 * IT-R2 G5 P2: when none of the selected rows are saved, the banner speaks
 * the footer's numbers: `6 new rows (not saved yet) × 1 run = 6
 * executions. Save them as v7 to run them.` Otherwise the r1 sentence.
 */
export function unsavedBannerSentence(
  info,
  { versionNumber, next, runs, trials = 1 },
) {
  if (!isSurfaceShown("dataset.versions") || !info?.unsaved) return null;
  if (info.unsaved !== info.total || !runs)
    return unsavedSentence(info, versionNumber);
  const n = info.total;
  const t = Number(trials) || 1;
  // v2.1 (knob:cost-line, g3 owed/06): rows × tries = tries, the words of
  // the footer, Review and the Settings card.
  const tries = (k) =>
    `${k.toLocaleString("en-US")} ${k === 1 ? "try" : "tries"}`;
  return `${plural(n, "new row")} (not saved yet) × ${t > 1 ? `${tries(t)} × ` : ""}${plural(runs, "run")} = ${tries(n * t * runs)}. Save ${n === 1 ? "it" : "them"} as v${next} to run ${n === 1 ? "it" : "them"}.`;
}

/**
 * @param {object} control react-hook-form control of the drawer
 * @param {{guard?: boolean, onRemap?: (map: object) => void,
 *   applyEvals?: (fn: (evals: object[]) => object[]) => void}} [opts]
 *   `guard`: while this step is mounted, a move past the Data step (Next,
 *   ⌘↵, a stepper click) with unsaved rows selected is undone, and `nudge`
 *   bumps so the banner can draw attention. `applyEvals`: how the caller
 *   rewrites the form's evaluations (P1 re-test defaults).
 */
export function useUnsavedSelection(
  control,
  { guard = false, onRemap, applyEvals } = {},
) {
  // Launch uses immediate writes, not pending changes. Keep this UI and its
  // save actions absent even if a caller supplies stale pending state.
  const versionsShown = isSurfaceShown("dataset.versions");
  const {
    datasetId,
    versions,
    splits,
    setSampleRowId,
    columns,
    rows,
    exampleCount,
  } = useExperimentForm();
  const { field: rowsField } = useController({ control, name: "rowIds" });
  const { field: splitField } = useController({ control, name: "splitIds" });
  const { field: versionField } = useController({
    control,
    name: "datasetVersionId",
  });
  const referenceColumnId = useWatch({ control, name: "columnId" });
  const experimentType = useWatch({ control, name: "experimentType" });
  const promptConfig = useWatch({ control, name: "promptConfig" });
  const trials = useWatch({ control, name: "trials" });
  const ops = useChangeSetStore((st) => st.sets?.[datasetId]?.ops) ?? EMPTY;
  const cs = useChangeSet(datasetId);
  const latestNumber = Math.max(0, ...(versions ?? []).map((v) => v.number));
  const latest = (versions ?? []).find((v) => v.number === latestNumber);
  const pinned =
    (versions ?? []).find((v) => v.id === versionField.value) ?? latest;
  const rowIds = rowsField.value ?? EMPTY;
  const splitIds = splitField.value ?? EMPTY;
  const info = useMemo(
    () => unsavedSelection({ ops, rowIds, splitIds, splits }),
    [ops, rowIds, splitIds, splits],
  );
  const runs = runCount(experimentType, promptConfig ?? []);
  const sentence = unsavedBannerSentence(info, {
    versionNumber: pinned?.number ?? latestNumber,
    next: latestNumber + 1,
    runs,
    trials,
  });
  const saveLabel = versionsShown
    ? `Save as v${latestNumber + 1} and continue`
    : null;

  const openSave = useOpenSaveVersion(datasetId);
  const save = useSaveDatasetVersion(datasetId);
  const { refreshGrid } = useDevelopDetailContext() ?? {};
  const queryClient = useQueryClient();
  const skipGuard = useRef(false);
  const blockRef = useRef(null);
  blockRef.current = sentence;
  const [nudge, setNudge] = useState(0);

  useEffect(() => {
    if (!guard) return undefined;
    return useRunExperimentStore.subscribe((st, prev) => {
      if (skipGuard.current || !blockRef.current) return;
      if (
        prev.currentStep === EXPERIMENT_STEP.DATA &&
        st.currentStep > EXPERIMENT_STEP.DATA
      ) {
        // Undo the move, including the "visited" mark it left behind.
        useRunExperimentStore.setState({
          currentStep: prev.currentStep,
          visitedSteps: prev.visitedSteps,
        });
        setNudge((n) => n + 1);
      }
    });
  }, [guard]);

  const doorIds = rowIds;
  const doorOps = useMemo(
    () => ops.filter((o) => o.op === "add_row" && doorIds.includes(o.temp_id)),
    [ops, doorIds],
  );
  // The re-test defaults are read BEFORE the save discards the change-set.
  const production = productionOfSelection({ rowIds: doorIds, ops, rows });
  const retest = useRef(null);
  retest.current = production
    ? {
        production,
        columns,
        referenceColumnId,
        selected: selectedRowValues({
          rowIds: doorIds,
          ops,
          rows,
          exampleCount,
        }),
      }
    : null;

  const afterSave = useCallback(
    (version, result) => {
      const map = result?.row_id_map ?? {};
      const remapped = remapRowIds(doorIds, map);
      const r = retest.current;
      if (r && applyEvals) applyEvals((evals) => retestEvals(evals ?? [], r));
      rowsField.onChange(remapped);
      onRemap?.(map);
      followSavedRows(datasetId, map);
      if (version?.id) versionField.onChange(version.id);
      // Split counts and the sample rows (Test on, Value on Row) are read
      // per key that a save does not change; read them again at the new
      // version.
      queryClient.invalidateQueries({
        queryKey: ["develop", "dataset-splits", datasetId],
      });
      queryClient.invalidateQueries({
        queryKey: ["experiment-create", "sample-table", datasetId],
      });
      if (remapped[0]) setSampleRowId?.(remapped[0]);
      skipGuard.current = true;
      // S7: step 3, so the stepper never skips Evaluations.
      useRunExperimentStore
        .getState()
        .setCurrentStep(EXPERIMENT_STEP.EVALUATIONS);
    },
    [
      doorIds,
      applyEvals,
      rowsField,
      onRemap,
      versionField,
      queryClient,
      datasetId,
      setSampleRowId,
    ],
  );

  /** `Edit note`: the Save version dialog, then the same continuation. */
  const editNote = useCallback(
    () => {
      if (!versionsShown) return;
      return openSave({ onSaved: afterSave });
    },
    [versionsShown, openSave, afterSave],
  );

  // Direct only when the change-set is exactly the door rows; anything else
  // pending is shown in the dialog before it is saved.
  const onlyDoorRows = doorOps.length > 0 && doorOps.length === ops.length;
  const note = doorNote(doorOps);

  const saveAndContinue = useCallback(async () => {
    if (!versionsShown) return;
    if (!onlyDoorRows) {
      editNote();
      return;
    }
    if (save.isPending) return;
    try {
      const result = await save.mutateAsync({
        base_version_id: cs.baseVersionId ?? latest?.id ?? null,
        description: note,
        operations: cs.toOperations(),
      });
      useChangeSetStore.getState().discard(datasetId);
      refreshGrid?.();
      const version = result?.version;
      notify(`Saved v${version?.number ?? ""} · ${note}`, {
        variant: "success",
        key: "version-saved",
      });
      afterSave(version, result);
    } catch (e) {
      // A conflict (someone saved first) or an error: the dialog explains
      // it and offers the way out.
      editNote();
    }
  }, [
    versionsShown,
    onlyDoorRows,
    editNote,
    save,
    cs,
    latest,
    note,
    datasetId,
    refreshGrid,
    afterSave,
  ]);

  return {
    ...info,
    sentence,
    saveLabel,
    saveAndContinue,
    editNote,
    saving: save.isPending,
    note: versionsShown && onlyDoorRows ? note : null,
    nudge,
    versionNumber: pinned?.number ?? latestNumber,
  };
}
