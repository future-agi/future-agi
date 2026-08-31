import { describe, it, expect, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import StageRoadmap from "../StageRoadmap";

const statusWith = (overrides = {}) => ({
  session: { id: "s1" },
  stage: "understand",
  stages: { reception: "", understand: "", build: "", scenarios: "", run: "" },
  agent: "drive_thru",
  have: {},
  busy: false,
  ...overrides,
});

describe("StageRoadmap", () => {
  it("shows each stage by its label, never its raw key", () => {
    render(<StageRoadmap status={statusWith()} onSelectStage={() => {}} />);
    // "Runs" is commented out of stages.js for now; add it back here when it returns.
    ["Agent", "Contract", "Environment", "Scenarios"].forEach((label) => {
      expect(screen.getByRole("button", { name: new RegExp(label) })).toBeInTheDocument();
    });
    expect(screen.queryByText("understand")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Runs/ })).not.toBeInTheDocument();
  });

  it("disables a stage the server says is blocked and quotes its reason", () => {
    const status = statusWith({
      stages: { reception: "", understand: "", build: "needs a contract first", scenarios: "", run: "" },
    });
    render(<StageRoadmap status={status} onSelectStage={() => {}} />);
    const environment = screen.getByRole("button", { name: /Environment/ });
    expect(environment).toBeDisabled();
    expect(environment).toHaveAttribute("title", "needs a contract first");
  });

  it("opens a reachable stage", async () => {
    const onSelectStage = vi.fn();
    render(<StageRoadmap status={statusWith()} onSelectStage={onSelectStage} />);
    await userEvent.click(screen.getByRole("button", { name: /Scenarios/ }));
    expect(onSelectStage).toHaveBeenCalledWith("scenarios");
  });

  it("summarises what each stage has produced", () => {
    const status = statusWith({
      have: { contract: true, world: true, sub_goals: 7, scenarios: 5, runs: 2, runs_passed: 1 },
    });
    render(<StageRoadmap status={status} onSelectStage={() => {}} />);
    expect(screen.getByText("7 sub-goals")).toBeInTheDocument();
    expect(screen.getByText("5 proved")).toBeInTheDocument();
    // The run tally goes with the hidden Runs stage.
    expect(screen.queryByText("1/2 passed")).not.toBeInTheDocument();
  });

  it("cannot open any stage before a session exists", () => {
    render(<StageRoadmap status={statusWith({ session: null })} onSelectStage={() => {}} />);
    expect(screen.getByRole("button", { name: /Contract/ })).toBeDisabled();
  });
});
