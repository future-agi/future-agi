// The show-unreleased switch is read through the tests-only launch override,
// so a test can pin both states: the shipped one (every registered surface
// shows) and the one where the launch gates decide.
import { afterEach, describe, expect, it } from "vitest";
import {
  SHOW_UNRELEASED_PARTS,
  hiddenSurfaceIds,
  isSurfaceShown,
  setLaunchOverrideForTest,
} from "../launchSwitch";

afterEach(() => {
  setLaunchOverrideForTest(null);
});

describe("show-unreleased switch", () => {
  it("ships on: every registered surface shows", () => {
    expect(SHOW_UNRELEASED_PARTS).toBe(true);
    expect(isSurfaceShown("exp.new")).toBe(true);
    expect(isSurfaceShown("exp.new.kindMenu")).toBe(true);
    expect(hiddenSurfaceIds()).toEqual([]);
  });

  it("an override that leaves showUnreleased out behaves as before", () => {
    setLaunchOverrideForTest({
      launch: true,
      experimentsBasic: false,
      madeReal: [],
    });
    expect(isSurfaceShown("exp.new")).toBe(true);
    expect(isSurfaceShown("exp.new.kindMenu")).toBe(true);
    expect(hiddenSurfaceIds()).toEqual([]);
  });

  it("with showUnreleased off, the launch gates decide", () => {
    setLaunchOverrideForTest({
      launch: true,
      experimentsBasic: false,
      madeReal: [],
      showUnreleased: false,
    });
    expect(isSurfaceShown("exp.new")).toBe(false);
    expect(isSurfaceShown("exp.new.kindMenu")).toBe(false);
    expect(hiddenSurfaceIds()).toContain("exp.new.kindMenu");

    setLaunchOverrideForTest({
      launch: true,
      experimentsBasic: true,
      madeReal: ["exp.new.kindMenu"],
      showUnreleased: false,
    });
    expect(isSurfaceShown("exp.new")).toBe(true);
    expect(isSurfaceShown("exp.new.kindMenu")).toBe(true);
  });

  it("with showUnreleased off, a build that is not a launch shows everything", () => {
    setLaunchOverrideForTest({ launch: false, showUnreleased: false });
    expect(isSurfaceShown("exp.new.kindMenu")).toBe(true);
    expect(hiddenSurfaceIds()).toEqual([]);
  });

  it("clearing the override returns to the shipped state", () => {
    setLaunchOverrideForTest({ launch: true, showUnreleased: false });
    setLaunchOverrideForTest(null);
    expect(isSurfaceShown("exp.new.kindMenu")).toBe(true);
  });
});
