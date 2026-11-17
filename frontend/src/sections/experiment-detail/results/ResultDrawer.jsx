import { promptLinkOf } from "src/sections/common/RunSettings/targetLinks";
import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import PropTypes from "prop-types";
import { Box, Button, IconButton, Stack, Typography } from "@mui/material";
import { useLocation, useNavigate } from "react-router-dom";
import Iconify from "src/components/iconify";
import RowPanelShell, {
  PanelIconButton,
} from "src/sections/common/RowPanel/RowPanelShell";
import useRowKeyboardNav from "src/sections/common/RowPanel/useRowKeyboardNav";
import ShortcutSheet from "src/sections/common/RowPanel/ShortcutSheet";
import SplitChips from "src/sections/common/Datasets/SplitChips";
import {
  rowLabel,
  useDatasetKind,
} from "src/sections/common/Datasets/datasetKind";
import RunChip from "src/sections/common/Runs/RunChip";
import useValueViewMode from "src/sections/common/ValueViewer/useValueViewMode";
import { useResultRow } from "src/api/develop/run-results";
import { useRunConfigs } from "src/api/develop/r2";
import { isInGrid, isTextInput } from "src/utils/keyboard-guards";
import { useGetTraceDetail } from "src/api/project/trace-detail";
import { flattenTrace } from "src/sections/common/Align/normalize";
import { taskCompareHref } from "src/sections/common/Analysis/analysisLinks";
import KeyHintTooltip from "src/sections/common/ux/KeyHintTooltip";
import useShortcuts from "src/sections/common/ux/useShortcuts";
import notify from "src/sections/common/ux/toast";
import { copyText } from "src/sections/common/ux/CopyButton";
import { fmtCount } from "src/sections/common/format/fmt";
import ContextMenu, {
  useContextMenu,
} from "src/sections/common/ux/oneview/ContextMenu";
import { useRegisterCommands } from "src/sections/common/CommandPalette/commandRegistry";
import { useRootCauses } from "../matrix/useMatrixData";
import { returnedValue } from "../matrix/MatrixHoverCard";
import { primaryPassMetric, trialOutcome } from "./resultsSimple";
import { missingToolsOf, whyLine } from "./drawer/whyLine";
import { listDirection } from "./triageRun";
import {
  RESULT_DRAWER_TABS,
  resultDrawerTab,
  visibleResultDrawerTabs,
} from "./drawer/ResultDrawerTabs";
import { DIFF_MODES } from "./useResultsUrlState";
import { useLayoutPrefs } from "src/sections/common/ux/oneview/layoutPrefs";
import IterateFromHereButton from "../iterate/IterateFromHereButton";
import FindBar from "src/sections/common/Find/FindBar";
import {
  clampOverlayWidth,
  overlayDefaultWidth,
  startEdgeDrag,
  useOverlayTop,
} from "./overlayPanel";

const OVERLAY_MIN = 400;
/** S1: below this window width the Trajectory tab opens full width. */
export const TRAJECTORY_FULL_BELOW = 1440;
/** Below the app's menus and popovers (1300), above the grid and bars. */
const OVERLAY_Z = 1150;

// A mouse click leaves focus on the clicked button, which blocks the
// drawer's single-key shortcuts (keyboard-guards). Release it, keeping text
// fields, grid cells and open menus focused.
function releaseFocus(e) {
  if (!e.detail) return;
  const el = document.activeElement;
  if (!el || el === document.body || isTextInput(el) || isInGrid(el)) return;
  if (el.closest?.(".MuiPopover-root, .MuiMenu-root, .MuiDialog-root")) return;
  el.blur();
}

// `D` cycles off → vs expected → vs baseline run (§4.1).
const DIFF_CYCLE = ["off", "expected", "baseline"];

/** integration (RV-09): what a locked tab says while the review is blind. */
const BLIND_TAB_HINT = "Turn Blind off to see which run is which";

/**
 * integration (RV-09): the drawer is locked to the blind pairwise review when
 * the URL says `blind=1`, the Review tab is open on its pairwise card and
 * there are two runs to compare. Pure.
 */
const blindLockOf = ({ blind, tab, reviewKind, runCount }) =>
  Boolean(blind) && tab === "review" && reviewKind !== "rubric" && runCount > 1;

const VERDICT_TONE = {
  pass: "pass",
  fail: "fail",
  flaky: "flaky",
  none: "none",
};

/** Run strip: the baseline fixed, the focused candidate highlighted; each
 * chip carries its pass/fail dot and `2/3 trials` (R3 MUST 8). */
const RunStrip = ({
  runs,
  colors,
  baselineId,
  focusedRunId,
  onFocus,
  outcomes = {},
}) => (
  <Stack
    direction="row"
    alignItems="center"
    gap={0.75}
    sx={{ flexWrap: "wrap" }}
  >
    {runs.map((run) => {
      const focused = run.run_id === focusedRunId;
      const isBase = run.run_id === baselineId && runs.length > 1;
      const o = outcomes[run.run_id];
      return (
        <Stack
          key={run.run_id}
          direction="row"
          alignItems="center"
          gap={0.5}
          data-testid="drawer-run-score"
          sx={{
            borderRadius: 0.75,
            outline: focused && !isBase ? "2px solid" : "none",
            outlineColor: "primary.main",
            outlineOffset: 1,
          }}
        >
          <RunChip
            run={run}
            color={colors[run.run_id]}
            isBaseline={isBase}
            showStatus={false}
            verdict={
              outcomes[run.run_id] && outcomes[run.run_id].tone !== "none"
                ? {
                    tone: VERDICT_TONE[outcomes[run.run_id].tone],
                    label: `${run.label}: ${outcomes[run.run_id].label}`,
                  }
                : undefined
            }
            onClick={isBase ? undefined : () => onFocus(run.run_id)}
            tooltipPlacement="bottom"
            // One line: with more than 3 runs the chips are labels (the
            // name is on hover and on each output card below).
            showName={runs.length <= 3}
          />
          {/* IT-R5 G3 (P5): each run's score on the primary eval, in
              view at 1280 (`0/3`, `Pass`). */}
          {o && o.tone !== "none" ? (
            <Typography
              typography="s2"
              fontWeight={600}
              sx={{
                pr: 0.5,
                fontVariantNumeric: "tabular-nums",
                whiteSpace: "nowrap",
                color:
                  o.tone === "pass"
                    ? "green.700"
                    : o.tone === "fail"
                      ? "red.700"
                      : "amber.700",
              }}
            >
              {o.n > 1 ? `${o.pass}/${o.n}` : o.label}
            </Typography>
          ) : null}
        </Stack>
      );
    })}
  </Stack>
);

RunStrip.propTypes = {
  outcomes: PropTypes.object,
  runs: PropTypes.array,
  colors: PropTypes.object,
  baselineId: PropTypes.string,
  focusedRunId: PropTypes.string,
  onFocus: PropTypes.func,
};

/**
 * Result drawer (PRODUCT-SPEC §3.11): RowPanelShell as an overlay on the
 * right below the page header (iterate r1 G3 P9: the grid keeps its widths,
 * drag the left edge to resize, remembered; one scroller, sticky footer
 * `Test a fix on #2G from here…`), header `Row 57 · ID ⧉ · splits · 3 of 7 · ▲ ▼ · Open in dataset ↗ ·
 * Expand · ✕`, run strip, tabs Compare | Trajectory | Trace | Review.
 * Prev/next follow R3 `neighbors` (the filtered, sorted set).
 */
const ResultDrawer = ({
  datasetId,
  view,
  runs,
  runsById,
  colors,
  baselineId,
  summary,
  readOnly,
  url,
  mode,
  grid,
  rowIds,
  findPrimaryInput,
  onFilterRows,
}) => {
  const navigate = useNavigate();
  const { pathname, search: locationSearch } = useLocation();
  const [full, setFull] = useState(false);
  const [help, setHelp] = useState(false);
  // iterate r1 G3 (P9): the drawer is an overlay on the right, below the
  // page header; the grid behind keeps its widths. Width remembered.
  const rootRef = useRef(null);
  const [drawerPrefs, patchDrawerPrefs] = useLayoutPrefs("resultDrawer");
  const [dragW, setDragW] = useState(null);
  const moreMenu = useContextMenu();
  const [format, setFormat] = useValueViewMode();
  const open = !!url.row;
  const overlayTop = useOverlayTop(rootRef, open);
  const savedW = drawerPrefs.extras?.width;
  const panelW = clampOverlayWidth(
    dragW ??
      (Number.isFinite(savedW)
        ? savedW
        : overlayDefaultWidth(window.innerWidth)),
    window.innerWidth,
    OVERLAY_MIN,
  );
  const [rootLeft, setRootLeft] = useState(0);
  useEffect(() => {
    if (!open) return undefined;
    setRootLeft(Math.round(rootRef.current?.getBoundingClientRect().left ?? 0));
    // iterate r2 G3 (P10): the panel measures its room on resize; after
    // Expand / Restore it re-measures, so the trace gets the full width.
    const t = setTimeout(() => window.dispatchEvent(new Event("resize")), 60);
    return () => clearTimeout(t);
  }, [open, full, dragW]);
  const { data: row, isFetching } = useResultRow(datasetId, view, url.row, {
    enabled: open,
  });
  // integration (RV-09): read by the tab-hint effect (set below, each render).
  const blindLockRef = useRef(false);
  // iterate r2 G3 (P10): each tab says what it is for, on hover (the
  // shell's tab labels are text, so the hint is the tab's own title).
  useEffect(() => {
    if (!open) return undefined;
    const t = setTimeout(() => {
      RESULT_DRAWER_TABS.forEach((tab) => {
        document
          .querySelectorAll(`[data-testid="row-tab-${tab.value}"]`)
          .forEach((el) => {
            // integration (RV-09): a tab locked by Blind says why.
            const locked = blindLockRef.current && tab.value !== "review";
            if (locked) el.setAttribute("title", BLIND_TAB_HINT);
            else if (tab.hint) el.setAttribute("title", tab.hint);
            if (locked) el.setAttribute("aria-disabled", "true");
            else el.removeAttribute("aria-disabled");
          });
      });
    }, 0);
    return () => clearTimeout(t);
  });

  const candidates = runs.filter((r) => r.run_id !== baselineId);
  // IT-R4 G3 (P5): the page's candidate (`cand=`) before the first other run.
  const pageCand = (() => {
    const c = new URLSearchParams(locationSearch).get("cand");
    return c && c !== baselineId && runsById[c] ? c : null;
  })();
  const focusedRunId =
    (url.run && runsById[url.run] && url.run) ||
    pageCand ||
    candidates[0]?.run_id ||
    baselineId;
  // The canonical run has no kind; `when(run)` reads R1 `include=config`
  // (kind, config) for the focused run (shared query, usually cached).
  const { data: runConfigs } = useRunConfigs(datasetId, { enabled: open });
  const focusedRun = useMemo(() => {
    const base = runsById[focusedRunId];
    const withConfig = (runConfigs?.results ?? []).find(
      (r) => r.run_id === focusedRunId,
    );
    return base && withConfig
      ? {
          ...base,
          kind: withConfig.kind,
          config: withConfig.config,
          source: withConfig.source,
        }
      : base;
  }, [runsById, focusedRunId, runConfigs]);
  // ------------------------------------------------ why line (R3 MUST 8)
  const primary = primaryPassMetric(summary?.metrics);
  const outcomes = useMemo(
    () =>
      Object.fromEntries(
        runs.map((r) => [
          r.run_id,
          trialOutcome(row?.results?.[r.run_id], primary?.key),
        ]),
      ),
    [runs, row, primary],
  );
  const rootQ = useRootCauses(
    { datasetId, runIds: runs.map((r) => r.run_id), baselineId },
    { enabled: open },
  );
  // iterate r3 G3 (P1): opened from a Worse than list, the line is about
  // the run that got worse here (`Regression in #7B: …`).
  const regression =
    listDirection({ show: view.show, pills: view.pills }) === "regressed";
  const defsByKey = useMemo(
    () =>
      Object.fromEntries(
        [
          ...(summary?.metrics ?? []),
          ...(summary?.human_metrics ?? []),
          ...(summary?.system_metrics ?? []),
        ].map((d) => [d.key, d]),
      ),
    [summary],
  );
  // IT-R5 G3 (S2): each run's attached tools (R1 config), so the why line
  // says `cancel_order is not attached to this prompt` vs `lookup_order was
  // attached but not called`.
  const toolsByRun = useMemo(
    () =>
      Object.fromEntries(
        (runConfigs?.results ?? [])
          .filter((r) => Array.isArray(r.config?.tools))
          .map((r) => [
            r.run_id,
            {
              names: r.config.tools.map((t) => t?.name).filter(Boolean),
              noun: r.kind === "prompt" ? "prompt" : "run",
            },
          ]),
      ),
    [runConfigs],
  );
  const whyArgs = useMemo(
    () => ({
      row,
      runs,
      focusedRunId,
      baselineId,
      primaryKey: primary?.key,
      causes: rootQ.data?.causes ?? [],
      regression,
      defsByKey,
      trial: url.trial ?? 1,
      candidateId: pageCand,
      toolsByRun,
    }),
    [
      pageCand,
      row,
      runs,
      focusedRunId,
      baselineId,
      primary,
      rootQ.data,
      regression,
      defsByKey,
      url.trial,
      toolsByRun,
    ],
  );
  const firstWhy = useMemo(
    () => (row ? whyLine(whyArgs) : null),
    [row, whyArgs],
  );
  // Tabs with `when(run)` (Artifacts) show only for the focused run they
  // apply to; an inapplicable `dtab=` falls back to Compare (A3-12: every
  // tab that applies, one view).
  const drawerTabs = visibleResultDrawerTabs(focusedRun ?? null);
  // IT-R5 G3 (P5): a row the focused run failed on a tool call or at a
  // step opens on Trajectory (where it went wrong); else on Compare.
  const autoTrajectory = useMemo(() => {
    const res = row?.results?.[focusedRunId];
    if (!res || !["fail", "flaky"].includes(outcomes[focusedRunId]?.tone))
      return false;
    const toolErrors = [res, ...(res.trials_detail ?? [])].some(
      (t) => (t?.steps?.tool_errors ?? 0) > 0,
    );
    // The why line names the failing step (`step 1, classify_intent`).
    const atStep = Boolean(firstWhy?.marker);
    return toolErrors || atStep;
  }, [row, focusedRunId, outcomes, firstWhy]);
  const tab = resultDrawerTab(
    url.dtabNamed || !autoTrajectory ? url.dtab : "trajectory",
    focusedRun ?? null,
  ).value;
  // `dtab=` for a tab pick: Compare is the default unless the row opens on
  // Trajectory (then Compare is named in the URL).
  const dtabOf = (v) => (v === "compare" && !autoTrajectory ? null : v);
  // integration (RV-09): `blind=1` (the Review tab's Blind switch) hides which
  // run is which: the pairwise card, the header and the other tabs.
  const blind = new URLSearchParams(locationSearch).get("blind") === "1";
  const [reviewKind, setReviewKind] = useState(null);
  const blindLock = blindLockOf({
    blind,
    tab,
    reviewKind,
    runCount: runs.length,
  });
  const pickTab = (v, extra = {}) => {
    if (blindLock && v !== "review") return;
    url.patch({ dtab: dtabOf(v), ...extra });
  };
  // S1 (iterate r3 G3): below 1440 px wide the Trajectory tab opens the
  // drawer full width (the steps need the room); Restore there is
  // remembered (`extras.trajectoryFull`), and leaving the tab puts back
  // the side panel when the tab widened it.
  const autoFull = useRef(false);
  const trajectoryFullPref = drawerPrefs.extras?.trajectoryFull !== false;
  useEffect(() => {
    if (!open) return;
    const narrow = window.innerWidth < TRAJECTORY_FULL_BELOW;
    if (tab === "trajectory" && narrow && trajectoryFullPref && !full) {
      autoFull.current = true;
      setFull(true);
    } else if (tab !== "trajectory" && autoFull.current) {
      autoFull.current = false;
      setFull(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, open, trajectoryFullPref]);
  const toggleFull = useCallback(() => {
    const next = !full;
    if (tab === "trajectory" && window.innerWidth < TRAJECTORY_FULL_BELOW) {
      autoFull.current = next;
      patchDrawerPrefs({ extras: { trajectoryFull: next } });
    } else autoFull.current = false;
    setFull(next);
  }, [full, tab, patchDrawerPrefs]);

  // P10: the failing run's trace (the trajectory's own query, shared
  // cache) — its first wrong step, else the first error it hit.
  const whyTraceId =
    firstWhy?.subjectId &&
    (firstWhy.failing || ["fail", "flaky"].includes(firstWhy.tone))
      ? row?.results?.[firstWhy.subjectId]?.trace_ids?.[
          Math.max(
            0,
            firstWhy.subjectId === focusedRunId ? (url.trial ?? 1) - 1 : 0,
          )
        ] ??
        row?.results?.[firstWhy.subjectId]?.trace_ids?.[0] ??
        null
      : null;
  const whyTrace = useGetTraceDetail(whyTraceId);
  const returned = useMemo(() => {
    if (!whyTrace.data || !firstWhy?.marker) return null;
    const step = flattenTrace(whyTrace.data).find(
      (st) => st.name === firstWhy.marker.stepName,
    );
    return returnedValue(step?.output);
  }, [whyTrace.data, firstWhy]);
  // `lookup_order → 404 order 6118 not found (expected an id like A-6118)`
  const firstError = useMemo(() => {
    if (!whyTrace.data) return null;
    const step = flattenTrace(whyTrace.data).find((st) => st.error);
    if (!step) return null;
    const msg = String(step.error)
      .replace(/^ERROR\s+/i, "")
      .trim();
    return msg ? `${step.name} → ${msg}` : null;
  }, [whyTrace.data]);
  // IT-R5 G3 (S2): `[Attach it]` opens the prompt when the focused prompt
  // run lacks a tool the row expects.
  const attachMissing = useMemo(
    () =>
      row && focusedRun?.kind === "prompt"
        ? missingToolsOf(
            row.results?.[focusedRunId],
            row.expected,
            url.trial ?? 1,
            toolsByRun[focusedRunId],
          )
        : [],
    [row, focusedRun, focusedRunId, url.trial, toolsByRun],
  );
  const promptId =
    focusedRun?.target?.prompt_id ?? focusedRun?.config?.prompt?.id ?? null;
  const attachHref =
    attachMissing.length && promptId
      ? promptLinkOf(focusedRun?.target, { config: focusedRun?.config })
      : null;
  const why = useMemo(
    () => (row && returned ? whyLine({ ...whyArgs, returned }) : firstWhy),
    [row, returned, whyArgs, firstWhy],
  );

  // ---------------------------------------- side by side (R3 MUST 4, `O`)
  const sideBySideLanes = useMemo(() => {
    const live = runs.filter((r) => !r.not_found).map((r) => r.run_id);
    const tone = outcomes[focusedRunId]?.tone;
    const lanes = [baselineId, focusedRunId].filter(
      (id, i, a) => id && live.includes(id) && a.indexOf(id) === i,
    );
    const disagree = live.filter(
      (id) => !lanes.includes(id) && outcomes[id]?.tone !== tone,
    );
    const rest = live.filter(
      (id) => !lanes.includes(id) && !disagree.includes(id),
    );
    return [...lanes, ...disagree, ...rest].slice(0, 4);
  }, [runs, outcomes, focusedRunId, baselineId]);
  const openSideBySide = useCallback(() => {
    if (!url.row) return;
    navigate(
      taskCompareHref({
        datasetId,
        runIds: sideBySideLanes,
        baselineId,
        rowId: url.row,
        trials:
          url.trial > 1 && focusedRunId
            ? { [focusedRunId]: url.trial }
            : undefined,
        from: `${pathname}${locationSearch}`,
      }),
    );
  }, [
    url.row,
    url.trial,
    navigate,
    datasetId,
    sideBySideLanes,
    baselineId,
    focusedRunId,
    pathname,
    locationSearch,
  ]);

  const close = useCallback(() => {
    setFull(false);
    url.patch({ row: null, run: null, dtab: null, trial: null, atab: null });
  }, [url]);
  const go = useCallback(
    (exampleId) => exampleId && url.patch({ row: exampleId, trial: null }),
    [url],
  );
  const neighbors = row?.neighbors;
  // P6: on Review the next row is fetched ahead, so a vote's advance is
  // instant.
  useResultRow(datasetId, view, neighbors?.next, {
    enabled: open && url.dtab === "review" && Boolean(neighbors?.next),
  });
  // iterate r3 G3 (P4): keys never drop. Each press moves from the last
  // target (not from the row still loading), over the rows in view in the
  // grid's order, so a held J scans and 6 quick presses move 6 rows.
  const target = useRef(url.row);
  useEffect(() => {
    target.current = url.row;
  }, [url.row]);
  const stepRow = useCallback(
    (delta, { to } = {}) => {
      const ids = rowIds ?? [];
      const cur = target.current ?? url.row;
      const i = ids.indexOf(cur);
      if (i < 0 || !ids.length) {
        const id = delta > 0 ? neighbors?.next : neighbors?.prev;
        if (id) {
          target.current = id;
          go(id);
        }
        return;
      }
      const j =
        to === "first"
          ? 0
          : to === "last"
            ? ids.length - 1
            : Math.max(0, Math.min(ids.length - 1, i + delta));
      if (j === i) return;
      target.current = ids[j];
      go(ids[j]);
    },
    [rowIds, url.row, neighbors, go],
  );
  const next = useCallback(() => stepRow(1), [stepRow]);
  const prev = useCallback(() => stepRow(-1), [stepRow]);
  useShortcuts(
    "drawer",
    {
      first: () => stepRow(0, { to: "first" }),
      last: () => stepRow(0, { to: "last" }),
      pageDown: () => stepRow(10),
      pageUp: () => stepRow(-10),
    },
    { enabled: open, register: false },
  );
  const focusRun = useCallback(
    (runId) => url.patch({ run: runId, trial: null }),
    [url],
  );
  const stepCandidate = useCallback(
    (step) => {
      if (!candidates.length) return;
      const i = candidates.findIndex((r) => r.run_id === focusedRunId);
      const nextIdx = (i + step + candidates.length) % candidates.length;
      focusRun(candidates[nextIdx].run_id);
    },
    [candidates, focusedRunId, focusRun],
  );
  const trials = row?.results?.[focusedRunId]?.trials ?? 1;
  const setTrial = useCallback(
    (t) => url.patch({ trial: t > 1 ? t : null }),
    [url],
  );
  const link = useMemo(
    () =>
      `${window.location.origin}${window.location.pathname}${window.location.search}`,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [url.row, url.run, url.dtab],
  );
  const { kind: datasetKind } = useDatasetKind(datasetId);
  const rowName = row ? rowLabel(datasetKind, row.row_order) : "Row";
  const copyLink = useCallback(async () => {
    const ok = await copyText(window.location.href);
    notify(
      ok
        ? `Link copied: ${rowName}${!blindLock && focusedRunId && runsById[focusedRunId] ? ` · ${runsById[focusedRunId].label}` : ""}${url.dtab ? ` · ${url.dtab[0].toUpperCase()}${url.dtab.slice(1)}` : ""}`
        : "Couldn't copy the link. Copy it from the address bar.",
      { key: "drawer-copy-link", variant: ok ? "success" : "error" },
    );
  }, [rowName, focusedRunId, runsById, url.dtab, blindLock]);

  useRowKeyboardNav({
    enabled: open,
    surface: "drawer",
    onNext: next,
    onPrev: prev,
    onClose: () => (full && !autoFull.current ? setFull(false) : close()),
    onFull: toggleFull,
    keys: {
      prevCandidate: () => stepCandidate(-1),
      nextCandidate: () => stepCandidate(1),
      prevTrial: () => url.trial > 1 && setTrial(url.trial - 1),
      nextTrial: () => url.trial < trials && setTrial(url.trial + 1),
      diff: () => {
        const i = DIFF_CYCLE.indexOf(url.diff);
        let nextMode = DIFF_CYCLE[(i + 1) % DIFF_CYCLE.length];
        if (nextMode === "baseline" && runs.length < 2) nextMode = "off";
        url.patch({ diff: nextMode === "off" ? null : nextMode });
      },
      raw: () => setFormat(format === "raw" ? "formatted" : "raw"),
      copyLink,
      help: () => setHelp(true),
      trajectoryTab: () => pickTab("trajectory", { atab: null }),
      openSideBySide: () => (blindLock ? undefined : openSideBySide()),
    },
  });

  const exampleSplits = (row?.splits ?? []).map(
    (name) =>
      (summary?.splits ?? []).find((s) => s.name === name) ?? {
        id: name,
        name,
        color: "default",
      },
  );
  const rowVersion = runsById[baselineId]?.dataset_version?.number;
  const openInDataset = () =>
    navigate(
      `/dashboard/develop/${datasetId}?tab=data&row=${row.example_id}${rowVersion ? `&version=${rowVersion}` : ""}`,
    );

  // IT-R5 G3 (P5): the row id lives in ⋯ (`Copy ID`), not in the header.
  const copyRowId = async () => {
    const ok = await copyText(row?.example_id ?? "");
    notify(ok ? `ID copied: ${row?.example_id}` : "Couldn't copy the ID.", {
      key: "drawer-copy-id",
      variant: ok ? "success" : "error",
    });
  };
  const rowMenuItems = [
    {
      id: "copy-id",
      label: "Copy ID",
      icon: "mdi:content-copy",
      run: copyRowId,
    },
    {
      id: "dataset",
      label: "Open in dataset",
      icon: "mdi:arrow-top-right",
      run: openInDataset,
    },
    {
      id: "link",
      label: "Copy link to this row",
      icon: "mdi:link-variant",
      shortcut: "copyLink",
      run: copyLink,
    },
    {
      id: "keys",
      label: "Keyboard shortcuts",
      icon: "mdi:keyboard-outline",
      shortcut: "help",
      run: () => setHelp(true),
    },
  ];
  useRegisterCommands(
    "result-drawer-row",
    open && row
      ? [
          {
            id: "drawer.openInDataset",
            label: "Open in dataset",
            keywords: ["row", "dataset", "edit row"],
            icon: "mdi:arrow-top-right",
            run: openInDataset,
          },
          {
            id: "drawer.copyLink",
            label: "Copy link to this row",
            keywords: ["share", "url"],
            icon: "mdi:link-variant",
            shortcut: "copyLink",
            run: copyLink,
          },
        ]
      : [],
    [open, row?.example_id],
  );

  blindLockRef.current = blindLock;
  const header = {
    title: rowName,
    position: neighbors
      ? {
          index: neighbors.position,
          total: neighbors.total,
          filtered:
            (view.pills?.length ?? 0) > 0 || (view.show && view.show !== "all"),
        }
      : undefined,
    onPrev: prev,
    onNext: next,
    prevDisabled: !neighbors?.prev,
    nextDisabled: !neighbors?.next,
    idChip:
      row && exampleSplits.length > 0 ? (
        // One line: when the panel is narrow the split chips drop whole onto
        // a clipped second line instead of being cut mid-name.
        <Stack
          direction="row"
          alignItems="center"
          gap={0.75}
          sx={{ minWidth: 0, flexWrap: "wrap", height: 24, rowGap: 1 }}
        >
          <SplitChips splits={exampleSplits} max={3} />
        </Stack>
      ) : null,
    actions: row ? (
      <Stack
        data-blind={blindLock ? "1" : undefined}
        direction="row"
        alignItems="center"
        gap={0.25}
        sx={{ flexShrink: 0 }}
      >
        {/* integration (RV-09): side by side names every run's lane. */}
        {blindLock ? null : (
          <KeyHintTooltip
            title="Open side by side: every run's steps in lanes"
            action="openSideBySide"
          >
            <Button
              size="small"
              variant="outlined"
              onClick={openSideBySide}
              startIcon={<Iconify icon="mdi:compare-horizontal" width={16} />}
              sx={{ typography: "s2", whiteSpace: "nowrap", mr: 0.5 }}
              data-testid="drawer-side-by-side"
            >
              Side by side
            </Button>
          </KeyHintTooltip>
        )}
        {/* One view: the row's secondary actions in one ⋯ (each keeps its
            key and is in ⌘K). */}
        <KeyHintTooltip title="More row actions">
          <IconButton
            size="small"
            aria-label="More row actions"
            aria-haspopup="menu"
            onClick={(e) => moreMenu.open(e.currentTarget, rowMenuItems)}
            sx={{ p: 0.5, color: "text.secondary" }}
            data-testid="drawer-more"
          >
            <Iconify icon="mdi:dots-horizontal" width={18} />
          </IconButton>
        </KeyHintTooltip>
        <ContextMenu {...moreMenu.menuProps} />
        <PanelIconButton
          title={full ? "Restore" : "Expand"}
          hintTitle={full ? "Back to the side panel" : "Expand to full width"}
          action="full"
          label={full ? "Restore" : "Expand"}
          icon={full ? "mdi:arrow-collapse" : "mdi:arrow-expand"}
          onClick={toggleFull}
        />
      </Stack>
    ) : null,
    // integration (RV-09): blind, the run chips, scores and why line give way
    // to one line that names no run.
    line2:
      row && blindLock ? (
        <Stack
          direction="row"
          alignItems="center"
          gap={0.75}
          data-testid="drawer-blind-line"
          sx={{ minWidth: 0, flexBasis: "100%" }}
        >
          <Iconify
            icon="mdi:eye-off-outline"
            width={18}
            sx={{ color: "text.secondary" }}
          />
          <Typography typography="s1" fontWeight="fontWeightMedium">
            Blind review · Response 1 vs Response 2
          </Typography>
        </Stack>
      ) : row && (why?.text || runs.length > 1) ? (
        // Its own full-width block: the why line never shares a row with the
        // run chips, and the chips' hover (copy id, tooltip) opens below.
        // IT-R5 G3 (P5): the verdict and the run chips (with each run's
        // score) share a line when there is room; else the chips sit
        // directly under the verdict.
        <Stack
          direction="row"
          flexWrap="wrap"
          alignItems="flex-start"
          gap={1}
          sx={{ minWidth: 0, flexBasis: "100%", rowGap: 1 }}
        >
          {why?.text ? (
            <Stack
              direction="row"
              alignItems="flex-start"
              gap={0.75}
              data-testid="drawer-why"
              sx={{ flex: "1 1 320px", minWidth: 0 }}
            >
              <Iconify
                icon={
                  why.tone === "pass"
                    ? "mdi:check-circle"
                    : why.tone === "flaky"
                      ? "mdi:circle-half-full"
                      : "mdi:close-circle"
                }
                width={18}
                sx={{
                  mt: "1px",
                  flexShrink: 0,
                  color:
                    why.tone === "pass"
                      ? "green.600"
                      : why.tone === "flaky"
                        ? "amber.500"
                        : "red.600",
                }}
              />
              <Stack sx={{ minWidth: 0 }}>
                <Typography typography="s1" fontWeight="fontWeightMedium">
                  {why.text}
                </Typography>
                {attachHref ? (
                  <Typography
                    typography="s2"
                    color="text.secondary"
                    data-testid="drawer-why-attach"
                    sx={{ mt: 0.25 }}
                  >
                    {`${attachMissing.join(", ")} ${attachMissing.length === 1 ? "is" : "are"} not on this prompt. `}
                    <Box
                      component="button"
                      type="button"
                      onClick={() => navigate(attachHref)}
                      sx={{
                        p: 0,
                        border: 0,
                        bgcolor: "transparent",
                        color: "primary.main",
                        typography: "s2",
                        fontWeight: 600,
                        textDecoration: "underline",
                        cursor: "pointer",
                      }}
                    >
                      Attach it
                    </Box>
                  </Typography>
                ) : null}
                {why.tone !== "pass" && firstError ? (
                  <Typography
                    typography="s2"
                    color="text.secondary"
                    data-testid="drawer-why-error"
                    sx={{ mt: 0.25 }}
                  >
                    {"First error: "}
                    <Box
                      component="span"
                      sx={{
                        fontFamily: "monospace",
                        color: "red.700",
                        wordBreak: "break-word",
                      }}
                    >
                      {firstError}
                    </Box>
                  </Typography>
                ) : null}
              </Stack>
            </Stack>
          ) : null}
          {runs.length > 1 && (
            <RunStrip
              runs={runs}
              colors={colors}
              baselineId={baselineId}
              focusedRunId={focusedRunId}
              onFocus={focusRun}
              outcomes={outcomes}
            />
          )}
        </Stack>
      ) : null,
  };

  const TabComponent = drawerTabs.find((t) => t.value === tab)?.Component;

  // P9: the next step sits in the sticky footer on every tab but Review
  // (Review has its own vote bar).
  const footer =
    row && focusedRunId && tab !== "review" ? (
      <IterateFromHereButton
        runId={focusedRunId}
        trial={url.trial}
        rowId={row.example_id}
      />
    ) : null;

  return (
    <Box
      ref={rootRef}
      onClick={releaseFocus}
      sx={{
        flex: 1,
        minHeight: 0,
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
      }}
    >
      {/* The grid never remounts and never narrows: the drawer covers it. */}
      <Box
        sx={{
          flex: 1,
          minHeight: 0,
          minWidth: 0,
          display: "flex",
          overflow: "hidden",
        }}
      >
        {grid}
      </Box>
      {open ? (
        <Box
          data-testid="result-drawer-overlay"
          data-fa-overlay-panel=""
          data-blind-lock={blindLock ? "1" : undefined}
          sx={{
            // integration (RV-09): the tabs Blind locks look disabled.
            ...(blindLock
              ? {
                  '& [data-testid^="row-tab-"]:not([data-testid="row-tab-review"])':
                    { opacity: 0.4, cursor: "not-allowed" },
                }
              : {}),
            position: "fixed",
            top: overlayTop,
            right: 0,
            bottom: 0,
            width: full
              ? Math.max(panelW, window.innerWidth - rootLeft)
              : panelW,
            maxWidth: "100vw",
            zIndex: OVERLAY_Z,
            display: "flex",
            flexDirection: "column",
            bgcolor: "background.paper",
            borderLeft: "1px solid",
            borderTop: "1px solid",
            borderColor: "divider",
            boxShadow: (t) =>
              `-12px 0 28px ${t.palette.mode === "dark" ? "rgba(0,0,0,0.45)" : "rgba(15,23,42,0.10)"}`,
          }}
        >
          {!full ? (
            <Box
              role="separator"
              aria-orientation="vertical"
              aria-label="Resize the row panel (← / →)"
              tabIndex={0}
              onPointerDown={(e) =>
                startEdgeDrag(e, {
                  width: panelW,
                  min: OVERLAY_MIN,
                  onWidth: setDragW,
                  onDone: (w) => {
                    patchDrawerPrefs({ extras: { width: w } });
                    setDragW(null);
                  },
                })
              }
              onKeyDown={(e) => {
                const step =
                  e.key === "ArrowLeft" ? 40 : e.key === "ArrowRight" ? -40 : 0;
                if (!step) return;
                e.preventDefault();
                e.stopPropagation();
                patchDrawerPrefs({
                  extras: {
                    width: clampOverlayWidth(
                      panelW + step,
                      window.innerWidth,
                      OVERLAY_MIN,
                    ),
                  },
                });
              }}
              sx={{
                position: "absolute",
                left: -3,
                top: 0,
                bottom: 0,
                width: 6,
                cursor: "col-resize",
                zIndex: 2,
                "&:hover, &:focus-visible": {
                  bgcolor: (t) => `${t.palette.primary.main}33`,
                  outline: "none",
                },
              }}
            />
          ) : null}
          <RowPanelShell
            open={open}
            // The panel covers the grid at every width (an overlay):
            // forced full, ends at the window edge, no split handle.
            overlayBelow={Number.MAX_SAFE_INTEGER}
            onClose={close}
            header={header}
            tabs={drawerTabs.map((t) => ({
              value: t.value,
              label:
                t.value === "trajectory" && why?.marker
                  ? `${t.label} · step ${why.marker.stepN}`
                  : t.value === "trace"
                    ? `${t.label}${row?.results?.[focusedRunId]?.trace_ids?.length ? ` · ${fmtCount(row.results[focusedRunId].trace_ids.length, "trial")}` : ""}`
                    : t.label,
            }))}
            tab={tab}
            onTab={(v) => pickTab(v, { atab: null })}
            footer={footer}
            grid={null}
          >
            <FindBar
              scope='[data-testid="row-panel"]'
              label="Find in this result"
              primaryInput={findPrimaryInput ?? undefined}
              onFilterRows={onFilterRows}
            />
            {!row ? (
              <Typography typography="s2" color="text.secondary" sx={{ p: 2 }}>
                {isFetching ? "Loading row…" : "This row is not in these runs"}
              </Typography>
            ) : (
              TabComponent && (
                <TabComponent
                  datasetId={datasetId}
                  row={row}
                  runs={runs}
                  baselineId={baselineId}
                  focusedRunId={focusedRunId}
                  trial={Math.min(url.trial, trials)}
                  blind={blind}
                  onReviewKind={setReviewKind}
                  onNext={neighbors?.next ? next : undefined}
                  // L6 extras (not in the frozen prop types; other tabs ignore them)
                  summary={summary}
                  colors={colors}
                  diff={DIFF_MODES.includes(url.diff) ? url.diff : "off"}
                  onDiffChange={(d) =>
                    url.patch({ diff: d === "off" ? null : d })
                  }
                  onTrialChange={setTrial}
                  onFocusRun={focusRun}
                  onOpenTab={(v, extra = {}) => pickTab(v, extra)}
                  readOnly={readOnly}
                  mode={mode}
                  link={link}
                  focusedRun={focusedRun}
                  rowIds={rowIds}
                  onClose={close}
                />
              )
            )}
          </RowPanelShell>
        </Box>
      ) : null}
      <ShortcutSheet
        open={help}
        onClose={() => setHelp(false)}
        surface="drawer"
      />
    </Box>
  );
};

ResultDrawer.propTypes = {
  datasetId: PropTypes.string.isRequired,
  view: PropTypes.object.isRequired,
  runs: PropTypes.array.isRequired,
  runsById: PropTypes.object.isRequired,
  colors: PropTypes.object.isRequired,
  baselineId: PropTypes.string,
  summary: PropTypes.object,
  readOnly: PropTypes.bool,
  url: PropTypes.object.isRequired,
  mode: PropTypes.string,
  grid: PropTypes.node.isRequired,
  /** P6: the rows in view, in order (Review's end-of-queue tally). */
  rowIds: PropTypes.arrayOf(PropTypes.string),
  /** W5 C4: the input field Find's "Filter rows with this" also reads. */
  findPrimaryInput: PropTypes.string,
  /** W5 C4: adds an expression pill to the page's filters. */
  onFilterRows: PropTypes.func,
};

export default ResultDrawer;
