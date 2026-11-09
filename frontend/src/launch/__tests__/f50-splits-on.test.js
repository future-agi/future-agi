import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SURFACE_BY_ID } from "../launchSurfaces";
import {
  experimentsBasicFromEnv,
  isSurfaceShown,
  setLaunchOverrideForTest,
  shortcutShownIn,
  surfaceShownIn,
  verbShownIn,
} from "../launchSwitch";
import { MADE_REAL, madeRealSurfaceIds } from "../madeReal";
import { sanitizeLaunchSearch } from "../launchSearch";

// All-on release flip, family "F50 splits" (owner Q2; RL 1380/1381; walk
// legs J09.ON.18 and J09.ON.54). Default pins for this family only:
// dataset.splits and exp.new.splitScope leave the 749 hold layer, so their
// existing lane claims (GRID DATA-E2E-008, XP EXP-E2E-006) admit them in
// every launch build. The other family's switches (experimentsBasic) are
// asserted only through invariants that hold whether or not it has flipped.

const F50 = { "dataset.splits": "GRID", "exp.new.splitScope": "XP" };
const IDS = Object.keys(F50);
// The hold layer keeps every other id; this family removes exactly two.
const STILL_HELD = [
  "list.organize.folders",
  "dataset.settings",
  "exp.new.scope",
  "exp.runControl",
  "evals.dag",
];
const DATASET_PAGE = "/dashboard/develop/9f0c2d1e-1111-4a2b-9c3d-000000000001";
const at = (experimentsBasic, madeReal = madeRealSurfaceIds()) => ({
  launch: true,
  experimentsBasic,
  madeReal: new Set(madeReal),
});

beforeEach(() => {
  // A real launch build: the census-(B) unit-test opt-in is OFF, so the
  // hold layer applies exactly as it does in every app build.
  vi.stubEnv("VITE_DSX_CENSUS_B_TEST", "false");
});
afterEach(() => {
  vi.unstubAllEnvs();
  setLaunchOverrideForTest(null);
  vi.resetModules();
});

describe("F50 splits: committed default ON", () => {
  // The registry and gate have no hosted/self-host dimension: both deployment
  // venues use launchState()/surfaceShownIn(). Exercise their captured build
  // env, not a hand-written launch override or a ships literal assertion.
  it.each(IDS)(
    "shows %s through the live gate with no optional build env or test override",
    async (id) => {
      vi.stubEnv("MODE", "production");
      for (const key of [
        "VITE_ENVIRONMENT",
        "VITE_PROTOTYPE_MODE",
        "VITE_DSX_EXPERIMENTS_BASIC",
        "VITE_DSX_CENSUS_B_TEST",
      ])
        vi.stubEnv(key, undefined);
      vi.resetModules();
      const launch = await import("../launchSwitch");
      expect(launch.launchBuildFromEnv({})).toBe(true);
      expect(launch.launchState().launch).toBe(true);
      expect(launch.censusBTestOptIn()).toBe(false);
      expect(launch.launchState().madeReal.has(id)).toBe(true);
      expect(launch.isSurfaceShown(id)).toBe(true);
    },
  );

  it.each(IDS)("keeps the existing lane claim for %s", (id) => {
    expect(MADE_REAL[F50[id]]).toContain(id);
    expect(madeRealSurfaceIds().has(id)).toBe(true);
    // Independent of the experimentsBasic family: no parent, no flag.
    expect(SURFACE_BY_ID[id].parent).toBeUndefined();
    expect(SURFACE_BY_ID[id].experiments).toBeUndefined();
  });

  it.each(IDS)(
    "shows %s in a launch build with experimentsBasic off or on",
    (id) => {
      for (const basic of [false, true])
        expect(surfaceShownIn(id, at(basic)), `basic=${basic}`).toBe(true);
    },
  );

  it.each(IDS)("admits %s through its claim, not a registry literal", (id) => {
    for (const basic of [false, true])
      expect(surfaceShownIn(id, at(basic, [])), `basic=${basic}`).toBe(false);
  });

  it("shows both through the live gate reader in a launch build", () => {
    for (const basic of [false, true]) {
      setLaunchOverrideForTest(at(basic));
      for (const id of IDS)
        expect(isSurfaceShown(id), `${id} basic=${basic}`).toBe(true);
    }
  });

  it("admits the split entry paths: verb, Row view key and deep links", () => {
    for (const basic of [false, true]) {
      const state = at(basic);
      expect(verbShownIn("row.splits", state)).toBe(true);
      expect(shortcutShownIn("row", "splits", state)).toBe(true);
      const search = sanitizeLaunchSearch(
        "?tab=data&splits=hard&split=test",
        DATASET_PAGE,
        state,
      );
      const kept = new URLSearchParams(search.search);
      expect(kept.get("splits"), `basic=${basic}`).toBe("hard");
      expect(kept.get("split"), `basic=${basic}`).toBe("test");
    }
  });
});

describe("F50 splits: only this family's switch lines move", () => {
  it.each(STILL_HELD)(
    "keeps the 749 hold on %s even when claimed with experimentsBasic on",
    (id) => {
      expect(surfaceShownIn(id, at(true, [...STILL_HELD]))).toBe(false);
    },
  );

  it("leaves the experimentsBasic contract to its own family", () => {
    // Explicit build inputs keep their meaning whatever the committed default.
    expect(
      experimentsBasicFromEnv({ VITE_DSX_EXPERIMENTS_BASIC: "true" }),
    ).toBe(true);
    expect(
      experimentsBasicFromEnv({ VITE_DSX_EXPERIMENTS_BASIC: "false" }),
    ).toBe(false);
    // Experiments-flagged surfaces stay dark while the flag is off.
    for (const id of ["dataset.tab.experiments", "dataset.experimentDefaults"])
      expect(surfaceShownIn(id, at(false)), id).toBe(false);
  });
});
