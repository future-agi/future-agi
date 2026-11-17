import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
import { resetRunControlForTest } from "../runControlAvailability";

vi.mock("src/api/develop/experiment-run-control", () => ({
  readRunControl: vi.fn(),
  changeRunControl: vi.fn(),
}));

beforeEach(() => {
  vi.stubEnv("VITE_DSX_CENSUS_B_TEST", "false");
  resetRunControlForTest();
  setLaunchOverrideForTest({
    showUnreleased: false,
    launch: true,
    experimentsBasic: true,
  });
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

const response = (status) => ({
  data: {
    result: {
      runs: [{ run_id: "selected", name: "Selected run", status }],
    },
  },
});

describe("Run control in launch builds", () => {
  it("shows Manage runs without a special build profile", () => {
    expect(madeRealSurfaceIds().has("exp.runControl")).toBe(true);
    expect(isSurfaceShown("exp.page")).toBe(true);
    expect(isSurfaceShown("exp.runControl")).toBe(true);
    render(<ExperimentRunControl experimentId="experiment" />);
    expect(screen.getByRole("button", { name: "Manage runs" }).disabled).toBe(
      false,
    );
    expect(readRunControl).not.toHaveBeenCalled();
    expect(changeRunControl).not.toHaveBeenCalled();
  });

  it("reads, stops and resumes the selected run in a launch build", async () => {
    readRunControl.mockResolvedValue(response("RUNNING"));
    changeRunControl
      .mockResolvedValueOnce(response("STOPPED"))
      .mockResolvedValueOnce(response("RUNNING"));
    render(<ExperimentRunControl experimentId="experiment" />);
    fireEvent.click(screen.getByRole("button", { name: "Manage runs" }));
    fireEvent.click(await screen.findByRole("button", { name: "Stop" }));
    await waitFor(() =>
      expect(changeRunControl).toHaveBeenCalledWith(
        "experiment",
        "selected",
        true,
      ),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Resume" }));
    await waitFor(() =>
      expect(changeRunControl).toHaveBeenCalledWith(
        "experiment",
        "selected",
        false,
      ),
    );
    await screen.findByRole("button", { name: "Stop" });
  });

  it.each([true, false])(
    "respects the experiment page choice in registry mode with experimentsBasic=%s",
    (experimentsBasic) => {
      setLaunchOverrideForTest({ showUnreleased: false });
      expect(surfaceShownIn("exp.runControl", claimed(experimentsBasic))).toBe(
        experimentsBasic,
      );
    },
  );

  it("keeps the full control in prototype and unit-test builds", () => {
    setLaunchOverrideForTest({
      showUnreleased: false,
      launch: false,
      experimentsBasic: false,
    });
    expect(isSurfaceShown("exp.runControl")).toBe(true);
    render(<ExperimentRunControl experimentId="experiment" />);
    expect(screen.getByRole("button", { name: "Manage runs" })).toBeTruthy();
  });

  it("requires the registered capability in registry mode", () => {
    setLaunchOverrideForTest({ showUnreleased: false });
    expect(surfaceShownIn("exp.runControl", claimed())).toBe(true);
    expect(
      surfaceShownIn("exp.runControl", { ...claimed(), madeReal: new Set() }),
    ).toBe(false);
  });

  it("leaves the classic whole-experiment Stop body empty", () => {
    expect(stopRequestBody(undefined, false)).toEqual({});
    expect(stopRequestBody(undefined, true)).toEqual({});
    expect(() => stopRequestBody(["run"], false)).toThrow(
      "Stopping individual runs is not available on this server.",
    );
  });
});
