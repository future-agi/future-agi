import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";

const useHarnessRunReport = vi.fn();
vi.mock("src/api/harness/releases", () => ({
  useHarnessRunReport: (...args) => useHarnessRunReport(...args),
}));
vi.mock("src/api/simulate-environments/runs", () => ({
  useEnvironmentRuns: () => ({ runs: [] }),
}));

const { default: RunReport } = await import("../RunReport");

const REPORT = {
  run_mode: "tool_routed",
  coverage: { runnable: 7, suite_scenarios: 8, base_scenarios: 8 },
};
const read = (data) => ({ data, error: null, isLoading: false });
const failed = (message, data) => ({
  data,
  error: { message },
  isLoading: false,
});
const idle = { data: undefined, error: null, isLoading: false };

const field = () => screen.queryByLabelText("Compare with run");
const coverage = () => screen.queryByText(/7 runnable of 8/);
const render_ = () =>
  render(<RunReport env={{ id: "env-1" }} executionId="run-1" live />);
const compareWith = (value) => {
  fireEvent.change(field(), { target: { value } });
  fireEvent.keyDown(field(), { key: "Enter" });
};

let own;
let compared;
beforeEach(() => {
  own = read(REPORT);
  compared = idle;
  useHarnessRunReport.mockImplementation((envId, runId, baseline) =>
    !envId ? idle : baseline ? compared : own,
  );
});

describe("RunReport", () => {
  it("keeps the report and the field when a baseline can't be compared with", () => {
    compared = failed("Must be a valid UUID.");
    render_();
    compareWith("nope");

    expect(screen.getByText("Must be a valid UUID.")).toBeInTheDocument();
    expect(field()).toHaveValue("nope");
    expect(coverage()).toBeInTheDocument();
  });

  it("keeps the report and the field while a comparison loads", () => {
    compared = { ...idle, isLoading: true };
    render_();
    compareWith("run-0");

    expect(field()).toHaveValue("run-0");
    expect(coverage()).toBeInTheDocument();
  });

  it("shows the comparison once it is read", () => {
    compared = read({
      ...REPORT,
      comparability: { comparable: false, differing: ["suite"] },
    });
    render_();
    compareWith("run-0");

    expect(screen.getByText(/The runs differ in: suite/)).toBeInTheDocument();
  });

  it("keeps a loaded report up when a later read of it fails", () => {
    own = failed("gateway timeout", REPORT);
    render_();

    expect(screen.getByText("gateway timeout")).toBeInTheDocument();
    expect(coverage()).toBeInTheDocument();
    expect(field()).toBeInTheDocument();
  });

  it("is only the error when there is nothing else to show", () => {
    own = failed("run not found");
    render_();

    expect(screen.getByText("run not found")).toBeInTheDocument();
    expect(field()).toBeNull();
  });
});
