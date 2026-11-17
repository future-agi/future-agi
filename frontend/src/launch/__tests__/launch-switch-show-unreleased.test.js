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
  it("ships on while judge improvement awaits a claim", () => {
    expect(SHOW_UNRELEASED_PARTS).toBe(true);
    expect(isSurfaceShown("exp.new")).toBe(true);
    expect(isSurfaceShown("exp.new.kindMenu")).toBe(true);
    expect(hiddenSurfaceIds()).toEqual(["evals.agreementTools"]);
  });

  it("an all-on launch shows experiment tools while agreement awaits a claim", () => {
    setLaunchOverrideForTest({
      launch: true,
      experimentsBasic: false,
      madeReal: [],
    });
    expect(isSurfaceShown("exp.new")).toBe(true);
    expect(isSurfaceShown("exp.new.kindMenu")).toBe(true);
    expect(isSurfaceShown("exp.results.ciComment")).toBe(true);
    expect(hiddenSurfaceIds()).toEqual(["evals.agreementTools"]);
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

  it("non-launch builds also require the improvement claim", () => {
    setLaunchOverrideForTest({ launch: false, showUnreleased: false });
    expect(isSurfaceShown("exp.new.kindMenu")).toBe(true);
    expect(hiddenSurfaceIds()).toEqual(["evals.agreementTools"]);
  });

  it("clearing the override returns to the shipped state", () => {
    setLaunchOverrideForTest({ launch: true, showUnreleased: false });
    setLaunchOverrideForTest(null);
    expect(isSurfaceShown("exp.new.kindMenu")).toBe(true);
  });
});
