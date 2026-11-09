import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  isLaunchBuild,
  isSurfaceShown,
  setLaunchOverrideForTest,
} from "src/launch/launchSwitch";
import { madeRealSurfaceIds } from "src/launch/madeReal";
import { sanitizeLaunchSearch } from "src/launch/launchSearch";
import { wizardSettingShown } from "src/launch/wizardSettings";
import {
  isRealBackend,
  setRealBackendForTests,
} from "src/sections/develop/realBackendCaps";
import { splitScopeShown } from "src/sections/develop-detail/Experiment/splitScopeVisibility";

const DATASET_PAGE = "/dashboard/develop/8d3c1f7e-2b6a-4c11-9d0e-5f6a7b8c9d01";
const committed = () => [...madeRealSurfaceIds()];
const state = (madeReal) => ({
  launch: true,
  experimentsBasic: true,
  madeReal: new Set(madeReal),
});

beforeEach(() => {
  vi.stubEnv("VITE_DSX_CENSUS_B_TEST", "false");
  setRealBackendForTests(null);
  setLaunchOverrideForTest(state(committed()));
  expect(isLaunchBuild()).toBe(true);
  expect(isRealBackend()).toBe(true);
  vi.stubEnv("VITE_DSX_CENSUS_B_TEST", "true");
  expect(isLaunchBuild()).toBe(true);
  expect(isRealBackend()).toBe(true);
});
afterEach(() => {
  vi.unstubAllEnvs();
  setLaunchOverrideForTest(null);
  setRealBackendForTests(null);
});

// Additive GATE-713-009/011/013 carry; the default-hidden tests stay separate.
describe("census-(B) opt-in split positives", () => {
  it("keeps the independent setting inside the separately gated wizard", () => {
    setLaunchOverrideForTest({
      launch: true,
      experimentsBasic: true,
      madeReal: ["exp.new.splitScope"],
    });
    expect(wizardSettingShown("splits")).toBe(true);
    expect(splitScopeShown()).toBe(true);
    setLaunchOverrideForTest({
      launch: true,
      experimentsBasic: false,
      madeReal: ["exp.new.splitScope"],
    });
    expect(wizardSettingShown("splits")).toBe(true);
    expect(splitScopeShown()).toBe(true);
    expect(isSurfaceShown("exp.new")).toBe(false);
  });

  it.each([
    [false, false],
    [false, true],
    [true, false],
    [true, true],
  ])("dataset claim %s, run scope claim %s", (datasetShown, runShown) => {
    const claims = committed().filter(
      (id) => id !== "dataset.splits" && id !== "exp.new.splitScope",
    );
    if (datasetShown) claims.push("dataset.splits");
    if (runShown) claims.push("exp.new.splitScope");
    const out = sanitizeLaunchSearch(
      "?new_experiment=1&splits=smoke&split=smoke",
      DATASET_PAGE,
      state(claims),
    );
    const params = new URLSearchParams(out.search);
    expect(params.get("new_experiment")).toBe("1");
    expect(params.get("splits")).toBe(datasetShown ? "smoke" : null);
    expect(params.get("split")).toBe(runShown ? "smoke" : null);
  });

  it("a ?split= deep link is kept for the wizard to preselect", () => {
    const out = sanitizeLaunchSearch(
      "?new_experiment=1&split=smoke",
      DATASET_PAGE,
      state(committed()),
    );
    expect(out.dropped).toEqual([]);
    expect(new URLSearchParams(out.search).get("split")).toBe("smoke");
  });
});
