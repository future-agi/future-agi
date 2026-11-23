import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import useSimulationAccess from "../../../hooks/useSimulationAccess";
import { ActorsCard } from "../ContractFactsCards";
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
    expect(cut.getByText("12 turns")).toBeInTheDocument();
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
      screen.getByText("World seed · 8537093545867607782"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/^Controls how the world's records are set up/),
    ).toHaveTextContent(
      "It does not make conversations or results repeat exactly.",
    );
  });

  it("does not infer seed scope for an unknown runtime", () => {
    renderPanel({
      endConditions: { seed: { value: "0", controls: ["unknown_rng"] } },
    });
    expect(screen.getByText("World seed · 0")).toBeInTheDocument();
    expect(
      screen.getByText(/^What this seed controls was not reported\./),
    ).toBeInTheDocument();
    expect(screen.queryByText(/^Controls how the world/)).toBeNull();
  });

  it("shows no seed when none is reported", () => {
    renderPanel({ endConditions: { ended_reasons: [], clock: "stepped" } });
    expect(screen.queryByText(/^World seed ·/)).toBeNull();
  });

  it("names a rule by its short label, with the full rule, what enforces it and its check", () => {
    const text =
      "Use simple, everyday words. Avoid fan slang and technical terms.";
    renderPanel({
      rules: [text, "Be polite"],
      ruleDetails: [
        {
          text,
          short: "simple_words_explain_terms",
          enforced_by: ["prompt"],
          sub_goal: "identity_checked",
          origin: { kind: "system_prompt", field: "general_prompt" },
        },
        { text: "Be polite" },
      ],
    });

    expect(screen.getByText("simple_words_explain_terms")).toBeInTheDocument();
    expect(screen.getByText(text)).toBeInTheDocument();
    expect(
      screen.getByText("Enforced by prompt · Checked by identity_checked"),
    ).toBeInTheDocument();
    expect(screen.getByText("System prompt")).toBeInTheDocument();
    expect(screen.getAllByText(/^Enforced by/)).toHaveLength(1);
  });

  it("shows where a use case was read from", () => {
    renderPanel({
      useCaseDetails: [
        {
          text: "Refund a late order",
          origin: { kind: "provider_tool_list", field: "issue_refund" },
        },
      ],
    });
    expect(screen.getByText("Tool list")).toBeInTheDocument();
  });

  it("lists the sub-goals with their kind, output and overlay", () => {
    renderPanel({
      subGoals: [
        {
          name: "identity_checked",
          what: "Identity was checked",
          kind: "judge",
          output: "pass_fail",
          overlay: "authentication",
        },
      ],
    });

    expect(screen.getByText("Sub-goals (1)")).toBeInTheDocument();
    expect(screen.getByText("identity_checked")).toBeInTheDocument();
    expect(screen.getByText("judge")).toBeInTheDocument();
    expect(screen.getByText("pass/fail")).toBeInTheDocument();
    expect(screen.getByText("authentication")).toBeInTheDocument();
  });

  it("shows no sub-goals card when the contract carries none", () => {
    renderPanel();
    expect(screen.queryByText(/^Sub-goals/)).toBeNull();
  });
});

describe("ActorsCard", () => {
  it("lists the actors with their role and origin", () => {
    render(
      <ActorsCard
        actors={[
          {
            name: "supervisor",
            role: "approves refunds over $200",
            origin: { kind: "system_prompt", field: "general_prompt" },
          },
        ]}
      />,
    );

    expect(screen.getByText("Actors (1)")).toBeInTheDocument();
    expect(screen.getByText("supervisor")).toBeInTheDocument();
    expect(screen.getByText("approves refunds over $200")).toBeInTheDocument();
    expect(screen.getByText("System prompt")).toBeInTheDocument();
  });

  it("renders nothing without actors", () => {
    const { container } = render(<ActorsCard actors={[]} />);
    expect(container.firstChild).toBeNull();
  });
});
