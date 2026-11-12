import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  changeRunControl,
  readRunControl,
} from "src/api/develop/experiment-run-control";
import { stopRequestBody } from "src/api/develop/experiment-stop-request";
import {
  isSurfaceShown,
  setLaunchOverrideForTest,
  surfaceShownIn,
} from "src/launch/launchSwitch";
import { madeRealSurfaceIds } from "src/launch/madeReal";
import ExperimentRunControl from "../ExperimentRunControl";

// F06 (DATA-E2E-021) keeps its XP claim, but the launch hold wins until its own
// final-head walk and independent 10x check pass. The server setting
// EXPERIMENT_RUN_CONTROL_ENABLED is a separate, default-OFF boundary.
vi.mock("src/api/develop/experiment-run-control", () => ({
  readRunControl: vi.fn(),
  changeRunControl: vi.fn(),
}));

beforeEach(() => {
  vi.stubEnv("VITE_DSX_CENSUS_B_TEST", "false");
  // Real MADE_REAL claims: no madeReal override.
  setLaunchOverrideForTest({ launch: true, experimentsBasic: true });
});
afterEach(() => {
  setLaunchOverrideForTest(null);
  vi.unstubAllEnvs();
  vi.clearAllMocks();
});

const claimed = (experimentsBasic = true) => ({
  launch: true,
  experimentsBasic,
  madeReal: madeRealSurfaceIds(),
});

describe("F06 run control stays held when F01/F02 are enabled", () => {
  it("hides Manage runs on the shown experiment page and never reads control", () => {
    expect(madeRealSurfaceIds().has("exp.runControl")).toBe(true);
    expect(isSurfaceShown("exp.page")).toBe(true);
    expect(isSurfaceShown("exp.runControl")).toBe(false);
    render(<ExperimentRunControl experimentId="experiment" />);
    expect(screen.queryByText("Manage runs")).toBeNull();
    expect(readRunControl).not.toHaveBeenCalled();
    expect(changeRunControl).not.toHaveBeenCalled();
  });

  it.each([true, false])(
    "holds the claimed control with experimentsBasic=%s",
    (experimentsBasic) => {
      expect(surfaceShownIn("exp.runControl", claimed(experimentsBasic))).toBe(
        false,
      );
    },
  );

  it("keeps the full control in prototype and unit-test builds", () => {
    setLaunchOverrideForTest({ launch: false, experimentsBasic: false });
    expect(isSurfaceShown("exp.runControl")).toBe(true);
    render(<ExperimentRunControl experimentId="experiment" />);
    expect(screen.getByRole("button", { name: "Manage runs" })).toBeTruthy();
  });

  it("shows the claimed control only in the qualification profile (ON-A)", () => {
    vi.stubEnv("VITE_DSX_CENSUS_B_TEST", "true");
    expect(surfaceShownIn("exp.runControl", claimed())).toBe(true);
    expect(surfaceShownIn("exp.runControl", claimed(false))).toBe(false);
    expect(
      surfaceShownIn("exp.runControl", { ...claimed(), madeReal: new Set() }),
    ).toBe(false);
  });

  it("leaves the classic whole-experiment Stop body empty", () => {
    // The legacy action never needs the selected-run capability.
    expect(stopRequestBody(undefined, false)).toEqual({});
    expect(stopRequestBody(undefined, true)).toEqual({});
    expect(() => stopRequestBody(["run"], false)).toThrow(
      "Stopping individual runs is not available on this server.",
    );
  });
});
