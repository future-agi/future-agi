import React, { useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, useMediaQuery } from "@mui/material";
import { useLocation } from "react-router-dom";
import CustomTooltip from "src/components/tooltip";
import { ResultsExportDialog } from "src/sections/common/export/ExportDialog";
import { resultsViewOf } from "src/sections/common/export/resultsExport";
import RunExperimentWithProvider from "src/sections/develop-detail/Experiment/RunExperiment";
import { useRunExperimentStoreShallow } from "src/sections/develop-detail/states";
import StartReviewButton from "src/sections/develop-detail/Review/StartReviewButton";
import { NO_REVIEW_FILTER } from "../reviewDoor";
import ActionBar from "src/sections/common/ux/oneview/ActionBar";

/** `Diff` target and its disabled reason (pure; exported for tests). */
export function diffTarget({ runCount, hasExpected }) {
  const target = runCount > 1 ? "baseline" : "expected";
  const reason =
    target === "expected" && !hasExpected
      ? "This dataset has no expected column to diff against"
      : null;
  return { target, reason };
}

const REVIEW_TEXT_SX = {
  typography: "s2",
  color: "text.secondary",
  minWidth: 0,
  px: 1,
  whiteSpace: "nowrap",
  "& .MuiTypography-root": { typography: "s2", fontWeight: 500 },
};
const REVIEW_ICON_SX = {
  ...REVIEW_TEXT_SX,
  px: 0.75,
  "& .MuiTypography-root": { display: "none" },
  "& .MuiButton-startIcon": { mr: 0 },
  "& .MuiButton-endIcon": { ml: 0.25 },
};

/**
 * Right side of the results toolbar, one view (A3-10/11): an ActionBar
 * (`surface="results"`) that keeps every control and folds by width:
 * `Diff` (toggle, ⇧D, priority 1) · `Review ▾` (icon-only below 1366 px,
 * tooltip `Review rows`) · `View` (density, columns, Why column, saved
 * views, Reset) · `More ▾` with `Edit` · `Download…` · `Add evaluations`
 * (experiment page; priority 3, shown as buttons when the window has room).
 * Every action is in ⌘K by its label. While rows are selected the page
 * shows SelectionBar here instead.
 */
const ExperimentBarDataRightSection = ({
  mode = "experiment",
  datasetId,
  experimentId,
  runIds = [],
  baselineId,
  pills = [],
  diff = "off",
  onDiffChange,
  hasExpected = true,
  readOnly = false,
  onAddEvaluations,
  onExperimentEdited,
  viewMenu,
  view: viewProp,
  datasetName,
}) => {
  const isExperiment = mode === "experiment";
  const reviewIconOnly = useMediaQuery("(max-width:1365.95px)");
  const reviewRef = useRef(null);
  const { initiateEditMode } = useRunExperimentStoreShallow((state) => ({
    initiateEditMode: state.initiateEditMode,
  }));

  const { search: locationSearch } = useLocation();
  const [exportOpen, setExportOpen] = useState(false);
  const runKey = runIds.join(",");
  const exportView = useMemo(
    () =>
      resultsViewOf({
        search: locationSearch,
        runIds,
        baselineId,
        view: viewProp,
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [locationSearch, runKey, baselineId, viewProp],
  );

  const { target, reason } = diffTarget({
    runCount: runIds.length,
    hasExpected,
  });
  const on = diff !== "off";

  const actions = useMemo(
    () =>
      [
        {
          id: "diff",
          label: "Diff",
          icon: "mdi:vector-difference",
          priority: 1,
          shortcut: "diffToggle",
          keywords: ["diff", target, "highlight changes", "compare output"],
          toggle: { on },
          disabled: Boolean(reason),
          reason,
          run: () => onDiffChange?.(on ? "off" : target),
        },
        {
          id: "review",
          label: "Review rows",
          icon: "mdi:account-check-outline",
          priority: 1,
          keywords: ["pairwise", "rubric", "annotate", "judge"],
          run: () => reviewRef.current?.querySelector("button")?.click(),
          render: () => (
            <CustomTooltip
              show={reviewIconOnly}
              size="small"
              placement="top"
              arrow
              title="Review rows"
            >
              <Box ref={reviewRef} sx={{ display: "inline-flex" }}>
                <StartReviewButton
                  datasetId={datasetId}
                  runIds={runIds}
                  baselineId={baselineId}
                  // L7c g6 (grid:review-rows, HIDE): `Current filter` is
                  // not offered from this door: the dialog counted a page
                  // filter against the rubric run only (`Current filter
                  // (0)`, no queue). All rows stays; owed to g8's dialog.
                  filters={NO_REVIEW_FILTER}
                  buttonProps={{
                    size: "small",
                    variant: "text",
                    color: "inherit",
                    "aria-label": "Review rows",
                    "data-testid": "results-review",
                    sx: reviewIconOnly ? REVIEW_ICON_SX : REVIEW_TEXT_SX,
                  }}
                />
              </Box>
            </CustomTooltip>
          ),
        },
        viewMenu && {
          id: "view",
          label: "View",
          priority: 1,
          render: () => viewMenu,
        },
        isExperiment && {
          id: "edit",
          label: "Edit",
          icon: "mdi:pencil-outline",
          priority: 3,
          keywords: ["edit experiment", "change runs", "settings"],
          disabled: readOnly,
          reason: readOnly ? "Editors can edit the experiment" : undefined,
          run: () => initiateEditMode(experimentId),
        },
        {
          // W3-6 B3: exactly this view (filters, show, sort) or every
          // row, as CSV / JSON / JSONL (was the run's CSV).
          id: "csv",
          label: "Download…",
          icon: "material-symbols:download",
          priority: 3,
          keywords: ["export", "csv", "json", "jsonl", "download", "this view"],
          disabled: !runIds.length,
          reason: runIds.length ? undefined : "No run to export yet",
          run: () => setExportOpen(true),
        },
        isExperiment && {
          id: "addEvaluations",
          label: "Add evaluations",
          icon: "mdi:plus",
          priority: 3,
          keywords: ["eval", "metric", "score", "judge"],
          disabled: readOnly || !onAddEvaluations,
          reason: readOnly ? "Editors can add evaluations" : undefined,
          run: () => onAddEvaluations?.(),
        },
      ].filter(Boolean),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [
      on,
      target,
      reason,
      reviewIconOnly,
      datasetId,
      runIds.join(","),
      baselineId,
      pills,
      viewMenu,
      isExperiment,
      readOnly,
      experimentId,
      onAddEvaluations,
      onDiffChange,
    ],
  );

  return (
    <>
      {/* Calm below 1536 px: Diff · Review · View · More (Edit, Download
          CSV, Add evaluations fold into More); wider windows show them. */}
      <ActionBar
        surface="results"
        actions={actions}
        sx={{
          ml: "auto",
          // Room for the priority-1 actions + More only (icon Review and
          // View below 1366 px, labelled up to 1536 px).
          "@media (max-width:1365.95px)": { maxWidth: 222 },
          "@media (min-width:1366px) and (max-width:1535.95px)": {
            maxWidth: 310,
          },
        }}
      />
      {isExperiment && (
        <RunExperimentWithProvider onSuccessfulSubmit={onExperimentEdited} />
      )}
      <ResultsExportDialog
        open={exportOpen}
        onClose={() => setExportOpen(false)}
        datasetId={datasetId}
        view={exportView}
        name={datasetName}
      />
    </>
  );
};

ExperimentBarDataRightSection.propTypes = {
  mode: PropTypes.oneOf(["experiment", "compare"]),
  datasetId: PropTypes.string,
  experimentId: PropTypes.string,
  runIds: PropTypes.arrayOf(PropTypes.string),
  baselineId: PropTypes.string,
  pills: PropTypes.array,
  diff: PropTypes.oneOf(["off", "baseline", "expected"]),
  onDiffChange: PropTypes.func,
  hasExpected: PropTypes.bool,
  readOnly: PropTypes.bool,
  onAddEvaluations: PropTypes.func,
  onExperimentEdited: PropTypes.func,
  viewMenu: PropTypes.node,
  // W3-6 B3 (optional): the grid's view `{runIds, baselineId, pills, show,
  // search, sort, agg}`; without it the URL's view is used (no search).
  view: PropTypes.object,
  datasetName: PropTypes.string,
};

export default ExperimentBarDataRightSection;
