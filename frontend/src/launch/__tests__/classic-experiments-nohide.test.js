import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  EXPERIMENTS_BASIC_DEFAULT,
  experimentsBasicFromEnv,
  isDatasetTabShown,
  isSurfaceShown,
  isVerbShown,
  setLaunchOverrideForTest,
} from "../launchSwitch";
import { madeRealSurfaceIds } from "../madeReal";
import { launchRedirect } from "../launchRoutes";
import { sanitizeLaunchSearch } from "../launchSearch";
import { launchCommandShown } from "src/sections/common/CommandPalette/commandRegistry";

// Both switch states, the way launch-gates.test.jsx pins them: the override
// launchSwitch reads and the export the product modules read.
const unreleased = vi.hoisted(() => ({ shown: false }));
vi.mock("../launchSwitch", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    get SHOW_UNRELEASED_PARTS() {
      return unreleased.shown;
    },
  };
});

// DSX J09 feature-age REVISED: split, never flip.
// The real claims, not an empty fixture, must preserve the launch holds.
// All-on flip: experimentsBasic now defaults ON, so this file pins the
// explicit off build input (launchSwitch.js experimentsBasicFromEnv), with
// unreleased parts hidden.
const defaults = () => ({
  launch: true,
  showUnreleased: false,
  experimentsBasic: experimentsBasicFromEnv({
    VITE_DSX_EXPERIMENTS_BASIC: "false",
  }),
  madeReal: madeRealSurfaceIds(),
});
// As shipped: the override leaves the switch to the build, which shows
// unreleased parts.
const shipped = () => ({
  launch: true,
  experimentsBasic: true,
  madeReal: madeRealSurfaceIds(),
});
beforeEach(() => {
  unreleased.shown = false;
});
afterEach(() => setLaunchOverrideForTest(null));

const NEW_SURFACES = [
  "exp.new",
  "dataset.tab.experiments",
  "exp.new.kindMenu",
  "exp.new.runningSources",
  "exp.new.scope",
  "exp.new.runSettings",
  "exp.new.record",
  "exp.new.readsTable",
  "exp.new.savedInputReads",
  "exp.new.evalCardChips",
  "exp.rename",
  "exp.runControl",
  "exp.results.matrix",
  "exp.results.paths",
  "exp.results.summary",
  "dataset.experimentDefaults",
];

describe("classic experiments in a build with experimentsBasic explicitly off", () => {
  it("keeps the classic tab and prompt drawer admitted with the flag off; the committed default is ON", () => {
    setLaunchOverrideForTest(defaults());
    expect(EXPERIMENTS_BASIC_DEFAULT).toBe(true);
    expect(isDatasetTabShown("experiments")).toBe(true);
    expect(
      sanitizeLaunchSearch(
        "?tab=experiments&new_experiment=1",
        "/dashboard/develop/d1",
      ).changed,
    ).toBe(false);
  });

  it.each(["experiment", "individual-experiment"])(
    "%s Data and Summary routes do not redirect with the flag off",
    (kind) => {
      setLaunchOverrideForTest(defaults());
      for (const tab of ["data", "summary"]) {
        expect(
          launchRedirect({ pathname: `/dashboard/develop/${kind}/e1/${tab}` }),
        ).toBeNull();
      }
    },
  );

  it("does not reveal NEW palette links or recents merely because classic routes open", () => {
    setLaunchOverrideForTest(defaults());
    for (const href of [
      "/dashboard/develop/experiment/e1/data",
      "/dashboard/develop/experiment/e1/summary",
      "/dashboard/develop/individual-experiment/e1/summary",
      "/dashboard/develop/d1?tab=experiments",
      "/dashboard/develop/d1?new_experiment=1",
    ])
      expect(launchCommandShown({ id: "recent.classic", href }), href).toBe(
        false,
      );
    setLaunchOverrideForTest({ ...defaults(), experimentsBasic: true });
    expect(
      launchCommandShown({
        id: "recent.classic",
        href: "/dashboard/develop/experiment/e1/summary",
      }),
    ).toBe(false);
    expect(
      launchCommandShown({
        id: "recent.classic",
        href: "/dashboard/develop/experiment/e1/data",
      }),
    ).toBe(true);
  });

  it.each(NEW_SURFACES)("keeps new surface %s hidden", (id) => {
    setLaunchOverrideForTest(defaults());
    expect(isSurfaceShown(id)).toBe(false);
  });

  it("keeps new verbs, Results routes, scope and picker deep links hidden", () => {
    setLaunchOverrideForTest(defaults());
    for (const verb of [
      "dataset.newExperiment",
      "newexp.prompt",
      "newexp.agent",
      "experiment.new",
      "experiments.open",
      "leaderboard.open",
    ]) {
      expect(isVerbShown(verb), verb).toBe(false);
    }
    for (const tab of ["matrix", "paths", "traces"]) {
      expect(
        launchRedirect({ pathname: `/dashboard/develop/experiment/e1/${tab}` }),
      ).not.toBeNull();
    }
    const search = sanitizeLaunchSearch(
      "?new_experiment=1&newExperiment=bakeoff-v2&source=agent&entry=http&src=agent&rows=r1&split=test&version_id=v1&form=connect",
      "/dashboard/develop/d1",
    );
    expect(new URLSearchParams(search.search).get("new_experiment")).toBe("1");
    for (const key of [
      "newExperiment",
      "source",
      "entry",
      "src",
      "rows",
      "version_id",
      "form",
    ]) {
      expect(new URLSearchParams(search.search).has(key), key).toBe(false);
    }
    // All-on F50: the split surfaces leave the hold; their claims admit them.
    expect(new URLSearchParams(search.search).get("split")).toBe("test");
    expect(isSurfaceShown("dataset.splits")).toBe(true);
    // Dataset settings, folders and decision-tree evals are no longer held
    // (launchSwitch.js holds nothing); their claims admit them in this build
    // too. They are not experiment surfaces, so the flag does not hide them.
    for (const id of [
      "dataset.settings",
      "list.organize.folders",
      "evals.dag",
    ]) {
      expect(isSurfaceShown(id), id).toBe(true);
    }
  });
});

// As shipped, the dataset's Experiments tab shows the runs view and, under
// it, the experiments the runs view cannot show in full; such an experiment
// opens at its classic Data and Summary routes. (The tab and the page are
// rendered in ExperimentTabClassic/__tests__/other-experiments-tab.test.jsx
// and pages/dashboard/Develop/__tests__/experiment-page-by-record.test.jsx.)
describe("classic experiments as shipped (unreleased parts shown)", () => {
  beforeEach(() => {
    unreleased.shown = true;
    setLaunchOverrideForTest(shipped());
  });

  it("keeps the dataset Experiments tab and its link open", () => {
    expect(isDatasetTabShown("experiments")).toBe(true);
    const search = sanitizeLaunchSearch(
      "?tab=experiments",
      "/dashboard/develop/d1",
    );
    expect(search.changed).toBe(false);
  });

  it.each(["data", "summary"])(
    "keeps the experiment %s route open for an experiment the runs view cannot show in full",
    (tab) => {
      const pathname = `/dashboard/develop/experiment/e1/${tab}`;
      expect(launchRedirect({ pathname })).toBeNull();
    },
  );

  it.each(NEW_SURFACES)("shows new surface %s", (id) => {
    expect(isSurfaceShown(id)).toBe(true);
  });
});
