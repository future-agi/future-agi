import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

// The report the server answered, and what the buttons asked for.
let report;
let loading = false;
let recheckPending = false;
const recheck = vi.fn();
const retryAgent = vi.fn();

vi.mock("src/api/simulate-environments/readiness", () => ({
  useReadiness: () =>
    loading
      ? { data: undefined, isLoading: true, isError: false }
      : { data: report, isLoading: false, isError: false },
  useRecheck: () => ({ mutate: recheck, isPending: recheckPending }),
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
  loading = false;
  recheckPending = false;
  recheck.mockReset();
  retryAgent.mockReset();
});

describe("ReadyToRunSection", () => {
  it("keeps one fix open at a time", async () => {
    report = blockedReport("pair");
    report.areas.push({ id: "reset", status: "blocked", rows: ["reset"] });
    report.rows.push({
      ...report.rows[0],
      id: "reset",
      reason: "The world did not reset",
      fix: {
        ...report.rows[0].fix,
        action: "Make the world return to its seeded state",
      },
    });
    render(<ReadyToRunSection envId="env-1" picks={picks} />);

    const [first, second] = screen.getAllByRole("button", { name: "Fix" });
    await userEvent.click(first);
    await userEvent.click(second);
    expect(
      screen.getByText("Make the world return to its seeded state"),
    ).toBeVisible();
    await waitFor(() =>
      expect(
        screen.queryByText("Point issue_refund at the test world", {
          exact: false,
        }),
      ).toBeNull(),
    );
  });

  it("calls nothing ready before anything is checked", () => {
    report = {
      pairing: { evidence: "none" },
      validation: { state: "not_run", error: null },
      answer: { kind: "unchecked" },
      areas: [],
      rows: [],
      selection: {
        requested: 3,
        eligible: 0,
        blocked: 0,
        untested: 3,
        grading: { gradable: 0, not_gradable: [] },
      },
    };
    render(<ReadyToRunSection envId="env-1" picks={picks} />);
    expect(screen.getByText(/Not checked yet/)).toBeVisible();
    expect(screen.queryByText(/ready:/)).toBeNull();
  });

  it("opens a re-check-only line on Fix with its reason and Re-check inside", async () => {
    report = blockedReport("pair");
    report.rows[0].fix = {
      type: "recheck",
      action: "Check this agent with this environment and suite",
      would: "The row reads what this agent does",
      recheck: { scope: "pair", method: "POST", path: "/x/" },
    };
    report.rows[0].consequence = "Check again before trusting these cases";
    render(<ReadyToRunSection envId="env-1" picks={picks} />);

    await userEvent.click(screen.getByRole("button", { name: "Fix" }));
    expect(
      screen.getByText("Check again before trusting these cases"),
    ).toBeVisible();
    await userEvent.click(
      screen.getByRole("button", { name: "Re-check this pair" }),
    );
    expect(recheck).toHaveBeenCalledWith(picks);
  });

  it("shows the check in progress as soon as a re-check is sent", () => {
    report = blockedReport("pair");
    recheckPending = true;
    render(<ReadyToRunSection envId="env-1" picks={picks} />);
    expect(screen.getByRole("button", { name: /Checking/ })).toBeDisabled();
    expect(screen.getByText(/Checking these versions together/)).toBeVisible();
    // The lines are the last results until the check ends: no Fix on them.
    expect(screen.queryByRole("button", { name: "Fix" })).toBeNull();
  });

  it("holds Run from the moment a re-check is sent", () => {
    report = blockedReport("pair");
    recheckPending = true;
    const onView = vi.fn();
    render(<ReadyToRunSection envId="env-1" picks={picks} onView={onView} />);
    expect(onView).toHaveBeenLastCalledWith(
      expect.objectContaining({ canRun: false }),
    );
  });

  it("holds Run until the report has loaded", () => {
    loading = true;
    const onView = vi.fn();
    render(<ReadyToRunSection envId="env-1" picks={picks} onView={onView} />);
    expect(onView).toHaveBeenLastCalledWith(
      expect.objectContaining({ canRun: false }),
    );
  });

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
