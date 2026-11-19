import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import TestExecutionDetailTabs from "../TestExecutionDetailTabs";
import FixMyAgentHeader from "../FixMyAgentDrawer/FixMyAgentHeader";
import FixableRecommendations from "../FixMyAgentDrawer/FixableRecommendations";

// The open check reads the run's calls; here the panel is open.
vi.mock("../common", async (importOriginal) => ({
  ...(await importOriginal()),
  useFixMyAgentBlocked: () => ({ disabled: false, reason: "" }),
}));

// The panel says what it does, as its Environments equivalent does.
const OLD_NAMES = /fix my agent|optimize my agent/i;

describe("Run Simulation's Debug failures panel", () => {
  it("opens from a Debug failures button", () => {
    render(
      <TestExecutionDetailTabs
        tabs={[]}
        currentTab=""
        onTabChange={vi.fn()}
        agentType="voice"
      />,
    );
    expect(
      screen.getByRole("button", { name: "Debug failures" }),
    ).toBeInTheDocument();
    expect(screen.queryByText(OLD_NAMES)).toBeNull();
  });

  it("is headed Debug failures", () => {
    render(<FixMyAgentHeader onClose={vi.fn()} />);
    expect(screen.getByText("Debug failures")).toBeInTheDocument();
    expect(screen.queryByText(OLD_NAMES)).toBeNull();
  });

  it("offers to optimize the prompt", () => {
    render(
      <FixableRecommendations
        summary="Callers are put on hold too long."
        domainLevelFixableRecommendations={[]}
        agentLevelFixableRecommendations={[]}
        refetch={vi.fn()}
      />,
    );
    expect(
      screen.getByRole("button", { name: "Optimize prompt" }),
    ).toBeInTheDocument();
    expect(screen.queryByText(OLD_NAMES)).toBeNull();
  });
});
