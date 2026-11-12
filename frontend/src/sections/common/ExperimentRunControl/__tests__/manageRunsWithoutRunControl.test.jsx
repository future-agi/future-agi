// Manage runs on a server without run control: its read answers 404
// "run_control_not_enabled", which no retry can change. The dialog says the
// shared line once and stops reading (it used to show "Could not refresh
// runs" and read again every 1.5 s), and the button then stays disabled with
// the same line. Both launch-switch states are pinned the way the
// launch-gate tests pin them.
import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { NOT_IN_RELEASE } from "src/components/NotInRelease/notInRelease";

const api = vi.hoisted(() => ({ read: vi.fn(), change: vi.fn() }));
const unreleased = vi.hoisted(() => ({ shown: true }));
vi.mock("src/launch/launchSwitch", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    get SHOW_UNRELEASED_PARTS() {
      return unreleased.shown;
    },
  };
});
vi.mock("src/api/develop/experiment-run-control", () => ({
  readRunControl: (...args) => api.read(...args),
  changeRunControl: (...args) => api.change(...args),
}));

const notEnabled = () => ({
  status: false,
  code: "run_control_not_enabled",
  detail: "Run control is not available.",
  message: "Run control is not available.",
  statusCode: 404,
});
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// A fresh module graph per test: what the page learned from the server
// lives only for one page session.
async function mount({ shown }) {
  vi.resetModules();
  unreleased.shown = shown;
  const launch = await import("src/launch/launchSwitch");
  launch.setLaunchOverrideForTest(
    shown
      ? { launch: true, experimentsBasic: true }
      : { launch: true, showUnreleased: false, experimentsBasic: true },
  );
  const { default: ExperimentRunControl } = await import(
    "../ExperimentRunControl"
  );
  return render(<ExperimentRunControl experimentId="experiment" />);
}

beforeAll(async () => {
  await import("../ExperimentRunControl");
}, 120000);
afterEach(async () => {
  const launch = await import("src/launch/launchSwitch");
  launch.setLaunchOverrideForTest(null);
  api.read.mockReset();
  api.change.mockReset();
});

describe.each([
  { state: "switch on (shipped build)", shown: true },
  { state: "switch off", shown: false },
])("Manage runs without run control, $state", ({ shown }) => {
  it("reads once, says the shared line, and never reads again", async () => {
    api.read.mockRejectedValue(notEnabled());
    await mount({ shown });
    fireEvent.click(screen.getByRole("button", { name: "Manage runs" }));
    expect(await screen.findByText(NOT_IN_RELEASE)).toBeInTheDocument();
    expect(screen.queryByText(/Could not refresh runs/)).toBeNull();
    expect(screen.queryByLabelText("Loading runs")).toBeNull();
    await pause(1700);
    expect(api.read).toHaveBeenCalledTimes(1);
    expect(api.change).not.toHaveBeenCalled();

    // Closed, the button stays where it was: disabled, with the same line.
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    const manage = screen.getByRole("button", { name: "Manage runs" });
    expect(manage).toBeDisabled();
    expect(
      document.getElementById(manage.getAttribute("aria-describedby")),
    ).toHaveTextContent(NOT_IN_RELEASE);
    fireEvent.click(manage);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(api.read).toHaveBeenCalledTimes(1);
  });

  it("keeps reading and controlling runs where run control answers", async () => {
    api.read.mockResolvedValue({
      data: {
        result: {
          runs: [
            { run_id: "selected", name: "First variant", status: "RUNNING" },
          ],
        },
      },
    });
    await mount({ shown });
    fireEvent.click(screen.getByRole("button", { name: "Manage runs" }));
    expect(await screen.findByText("First variant")).toBeInTheDocument();
    expect(screen.queryByText(NOT_IN_RELEASE)).toBeNull();
    await waitFor(() => expect(api.read.mock.calls.length).toBeGreaterThan(1), {
      timeout: 2500,
    });
  });
});
