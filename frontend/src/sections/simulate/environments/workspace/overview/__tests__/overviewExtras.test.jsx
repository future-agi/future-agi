import { describe, it, expect, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { render } from "src/utils/test-utils";
import { MOCK_WORLD } from "src/api/simulate-environments/_fixtures/world";
import OverviewPanel from "../OverviewPanel";
import NextStepsChecklist from "../NextStepsChecklist";
import SourceToSandboxMap from "../SourceToSandboxMap";

describe("Overview extras wired into OverviewPanel", () => {
  const connected = {
    agent: { via: "endpoint", values: {} },
    scenarios: [],
  };

  it("no longer renders the source-to-sandbox map on an env with a derived world", () => {
    render(
      <OverviewPanel env={MOCK_WORLD} envState={connected} onGo={vi.fn()} agentConnected />
    );
    // The map's origin chips and sandbox targets were derived by position and
    // keyword rather than reported by ALK, so the panel dropped it.
    expect(screen.queryByText("How the world was built")).toBeNull();
  });

  it("renders the next-steps checklist only when there is no agent and no derived world", () => {
    render(
      <OverviewPanel
        env={{ ...MOCK_WORLD, tools: [], rules: [], seed: { tables: [] } }}
        envState={{ scenarios: [] }}

        onGo={vi.fn()}
        agentConnected={false}
      />
    );
    expect(screen.getByText("Next steps")).toBeInTheDocument();
  });

  it("hides the checklist once the env carries a derived world", () => {
    render(
      <OverviewPanel env={MOCK_WORLD} envState={connected} onGo={vi.fn()} agentConnected />
    );
    expect(screen.queryByText("Next steps")).not.toBeInTheDocument();
  });
});

describe("NextStepsChecklist", () => {
  const renderChecklist = (props = {}) =>
    render(<NextStepsChecklist env={MOCK_WORLD} envState={{}} onGo={vi.fn()} {...props} />);

  it("lists every setup step, keyed off its body copy", () => {
    // The scenario/eval step titles collide with their empty-state CTA labels,
    // so each step is identified by its unique body prose.
    renderChecklist();
    expect(screen.getByText(/is set up and ready for configuration/)).toBeInTheDocument();
    expect(screen.getByText(/Wire your agent so it can act/)).toBeInTheDocument();
    expect(screen.getByText(/Tasks the agent has to complete/)).toBeInTheDocument();
    expect(screen.getByText(/Graders that decide whether each run passed/)).toBeInTheDocument();
    expect(screen.getByText(/Kick off a run/)).toBeInTheDocument();
  });

  it("routes the live step CTAs through onGo; the deferred connect-agent step is disabled", async () => {
    const user = userEvent.setup();
    const onGo = vi.fn();
    renderChecklist({ onGo });

    // Connecting an agent is deferred (no Agents tab) — the CTA is disabled
    // rather than a button that no-ops.
    expect(screen.getByRole("button", { name: /connect agent/i })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: /add scenarios/i }));
    expect(onGo).toHaveBeenCalledWith("scenarios");

    await user.click(screen.getByRole("button", { name: /add evaluations/i }));
    expect(onGo).toHaveBeenCalledWith("evals");

    // The disabled connect-agent CTA never fires onGo.
    expect(onGo).not.toHaveBeenCalledWith("agent");
  });

  it("counts completed steps and retires their CTAs as progress is made", () => {
    renderChecklist({ envState: { scenarios: [{}, {}], evals: [{}] } });
    // created + scenarios + evals are done; agent + run remain.
    expect(screen.getByText("3 of 5 complete, 2 left to run your first simulation")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /connect agent/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /add scenarios/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /add evaluations/i })).not.toBeInTheDocument();
  });
});

describe("SourceToSandboxMap", () => {
  const renderMap = (props = {}) => render(<SourceToSandboxMap env={MOCK_WORLD} {...props} />);

  it("renders the source and sandbox columns for the env", () => {
    renderMap();
    expect(screen.getByText("How the world was built")).toBeInTheDocument();
    expect(screen.getByText("Read from source")).toBeInTheDocument();
    expect(screen.getByText("In the sandbox world")).toBeInTheDocument();
    // a mapped tool row and a seeded store row (issue_refund is unique to the
    // tools group; verify_identity also stands in as the external-service actor)
    expect(screen.getByText("issue_refund")).toBeInTheDocument();
    expect(screen.getByText("customers · 240")).toBeInTheDocument();
  });

  it("is a read-only ledger — no resolve homework (that moved to the Contract tab)", () => {
    renderMap();
    // Every tool row states a sandbox target rather than holding one open with a
    // "Needs your answer" prompt; the read/write override lives on Contract now.
    expect(screen.queryByText("Needs your answer")).toBeNull();
    expect(screen.queryByRole("button", { name: /confirm read-only/i })).toBeNull();
  });

  it("reflects a tool-effect override set on the Contract tab (you-confirmed + overridden target)", () => {
    // issue_refund heuristically classifies as a write ("writes to the sandbox").
    // A human override to read-only on Contract stores the singular "read"; the
    // map normalizes it and shows the overridden target + a "you confirmed" mark.
    renderMap({ envState: { toolResolutions: { issue_refund: "read" } } });
    expect(screen.getByText("you confirmed")).toBeInTheDocument();
    expect(screen.getAllByText("reads from fixture state").length).toBeGreaterThan(0);
  });
});
