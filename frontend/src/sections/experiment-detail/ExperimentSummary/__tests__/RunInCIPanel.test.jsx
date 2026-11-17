import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { SURFACE_BY_ID } from "src/launch/launchSurfaces";
import { madeRealSurfaceIds } from "src/launch/madeReal";
import { ciCommentStep } from "src/sections/common/RunSources/snippets/ciComment";
import RunInCIPanel from "../RunInCIPanel";

const surface = "exp.results.ciComment";
const experimentId = "10000000-0000-4000-8000-000000000001";
const candidate = "20000000-0000-4000-8000-000000000002";
const baseline = "30000000-0000-4000-8000-000000000003";
const other = "40000000-0000-4000-8000-000000000004";
const props = {
  experimentId,
  baselineId: baseline,
  runs: [
    { run_id: candidate, label: "Candidate" },
    { run_id: baseline, label: "Baseline" },
    { run_id: other, label: "Earlier run" },
  ],
};
const clipboardDescriptor = Object.getOwnPropertyDescriptor(
  navigator,
  "clipboard",
);

afterEach(() => {
  cleanup();
  setLaunchOverrideForTest(null);
  if (clipboardDescriptor)
    Object.defineProperty(navigator, "clipboard", clipboardDescriptor);
  else delete navigator.clipboard;
  vi.restoreAllMocks();
});

describe("Run in CI panel", () => {
  it.each([false, true])(
    "shows a scope line without a claim with all-on=%s",
    (allOn) => {
      setLaunchOverrideForTest({
        launch: true,
        experimentsBasic: true,
        madeReal: [],
        showUnreleased: allOn,
      });
      render(<RunInCIPanel {...props} />);
      expect(screen.getByTestId(`${surface}-scope-line`)).toHaveTextContent(
        "Copy a GitHub Actions step to comment on run regressions after your existing evaluation upload.",
      );
      expect(screen.getByRole("button", { name: "Run in CI" })).toBeVisible();
      expect(SURFACE_BY_ID[surface].ships).toBe("shown");
      expect(SURFACE_BY_ID[surface].requiresClaim).toBeUndefined();
      expect(madeRealSurfaceIds().has(surface)).toBe(false);
    },
  );

  it("shows the entry point with experiment pages off and no claim", () => {
    setLaunchOverrideForTest({
      launch: true,
      experimentsBasic: false,
      madeReal: [],
      showUnreleased: true,
    });
    render(<RunInCIPanel {...props} />);
    expect(screen.getByRole("button", { name: "Run in CI" })).toBeVisible();
    expect(screen.getByTestId(`${surface}-scope-line`)).toBeVisible();
  });

  it("copies the step with the user's baseline and threshold", async () => {
    setLaunchOverrideForTest({
      launch: true,
      experimentsBasic: true,
      madeReal: [surface],
      showUnreleased: false,
    });
    const copy = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: copy },
    });
    render(<RunInCIPanel {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "Run in CI" }));
    expect(screen.queryByLabelText("Candidate run")).toBeNull();
    expect(screen.getByRole("dialog", { name: "Run in CI" })).toBeVisible();
    expect(screen.getByLabelText("Baseline run")).toHaveValue(baseline);
    fireEvent.click(
      screen.getByRole("button", { name: "Copy GitHub Actions step" }),
    );
    await waitFor(() =>
      expect(copy).toHaveBeenLastCalledWith(
        ciCommentStep({ experimentId, baselineRunId: baseline }),
      ),
    );
    fireEvent.change(screen.getByLabelText("Baseline run"), {
      target: { value: "" },
    });
    expect(screen.queryByTestId("ci-comment-snippet")).toBeNull();
    expect(screen.getByRole("alert").textContent).toBe(
      "Choose an experiment and a baseline run.",
    );
    fireEvent.change(screen.getByLabelText("Baseline run"), {
      target: { value: other },
    });
    fireEvent.change(screen.getByLabelText("Allowed regressions"), {
      target: { value: "2" },
    });
    fireEvent.change(screen.getByLabelText("Regressed rows to include"), {
      target: { value: "3" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Copy GitHub Actions step" }),
    );
    await waitFor(() =>
      expect(copy).toHaveBeenLastCalledWith(
        ciCommentStep({
          experimentId,
          baselineRunId: other,
          maxRegressed: 2,
          topN: 3,
        }),
      ),
    );
    expect(copy).toHaveBeenCalledTimes(2);
  });

  it("offers the dataset baseline and explains a missing baseline", () => {
    setLaunchOverrideForTest({ launch: false });
    const { rerender } = render(
      <RunInCIPanel experimentId={experimentId} runs={[props.runs[0]]} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Run in CI" }));
    expect(screen.queryByTestId("ci-comment-snippet")).toBeNull();
    expect(screen.getByRole("alert").textContent).toBe(
      "Choose an experiment and a baseline run.",
    );
    rerender(
      <RunInCIPanel
        experimentId={experimentId}
        runs={[props.runs[0]]}
        datasetBaseline={{ run_id: other, label: "Dataset baseline" }}
      />,
    );
    fireEvent.change(screen.getByLabelText("Baseline run"), {
      target: { value: other },
    });
    expect(screen.getByLabelText("Baseline run")).toHaveValue(other);
    expect(screen.getByTestId("ci-comment-snippet")).toBeVisible();
    fireEvent.change(screen.getByLabelText("Regressed rows to include"), {
      target: { value: "21" },
    });
    expect(screen.queryByTestId("ci-comment-snippet")).toBeNull();
    expect(screen.getByRole("alert").textContent).toBe(
      "Use a non-negative threshold and 1–20 regressed rows.",
    );
  });
});
