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
// The switch ships on, so every surface shows (the last block below). The
// cases that pin what the launch gates hide set the same launch override with
// the switch off first, so the gates decide as they do with the switch off.
const switchOff = () =>
  setLaunchOverrideForTest({ ...state(), showUnreleased: false });
describe("census-(B) test opt-in", () => {
  it("retains launch and real-backend mode while overriding only the census holds", () => {
    switchOff();
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
    switchOff();
    const claimed = state(["exp.new.splitScope"], false);
    // All-on F50: no longer held, so its claim admits it without the opt-in.
    expect(surfaceShownIn("exp.new.splitScope", claimed)).toBe(true);
    vi.stubEnv("VITE_DSX_CENSUS_B_TEST", "true");
    expect(surfaceShownIn("exp.new.splitScope", claimed)).toBe(true);
    expect(surfaceShownIn("exp.new", claimed)).toBe(false);
  });
  it("keeps an explicitly claimed child hidden with its experiment parent", () => {
    switchOff();
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
    switchOff();
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

// The release as shipped: the switch stays on, so the surfaces the opt-in
// cases pin hidden show without a claim or the opt-in, flag OFF and ON, in
// the same launch build. A gate check only; it checks no words.
describe("census-(B) test opt-in as shipped (unreleased parts shown)", () => {
  it("shows the pinned surfaces without a claim or the opt-in, flag OFF and ON", () => {
    expect(censusBTestOptIn()).toBe(false);
    expect(isLaunchBuild()).toBe(true);
    for (const basic of [false, true]) {
      const hidden = [
        "dataset.export",
        "dataset.evalVersions",
        "evals.remote",
        "list.organize.metadata",
        "list.organize.agents",
        "exp.new",
        "exp.new.kindMenu",
        "palette.runs",
      ].filter((id) => !surfaceShownIn(id, state([], basic)));
      expect(hidden, `experimentsBasic=${basic}`).toEqual([]);
    }
  });
});
