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

  it("routes the live step CTAs through onGo; the deferred connect-agent step is disabled", async () => {
    const user = userEvent.setup();
    const onGo = vi.fn();
    renderChecklist({ onGo });

    // Connecting an agent is deferred (no Agents tab) — the CTA is disabled
    // behind the coming-soon tooltip rather than a button that no-ops.
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
  const renderMap = (env = {}, props = {}) =>
    render(<SourceToSandboxMap env={{ ...MOCK_WORLD, ...env }} {...props} />);

  it("renders the source and sandbox columns with each group's count", () => {
    renderMap();
    expect(screen.getByText("How the world was built")).toBeInTheDocument();
    expect(screen.getByText("Read from source")).toBeInTheDocument();
    expect(screen.getByText("In the sandbox world")).toBeInTheDocument();
    expect(screen.getByText(`${MOCK_WORLD.tools.length} mapped`)).toBeInTheDocument();
    expect(screen.getByText("customers")).toBeInTheDocument();
    expect(screen.getByText("240 rows seeded")).toBeInTheDocument();
  });

  it("states a tool's sandbox target only from the effect ALK reported", () => {
    renderMap({
      tools: [
        { name: "lookup_order", effectKind: "read", origin: { kind: "tool_list", field: "lookup_order" } },
        { name: "issue_refund" },
      ],
    });
    expect(screen.getByText("reads from the sandbox")).toBeInTheDocument();
    expect(screen.getByText("effect not reported")).toBeInTheDocument();
    expect(screen.getByText("TOOL LIST")).toBeInTheDocument();
    expect(screen.queryByText("writes to the sandbox")).toBeNull();
  });

  it("says what enforces a rule and which sub-goal checks it", () => {
    renderMap({
      rules: ["Verify identity", "Be polite"],
      ruleDetails: [
        {
          text: "Verify identity",
          enforced_by: ["prompt"],
          sub_goal: "identity_checked",
          origin: { kind: "system_prompt", field: "general_prompt" },
        },
        { text: "Be polite" },
      ],
    });
    expect(screen.getByText("enforced by prompt · checked by identity_checked")).toBeInTheDocument();
    expect(screen.getByText("enforcement not reported")).toBeInTheDocument();
    expect(screen.getByText("SYSTEM PROMPT")).toBeInTheDocument();
  });

  it("names a rule by its short label and shows the full rule under it", () => {
    const text =
      "Use simple, everyday words. Avoid fan slang and technical terms. If you must use a One Piece word, explain it in a few plain words.";
    renderMap({
      rules: [text, "Be polite"],
      ruleDetails: [{ text, short: "simple_words_explain_terms", enforced_by: ["prompt"] }],
    });

    expect(screen.getByText("simple_words_explain_terms")).toBeInTheDocument();
    const fullRule = screen.getByText(text);
    expect(fullRule).toBeInTheDocument();
    expect(getComputedStyle(fullRule).whiteSpace).not.toBe("nowrap");
    const unlabelled = screen.getByText("Be polite");
    expect(getComputedStyle(unlabelled).whiteSpace).not.toBe("nowrap");
  });

  it("lists the backed stores' tables, or says none were seeded", () => {
    const { unmount } = renderMap({}, { stores: [{ capability: "orders", tables: [{ name: "orders", rows: 8 }] }] });
    expect(screen.getByText("orders")).toBeInTheDocument();
    expect(screen.getByText("8 rows seeded")).toBeInTheDocument();
    unmount();

    renderMap({}, { stores: [] });
    expect(screen.getByText("No stores seeded for this environment.")).toBeInTheDocument();
  });

  it("hides ALK's internal tables from the stores group", () => {
    renderMap(
      {},
      {
        stores: [
          {
            capability: "world_db",
            tables: [
              { name: "_alk_tool_trace", rows: 0, internal: true },
              { name: "harness_seed_sentinel", rows: 1, internal: true },
              { name: "bookings", rows: 12 },
            ],
          },
        ],
      },
    );
    expect(screen.getByText("bookings")).toBeInTheDocument();
    expect(screen.queryByText("_alk_tool_trace")).toBeNull();
    expect(screen.queryByText("harness_seed_sentinel")).toBeNull();
  });

  it("hides ALK's internal tables and lists starting-data collections as stores", () => {
    renderMap(
      { baseEnvironment: { patients: [{ id: "p1" }, { id: "p2" }], notes: "free text" } },
      {
        stores: [
          {
            capability: "world_db",
            tables: [
              { name: "_alk_tool_trace", rows: 0, internal: true },
              { name: "harness_seed_sentinel", rows: 1, internal: true },
              { name: "bookings", rows: 12 },
            ],
          },
        ],
      },
    );
    expect(screen.getByText("bookings")).toBeInTheDocument();
    expect(screen.getByText("patients")).toBeInTheDocument();
    expect(screen.getByText("2 rows in starting data")).toBeInTheDocument();
    expect(screen.queryByText("notes")).toBeNull();
    expect(screen.queryByText("_alk_tool_trace")).toBeNull();
    expect(screen.queryByText("harness_seed_sentinel")).toBeNull();
  });

  it("shows the actors group only when the contract names actors", () => {
    const { unmount } = renderMap();
    expect(screen.queryByText("Actors")).toBeNull();
    unmount();

    renderMap({ actors: [{ name: "supervisor", role: "approves refunds over $200" }] });
    expect(screen.getByText("Actors")).toBeInTheDocument();
    expect(screen.getByText("supervisor")).toBeInTheDocument();
    expect(screen.getByText("approves refunds over $200")).toBeInTheDocument();
  });

  it("shows a full tool name in a tooltip only when the row cuts it off", async () => {
    const user = userEvent.setup();
    const tool = "lookup_prescription_history_for_verified_caller";
    const widths = (scroll, client) => {
      vi.spyOn(HTMLElement.prototype, "scrollWidth", "get").mockReturnValue(scroll);
      vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(client);
    };

    widths(100, 100);
    const { unmount } = renderMap({ tools: [{ name: tool }], rules: [] });
    await user.hover(screen.getByText(tool));
    expect(screen.queryByRole("tooltip")).toBeNull();
    unmount();

    widths(500, 100);
    renderMap({ tools: [{ name: tool }], rules: [] });
    await user.hover(screen.getByText(tool));
    expect(await screen.findByRole("tooltip")).toHaveTextContent(tool);
    vi.restoreAllMocks();
  });

  it("is a read-only ledger with no resolve prompt or confirmed mark", () => {
    renderMap();
    expect(screen.queryByText("Needs your answer")).toBeNull();
    expect(screen.queryByText("you confirmed")).toBeNull();
  });
});
