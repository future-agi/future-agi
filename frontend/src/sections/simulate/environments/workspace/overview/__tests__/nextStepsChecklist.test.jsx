import { describe, it, expect, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { render } from "src/utils/test-utils";
import { MOCK_WORLD } from "src/api/simulate-environments/_fixtures/world";
import { NOT_IN_RELEASE } from "src/components/NotInRelease/notInRelease";
import { paths } from "src/routes/paths";
import { ENTRY_TAB } from "../../../environmentOptions";
import NextStepsChecklist from "../NextStepsChecklist";
import OverviewPanel from "../OverviewPanel";
import { ToolsCard } from "../OverviewCards";
import { CHECKLIST_COPY } from "../overview.constants";

// The drawer's real-data panel has its own tests; here it only has to mount.
vi.mock("../../agents/AgentVersionsPanel", () => ({
  default: ({ env }) => (
    <div data-testid="agent-versions-panel">{env.id}</div>
  ),
}));

// An environment the checklist shows for: no agent, no scenarios, rules or
// tools yet.
const UNSEEDED_ENV = { ...MOCK_WORLD, rules: [], tools: [] };
const UNSEEDED_STATE = { scenarios: [] };
const BLOCKED = "Add a scenario to run this environment.";

// An agent is set when an environment is built, so its step opens the Build
// tab, which makes a new environment from the agent.
const BUILD_FROM_AGENT = "Build from an agent";
const BUILD_TAB = `${paths.dashboard.simulate.environments.root}?tab=${ENTRY_TAB.BUILD}`;
const at = () => `${window.location.pathname}${window.location.search}`;

describe("Build from an agent", () => {
  it("opens the Build tab from the checklist, and no step reads not in this release", async () => {
    window.history.replaceState(null, "", "/");
    render(
      <NextStepsChecklist
        env={UNSEEDED_ENV}
        envState={UNSEEDED_STATE}
        onGo={vi.fn()}
      />,
    );

    const button = screen.getByRole("button", { name: BUILD_FROM_AGENT });
    expect(button).toBeEnabled();
    expect(screen.queryByText(NOT_IN_RELEASE)).toBeNull();
    expect(
      screen.getByText(/Builds a new environment from your agent/),
    ).toBeInTheDocument();
    await userEvent.click(button);
    expect(at()).toBe(BUILD_TAB);
  });

  it("opens the Build tab from the tools card of an environment with no agent", async () => {
    window.history.replaceState(null, "", "/");
    render(<ToolsCard env={UNSEEDED_ENV} agentConnected={false} />);

    const button = screen.getByRole("button", { name: BUILD_FROM_AGENT });
    expect(button).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Connect agent" })).toBeNull();
    await userEvent.click(button);
    expect(at()).toBe(BUILD_TAB);
  });
});

describe("NextStepsChecklist actions that can't act now", () => {

  it("shows Run disabled, with the workspace's reason a run can't start", () => {
    render(
      <NextStepsChecklist
        env={UNSEEDED_ENV}
        envState={UNSEEDED_STATE}
        onGo={vi.fn()}
        onStartRun={vi.fn()}
        canRun={false}
        runBlockedReason={BLOCKED}
      />,
    );

    const button = screen.getByRole("button", {
      name: CHECKLIST_COPY.run.cta,
    });
    expect(button).toBeDisabled();
    expect(button).toHaveAccessibleDescription(BLOCKED);
  });

  it("starts a run from the Run step when one can start", async () => {
    const onStartRun = vi.fn();
    render(
      <NextStepsChecklist
        env={UNSEEDED_ENV}
        envState={{ scenarios: [{ id: "scenario-1" }] }}
        onGo={vi.fn()}
        onStartRun={onStartRun}
        canRun
      />,
    );

    await userEvent.click(
      screen.getByRole("button", { name: CHECKLIST_COPY.run.cta }),
    );
    expect(onStartRun).toHaveBeenCalledWith(undefined, 1);
  });

  it("gets the workspace's run reason from the Overview", () => {
    render(
      <OverviewPanel
        env={UNSEEDED_ENV}
        envState={UNSEEDED_STATE}
        onGo={vi.fn()}
        agentConnected={false}
        onStartRun={vi.fn()}
        canRun={false}
        runBlockedReason={BLOCKED}
      />,
    );

    const button = screen.getByRole("button", {
      name: CHECKLIST_COPY.run.cta,
    });
    expect(button).toBeDisabled();
    expect(button).toHaveAccessibleDescription(BLOCKED);
  });
});
