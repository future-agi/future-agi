import { afterEach, describe, expect, it } from "vitest";
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

// DSX J09 feature-age REVISED / release-lead 767: split, never flip.
// The real claims, not an empty fixture, must preserve the launch holds.
// All-on flip: experimentsBasic now defaults ON, so this file pins the
// explicit off build input (launchSwitch.js experimentsBasicFromEnv).
const defaults = () => ({
  launch: true,
  experimentsBasic: experimentsBasicFromEnv({
    VITE_DSX_EXPERIMENTS_BASIC: "false",
  }),
  madeReal: madeRealSurfaceIds(),
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
    for (const id of [
      "dataset.settings",
      "list.organize.folders",
      "evals.dag",
    ]) {
      expect(isSurfaceShown(id), id).toBe(false);
    }
  });
});
