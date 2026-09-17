import { describe, it, expect, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { render } from "src/utils/test-utils";
import { MOCK_WORLD } from "src/api/simulate-environments/_fixtures/world";
import OverviewPanel from "../OverviewPanel";
import AgentRefreshBanner from "../AgentRefreshBanner";
import NextStepsChecklist from "../NextStepsChecklist";
import SourceToSandboxMap from "../SourceToSandboxMap";

const REFRESH_HEADING = "Agent v2 is newer than this environment";

describe("AgentRefreshBanner", () => {
  it("shows when the active agent version has moved ahead of the derived-for version", () => {
    render(
      <AgentRefreshBanner
        env={MOCK_WORLD}
        envState={{
          agentVersions: [{ label: "v1" }, { label: "v2" }],
          activeAgentVersion: "v2",
          envDerivedForAgent: "v1",
        }}
        patch={vi.fn()}
      />
    );
    expect(screen.getByText(REFRESH_HEADING)).toBeInTheDocument();
  });

  it("hides when the environment was already derived against the active agent", () => {
    render(
      <AgentRefreshBanner
        env={MOCK_WORLD}
        envState={{
          agentVersions: [{ label: "v1" }, { label: "v2" }],
          activeAgentVersion: "v2",
          envDerivedForAgent: "v2",
        }}
        patch={vi.fn()}
      />
    );
    expect(screen.queryByText(REFRESH_HEADING)).not.toBeInTheDocument();
  });

  it("hides when there is no divergence (no envDerivedForAgent, agent on v1)", () => {
    render(
      <AgentRefreshBanner
        env={MOCK_WORLD}
        envState={{ activeAgentVersion: "v1" }}
        patch={vi.fn()}
      />
    );
    expect(screen.queryByText(/is newer than this environment/)).not.toBeInTheDocument();
  });

  it("re-derives the world against the active agent on refresh", async () => {
    const user = userEvent.setup();
    const patch = vi.fn();
    render(
      <AgentRefreshBanner
        env={MOCK_WORLD}
        envState={{
          agentVersions: [{ label: "v1" }, { label: "v2" }],
          activeAgentVersion: "v2",
          envDerivedForAgent: "v1",
        }}
        patch={patch}
      />
    );
    await user.click(screen.getByRole("button", { name: /refresh environment/i }));
    expect(patch).toHaveBeenCalledTimes(1);
    expect(patch.mock.calls[0][0]).toMatchObject({ envDerivedForAgent: "v2" });
  });
});

describe("AgentRefreshBanner inside OverviewPanel", () => {
  const movedAhead = {
    agent: { via: "endpoint", values: {} },
    agentVersions: [{ label: "v1" }, { label: "v2" }],
    activeAgentVersion: "v2",
    envDerivedForAgent: "v1",
    scenarios: [],
  };

  it("renders the refresh banner on an unlocked env that moved ahead", () => {
    render(
      <OverviewPanel env={MOCK_WORLD} envState={movedAhead} patch={vi.fn()} onGo={vi.fn()} agentConnected />
    );
    expect(screen.getByText(REFRESH_HEADING)).toBeInTheDocument();
  });

  it("never renders the refresh banner on a locked env", () => {
    render(
      <OverviewPanel env={MOCK_WORLD} envState={movedAhead} patch={vi.fn()} onGo={vi.fn()} agentConnected locked />
    );
    expect(screen.queryByText(REFRESH_HEADING)).not.toBeInTheDocument();
  });
});

describe("Overview extras wired into OverviewPanel", () => {
  const connected = {
    agent: { via: "endpoint", values: {} },
    agentVersions: [{ label: "v1" }],
    activeAgentVersion: "v1",
    scenarios: [],
  };

  it("renders the source-to-sandbox map on an env with a derived world", () => {
    render(
      <OverviewPanel env={MOCK_WORLD} envState={connected} patch={vi.fn()} onGo={vi.fn()} agentConnected />
    );
    expect(screen.getByText("How the world was built")).toBeInTheDocument();
  });

  it("renders the next-steps checklist only when there is no agent and no derived world", () => {
    render(
      <OverviewPanel
        env={{ ...MOCK_WORLD, tools: [], rules: [], seed: { tables: [] } }}
        envState={{ scenarios: [] }}
        patch={vi.fn()}
        onGo={vi.fn()}
        agentConnected={false}
      />
    );
    expect(screen.getByText("Next steps")).toBeInTheDocument();
  });

  it("hides the checklist once the env carries a derived world", () => {
    render(
      <OverviewPanel env={MOCK_WORLD} envState={connected} patch={vi.fn()} onGo={vi.fn()} agentConnected />
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

  it("routes each step's CTA through onGo", async () => {
    const user = userEvent.setup();
    const onGo = vi.fn();
    renderChecklist({ onGo });

    await user.click(screen.getByRole("button", { name: /connect agent/i }));
    expect(onGo).toHaveBeenCalledWith("agent");

    await user.click(screen.getByRole("button", { name: /add scenarios/i }));
    expect(onGo).toHaveBeenCalledWith("scenarios");

    await user.click(screen.getByRole("button", { name: /add evaluations/i }));
    expect(onGo).toHaveBeenCalledWith("evals");
  });

  it("counts completed steps and retires their CTAs as progress is made", () => {
    renderChecklist({ envState: { scenarios: [{}, {}], evals: [{}] } });
    // created + scenarios + evals are done; agent + run remain.
    expect(screen.getByText("3 of 5 complete — 2 left to run your first simulation")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /connect agent/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /add scenarios/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /add evaluations/i })).not.toBeInTheDocument();
  });
});

describe("SourceToSandboxMap", () => {
  const renderMap = (props = {}) =>
    render(<SourceToSandboxMap env={MOCK_WORLD} envState={{}} patch={vi.fn()} {...props} />);

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

  it("holds the last unclassifiable tool open for the reader to resolve", () => {
    renderMap();
    expect(screen.getByText("Needs your answer")).toBeInTheDocument();
    expect(screen.getByText(/Does/)).toBeInTheDocument();
  });

  it("records the reader's resolution via patch", async () => {
    const user = userEvent.setup();
    const patch = vi.fn();
    renderMap({ patch });

    // the inline resolver defaults to read-only
    await user.click(screen.getByRole("button", { name: /confirm read-only/i }));
    expect(patch).toHaveBeenCalledTimes(1);
    expect(patch.mock.calls[0][0]).toEqual({
      toolResolutions: { escalate_to_human: "reads" },
    });
  });

  it("shows a confirmed chip once a tool has been resolved", () => {
    renderMap({ envState: { toolResolutions: { escalate_to_human: "writes" } } });
    expect(screen.getByText("you confirmed")).toBeInTheDocument();
  });
});
