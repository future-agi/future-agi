import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  censusBTestOptIn,
  isLaunchBuild,
  setLaunchOverrideForTest,
  surfaceShownIn,
} from "../launchSwitch";
import {
  isRealBackend,
  setRealBackendForTests,
} from "src/sections/develop/realBackendCaps";

// Independent census contract; never derive this expected list from product holds.
// main 11-09 lifted all five former holds (they now admit through their claims).
const HOLDS = [];
const LIFTED = [
  "list.organize.folders",
  "dataset.settings",
  "exp.new.scope",
  "exp.runControl",
  "evals.dag",
];
const state = (madeReal = HOLDS, experimentsBasic = true) => ({
  launch: true,
  experimentsBasic,
  madeReal: new Set(madeReal),
});
beforeEach(() => {
  vi.stubEnv("VITE_DSX_CENSUS_B_TEST", "false");
  setRealBackendForTests(null);
  setLaunchOverrideForTest(state());
});
afterEach(() => {
  vi.unstubAllEnvs();
  setLaunchOverrideForTest(null);
  setRealBackendForTests(null);
});
describe("census-(B) test opt-in", () => {
  it("retains launch and real-backend mode while overriding only the census holds", () => {
    expect(censusBTestOptIn()).toBe(false);
    for (const id of HOLDS) expect(surfaceShownIn(id, state()), id).toBe(false);
    expect(isLaunchBuild()).toBe(true);
    expect(isRealBackend()).toBe(true);
    vi.stubEnv("VITE_DSX_CENSUS_B_TEST", "true");
    expect(censusBTestOptIn()).toBe(true);
    for (const id of HOLDS) expect(surfaceShownIn(id, state()), id).toBe(true);
    expect(isLaunchBuild()).toBe(true);
    expect(isRealBackend()).toBe(true);
    for (const id of [
      "dataset.export",
      "dataset.evalVersions",
      "evals.remote",
      "list.organize.metadata",
      "list.organize.agents",
    ])
      expect(surfaceShownIn(id, state()), id).toBe(false);
  });
  it("shows the lifted former holds through their claims without the opt-in", () => {
    expect(censusBTestOptIn()).toBe(false);
    for (const id of LIFTED) expect(surfaceShownIn(id, state(LIFTED)), id).toBe(true);
  });
  it("never creates claims or accepts unknown surfaces", () => {
    vi.stubEnv("VITE_DSX_CENSUS_B_TEST", "true");
    for (const id of HOLDS)
      expect(surfaceShownIn(id, state([])), id).toBe(false);
    expect(() => surfaceShownIn("not-a-surface", state())).toThrow(
      "Unknown launch surface",
    );
  });
  it("keeps claimed split scope independent of the hidden wizard", () => {
    const claimed = state(["exp.new.splitScope"], false);
    // All-on F50: no longer held, so its claim admits it without the opt-in.
    expect(surfaceShownIn("exp.new.splitScope", claimed)).toBe(true);
    vi.stubEnv("VITE_DSX_CENSUS_B_TEST", "true");
    expect(surfaceShownIn("exp.new.splitScope", claimed)).toBe(true);
    expect(surfaceShownIn("exp.new", claimed)).toBe(false);
  });
  it("keeps an explicitly claimed child hidden with its experiment parent", () => {
    vi.stubEnv("VITE_DSX_CENSUS_B_TEST", "true");
    const claims = ["exp.new.kindMenu"];
    // This child has both parent and experiments gates in the registry.
    expect(surfaceShownIn("exp.new", state(claims, false))).toBe(false);
    expect(surfaceShownIn("exp.new.kindMenu", state(claims, false))).toBe(
      false,
    );
    expect(surfaceShownIn("exp.new", state(claims, true))).toBe(true);
    expect(surfaceShownIn("exp.new.kindMenu", state(claims, true))).toBe(true);
  });
  it("retains experimentsBasic for an explicitly claimed surface without a parent", () => {
    vi.stubEnv("VITE_DSX_CENSUS_B_TEST", "true");
    const claims = ["palette.runs"];
    expect(surfaceShownIn("palette.runs", state(claims, false))).toBe(false);
    expect(surfaceShownIn("palette.runs", state(claims, true))).toBe(true);
  });
  it.each([undefined, "", "false", "TRUE", "1"])(
    "does not treat %s as an opt-in",
    (value) => {
      vi.stubEnv("VITE_DSX_CENSUS_B_TEST", value);
      expect(censusBTestOptIn()).toBe(false);
      for (const id of HOLDS)
        expect(surfaceShownIn(id, state()), id).toBe(false);
    },
  );
});
