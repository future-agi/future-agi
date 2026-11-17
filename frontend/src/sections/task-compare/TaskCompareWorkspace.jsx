import React, {
  Fragment,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import PropTypes from "prop-types";
import { useLocation, useNavigate } from "react-router-dom";
import {
  Box,
  Button,
  Skeleton,
  Stack,
  Typography,
  useMediaQuery,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { useAuthContext } from "src/auth/hooks";
import { ROLES } from "src/utils/rolePermissionMapping";
import { useHideFab } from "src/sections/common/ux/useFabGutter";
import { useAskFalcon } from "src/sections/common/CommandPalette/CommandPalette";
import { iterateHref } from "src/sections/common/Analysis/analysisLinks";
import { openAddData } from "src/sections/common/AddData/addDataStore";
import { copyText } from "src/sections/common/ux/CopyButton";
import notify from "src/sections/common/ux/toast";
import EmptyState from "src/sections/common/ux/EmptyState";
import ErrorState from "src/sections/common/ux/ErrorState";
import {
  useLayoutPrefs,
  useOutline,
  useViewportDensity,
} from "src/sections/common/ux/oneview";
import {
  howAlignedHelp,
  stepNumbersHelp,
} from "src/sections/common/Align/milestones";
import TraceDetailDrawerV2 from "src/components/traceDetail/TraceDetailDrawerV2";
import TaskCompareBoundary, { useBoundaryReport } from "./TaskCompareBoundary";
import useTaskCompareState from "./useTaskCompareState";
import useWorkspaceData from "./useWorkspaceData";
import useWorkspaceActions from "./useWorkspaceActions";
import TopBar from "./TopBar";
import SummaryCard from "./SummaryCard";
import {
  firstWrongNote,
  laneNames,
  resultSummary,
  storyGrid,
  summarySentence,
  whyLine,
  differentlyLine,
} from "./storyGrid";
import TaskRail, { RAIL_COLLAPSED, RAIL_WIDTH } from "./TaskRail";
import LanePicker, { sortLaneIds } from "./LanePicker";
import LaneHeader from "./LaneHeader";
import RunStrip from "./RunStrip";
import MapStrip from "./MapStrip";
import StepsView from "./StepsView";
import InspectBody from "./RawView";
import { InspectSheet, InspectStrip, INSPECT_MIN_H } from "./InspectPanel";
import BottomDock from "./BottomDock";
import VerdictBar from "./VerdictBar";
import { NotesPanel } from "./DockPanels";
import CardsView from "./CardsView";
import TcViewMenu, { TC_VIEW_DEFAULTS } from "./TcViewMenu";
import { laneTemplate } from "./LanesGrid";
import { maxLanesFor, stepsMatchText, trialCaption } from "./workspaceModel";
import { cellSteps, stepRows } from "./stepModel";
import { depthOf, gapId, inspectTabsFor, lineId } from "./oneViewParams";
import ViewSwitch from "src/sections/common/Trajectory/ViewSwitch";
import ModalityLens from "src/sections/common/Lenses/ModalityLens";
import { lensKindOf } from "src/sections/common/Lenses/lensKind";
import useLaneGraphs from "./useLaneGraphs";
import useLaneCalls from "src/sections/common/Lenses/useLaneCalls";
import CallTurnTimeline from "src/sections/common/Lenses/CallTurnTimeline";
import { fmtMs } from "src/sections/common/format/fmt";
import { scoreWhy, voiceChangeLine, voiceWhy } from "./whyModel";
import TcGraphView from "./TcGraphView";
import SpanDetailPane from "src/components/traceDetail/SpanDetailPane";
import { findEntry } from "src/sections/common/Trajectory/useTraceTree";
import { useRootCauses } from "src/sections/experiment-detail/matrix/useMatrixData";
import { fixStepHref } from "./fixStepHref";
import { canIterate } from "src/sections/experiment-detail/iterate/iterateModel";
import { spanIdsByStep } from "src/sections/common/Trajectory/trajectoryMarkers";

// The call lens with its audio lanes and 3 transcript turns (IT-R2 G4 P6).
const CALL_LENS_MIN = 520;
const RAIL_KEY = "fa.taskCompare.railCollapsed";
const ls = {
  get: (k) => {
    try {
      return window.localStorage.getItem(k);
    } catch {
      return null;
    }
  },
  set: (k, v) => {
    try {
      window.localStorage.setItem(k, v);
    } catch {
      /* per-viewer convenience */
    }
  },
};

function BodySkeleton({ n = 3 }) {
  return (
    <Box sx={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: laneTemplate(n),
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        {Array.from({ length: n }).map((_, i) => (
          <Box key={i} sx={{ px: 1.25, py: 0.75, height: 56 }}>
            <Skeleton width="55%" height={20} />
            <Skeleton width="75%" height={18} />
          </Box>
        ))}
      </Box>
      {Array.from({ length: 8 }).map((_, r) => (
        <Box
          key={r}
          sx={{
            display: "grid",
            gridTemplateColumns: laneTemplate(n),
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          {Array.from({ length: n }).map((__, i) => (
            <Box key={i} sx={{ p: 1.25 }}>
              <Skeleton
                width={`${60 + ((r * 7 + i * 13) % 30)}%`}
                height={16}
              />
            </Box>
          ))}
        </Box>
      ))}
    </Box>
  );
}
BodySkeleton.propTypes = { n: PropTypes.number };

/** Width of an element, live (0 until measured). */
function useWidth(ref) {
  const [w, setW] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(([e]) =>
      setW(Math.round(e.contentRect.width)),
    );
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return w;
}
function useHeight(ref, dep) {
  const [h, setH] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(([e]) =>
      setH(Math.round(e.contentRect.height)),
    );
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref, dep]);
  return h;
}

/**
 * TaskCompareWorkspace — Side by side in ONE view (ONE-VIEW-SPEC §7): the
 * plain reading line of each step that opens in place to its exact call,
 * a collapsible Map, one toolbar with a `View ▾` menu, one lane header
 * with hover-card metrics, an Inspect panel (Answers · Checks · Timeline ·
 * Tool calls · Prompts · Workspace · Scores · JSON) and the review bar.
 *
 * Props (frozen):
 * @param {string} props.datasetId
 */
/**
 * Side by side, inside its own error boundary (IT-R3 G4 P1): a render error
 * in one task keeps the app shell and offers `Open raw trace` / `Back to
 * the list`; route, runs and row changes reset it.
 */
export default function TaskCompareWorkspace({ datasetId }) {
  return (
    <TaskCompareBoundary datasetId={datasetId}>
      <Workspace datasetId={datasetId} />
    </TaskCompareBoundary>
  );
}

TaskCompareWorkspace.propTypes = { datasetId: PropTypes.string.isRequired };

function Workspace({ datasetId }) {
  useHideFab(true);
  const { role, user } = useAuthContext();
  const isViewer = role === ROLES.VIEWER || role === ROLES.WORKSPACE_VIEWER;
  const me = user?.name ?? user?.full_name ?? "You";
  const wide = useMediaQuery("(min-width:1440px)");
  const roomy = useMediaQuery("(min-width:1600px)");
  const short = useViewportDensity() === "short";
  const rootRef = useRef(null);
  const rootW = useWidth(rootRef);
  const [railCollapsed, setRailCollapsed] = useState(
    () => ls.get(RAIL_KEY) === "1",
  );
  const [railPinned, setRailPinned] = useState(false);
  const railStrip = wide ? railCollapsed : !railPinned;
  // Default lanes = what fits (A4-15): baseline + the runs that disagree,
  // max(2, floor(lane area / 300)); a URL `runs=` always wins.
  const laneArea =
    (rootW ||
      (typeof window !== "undefined" ? window.innerWidth - 200 : 1080)) -
    (railStrip ? RAIL_COLLAPSED : RAIL_WIDTH);
  const maxLanes = maxLanesFor(laneArea);
  const st = useTaskCompareState();
  const d = useWorkspaceData({ datasetId, st, maxLanes, user: me });
  const [prefs, patchPrefs] = useLayoutPrefs("taskCompare");
  const [focusIdx, setFocusIdx] = useState(1);
  const [traceId, setTraceId] = useState(null);
  const [query, setQuery] = useState("");
  const [laneSort, setLaneSort] = useState("matrix");
  const askFalcon = useAskFalcon();
  const navigate = useNavigate();
  const { params: p, view } = st;
  const lanes = d.lanes;
  // What the boundary's `Open raw trace` opens if a child throws (written
  // during render on purpose: effects never run for a render that threw).
  const report = useBoundaryReport();
  if (report)
    report.current = {
      traceId:
        lanes.find((l) => l.trial?.trace_id)?.trial?.trace_id ??
        d.row?.source?.trace_id ??
        null,
    };
  const names = useMemo(() => laneNames(lanes), [lanes]);
  const rowChecks = d.row?.checks;
  const checkName = (k) =>
    (rowChecks ?? []).find((c) => c.key === k)?.name ?? k;
  const grid = useMemo(
    () =>
      storyGrid({
        rows: d.align?.rows ?? [],
        lanes: lanes.map((l) => ({
          id: l.id,
          story: l.story,
          checks: l.checks,
        })),
        firstWrong: d.marks.firstWrongStep,
      }),
    [d.align, lanes, d.marks],
  );

  // ---- one view state: map, view menu, Inspect panel (remembered)
  const extras = prefs.extras ?? {};
  // IT-R2 G4 P8: the map opens on demand (remembered); closed, it folds
  // into the toolbar as `Map ▸ · First wrong step: 1`.
  const mapOpen = prefs.sections?.map ?? false;
  const toggleMap = () => patchPrefs({ sections: { map: !mapOpen } });
  const tcView = {
    level: p.level,
    align: view.align,
    wrap: view.wrap,
    prompts: view.prompts,
    allTrials: view.allTrials,
    laneMetrics: extras.laneMetrics === "all" ? "all" : "basic",
  };
  const setTcView = (patch) => {
    const { laneMetrics, ...url } = patch;
    if (laneMetrics)
      patchPrefs({
        extras: { laneMetrics: laneMetrics === "all" ? "all" : undefined },
      });
    if (Object.keys(url).length) st.set(url);
  };
  const resetTcView = () => {
    const { laneMetrics, ...url } = TC_VIEW_DEFAULTS;
    st.set(url);
    patchPrefs({
      extras: { laneMetrics: undefined },
      sections: { map: undefined },
    });
  };
  const allInspectTabs = useMemo(() => inspectTabsFor(lanes), [lanes]);
  // Media-only rows show the answers in the steps area (not twice).
  const inspectTabs = useMemo(() => {
    const media = ["image", "audio", "document", "video"].includes(
      lensKindOf(lanes.map((l) => l.trial?.output ?? l.trial?.final_answer)),
    );
    const noSteps = lanes.every((l) => !(l.steps ?? []).length);
    return media && noSteps && lanes.length
      ? allInspectTabs.filter((t) => t.value !== "answers")
      : allInspectTabs;
  }, [allInspectTabs, lanes]);
  const inspectTab = inspectTabs.some((t) => t.value === view.inspect)
    ? view.inspect
    : null;
  const setInspect = (v) => {
    st.set({ inspect: v });
    if (v) patchPrefs({ extras: { inspectTab: v } });
  };
  const toggleInspect = () =>
    setInspect(
      inspectTab
        ? null
        : inspectTabs.some((t) => t.value === extras.inspectTab)
          ? extras.inspectTab
          : inspectTabs[0]?.value ?? null,
    );

  // ---- R4 depth (implementation): Steps (the one view) · Graph · Call. URL
  // `depth=graph|call` beats the user's last choice (layout store, per row
  // kind), which beats the first-visit default (Call on call rows).
  const isCallRow =
    lensKindOf(lanes.map((l) => l.trial?.output ?? l.trial?.final_answer)) ===
    "call";
  // IT-R1 G4: a row whose runs return media (image / audio / document) and
  // recorded no steps opens on the answers, rendered, instead of an empty
  // Steps area.
  const mediaKind = lensKindOf(
    lanes.map((l) => l.trial?.output ?? l.trial?.final_answer),
  );
  const mediaOnly =
    ["image", "audio", "document", "video"].includes(mediaKind) &&
    lanes.length > 0 &&
    lanes.every((l) => !(l.steps ?? []).length);
  const laneCalls = useLaneCalls(
    isCallRow
      ? lanes.map((l) => ({ value: l.trial?.output ?? l.trial?.final_answer }))
      : [],
  );
  const depthKey = isCallRow ? "depthCall" : "depth";
  const depthOptions = isCallRow
    ? ["call", "steps", "graph"]
    : ["steps", "graph"];
  const pickDepth = (v) => (depthOptions.includes(v) ? v : null);
  // IT-R3 G4 P4: Graph is a tab the user picks (URL `depth=graph`), never
  // a remembered default, so `See why` and the rail always land on Steps.
  const storedDepth = pickDepth(extras[depthKey]);
  const depth =
    pickDepth(depthOf(st.sp.get("depth"))) ??
    (storedDepth === "graph" ? null : storedDepth) ??
    (isCallRow ? "call" : "steps");
  const prevDepth = useRef("steps");
  const setDepth = (v) => {
    if (!depthOptions.includes(v)) return;
    if (v !== depth) prevDepth.current = depth;
    st.set({
      depth: v === "steps" ? null : v,
      ...(v === "graph" ? {} : { gsel: null }),
    });
    patchPrefs({ extras: { [depthKey]: v } });
  };
  const laneGraphs = useLaneGraphs({ datasetId, lanes, names });
  // `Open span` on a step → Observe's SpanDetailPane in a right panel.
  const [spanPick, setSpanPick] = useState(null);
  const spanPanel = (() => {
    if (!spanPick || depth !== "steps") return null;
    const g = laneGraphs.graphs[spanPick.l];
    if (!g?.tree) return null;
    const id = spanIdsByStep(g.tree)[spanPick.n];
    const entry = findEntry(g.tree, id);
    return entry ? { entry, tree: g.tree, name: g.name, color: g.color } : null;
  })();
  const gsel = st.sp.get("gsel");
  const gdiffOff = st.sp.get("gdiff") === "0";
  const depthSwitch = (
    <ViewSwitch
      value={depth}
      onChange={setDepth}
      options={depthOptions}
      recommended={laneGraphs.recommended}
      ariaLabel="Side by side view"
    />
  );

  // ---- steps: one line each, opened in place (outline per task + lanes)
  const fw = d.marks.firstWrongStep;
  const stepLanes = useMemo(
    () =>
      lanes.map((l) => ({
        id: l.id,
        steps: l.steps,
        story: l.story,
        checks: l.checks,
        // IT-R5 G4 P4: the trial's own result (its last step shows it).
        outcome: l.trial?.outcome ?? null,
      })),
    [lanes],
  );
  const built = useMemo(() => {
    if (!d.align && view.align !== "none") return null;
    const rows =
      view.align === "none"
        ? [
            {
              id: "all",
              kind: "list",
              cells: lanes.map((l) =>
                l.noTrace ? null : { type: "list", steps: l.steps },
              ),
            },
          ]
        : d.align.rows;
    return stepRows({ rows, lanes: stepLanes, level: p.level, firstWrong: fw });
  }, [d.align, view.align, lanes, stepLanes, p.level, fw]);
  // IT-R4 G4 P4: the other run's first action that the baseline never took
  // (its first "Only in …" group with a tool / retrieval) opens by default.
  const candGapId = useMemo(() => {
    if (!d.align || lanes.length < 2) return null;
    const won = (l) =>
      l.verdict?.scored ? l.verdict.pass === l.verdict.scored : null;
    let ci = lanes.findIndex((l, i) => i > 0 && won(l) !== won(lanes[0]));
    if (ci < 0) ci = 1;
    const ACTION = new Set(["tool", "retrieval", "command", "file_edit"]);
    const row = d.align.rows.find(
      (r) =>
        r.cells[ci]?.type === "gap" &&
        (r.cells[ci].steps ?? []).some((x) => ACTION.has(x.kind)),
    );
    return row ? gapId(row.id, lanes[ci].id) : null;
  }, [d.align, lanes]);
  const outline = useOutline({
    surface: "taskCompare",
    scopeId: `${d.rowId ?? "none"}:${d.laneIds.join(",")}`,
    ids: built?.ids ?? [],
    defaultOpen: (id) =>
      (p.level === "all" && id.startsWith("gap|")) || id === candGapId,
  });
  const openIds = useCallback(
    (ids) => ids.forEach((id) => outline.open(id)),
    [outline],
  );
  const scrollToRow = (rowId) =>
    setTimeout(
      () =>
        document
          .getElementById(`tc-row-${rowId}`)
          ?.scrollIntoView({ block: "center", behavior: "smooth" }),
      60,
    );
  // Open a row in every lane (Shift+click, the map, N / P), or one step.
  const openRow = (rowId, focus) => {
    const row = d.align?.rows.find((r) => r.id === rowId);
    if (!row) return;
    const ids = [];
    lanes.forEach((lane, l) => {
      const cell = row.cells[l];
      if (!cell) return;
      if (cell.type === "gap") ids.push(gapId(row.id, lane.id));
      if (focus) {
        if (focus.laneId === lane.id)
          ids.push(lineId(row.id, lane.id, focus.n));
      } else if (cell.type === "step")
        ids.push(lineId(row.id, lane.id, cell.step.n));
    });
    openIds(ids);
    scrollToRow(row.id);
  };
  const onAcross = (row, l, n) => {
    const ids = [lineId(row.id, lanes[l].id, n)];
    lanes.forEach((lane, o) => {
      const cell = row.cells[o];
      if (cell?.type === "step") ids.push(lineId(row.id, lane.id, cell.step.n));
    });
    openIds(ids);
  };
  const selectStep = (laneId, n) => {
    const li = lanes.findIndex((l) => l.id === laneId);
    const row = (d.align?.rows ?? []).find((r) =>
      cellSteps(r.cells[li]).some((s) => s.n === n),
    );
    if (!row) return;
    st.set({ step: row.id.startsWith("m") ? row.id : null });
    openRow(row.id, { laneId, n });
  };
  const jumpFirstWrong = () => {
    if (fw?.rowId) {
      st.set({ step: fw.rowId.startsWith("m") ? fw.rowId : null });
      openRow(fw.rowId, { laneId: fw.laneId, n: fw.stepN });
    } else if (d.diffRows[0]) {
      st.set({ step: d.diffRows[0] });
      openRow(d.diffRows[0]);
    }
  };
  // An old `depth=steps` link: every step open once the steps are known.
  useEffect(() => {
    if (st.legacyExpandAll && built?.ids.length) {
      outline.expandAll();
      st.clearLegacyExpandAll();
    }
  }, [st.legacyExpandAll, built]); // eslint-disable-line react-hooks/exhaustive-deps

  // From a card (fr-5): once the promoted lanes are aligned, open the
  // first wrong step (highlighted, scrolled into view).
  const jumpOnLoad = useRef(false);
  const jumpRef = useRef(null);
  jumpRef.current = jumpFirstWrong;
  useEffect(() => {
    if (
      !jumpOnLoad.current ||
      d.cards ||
      d.tcQ.isFetching ||
      !built?.ids.length ||
      !fw?.rowId
    )
      return undefined;
    if (!d.laneIds.includes(fw.laneId)) return undefined;
    jumpOnLoad.current = false;
    // after the outline re-scopes to the new lanes
    const t = setTimeout(() => jumpRef.current?.(), 250);
    return () => clearTimeout(t);
  }, [d.cards, built, fw, d.tcQ.isFetching]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (focusIdx > lanes.length - 1) setFocusIdx(Math.max(0, lanes.length - 1));
    if (focusIdx === 0 && lanes.length > 1 && !d.blind) setFocusIdx(1);
  }, [lanes.length]); // eslint-disable-line react-hooks/exhaustive-deps

  const openTrace = (laneId) => {
    const lane = lanes.find((l) => l.id === laneId);
    if (lane?.trial?.trace_id) setTraceId(lane.trial.trace_id);
    else notify("No trace for this trial", { key: "tc-trace" });
  };
  const a = useWorkspaceActions({
    datasetId,
    st,
    d,
    focusIdx,
    setFocusIdx,
    ui: {
      me,
      openTrace,
      dialogOpen: Boolean(traceId),
      nameOf: (l) => names.get(l?.id),
      openRow,
      expandAll: outline.expandAll,
      collapseAll: outline.collapseAll,
      toggleMap,
      mapOpen,
      toggleInspect,
      inspectOpen: Boolean(inspectTab),
      closeInspect: () => setInspect(null),
      // Explicitly opened steps only (Level Everything opens gaps itself).
      anyOpen: (built?.ids ?? []).some(
        (id) => !id.startsWith("gap|") && outline.isOpen(id),
      ),
      jumpFirstWrong,
      extraHandlers: {
        graphView: () =>
          setDepth(depth === "graph" ? prevDepth.current ?? "steps" : "graph"),
        ...(depth === "graph" ? { inspect: undefined } : {}),
        // `Q` (blink) belongs to the image lens; listed greyed until one is
        // mounted (the lens binds its own handler while focused).
        blink: {
          run: () => {},
          when: () => false,
          reason: "Needs an image lens",
        },
        ...(depth === "graph" && gsel
          ? {
              escape: () => {
                const el = document.activeElement;
                const overlay = document.querySelector(
                  ".MuiPopover-root, .MuiMenu-root, .MuiDialog-root, .MuiDrawer-root",
                );
                if (
                  overlay ||
                  (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA"))
                )
                  return;
                st.set({ gsel: null });
              },
            }
          : {}),
      },
    },
  });

  const dockTab = view.dock ?? "closed";
  const setDock = (v) => st.set({ dock: v === "closed" ? null : v });

  const setLanes = (ids) =>
    st.set({
      runIds: ids,
      baselineId: ids.includes(d.baselineId)
        ? d.baselineId
        : ids.find((x) => !x.startsWith("session:")) ?? null,
    });
  const productionId = d.row?.source?.ref
    ? `session:${d.row.source.ref}`
    : null;

  // `Test a fix` (R4 implementation, fr-6/fr-7): opens X's dock on the run's
  // experiment (`from: failed_lane`; `node` = the step to fix, when known).
  // IT-R2 G4 (S1): scoped to the rows that fail for the same cause (R5
  // root causes), with the pair (`cand`, `baseline`) and the way back.
  const causeRuns = lanes.filter((l) => l.kind !== "production");
  const { data: rootCauses } = useRootCauses(
    {
      datasetId,
      runIds: causeRuns.map((l) => l.id),
      baselineId: d.baselineId ?? causeRuns[0]?.id ?? null,
    },
    { enabled: causeRuns.length > 1 && !isViewer },
  );
  const location = useLocation();
  const testFix = (lane, node) =>
    navigate(
      fixStepHref({
        lane,
        node,
        rowId: d.rowId,
        causes: rootCauses?.causes,
        // The pair: the baseline, or (fixing the baseline itself) the run
        // it is compared with.
        baselineId:
          [d.baselineId, ...causeRuns.map((l) => l.id)].find(
            (x) => x && x !== lane.id,
          ) ?? null,
        back: `${location.pathname}${location.search}`,
        iterateHref,
      }),
    );
  // dataset integration FE-0: `Fix this step` only for a lane Test a fix applies to (a
  // prompt or agent run); a code, harness or voice lane gets none.
  const canTestFix = (lane) =>
    !isViewer &&
    lane.kind !== "production" &&
    Boolean(lane.experiment_id) &&
    canIterate({ kind: lane.kind });

  const laneActions = (lane, i) => [
    {
      label: "Make baseline",
      keyCap: "B",
      icon: "mdi:pin-outline",
      onClick: () => a.makeBaseline(i),
      disabled: i === 0 || lane.kind === "production" || d.blind,
      reason:
        i === 0
          ? "Already the baseline"
          : d.blind
            ? "Turn Blind off to change the baseline"
            : "A production trace can't be the baseline",
    },
    {
      label: "Expand all steps in this lane",
      icon: "mdi:unfold-more-horizontal",
      onClick: () =>
        openIds(
          (built?.ids ?? []).filter(
            (id) => id.includes(`|${lane.id}|`) || id.endsWith(`|${lane.id}`),
          ),
        ),
      disabled: lane.noTrace,
      reason: "No steps for this trial",
    },
    {
      label: "Open full trace ↗",
      icon: "mdi:open-in-new",
      onClick: () => openTrace(lane.id),
      disabled: !lane.trial?.trace_id,
      reason: "No trace for this trial",
    },
    {
      label: "Copy run id",
      icon: "mdi:content-copy",
      onClick: async () =>
        notify(
          (await copyText(lane.id))
            ? `Copied ${lane.label} run id`
            : "Couldn't copy",
          { key: "tc-copy" },
        ),
      disabled: lane.kind === "production" || d.blind,
      reason: d.blind
        ? "Hidden in blind review"
        : "Production lanes have no run id",
    },
    {
      label: view.allTrials ? "Hide every trial" : "Show every trial",
      icon: "mdi:chart-gantt",
      onClick: () => st.set({ allTrials: !view.allTrials }),
      disabled: (lane.trials?.length ?? 0) < 2,
      reason: "This run has one trial here",
    },
    // L7b G3: only on a lane Test a fix applies to (a prompt or agent run);
    // a code, harness or voice lane has no such item.
    ...(canTestFix(lane)
      ? [
          {
            label: "Test a fix",
            icon: "mdi:wrench-cog-outline",
            onClick: () => testFix(lane),
            tooltip: `Iterate ${lane.label} on ${d.row?.label ?? "this task"}`,
          },
        ]
      : []),
    {
      label: "Add output to dataset as expected…",
      icon: "mdi:database-plus-outline",
      onClick: () =>
        openAddData({
          source: {
            kind: "experiment",
            dataset_id: datasetId,
            run_id: lane.id,
            row_ids: [d.rowId],
          },
          target: null,
          shape: "rows",
        }),
      disabled: lane.kind === "production" || isViewer,
      reason: isViewer
        ? "You have view access. Ask an editor to add data."
        : "Use Observe to add production traces",
    },
    { divider: true },
    {
      label: "Hide lane",
      icon: "mdi:eye-off-outline",
      danger: true,
      onClick: () => {
        const prev = p.runIds;
        setLanes(d.laneIds.filter((x) => x !== lane.id));
        notify(`Hid ${lane.blindName ?? lane.label}`, {
          key: "tc-remove",
          undo: { onUndo: () => st.set({ runIds: prev }) },
        });
      },
    },
  ];

  const onDelta = (lane) => (key) => {
    if (key === "steps") st.set({ diff: true });
    else if (key === "toolErrors") {
      const err = (lane.steps ?? []).find((s) => s.error);
      if (err) selectStep(lane.id, err.n);
    } else setInspect("timeline");
  };

  const header = (gutters) =>
    lanes.map((l, i) => (
      <Fragment key={l.id}>
        {gutters && i > 0 ? <Box /> : null}
        <Box
          sx={{
            minWidth: 0,
            outline: focusIdx === i ? "2px solid" : "none",
            outlineColor: l.color?.solid,
            outlineOffset: -2,
            borderLeft: i && !gutters ? "1px solid" : 0,
            borderColor: "divider",
          }}
        >
          <LaneHeader
            lane={l}
            trial={l.trial}
            baseTrial={lanes[0]?.trial}
            isBaseline={i === 0 && !d.blind}
            focused={focusIdx === i}
            onFocus={() => setFocusIdx(i)}
            onTrial={(n) => st.set({ trials: { ...p.trials, [l.id]: n } })}
            onStepTrial={(dir) => a.setTrial(i, dir)}
            fmt={p.fmt}
            onFmt={(v) => st.set({ fmt: v })}
            onDelta={onDelta(l)}
            blind={d.blind}
            actions={laneActions(l, i)}
            allTrials={view.allTrials}
            metricsAll={tcView.laneMetrics === "all"}
            displayName={names.get(l.id)}
            run={laneGraphs.runOf?.(l.id) ?? undefined}
            onTestFix={canTestFix(l) && !d.blind ? () => testFix(l) : undefined}
          />
        </Box>
      </Fragment>
    ));

  const loadingBody =
    d.runsQ.isLoading ||
    (d.selection.length && d.matrixQ.isLoading) ||
    (d.tcQ.isLoading && !d.data);
  const untraced = lanes.filter((l) => l.noTrace && l.kind !== "production");
  const untracedNote =
    untraced.length && !mediaOnly
      ? `${untraced.map((l) => names.get(l.id) ?? l.label).join(" and ")} ${untraced.length === 1 ? "has" : "have"} nothing to compare here: ${untraced[0].noTraceReason}. Pick another trial with [ or ].`
      : null;
  const summary = resultSummary({
    lanes,
    names,
    baselineId:
      d.blind || lanes[0]?.kind === "production" ? null : lanes[0]?.id,
  });
  // Call rows: what changed between the two calls' configs (IT-R1 G4).
  const callChange =
    isCallRow && lanes.length >= 2 && !d.blind
      ? voiceChangeLine(
          laneGraphs.runOf?.(lanes[0].id)?.config,
          laneGraphs.runOf?.(lanes[1].id)?.config,
        )
      : null;
  // Call rows: the Why comes from the calls' own metrics (IT-R1 G4).
  const callWhy = isCallRow
    ? voiceWhy({ lanes, calls: laneCalls.calls, names, fmtMs })
    : null;
  const whyText =
    callWhy ??
    // IT-R5 G4 P2: a media row says which check failed and where it passes.
    (mediaOnly ? scoreWhy({ lanes, names }) : null) ??
    whyLine({
      grid,
      rows: d.align?.rows ?? [],
      lanes,
      names,
      checkName,
    }) ??
    untracedNote;
  const fwb = grid.firstWrongBeat;
  const fwNote = fwb
    ? firstWrongNote({
        beat: fwb.beat,
        row: d.align?.rows.find((r) => r.id === fwb.rowId),
        laneIdx: fwb.lane,
        checkName: fw?.checkKey ? checkName(fw.checkKey) : null,
      })
    : null;
  const notes = d.review?.notes ?? [];
  const anchorStep = (() => {
    const row = d.align?.rows.find((r) => r.id === d.anchorRowId);
    const cell = row?.cells[focusIdx] ?? row?.cells[0];
    const lane = lanes[row?.cells[focusIdx] ? focusIdx : 0];
    return cell?.step
      ? {
          laneId: lane.id,
          label: lane.blindName ?? lane.label,
          stepN: cell.step.n,
        }
      : null;
  })();

  // Steps area height: the Inspect panel opens at 40 % of it (remembered).
  const areaRef = useRef(null);
  const showSteps =
    !d.cards && d.laneIds.length >= 2 && !d.tcQ.isError && !loadingBody;
  const areaH = useHeight(areaRef, showSteps);
  // IT-R2 G4 P6: on a call row the call keeps its audio + 3 turns
  // (CALL_LENS_MIN): Inspect gets the rest, and floats as a sheet when
  // that is less than its minimum.
  const callCap =
    depth === "call" ? Math.max(0, (areaH || 0) - 60 - CALL_LENS_MIN) : null;
  const inspectH = Math.max(
    INSPECT_MIN_H,
    Math.min(
      Math.max(INSPECT_MIN_H, areaH - 80),
      callCap ?? Infinity,
      Number(extras.inspectH) || Math.round((areaH || 400) * 0.4),
    ),
  );
  const inspectSheet =
    (short && depth !== "call") ||
    (depth === "call" && callCap != null && callCap < INSPECT_MIN_H);

  let body;
  if (d.cards && d.pairPending) {
    body = <BodySkeleton n={2} />;
  } else if (d.cards) {
    body = d.tcQ.isError ? (
      <ErrorState
        thing="these runs"
        error={d.tcQ.error}
        onRetry={() => d.tcQ.refetch()}
      />
    ) : loadingBody ? (
      <BodySkeleton n={4} />
    ) : (
      <CardsView
        lanes={lanes}
        defaultIds={d.defaultIds}
        onPromote={(ids) => {
          jumpOnLoad.current = true;
          setLanes(ids);
        }}
        onOpenSteps={(id) => {
          const base = d.baselineId ?? d.defaultIds[0];
          const ids =
            id === base
              ? d.defaultIds
              : [base, id].filter((x, i, a) => x && a.indexOf(x) === i);
          jumpOnLoad.current = true;
          setFocusIdx(1);
          setLanes(ids);
        }}
      />
    );
  } else if (d.laneIds.length < 2) {
    body = (
      <EmptyState
        icon="mdi:compare-horizontal"
        title="Pick at least 2 runs to compare side by side."
        body="Side by side shows what each run did on the same task, step by step."
        actions={[
          ...(d.defaultIds.length > 1 && d.laneIds.length === 1
            ? [
                {
                  label: `Add ${d.runById.get(d.defaultIds[1])?.label ?? "the most different run"} (most different here)`,
                  onClick: () =>
                    setLanes([
                      d.laneIds[0],
                      d.defaultIds.find((x) => x !== d.laneIds[0]),
                    ]),
                },
              ]
            : []),
          { label: "Back", onClick: a.goBack },
        ]}
        sx={{ mt: 6 }}
      />
    );
  } else if (d.tcQ.isError) {
    body = (
      <ErrorState
        thing="this task"
        error={d.tcQ.error}
        onRetry={() => d.tcQ.refetch()}
        sx={{ mt: 6 }}
      />
    );
  } else if (loadingBody) {
    body = <BodySkeleton n={d.laneIds.length} />;
  } else {
    const diffNav = {
      total: d.diffRows.length,
      index: d.diffRows.indexOf(d.anchorRowId) + 1,
      next: () => a.stepDiff(1),
      prev: () => a.stepDiff(-1),
    };
    const stepLaneList = lanes.map((l) => ({
      ...l,
      label: names.get(l.id) ?? l.blindName ?? l.label,
    }));
    // Map closed: its two markers fold into the toolbar (one click jumps).
    const foldedMark =
      !mapOpen && d.align
        ? fw?.rowId
          ? {
              text: `First wrong step: ${fw.stepN ?? ""}`.trim(),
              tip: d.firstWrongText,
              wrong: true,
              go: jumpFirstWrong,
            }
          : d.marks.firstDifference?.rowId
            ? {
                text: `First difference: step ${d.marks.firstDifference.baseStepN}`,
                tip: d.firstDiffText,
                wrong: false,
                go: () => {
                  const id = d.marks.firstDifference.rowId;
                  st.set({ step: id.startsWith("m") ? id : null });
                  openRow(id);
                },
              }
            : null
        : null;
    const toolbarEnd = (
      <>
        {foldedMark ? (
          <CustomTooltip
            describeChild
            show
            size="small"
            title={`${foldedMark.tip || foldedMark.text} · click to open it`}
          >
            <Button
              size="small"
              color="inherit"
              onClick={foldedMark.go}
              data-testid="tc-folded-mark"
              startIcon={
                <Iconify
                  icon={foldedMark.wrong ? "mdi:triangle" : "mdi:ray-vertex"}
                  width={12}
                />
              }
              sx={{
                typography: "s2",
                minWidth: 0,
                flexShrink: 1,
                overflow: "hidden",
                whiteSpace: "nowrap",
                color: foldedMark.wrong ? "error.main" : "primary.main",
              }}
            >
              {foldedMark.text}
            </Button>
          </CustomTooltip>
        ) : null}
        {/* IT-R4 G4 P5: Map lives in the View menu (M still toggles it). */}
        <TcViewMenu
          alignHelp={`${d.mixedKinds ? `Comparing different kinds of run (${d.mixedKinds}): steps are aligned on actions, not on wording. ` : ""}${howAlignedHelp()} ${stepNumbersHelp(names.get(lanes[0]?.id) ?? "the baseline")}`}
          mapModified={prefs.sections?.map !== undefined}
          view={tcView}
          mapOpen={mapOpen}
          onChange={setTcView}
          onMap={(on) => on !== mapOpen && toggleMap()}
          onReset={resetTcView}
          blind={Boolean(p.blind)}
          onBlind={d.cards ? undefined : () => st.set({ blind: !p.blind })}
          diffOnly={Boolean(p.diff)}
          onDiffOnly={(v) => st.set({ diff: v })}
          outline={outline}
        />
      </>
    );
    const inspectBody = inspectTab ? (
      <InspectBody
        tab={inspectTab}
        graphs={laneGraphs.graphs}
        onVote={(v) =>
          a.choose(
            { a: "left", left: "left", tie: "tie", b: "right", right: "right" }[
              v
            ] ?? v,
          )
        }
        lanes={lanes}
        runOf={laneGraphs.runOf}
        input={
          d.row?.input_image ? { photo: d.row.input_image } : d.row?.input_text
        }
        wf={view.wf}
        onWf={(v) => st.set({ wf: v })}
        rowChecks={rowChecks ?? []}
        onOpenTrace={openTrace}
        onSelectStep={selectStep}
        onClose={() => setInspect(null)}
      />
    ) : null;
    const sheet = inspectTab ? (
      <InspectSheet
        height={inspectH}
        maxHeight={Math.max(INSPECT_MIN_H, areaH - 80)}
        onHeight={(h) => patchPrefs({ extras: { inspectH: h } })}
        sheet={inspectSheet}
        label={inspectTabs.find((t) => t.value === inspectTab)?.label}
      >
        {inspectBody}
      </InspectSheet>
    ) : null;
    body = (
      <>
        {/* IT-R5 G4 P5: the Call lens answers first itself (its one
            `First difference` line), so the summary banner that repeats
            it is not shown at the call depth. */}
        {depth === "call" ? null : (
          <SummaryCard
            summary={summary}
            sentence={summarySentence({
              lanes,
              names,
              why: whyText,
            })}
            // IT-R3 G4 P5: two lines, so the result and its why are never
            // cut mid-sentence on a laptop (a click shows it all).
            lines={2}
            why={whyText}
            trials={(() => {
              const caption = trialCaption({ lanes, names, picked: p.trials });
              if (!caption) return null;
              const ts = lanes.slice(0, 3).map((l) => l.trial?.trial ?? 1);
              const n = Math.max(...lanes.map((l) => l.trials?.length ?? 1));
              return { caption, short: `Trials ${ts.join(" · ")} of ${n}` };
            })()}
            differently={
              isCallRow || d.blind
                ? null
                : differentlyLine({
                    grid,
                    rows: d.align?.rows ?? [],
                    lanes,
                    names,
                  })
            }
            lanes={lanes}
            names={names}
            baselineId={
              d.blind || lanes[0]?.kind === "production" ? null : lanes[0]?.id
            }
            // IT-R4 G4 P5: the toolbar's `First wrong step` jumps there and
            // the top bar's Ask Falcon rewrites; no second button for either.
            onJump={
              (fw || d.diffRows.length) && !foldedMark ? jumpFirstWrong : null
            }
            onRewrite={null}
          />
        )}
        {mapOpen && d.align ? (
          <MapStrip
            rows={d.align.rows}
            lanes={lanes}
            names={names}
            firstWrong={fw}
            firstDifference={d.marks.firstDifference}
            width={view.width}
            onWidth={(v) => st.set({ width: v })}
            onHide={toggleMap}
            onSelect={(r) => {
              st.set({ step: r.id.startsWith("m") ? r.id : null });
              openRow(r.id);
            }}
            onFirstWrong={jumpFirstWrong}
            firstWrongText={d.firstWrongText}
            firstDiffText={d.firstDiffText}
            onFirstDifference={() => {
              const id = d.marks.firstDifference?.rowId;
              if (!id) return;
              st.set({ step: id.startsWith("m") ? id : null });
              openRow(id);
            }}
            anchor={d.anchorRowId}
          />
        ) : null}
        <Box
          ref={areaRef}
          sx={{
            position: "relative",
            flex: 1,
            minHeight: 0,
            display: "flex",
            flexDirection: "column",
          }}
        >
          {depth === "graph" && isCallRow ? (
            // IT-R2 G4 P5 (rule 15): a call declares no agent graph, so its
            // Graph tab draws the calls' turns on one time axis.
            <Stack sx={{ flex: 1, minHeight: 0 }}>
              <Stack
                direction="row"
                alignItems="center"
                gap={1.5}
                sx={{
                  px: 1.5,
                  height: 36,
                  flexShrink: 0,
                  borderBottom: "1px solid",
                  borderColor: "divider",
                  minWidth: 0,
                }}
              >
                {depthSwitch}
                <Typography typography="s2" color="text.secondary" noWrap>
                  Turn timeline · the calls declare no agent graph
                </Typography>
              </Stack>
              <CallTurnTimeline
                lanes={lanes.map((l) => ({
                  runId: l.id,
                  label: names.get(l.id) ?? l.label,
                  color: l.color?.solid,
                }))}
                calls={laneCalls.calls}
                onFocusTurn={(t) => {
                  st.set({ turn: t || null });
                  setDepth("call");
                }}
              />
            </Stack>
          ) : depth === "graph" ? (
            <TcGraphView
              graphs={laneGraphs.graphs}
              gsel={gsel}
              onGsel={(v) => st.set({ gsel: v })}
              overlay={!gdiffOff}
              onOverlay={(on) => st.set({ gdiff: on ? null : "0" })}
              depthSwitch={depthSwitch}
              keysEnabled={!traceId}
              onOpenStep={({ laneId, name, spanIds }) => {
                // IT-R5 G4 P1: a node click opens its step in Steps.
                const l = lanes.findIndex((x) => x.id === laneId);
                const g = laneGraphs.graphs[l];
                if (l < 0 || !g?.tree) return;
                const bySpan = Object.entries(spanIdsByStep(g.tree));
                const hit =
                  bySpan.find(([, id]) => (spanIds ?? []).includes(id)) ?? null;
                const n = hit ? Number(hit[0]) : null;
                const row = d.align?.rows.find(
                  (r) =>
                    r.cells[l]?.type === "step" &&
                    (n != null
                      ? r.cells[l].step.n === n
                      : r.cells[l].step.name === name),
                );
                setDepth("steps");
                if (row)
                  setTimeout(
                    () =>
                      openRow(row.id, n != null ? { laneId, n } : undefined),
                    120,
                  );
              }}
            />
          ) : depth === "call" ? (
            <Stack sx={{ flex: 1, minHeight: 0 }}>
              <Stack
                direction="row"
                alignItems="center"
                gap={1.5}
                sx={{
                  px: 1.5,
                  height: 36,
                  flexShrink: 0,
                  borderBottom: "1px solid",
                  borderColor: "divider",
                  minWidth: 0,
                }}
              >
                {depthSwitch}
                {callChange ? (
                  <Typography
                    typography="s2"
                    color="text.secondary"
                    noWrap
                    title={`What changed: ${callChange}`}
                    data-testid="tc-call-changed"
                    sx={{ minWidth: 0 }}
                  >
                    <Box
                      component="span"
                      sx={{ color: "text.primary", fontWeight: 600 }}
                    >
                      What changed:
                    </Box>{" "}
                    {callChange}
                  </Typography>
                ) : null}
              </Stack>
              <Box sx={{ flex: 1, minHeight: 0, overflow: "auto", p: 1.5 }}>
                <ModalityLens
                  kind="call"
                  // Fills the pane: the transcript takes the room left.
                  height={Math.max(320, (areaH || 600) - 36 - 24)}
                  rowId={d.rowId}
                  lanes={lanes.map((l, i) => ({
                    runId: l.id,
                    label: names.get(l.id) ?? l.label,
                    color: l.color?.solid,
                    isBaseline: i === 0 && !d.blind,
                    // IT-R5 G4 P5: the checks this call failed (chips under
                    // the first-difference line).
                    // (a voice call has no anchored checks: its failed
                    // evals, by name).
                    failing: [
                      ...(l.trial?.checks ?? [])
                        .filter((c) => c?.passed === false)
                        .map((c) => checkName(c.key)),
                      ...Object.values(l.trial?.scores ?? {})
                        .filter((x) => x?.status === "failed" && x.name)
                        .map((x) => x.name),
                    ],
                    value: l.trial?.output ?? l.trial?.final_answer,
                    trace_id: l.trial?.trace_id ?? undefined,
                    // Task completion for every run (never a dash).
                    passed: l.verdict?.scored
                      ? l.verdict.pass === l.verdict.scored
                      : undefined,
                  }))}
                  focusTurn={
                    Number(st.sp.get("turn")) > 0
                      ? Number(st.sp.get("turn"))
                      : undefined
                  }
                  onFocusTurn={(t) => st.set({ turn: t || null })}
                  keysEnabled
                />
              </Box>
            </Stack>
          ) : mediaOnly ? (
            <Stack
              sx={{ flex: 1, minHeight: 0 }}
              data-testid="tc-media-answers"
            >
              <Typography
                typography="s2"
                color="text.secondary"
                sx={{
                  px: 1.5,
                  py: 0.75,
                  flexShrink: 0,
                  borderBottom: "1px solid",
                  borderColor: "divider",
                }}
              >
                {`No steps — ${lanes.length > 1 ? "these runs return" : "this run returns"} ${mediaKind === "image" ? "an image" : mediaKind === "audio" ? "audio" : mediaKind === "video" ? "a video" : "a document"}. Their answers, side by side:`}
              </Typography>
              <Box sx={{ flex: 1, minHeight: 0, overflow: "auto", p: 1.5 }}>
                <InspectBody
                  tab="answers"
                  compact={false}
                  lensHeight={Math.max(320, (areaH || 600) - 72)}
                  lanes={lanes}
                  input={
                    d.row?.input_image
                      ? { photo: d.row.input_image }
                      : d.row?.input_text
                  }
                  onVote={(v) =>
                    a.choose(
                      {
                        a: "left",
                        left: "left",
                        tie: "tie",
                        b: "right",
                        right: "right",
                      }[v] ?? v,
                    )
                  }
                />
              </Box>
            </Stack>
          ) : (
            <Box sx={{ flex: 1, minHeight: 0, display: "flex" }}>
              <Box
                sx={{
                  flex: 1,
                  minWidth: 0,
                  minHeight: 0,
                  display: "flex",
                  flexDirection: "column",
                }}
              >
                <StepsView
                  // IT-R3 G4 P4: no `Graph recommended` banner; the Graph
                  // tab keeps its dot and tooltip (ViewSwitch).
                  onOpenSpan={(l, n) => setSpanPick({ l, n })}
                  canFixStep={(l) =>
                    lanes[l]?.kind === "production" ||
                    canIterate({ kind: lanes[l]?.kind })
                  }
                  onFixStep={(l) =>
                    canTestFix(lanes[l]) && !d.blind
                      ? testFix(lanes[l], laneGraphs.graphs[l]?.failedName)
                      : notify("Only runs from an experiment can test a fix", {
                          key: "tc-fix",
                        })
                  }
                  toolbarStart={depthSwitch}
                  built={built}
                  lanes={stepLaneList}
                  header={header(view.align !== "none")}
                  view={view}
                  setView={st.set}
                  diffOnly={p.diff}
                  outline={outline}
                  onAcross={onAcross}
                  anchor={d.anchorRowId}
                  firstDifferenceRowId={d.marks.firstDifference?.rowId}
                  query={query}
                  onQuery={setQuery}
                  searchRef={a.stepSearchRef}
                  diffNav={diffNav}
                  toolbarEnd={toolbarEnd}
                  fwNote={fwNote}
                  checkName={checkName}
                  promptOf={(l, n) => lanes[l]?.promptOf?.(n) ?? null}
                  onOpenTrace={(l) => openTrace(lanes[l]?.id)}
                />
              </Box>
              {spanPanel ? (
                <Box
                  data-testid="tc-span-panel"
                  sx={{
                    width: "40%",
                    minWidth: 340,
                    maxWidth: 560,
                    borderLeft: "1px solid",
                    borderColor: "divider",
                    borderTop: "3px solid",
                    borderTopColor: spanPanel.color ?? "divider",
                    overflow: "auto",
                    display: "flex",
                    flexDirection: "column",
                    bgcolor: "background.paper",
                  }}
                >
                  <SpanDetailPane
                    entry={spanPanel.entry}
                    allSpans={spanPanel.tree}
                    traceStartTime={
                      spanPanel.tree?.[0]?.observation_span?.start_time
                    }
                    isRootSpan={false}
                    traceTags={[]}
                    onClose={() => setSpanPick(null)}
                    onSelectSpan={() => {}}
                    drawerOpen
                  />
                </Box>
              ) : null}
            </Box>
          )}
          {/* Short windows float the panel over the steps; on a call it
              docks (pushes the call up) while the call keeps its audio and
              3 turns, else it floats too (IT-R2 G4 P6). */}
          {inspectSheet ? sheet : null}
        </Box>
        {inspectSheet ? null : sheet}
        <InspectStrip
          tabs={inspectTabs}
          tab={inspectTab}
          onTab={setInspect}
          onOpen={toggleInspect}
          aside={
            d.align ? (
              <Typography
                typography="s3"
                color="text.secondary"
                sx={{
                  display: { xs: "none", lg: "flex" },
                  alignItems: "center",
                  gap: 0.75,
                  whiteSpace: "nowrap",
                  fontVariantNumeric: "tabular-nums",
                  mr: 0.5,
                }}
              >
                {lanes.length === 2 ? (
                  // IT-R4 G4 P4: the gutter marks, in one line.
                  <span data-testid="tc-gutter-legend">
                    {`+ only in ${names.get(lanes[1]?.id) ?? "B"} · ~ both, different · − only in ${names.get(lanes[0]?.id) ?? "A"} ·`}
                  </span>
                ) : null}
                {stepsMatchText(d.align)}
              </Typography>
            ) : null
          }
        />
        <BottomDock
          viewed={a.viewed}
          onViewed={a.toggleViewed}
          notesCount={notes.length}
          notesOpen={dockTab === "notes"}
          onNotes={(o) => setDock(o ? "notes" : "closed")}
          notes={
            <NotesPanel
              notes={notes}
              onAdd={a.addNote}
              onDelete={a.deleteNote}
              inputRef={a.noteRef}
              anchorStep={anchorStep}
              laneLabelOf={(id) =>
                names.get(id) ??
                d.runById.get(id)?.label ??
                lanes.find((l) => l.id === id)?.label ??
                "a run"
              }
              me={me}
              busy={a.saving}
            />
          }
          verdictBar={
            lanes.length >= 2 ? (
              <VerdictBar
                left={a.left}
                right={a.right}
                current={a.current}
                onChoose={a.choose}
                onReason={a.reason}
                blind={d.blind}
                names={d.blind ? null : names}
                terse={!roomy}
                revealed={a.revealed}
                onReveal={() => {
                  a.setRevealed(true);
                  notify(
                    lanes
                      .map((l) => `${l.blindName} was ${l.name ?? l.label}`)
                      .join(" · "),
                    { key: "tc-reveal", autoHideDuration: 8000 },
                  );
                }}
                reveal={lanes
                  .map((l) => `${l.blindName} was ${l.name ?? l.label}`)
                  .join(" · ")}
              />
            ) : null
          }
        />
      </>
    );
  }

  return (
    <Box
      ref={rootRef}
      data-testid="task-compare"
      sx={{
        flex: 1,
        minHeight: 0,
        height: "100%",
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        bgcolor: "background.default",
      }}
    >
      <TopBar
        row={d.row}
        onBack={a.goBack}
        backLabel={a.backLabel}
        onCopyLink={a.copyLink}
        onAskFalcon={() =>
          askFalcon(
            d.row
              ? `On ${d.row.label} (${d.row.title}), compare ${lanes.map((l) => names.get(l.id) ?? l.label).join(", ")}: why do they differ?`
              : "",
          )
        }
        blind={d.blind}
        onBlind={() => st.set({ blind: !p.blind })}
        onOpenSource={() =>
          d.row?.source?.trace_id && setTraceId(d.row.source.trace_id)
        }
        shownRuns={lanes.filter((l) => l.kind !== "production").length}
        selectionCount={d.cards ? 0 : d.selection.length}
        onCards={() => st.set({ runIds: d.selection })}
        lanePicker={
          <LanePicker
            runs={d.selectionRuns}
            laneIds={d.cards ? [] : d.laneIds}
            cells={d.mrow?.cells}
            onChange={(ids) =>
              setLanes(sortLaneIds(ids, laneSort, d.mrow?.cells))
            }
            sort={laneSort}
            onSort={(s) => (
              setLaneSort(s), setLanes(sortLaneIds(d.laneIds, s, d.mrow?.cells))
            )}
            sourceRef={d.row?.source?.ref}
            productionId={productionId}
            blindOn={Boolean(p.blind)}
            onBlind={d.cards ? undefined : () => st.set({ blind: !p.blind })}
            onCards={() => st.set({ runIds: d.selection })}
            blind={d.blind}
          />
        }
      />
      <Box sx={{ flex: 1, minHeight: 0, display: "flex" }}>
        <TaskRail
          rows={d.matrix?.rows ?? []}
          sets={d.sets}
          rail={st.rail}
          note={railStrip ? null : d.railNote}
          onRail={(v) => st.set({ rail: v })}
          focusId={st.sp.get("focus") ?? undefined}
          baselineId={d.baselineId ?? undefined}
          currentRowId={d.rowId}
          onSelectRow={a.goRow}
          runs={d.selectionRuns}
          laneIds={d.cards ? p.runIds : d.laneIds}
          rowsState={d.rowsState}
          width={RAIL_WIDTH}
          collapsed={railStrip}
          onCollapse={(v) =>
            wide
              ? (setRailCollapsed(v), ls.set(RAIL_KEY, v ? "1" : "0"))
              : setRailPinned(!v)
          }
          loading={!d.matrix}
          searchRef={a.railSearchRef}
        />
        <Box
          component="section"
          aria-label="Runs"
          sx={{
            flex: 1,
            minWidth: 0,
            minHeight: 0,
            display: "flex",
            flexDirection: "column",
          }}
        >
          {d.railNote && railStrip ? (
            // IT-R5 G4 P4 (rule 39): the rail is folded at this width, so
            // the widen note shows above the lanes.
            <Typography
              typography="s3"
              color="text.secondary"
              data-testid="tc-rail-note"
              sx={{
                px: 1.5,
                py: 0.25,
                flexShrink: 0,
                borderBottom: "1px solid",
                borderColor: "divider",
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
              }}
            >
              {d.railNote}
            </Typography>
          ) : null}
          {d.pairOf && !d.cards ? (
            <RunStrip
              runs={d.pairRuns}
              laneIds={d.laneIds}
              cells={d.mrow?.cells}
              blind={Boolean(d.blind)}
              onCompare={(ids) => {
                const base = ids.includes(d.baselineId) ? d.baselineId : ids[0];
                if (ids.length === 2)
                  st.set({
                    baselineId: base,
                    cand: ids.find((x) => x !== base),
                  });
                else setLanes(ids);
              }}
            />
          ) : null}
          {body}
        </Box>
      </Box>
      {traceId ? (
        <TraceDetailDrawerV2
          traceId={traceId}
          open
          onClose={() => setTraceId(null)}
        />
      ) : null}
    </Box>
  );
}

Workspace.propTypes = { datasetId: PropTypes.string.isRequired };
