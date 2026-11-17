import React, { useLayoutEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { NO_DELETE, NO_EDIT } from "./leaderboardAccess";
import {
  Box,
  Button,
  Divider,
  Menu,
  MenuItem,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import {
  compareSelection,
  experimentRunsOf,
  compareUrl,
  wholeExperiments,
} from "./leaderboardModel";
import { pickHeadline } from "./leaderboardHeadline";
import { canRerunHere, defaultRunsTab } from "src/sections/common/Runs/runKind";
import { canIterate } from "src/sections/experiment-detail/iterate/iterateModel";
import {
  iterateHref,
  taskCompareHref,
} from "src/sections/common/Analysis/analysisLinks";
import { KeyCap } from "src/sections/common/ux/KeyCap";
import RunSelectionMore from "src/sections/common/Selection/RunSelectionMore";
import {
  ITERATE_TIP,
  VIEWER_TIP,
} from "src/sections/experiment-detail/iterate/IterateButton";

// the interface contract §9 (M6): edit and run doors read the tab's access gates and tips.

const baselineRunOf = (runs, id) =>
  (runs ?? []).find((r) => r.run_id === id) ?? null;

// IT-R4 G2 P3: the `Esc` keycap hides below 1300 px (the bar stays one line).
const WideOnly = ({ children }) => (
  <Box
    component="span"
    sx={{
      display: "inline-flex",
      "@media (max-width:1299px)": { display: "none" },
    }}
  >
    {children}
  </Box>
);
WideOnly.propTypes = { children: PropTypes.node };

/**
 * IT-R4 G2 P3: the bar sits above the leaderboard footer (bottom offset =
 * the footer's distance from the window bottom + 8 px), never on it.
 */
function useAboveFooter() {
  const [bottom, setBottom] = useState(null);
  useLayoutEffect(() => {
    const sync = () => {
      const el = document.querySelector('[data-testid="leaderboard-footer"]');
      if (!el) return setBottom(null);
      const r = el.getBoundingClientRect();
      setBottom(Math.max(16, Math.ceil(window.innerHeight - r.top) + 8));
    };
    sync();
    window.addEventListener("resize", sync);
    return () => window.removeEventListener("resize", sync);
  }, []);
  return bottom;
}

// `<body>` var the leaderboard grid pads its bottom by (LeaderboardGrid).
export const SELECTION_GUTTER_VAR = "--fa-selection-gutter";
const SELECTION_GAP = 16;

/**
 * Centres the bar on the page content (`<main>`), not the window, so a
 * one-line bar never covers the left nav; returns `{left, maxWidth}`.
 */
function useMainCentre() {
  const [place, setPlace] = useState(null);
  useLayoutEffect(() => {
    const main =
      document.getElementById("main-content") ?? document.querySelector("main");
    if (!main) return undefined;
    const sync = () => {
      const r = main.getBoundingClientRect();
      setPlace({ left: r.left + r.width / 2, maxWidth: r.width - 32 });
    };
    sync();
    window.addEventListener("resize", sync);
    const ro =
      typeof ResizeObserver === "undefined" ? null : new ResizeObserver(sync);
    ro?.observe(main);
    return () => {
      window.removeEventListener("resize", sync);
      ro?.disconnect();
    };
  }, []);
  return place;
}

/** Publishes the bar's height + 16 px while it is mounted. */
function useSelectionGutter(ref) {
  useLayoutEffect(() => {
    const el = ref.current;
    const body = document.body;
    if (!el) return undefined;
    const sync = () =>
      body.style.setProperty(
        SELECTION_GUTTER_VAR,
        `${Math.ceil(el.getBoundingClientRect().height) + SELECTION_GAP}px`,
      );
    sync();
    const ro =
      typeof ResizeObserver === "undefined" ? null : new ResizeObserver(sync);
    ro?.observe(el);
    return () => {
      ro?.disconnect();
      body.style.removeProperty(SELECTION_GUTTER_VAR);
    };
  }, [ref]);
}

const Action = ({
  label,
  icon,
  onClick,
  disabled,
  tip,
  color = "text.primary",
  endIcon,
  buttonRef,
  after,
  variant = "text",
}) => (
  <CustomTooltip show={!!tip} title={tip} placement="bottom" size="small" arrow>
    <span>
      <Button
        ref={buttonRef}
        variant={variant}
        color={variant === "contained" ? "primary" : undefined}
        size="small"
        disabled={disabled}
        onClick={onClick}
        startIcon={icon ? <Iconify icon={icon} width={16} /> : undefined}
        endIcon={endIcon ? <Iconify icon={endIcon} width={16} /> : undefined}
        sx={{
          height: 30,
          px: 1.25,
          whiteSpace: "nowrap",
          gap: 0.5,
          ...(variant === "contained" ? {} : { color }),
        }}
      >
        {label}
        {after}
      </Button>
    </span>
  </CustomTooltip>
);

Action.propTypes = {
  label: PropTypes.string,
  icon: PropTypes.string,
  onClick: PropTypes.func,
  disabled: PropTypes.bool,
  tip: PropTypes.string,
  color: PropTypes.string,
  endIcon: PropTypes.string,
  buttonRef: PropTypes.any,
  after: PropTypes.node,
  variant: PropTypes.string,
};

/**
 * Bulk actions on selected runs (§3.7), in the place and style of the old
 * `{n} selected | Re-Run | Delete | Cancel` bar: Compare runs (1–8),
 * Pairwise review (exactly 2), Pin as baseline (1), Rerun ▾, Delete (whole
 * experiments only), Cancel (Esc).
 */
const SelectionBar = ({
  selected,
  allRuns,
  datasetId,
  baselineId,
  pinnedRunId,
  canEdit,
  canRun,
  canDelete,
  tips,
  fromUrl,
  navigate,
  onPin,
  onRerun,
  onDelete,
  onCancel,
  onFilterToSelected,
}) => {
  const rerunRef = useRef(null);
  const barRef = useRef(null);
  const [rerunOpen, setRerunOpen] = useState(false);
  // Critic r3 #5: the grid ends above this bar while it shows (like the
  // Falcon gutter), so no selected row's numbers hide behind it.
  useSelectionGutter(barRef);
  const place = useMainCentre();
  const aboveFooter = useAboveFooter();
  const n = selected.length;
  // IT-R4 G2 P4 (S2): the pinned baseline when selected; cand = the
  // headline candidate when selected, else the newest selected run.
  const pick = {
    pinnedId: pinnedRunId,
    candId: pickHeadline(allRuns ?? [], baselineRunOf(allRuns, baselineId))
      ?.candidate?.run_id,
  };
  const compare = compareSelection(selected, baselineId, pick);
  const pairwise =
    n === 2
      ? compareSelection(selected, baselineId, pick)
      : { error: `Pairwise needs exactly 2 runs · you selected ${n}` };
  // MUST 5: the side-by-side workspace takes 2–4 lanes, baseline first.
  const sideIds = compare.error ? [] : compare.runIds;
  const sideTip =
    n < 2
      ? "Select 2–4 runs. One run? Use its row's Side by side to compare with the baseline."
      : n > 4
        ? `Side by side shows up to 4 runs · you selected ${n}`
        : `Step through ${selected.map((r) => r.label).join(", ")} task by task, starting where they differ`;
  const whole = wholeExperiments(selected, allRuns);
  const deleteTip = !canDelete
    ? tips?.delete ?? NO_DELETE
    : whole.partial
      ? "Select all runs of an experiment to delete it"
      : null;
  const single = n === 1 ? selected[0] : null;
  const runOk = canRun ?? canEdit;
  const pinTip = !canEdit
    ? tips?.edit ?? NO_EDIT
    : n !== 1
      ? `Pin as baseline needs exactly 1 run · you selected ${n}`
      : single?.run_id === pinnedRunId
        ? `${single.label} is already the dataset baseline`
        : null;
  const failedCount = selected.filter(
    (r) => (r.progress?.errors ?? 0) > 0 || r.status === "Cancelled",
  ).length;
  // dataset integration FE-0: rows rerun only for runs Future AGI starts itself (not a
  // code, harness, voice or uploaded run); evals re-score on any stored run.
  const rerunnable = selected.filter(canRerunHere);
  const rescorable = selected.filter((r) => r.target_type !== "external");

  // EL#4: floats bottom-centre above the Falcon gutter; the toolbar stays
  // where it was and the grid gets a bottom inset of the bar's height.
  return (
    <Stack
      ref={barRef}
      direction="row"
      alignItems="center"
      role="toolbar"
      aria-label={`${n} ${n === 1 ? "run" : "runs"} selected`}
      data-testid="selection-bar"
      sx={{
        position: "fixed",
        left: place ? `${place.left}px` : "50%",
        transform: "translateX(-50%)",
        bottom: aboveFooter
          ? `max(${aboveFooter}px, calc(16px + var(--fa-fab-gutter, 0px)))`
          : "calc(16px + var(--fa-fab-gutter, 0px))",
        zIndex: (t) => t.zIndex.appBar + 2,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        boxShadow: (t) => t.customShadows?.z16 ?? t.shadows[8],
        pl: 2,
        pr: 0.5,
        py: 0.5,
        gap: 0.5,
        bgcolor: "background.paper",
        // One line whenever it fits: a left offset alone would cap the
        // shrink-to-fit width (it wrapped to two lines at 1440).
        width: "max-content",
        maxWidth: place ? `${place.maxWidth}px` : "calc(100vw - 32px)",
        flexWrap: "nowrap",
      }}
    >
      <Typography
        typography="s1"
        fontWeight="fontWeightMedium"
        sx={{
          pr: 1.5,
          mr: 0.5,
          borderRight: "1px solid",
          borderColor: "divider",
          whiteSpace: "nowrap",
        }}
      >
        {n} {n === 1 ? "run" : "runs"} selected
      </Typography>
      <Action
        label={n === 1 ? "Compare with baseline" : `Compare ${n} runs`}
        icon="mdi:compare-horizontal"
        color="primary.main"
        variant={compare.error ? "text" : "contained"}
        disabled={!!compare.error}
        tip={
          compare.error ??
          (n === 1
            ? "Every task vs the baseline run in the Matrix  ·  C"
            : "Every task, every try, in the Matrix  ·  C")
        }
        endIcon={null}
        onClick={() =>
          navigate(compareUrl(datasetId, { ...compare, from: fromUrl }))
        }
        after={<KeyCap>C</KeyCap>}
      />
      <Action
        label={`Side by side (${Math.min(Math.max(n, 2), 4) === n ? n : "2–4"})`}
        icon="mdi:view-split-vertical"
        disabled={n < 2 || n > 4 || !sideIds.length}
        tip={sideTip}
        onClick={() =>
          navigate(
            taskCompareHref({
              datasetId,
              runIds: sideIds,
              baselineId: compare.baselineId,
              from: fromUrl,
            }),
          )
        }
      />
      <Action
        label="Pairwise review"
        icon="mdi:account-multiple-check-outline"
        disabled={!!pairwise.error}
        tip={
          pairwise.error ??
          "Review the two runs side by side, one row at a time"
        }
        onClick={() =>
          navigate(
            compareUrl(datasetId, {
              ...pairwise,
              review: "pairwise",
              from: fromUrl,
            }),
          )
        }
      />
      <Action
        label="Pin as baseline"
        icon="mdi:pin-outline"
        disabled={!!pinTip}
        tip={pinTip ?? "Every delta on this dataset is measured against it"}
        onClick={() => onPin(single.run_id)}
      />
      {single && canIterate(single) ? (
        <Action
          label="Test a fix…"
          icon="mdi:source-branch"
          disabled={!canEdit}
          tip={canEdit ? ITERATE_TIP : VIEWER_TIP}
          onClick={() =>
            navigate(
              iterateHref(
                // IT-R2 G2 P1: the dock opens over the Matrix when the
                // experiment holds 3+ runs (its answer), else over Data.
                `/dashboard/develop/experiment/${single.experiment_id}/${defaultRunsTab(experimentRunsOf(allRuns, single.experiment_id))}`,
                { runId: single.run_id, scope: "all" },
              ),
            )
          }
        />
      ) : null}
      {rerunnable.length || rescorable.length ? (
        <Action
          label={rerunnable.length ? "Rerun" : "Re-score"}
          icon="mdi:refresh"
          endIcon="mdi:chevron-down"
          buttonRef={rerunRef}
          disabled={!runOk}
          tip={!runOk ? tips?.run ?? NO_EDIT : undefined}
          onClick={() => setRerunOpen(true)}
        />
      ) : null}
      <Menu
        anchorEl={rerunRef.current}
        open={rerunOpen}
        onClose={() => setRerunOpen(false)}
      >
        {rerunnable.length ? (
          <MenuItem
            sx={{ typography: "s1" }}
            onClick={() => {
              setRerunOpen(false);
              onRerun({ mode: "all", runs: rerunnable });
            }}
          >
            Rerun all rows
          </MenuItem>
        ) : null}
        {rerunnable.length ? (
          <MenuItem
            sx={{ typography: "s1" }}
            disabled={!failedCount}
            onClick={() => {
              setRerunOpen(false);
              onRerun({ mode: "failed", runs: rerunnable });
            }}
          >
            Rerun failed rows{failedCount ? "" : " · none failed"}
          </MenuItem>
        ) : null}
        {rescorable.length ? (
          <MenuItem
            sx={{ typography: "s1" }}
            onClick={() => {
              setRerunOpen(false);
              onRerun({ mode: "rescore", runs: rescorable });
            }}
          >
            Re-score evals
          </MenuItem>
        ) : null}
      </Menu>
      <RunSelectionMore
        runs={selected}
        onFilterToSelected={onFilterToSelected}
      />
      <Action
        label="Delete"
        icon="mdi:trash-can-outline"
        color="error.main"
        disabled={!!deleteTip}
        tip={deleteTip ?? undefined}
        onClick={() =>
          onDelete({
            ids: whole.experimentIds,
            runCount: n,
            name:
              whole.experimentIds.length === 1
                ? selected[0].experiment_name
                : undefined,
          })
        }
      />
      <Divider orientation="vertical" flexItem sx={{ mx: 0.5, my: 0.5 }} />
      <Action
        label="Clear"
        tip="Clear the selection  ·  Esc"
        onClick={onCancel}
        after={
          <WideOnly>
            <KeyCap>Esc</KeyCap>
          </WideOnly>
        }
      />
    </Stack>
  );
};

SelectionBar.propTypes = {
  onFilterToSelected: PropTypes.func,
  selected: PropTypes.array.isRequired,
  allRuns: PropTypes.array.isRequired,
  datasetId: PropTypes.string.isRequired,
  baselineId: PropTypes.string,
  pinnedRunId: PropTypes.string,
  canEdit: PropTypes.bool,
  // the interface contract §9 (M6): run-class doors and the access tips (leaderboardGates).
  canRun: PropTypes.bool,
  canDelete: PropTypes.bool,
  tips: PropTypes.object,
  fromUrl: PropTypes.string,
  navigate: PropTypes.func.isRequired,
  onPin: PropTypes.func.isRequired,
  onRerun: PropTypes.func.isRequired,
  onDelete: PropTypes.func.isRequired,
  onCancel: PropTypes.func.isRequired,
};

export default SelectionBar;
