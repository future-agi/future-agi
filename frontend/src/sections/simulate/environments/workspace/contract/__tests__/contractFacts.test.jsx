import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import useSimulationAccess from "../../../hooks/useSimulationAccess";
import RlContractPanel from "../RlContractPanel";

vi.mock("../../../hooks/useSimulationAccess", () => ({ default: vi.fn() }));

const baseEnv = {
  name: "Refund Copilot",
  rules: ["Verify identity", "Be polite"],
  useCases: ["Refund a late order"],
  endConditions: {
    ended_reasons: ["assistant-ended-call", "customer-ended-call", "cancelled"],
    terminate_reasons: ["assistant-ended-call", "customer-ended-call"],
    truncate_reasons: ["cancelled", "max-duration-reached"],
    max_turns: 12,
    clock: "real-time",
    seed: { value: "7", controls: ["scenario_world_rng"] },
  },
};

const renderPanel = (env = {}) =>
  render(<RlContractPanel env={{ ...baseEnv, ...env }} backed />);

const column = (label) => screen.getByText(label).parentElement;

beforeEach(() => {
  vi.mocked(useSimulationAccess).mockReturnValue({
    canEdit: true,
    canRun: true,
  });
});

describe("RlContractPanel contract facts", () => {
  it("puts each end reason in its own column and keeps the limits with the cut-offs", () => {
    renderPanel();

    const ends = within(column("It ends"));
    const cut = within(column("It's cut off"));
    expect(ends.getByText("assistant-ended-call")).toBeInTheDocument();
    expect(ends.getByText("customer-ended-call")).toBeInTheDocument();
    expect(ends.queryByText("cancelled")).toBeNull();
    expect(cut.getByText("cancelled")).toBeInTheDocument();
    expect(cut.getByText("max-duration-reached")).toBeInTheDocument();
    expect(cut.getByText("12 agent turns")).toBeInTheDocument();
    expect(
      cut.getByText(
        "The most turns across the suite. Each scenario can carry its own limit.",
      ),
    ).toBeInTheDocument();
  });

  it("falls back to the ended reasons when the split is not reported", () => {
    renderPanel({
      endConditions: { ended_reasons: ["silence-timed-out"], clock: "stepped" },
    });

    expect(
      within(column("It ends")).getByText("silence-timed-out"),
    ).toBeInTheDocument();
  });

  it("shows every seed digit and limits its scope to how the world is set up", () => {
    renderPanel({
      endConditions: JSON.parse(
        '{"seed":{"value":"8537093545867607782","controls":["scenario_world_rng"]}}',
      ),
    });
    expect(
      screen.getByText("Same starting data every time"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Every conversation starts from the same data (seed 8537093545867607782). The caller's turns and the agent's replies can still differ between runs.",
      ),
    ).toBeInTheDocument();
  });

  it("does not infer seed scope for an unknown runtime", () => {
    renderPanel({
      endConditions: { seed: { value: "0", controls: ["unknown_rng"] } },
    });
    expect(
      screen.getByText("Same starting data every time"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Every conversation starts from the same seed (0). What else it controls was not reported, so results may still differ between runs.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/starts from the same data/)).toBeNull();
  });

  it("shows no seed when none is reported", () => {
    renderPanel({ endConditions: { ended_reasons: [], clock: "stepped" } });
    expect(screen.getByText("Time · Turn by turn")).toBeInTheDocument();
    expect(screen.queryByText("Same starting data every time")).toBeNull();
  });

  it("no longer shows a sub-goals card, even when the contract carries sub-goals", () => {
    renderPanel({
      subGoals: [
        {
          name: "identity_checked",
          what: "Identity was checked",
          kind: "judge",
          output: "pass_fail",
        },
      ],
    });
    expect(screen.queryByText(/^Sub-goals/)).toBeNull();
    expect(screen.queryByText("Identity was checked")).toBeNull();
  });
});
