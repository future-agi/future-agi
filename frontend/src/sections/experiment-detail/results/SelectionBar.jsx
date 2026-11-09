import React, { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Stack } from "@mui/material";
import { enqueueSnackbar } from "notistack";
import ActionBar from "src/sections/common/ux/oneview/ActionBar";
import StartReviewDialog from "src/sections/develop-detail/Review/StartReviewDialog";
import AssignDialog from "src/sections/common/Collab/AssignDialog";
import useDatasetAccess from "src/sections/common/Access/useDatasetAccess";
import { resultsCollabActions } from "./resultsCollabActions";
import { useRerunResultCells } from "src/api/develop/run-results";
import { useLocation, useNavigate } from "react-router-dom";
import { iterateHref } from "src/sections/common/Analysis/analysisLinks";
import { openRerunWithChanges } from "src/sections/common/RunSources/rerunBus";
import AddResultsToDatasetDialog from "./AddResultsToDatasetDialog";
import { canRerunHere } from "src/sections/common/Runs/runKind";
import { canIterate } from "../iterate/iterateModel";
import { useRescore } from "src/sections/common/Runs/useRescore";
import { scoreWithChoices } from "src/sections/common/Runs/rescoreCopyModel";
import { askFalconAboutRows } from "src/sections/common/Selection/askFalconAboutRows";
import { useSelectAllMatching } from "src/sections/common/Selection/useSelectAllMatching";
import SelectionCount from "src/sections/common/Selection/SelectionCount";
// HO results the regression check (the interface contract): the selection through the interface contract's results Download….
import { ResultsExportDialog } from "src/sections/common/export/ExportDialog";
import { resultsSelectionView } from "src/sections/common/Selection/exportSelection";

/**
 * Selection actions (PRODUCT-SPEC §3.10.8): `N selected · Add to dataset ·
 * Send to verification… · Assign to… · Comment… · Review pairwise · Re-run ▾ ·
 * Clear` (the interface contract §9 integration, M6: Send to verification opens Start review with the rows).
 */
const SelectionBar = ({
  datasetId,
  selectedRows,
  runs,
  baselineId,
  focusedRunId,
  pills,
  summary,
  readOnly,
  onClear,
  addRequest = 0,
  onAddHandled,
  onComment,
  view,
  total,
  loadedCount,
}) => {
  // the interface contract: every matching row of the view on request (R3 ids_only).
  const selection = useSelectAllMatching({
    datasetId,
    view,
    selectedIds: selectedRows.map((r) => r.example_id),
    loadedCount,
    total,
  });
  const exampleIds = selection.ids;
  const navigate = useNavigate();
  const { pathname, search } = useLocation();
  const [addOpen, setAddOpen] = useState(false);
  // R4 implementation (8f): `A` on the grid opens the dialog (a new request number).
  // iterate r2 G3 (P11): a request made before this bar mounted (A right
  // after X) still opens it, once (`onAddHandled` clears the request).
  useEffect(() => {
    if (addRequest > 0 && !readOnly) {
      setAddOpen(true);
      onAddHandled?.();
    }
  }, [addRequest, readOnly, onAddHandled]);
  const [reviewOpen, setReviewOpen] = useState(false);
  // the interface contract §9 (M6): Send to verification (a rubric review of the rows) and Assign to.
  const [qaOpen, setQaOpen] = useState(false);
  const [assignOpen, setAssignOpen] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const access = useDatasetAccess(datasetId);
  const anchorRef = useRef(null);
  const candidate = runs.find((r) => r.run_id !== baselineId);
  // The run `Rerun with changes` iterates on: the focused one, else the
  // candidate, else the only run.
  const iterateRun =
    runs.find((r) => r.run_id === focusedRunId) ?? candidate ?? runs[0];
  // dataset integration FE-0: cells are re-run only for runs Future AGI starts itself.
  const rerunnable = runs.filter(canRerunHere);
  const rerun = useRerunResultCells(datasetId, {
    onSuccess: (_, cells) =>
      enqueueSnackbar(
        `Re-running ${cells.length} ${cells.length === 1 ? "cell" : "cells"}`,
        { variant: "success" },
      ),
  });
  const rerunCells = (runList) =>
    rerun.mutate(
      runList.flatMap((run) =>
        exampleIds.map((exampleId) => ({ run, exampleId })),
      ),
    );
  const n = exampleIds.length;
  // the interface contract Score with…: the selected rows of the focused run scored with one
  // eval (its own or the dataset's) in a new run; the rest is carried.
  const rescore = useRescore({ datasetId });
  const scoreRun = iterateRun && canRerunHere(iterateRun) ? iterateRun : null;
  const scoreChoices = scoreRun
    ? scoreWithChoices(scoreRun, {
        metrics: summary?.metrics ?? [],
        datasetEvals: summary?.dataset_evals ?? [],
      })
    : [];
  // One view: the selection actions fold by priority like the toolbar they
  // replace (never a second or third row at 1280).
  const actions = [
    {
      id: "addToDataset",
      // iterate r2 G3 (P6): one verb with the dialog (`Tag 3 rows with a
      // split`, `Tag 3 rows as hard and save v7`).
      label: "Tag rows…",
      keywords: ["add to dataset", "split", "tag", "save version"],
      icon: "mdi:plus",
      priority: 2,
      disabled: readOnly,
      reason: readOnly ? "Editors can add rows to a dataset" : undefined,
      run: () => setAddOpen(true),
    },
    ...resultsCollabActions({
      n,
      readOnly,
      access,
      qaRun: iterateRun,
      onSendToQa: () => setQaOpen(true),
      onAssign: () => setAssignOpen(true),
      onComment: onComment
        ? () => onComment(selectedRows[0], iterateRun?.run_id)
        : null,
    }),
    {
      id: "reviewPairwise",
      label: "Review pairwise",
      icon: "mdi:scale-balance",
      priority: 2,
      disabled: !candidate,
      reason: candidate ? undefined : "Pairwise review needs two runs",
      run: () => setReviewOpen(true),
    },
    ...(rerunnable.length
      ? [
          {
            id: "rerun",
            label: "Re-run",
            icon: "mdi:refresh",
            priority: 2,
            disabled: readOnly,
            reason: readOnly ? "Editors can start runs" : undefined,
            menu: [
              {
                id: "all",
                label: "Selected rows, all runs",
                run: () => rerunCells(rerunnable),
              },
              ...rerunnable.map((run) => ({
                id: run.run_id,
                label: `Selected rows, ${run.label} only`,
                run: () => rerunCells([run]),
              })),
            ],
          },
        ]
      : []),
    ...(scoreChoices.length
      ? [
          {
            id: "scoreWith",
            label: "Score with…",
            keywords: ["re-score", "eval", "judge", "score"],
            icon: "mdi:check-decagram-outline",
            priority: 3,
            disabled: readOnly,
            reason: readOnly ? "Editors can score runs" : undefined,
            menu: scoreChoices.map((c) => ({
              id: `score-${c.key}`,
              label: c.label,
              run: () =>
                rescore.request({
                  runs: [scoreRun],
                  evalIdsOf: () => [c.id],
                  evalNames: [c.name],
                  rowIds: exampleIds,
                }),
            })),
          },
        ]
      : []),
    // options-truth G6: the dock (Test a fix) and the dialog (Rerun with
    // changes) are two doors with two names. dataset integration FE-0 (owner Q12): each
    // is absent for a run it does not apply to (no disabled dead end).
    ...(iterateRun && !canIterate(iterateRun)
      ? []
      : [
          {
            id: "testFix",
            label: "Test a fix on these rows",
            icon: "mdi:source-branch",
            priority: 3,
            disabled: readOnly || !iterateRun,
            reason: readOnly
              ? "Editors can start runs"
              : !iterateRun
                ? "Pick a run first"
                : undefined,
            run: () =>
              navigate(
                `${pathname}${iterateHref(search, {
                  runId: iterateRun.run_id,
                  scope: "rows",
                  rows: exampleIds,
                })}`,
              ),
          },
        ]),
    ...(iterateRun && !canRerunHere(iterateRun)
      ? []
      : [
          {
            id: "rerunWithChanges",
            label: "Rerun with changes…",
            icon: "mdi:tune-variant",
            priority: 3,
            disabled: readOnly || !iterateRun,
            reason: readOnly
              ? "Editors can start runs"
              : !iterateRun
                ? "Pick a run first"
                : undefined,
            run: () =>
              openRerunWithChanges({
                runId: iterateRun.run_id,
                scope: "rows",
                rowIds: exampleIds,
              }),
          },
        ]),
    // HO the regression check (the interface contract): exactly the selected rows, as CSV / JSON / JSONL.
    {
      id: "downloadSelection",
      label: "Download selection…",
      keywords: ["export", "csv", "json", "jsonl", "download"],
      icon: "material-symbols:download",
      priority: 3,
      disabled: access.can.download === false,
      reason:
        access.can.download === false ? access.reason("download") : undefined,
      run: () => setExportOpen(true),
    },
    // the interface contract: the selected rows go to Falcon by reference (ids, not text).
    {
      id: "askFalcon",
      // L7c g6 (bulk:ask-falcon, RELABEL): what it does — Falcon opens
      // with a rule-based summary of these rows' scores and failed evals
      // (no model call), the words its answer uses.
      label: `Rule-based summary of ${n === 1 ? "this row" : `these ${n} rows`} (Falcon)`,
      keywords: ["falcon", "explain", "why", "summary", "rule-based"],
      icon: "mdi:creation-outline",
      priority: 3,
      run: () =>
        askFalconAboutRows({
          datasetId,
          experimentId: /\/experiment\/([^/]+)/.exec(pathname)?.[1],
          runs,
          baselineId,
          rowIds: exampleIds,
          url: `${pathname}${search}`,
        }),
    },
    {
      id: "clear",
      label: "Clear selection",
      icon: "mdi:close",
      variant: "icon",
      priority: 1,
      keywords: ["deselect", "selection"],
      run: onClear,
    },
  ];
  return (
    <Stack
      ref={anchorRef}
      direction="row"
      alignItems="center"
      sx={{ flex: 1, minWidth: 0 }}
      gap={1}
      data-testid="results-selection-bar"
    >
      <SelectionCount state={selection} />
      <ActionBar surface="results-selection" actions={actions} />
      {rescore.dialog}
      <AddResultsToDatasetDialog
        open={addOpen}
        onClose={(done) => {
          setAddOpen(false);
          if (done) onClear();
        }}
        datasetId={datasetId}
        datasetName={summary?.dataset?.name}
        exampleIds={exampleIds}
        rows={selectedRows}
        runs={runs}
        baselineId={baselineId}
        pills={pills}
        focusedRunId={focusedRunId}
        splits={summary?.splits ?? []}
      />
      {qaOpen && (
        <StartReviewDialog
          open
          onClose={() => setQaOpen(false)}
          datasetId={datasetId}
          mode="rubric"
          runA={iterateRun?.run_id}
          exampleIds={exampleIds}
          returnTo={`${window.location.pathname}${window.location.search}`}
        />
      )}
      {assignOpen && (
        <AssignDialog
          open
          onClose={() => setAssignOpen(false)}
          datasetId={datasetId}
          rowIds={exampleIds}
          runId={iterateRun?.run_id}
          onAssigned={() => setAssignOpen(false)}
        />
      )}
      {exportOpen && (
        <ResultsExportDialog
          open
          onClose={() => setExportOpen(false)}
          datasetId={datasetId}
          view={resultsSelectionView(view, exampleIds)}
          name={summary?.dataset?.name}
        />
      )}
      <StartReviewDialog
        open={reviewOpen}
        onClose={() => setReviewOpen(false)}
        datasetId={datasetId}
        mode="pairwise"
        runA={baselineId}
        runB={candidate?.run_id}
        exampleIds={exampleIds}
        returnTo={`${window.location.pathname}${window.location.search}`}
      />
    </Stack>
  );
};

SelectionBar.propTypes = {
  addRequest: PropTypes.number,
  onAddHandled: PropTypes.func,
  // the interface contract §9 (M6): open a row's answer on the Review tab, where its comments are.
  onComment: PropTypes.func,
  datasetId: PropTypes.string.isRequired,
  selectedRows: PropTypes.array.isRequired,
  runs: PropTypes.array.isRequired,
  baselineId: PropTypes.string,
  focusedRunId: PropTypes.string,
  pills: PropTypes.array,
  summary: PropTypes.object,
  readOnly: PropTypes.bool,
  onClear: PropTypes.func.isRequired,
  view: PropTypes.object,
  total: PropTypes.number,
  loadedCount: PropTypes.number,
};

export default SelectionBar;
