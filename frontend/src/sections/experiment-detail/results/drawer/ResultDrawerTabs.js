import ResultCompareTab from "./ResultCompareTab";
import TrajectoryTab from "./TrajectoryTab";
import ReviewTab from "./ReviewTab";
import ArtifactsTab from "./ArtifactsTab";
import { isArtifactsRun } from "src/sections/common/Runs/runKind";

/**
 * Result drawer tab registry (L0; PRODUCT-SPEC §3.11, §7.2). The drawer
 * shell (L6) renders the tabs in this order and switches them with `dtab=`;
 * each lane fills only its own component:
 *   compare     ResultCompareTab  (L6)
 *   trajectory  TrajectoryTab     (R4 lane T: Trace + Trajectory merged on
 *               RunTrajectory; `dtab=trace` resolves here)
 *   review      ReviewTab         (L7)
 *   artifacts   ArtifactsTab      (R2 L3a; harness and code runs only)
 * Every tab gets the props in ResultDrawerTabs.propTypes.js. A tab with
 * `when(run)` shows only when it returns true for the focused run.
 */
// iterate r2 G3 (P10): each tab says what it is for (its hover hint).
export const RESULT_DRAWER_TABS = Object.freeze([
  {
    value: "compare",
    label: "Compare",
    hint: "Compare: the outputs and scores vs another run",
    Component: ResultCompareTab,
  },
  {
    value: "trajectory",
    label: "Trajectory",
    hint: "Trajectory: the steps the agent took",
    Component: TrajectoryTab,
  },
  {
    value: "review",
    label: "Review",
    hint: "Review: rate this row",
    Component: ReviewTab,
  },
  {
    value: "artifacts",
    label: "Artifacts",
    hint: "Artifacts: the files the run produced",
    Component: ArtifactsTab,
    when: isArtifactsRun,
  },
]);

export const RESULT_DRAWER_TAB_COMPONENTS = Object.freeze({
  compare: ResultCompareTab,
  trajectory: TrajectoryTab,
  // R4: the R3 Trace tab is the Trajectory tab on Timeline.
  trace: TrajectoryTab,
  review: ReviewTab,
  artifacts: ArtifactsTab,
});

export const DEFAULT_RESULT_DRAWER_TAB = "compare";

/** The tabs that apply to the focused run (`when(run)` true or absent). */
export function visibleResultDrawerTabs(run) {
  return RESULT_DRAWER_TABS.filter((tab) => !tab.when || tab.when(run));
}

/**
 * The tab for `dtab=`; Compare when unknown, or when `run` is given and the
 * tab does not apply to it (e.g. `dtab=artifacts` on a prompt run).
 */
export function resultDrawerTab(value, run) {
  const tabs =
    run === undefined ? RESULT_DRAWER_TABS : visibleResultDrawerTabs(run);
  // R4: `dtab=trace` → Trajectory (the tab adds `tview=timeline`).
  const want = value === "trace" ? "trajectory" : value;
  return tabs.find((tab) => tab.value === want) ?? RESULT_DRAWER_TABS[0];
}
