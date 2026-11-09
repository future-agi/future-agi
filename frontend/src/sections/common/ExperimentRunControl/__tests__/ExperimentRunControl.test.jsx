import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  changeRunControl,
  readRunControl,
} from "src/api/develop/experiment-run-control";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import ExperimentRunControl from "../ExperimentRunControl";

vi.mock("src/api/develop/experiment-run-control", () => ({
  readRunControl: vi.fn(),
  changeRunControl: vi.fn(),
}));
afterEach(() => {
  setLaunchOverrideForTest(null);
  vi.clearAllMocks();
});
const runs = [
  { run_id: "selected", name: "First variant", status: "RUNNING" },
  { run_id: "sibling", name: "Second variant", status: "RUNNING" },
];

describe("experiment run controls", () => {
  it("does not mount or fetch without its independent capability", () => {
    setLaunchOverrideForTest({
      launch: true,
      experimentsBasic: true,
      madeReal: ["exp.results.matrix"],
    });
    render(<ExperimentRunControl experimentId="experiment" />);
    expect(screen.queryByText("Manage runs")).toBeNull();
    expect(readRunControl).not.toHaveBeenCalled();
  });
  it("stops and resumes only the selected run and preserves its sibling", async () => {
    readRunControl.mockResolvedValue({ data: { result: { runs } } });
    changeRunControl
      .mockResolvedValueOnce({
        data: { result: { runs: [{ ...runs[0], status: "STOPPED" }] } },
      })
      .mockResolvedValueOnce({ data: { result: { runs: [runs[0]] } } });
    render(<ExperimentRunControl experimentId="experiment" />);
    fireEvent.click(screen.getByRole("button", { name: "Manage runs" }));
    const selected = await screen.findByTestId("run-control-selected");
    fireEvent.click(within(selected).getByRole("button", { name: "Stop" }));
    await waitFor(() =>
      expect(changeRunControl).toHaveBeenCalledWith(
        "experiment",
        "selected",
        true,
      ),
    );
    fireEvent.click(
      await within(selected).findByRole("button", { name: "Resume" }),
    );
    await waitFor(() =>
      expect(changeRunControl).toHaveBeenLastCalledWith(
        "experiment",
        "selected",
        false,
      ),
    );
    expect(
      within(screen.getByTestId("run-control-sibling")).getByText("Running"),
    ).toBeTruthy();
  });
  it("does not report a rejected stop as stopped", async () => {
    readRunControl.mockResolvedValue({ data: { result: { runs } } });
    changeRunControl.mockRejectedValue(new Error("Run not found."));
    render(<ExperimentRunControl experimentId="experiment" />);
    fireEvent.click(screen.getByRole("button", { name: "Manage runs" }));
    const selected = await screen.findByTestId("run-control-selected");
    fireEvent.click(within(selected).getByRole("button", { name: "Stop" }));
    expect(await screen.findByText("Run not found.")).toBeTruthy();
    expect(within(selected).getByText("Running")).toBeTruthy();
  });
});
