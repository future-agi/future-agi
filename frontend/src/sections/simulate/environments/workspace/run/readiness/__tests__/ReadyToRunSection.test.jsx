import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

// The report the server answered, and what the buttons asked for.
let report;
const recheck = vi.fn();
const retryAgent = vi.fn();

vi.mock("src/api/simulate-environments/readiness", () => ({
  useReadiness: () => ({ data: report, isLoading: false, isError: false }),
  useRecheck: () => ({ mutate: recheck, isPending: false }),
}));
vi.mock("src/api/simulate-environments/versions", async (importOriginal) => ({
  ...(await importOriginal()),
  useRetryAgentVersion: () => ({ mutate: retryAgent, isPending: false }),
}));

const { default: ReadyToRunSection } = await import("../ReadyToRunSection");

const blockedReport = (recheckScope) => ({
  pairing: {
    evidence: "build_certificate",
    agent_version: { id: "agent-v2", label: "v2" },
  },
  validation: { state: "not_run", error: null },
  answer: { kind: "blocked" },
  areas: [{ id: "tool_binding", status: "blocked", rows: ["tool_routing"] }],
  rows: [
    {
      id: "tool_routing",
      status: "blocked",
      measured: true,
      reason: "issue_refund still reaches production",
      affected: { count: 4, of: 10, keys: [] },
      fix: {
        action: "Point issue_refund at the test world",
        would: "issue_refund lands in the test world",
        recheck: { scope: recheckScope, method: "POST", path: "/x/" },
      },
    },
  ],
  selection: { requested: 10, eligible: 6, blocked: 4, stale: 0, untested: 0 },
});

const picks = { agent: "agent-v2" };

beforeEach(() => {
  recheck.mockReset();
  retryAgent.mockReset();
});

describe("ReadyToRunSection", () => {
  it("opens a fix with the server's instruction and re-checks the pair", async () => {
    report = blockedReport("pair");
    render(<ReadyToRunSection envId="env-1" picks={picks} />);

    await userEvent.click(screen.getByRole("button", { name: "Fix" }));
    expect(
      screen.getByText("Point issue_refund at the test world", {
        exact: false,
      }),
    ).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "Re-check this pair" }),
    );
    expect(recheck).toHaveBeenCalledWith(picks);
  });

  it("retries the agent version when the fix is the agent's own startup", async () => {
    report = blockedReport("agent");
    render(<ReadyToRunSection envId="env-1" picks={picks} />);

    await userEvent.click(screen.getByRole("button", { name: "Fix" }));
    await userEvent.click(
      screen.getByRole("button", { name: "Retry the agent's start" }),
    );
    expect(retryAgent).toHaveBeenCalledWith(
      { versionId: "agent-v2" },
      expect.anything(),
    );
    expect(recheck).not.toHaveBeenCalled();
  });
});
