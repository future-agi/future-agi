import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, render, screen, waitFor } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

// The report the server answered, and what the buttons asked for.
let report;
let loading = false;
let recheckPending = false;
let recheckResult = {};
let cases = { names: ["Refund over limit"], isError: false };
const recheck = vi.fn();
const retryAgent = vi.fn();
const refetch = vi.fn();

vi.mock("src/api/simulate-environments/readiness", () => ({
  useReadiness: () =>
    loading
      ? { data: undefined, isLoading: true, isError: false }
      : { data: report, isLoading: false, isError: false, refetch },
  useRecheck: () => ({
    mutate: recheck,
    isPending: recheckPending,
    ...recheckResult,
  }),
  useReadinessCases: (envId, { bucket, enabled }) => {
    const asked = enabled && bucket === "blocked";
    return {
      data:
        asked && !cases.isError
          ? cases.names.map((name, at) => ({ key: `k${at}`, name }))
          : undefined,
      isError: asked && cases.isError,
      isLoading: false,
    };
  },
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
  recheckResult = {};
  cases = { names: ["Refund over limit"], isError: false };
  recheck.mockReset();
  refetch.mockReset();
  retryAgent.mockReset();
});

describe("ReadyToRunSection", () => {
  it("closes an open fix when other versions are picked", async () => {
    report = blockedReport("pair");
    const { rerender } = render(
      <ReadyToRunSection envId="env-1" picks={picks} />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Fix" }));
    rerender(<ReadyToRunSection envId="env-1" picks={{ agent: "agent-v3" }} />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Fix" })).toHaveAttribute(
        "aria-expanded",
        "false",
      ),
    );
  });

  it("clears what it told the popup when it goes away", () => {
    report = blockedReport("pair");
    const onView = vi.fn();
    const { unmount } = render(
      <ReadyToRunSection envId="env-1" picks={picks} onView={onView} />,
    );
    unmount();
    expect(onView).toHaveBeenLastCalledWith(null);
  });

  it("lets keyboard users reach the counts and the fixes", async () => {
    report = blockedReport("pair");
    render(<ReadyToRunSection envId="env-1" picks={picks} />);
    expect(screen.getByText("4 blocked")).toHaveAttribute("tabindex", "0");
    const fix = screen.getByRole("button", { name: "Fix" });
    expect(fix).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(fix);
    expect(screen.getByRole("button", { name: "Hide" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
  });

  it("announces when a check starts", () => {
    report = blockedReport("pair");
    recheckPending = true;
    render(<ReadyToRunSection envId="env-1" picks={picks} />);
    expect(
      screen.getByText(/Checking these versions together/),
    ).toHaveAttribute("aria-live", "polite");
  });

  it("lists the first names and counts the rest from the header", async () => {
    report = blockedReport("pair");
    cases.names = Array.from({ length: 12 }, (_, at) => `Scenario ${at + 1}`);
    report.selection.blocked = 14;
    render(<ReadyToRunSection envId="env-1" picks={picks} />);
    await userEvent.hover(screen.getByText("14 blocked"));
    expect(await screen.findByText("and 4 more")).toBeVisible();
  });

  it("says when the names can't load", async () => {
    report = blockedReport("pair");
    cases.isError = true;
    render(<ReadyToRunSection envId="env-1" picks={picks} />);
    await userEvent.hover(screen.getByText("4 blocked"));
    expect(
      await screen.findByText("Couldn't load these scenarios."),
    ).toBeVisible();
  });

  it("explains a refused re-check with the server's words", () => {
    report = blockedReport("pair");
    recheckResult = {
      isError: true,
      error: { message: "Usage limit reached" },
    };
    render(<ReadyToRunSection envId="env-1" picks={picks} />);
    expect(screen.getByText(/Usage limit reached/)).toBeVisible();
  });

  it("says when these versions can't be checked", () => {
    report = blockedReport("pair");
    recheckResult = { data: { validation: { state: "unavailable" } } };
    render(<ReadyToRunSection envId="env-1" picks={picks} />);
    expect(screen.getByText(/can't be checked/)).toBeVisible();
  });

  it("holds Run until the report is read again after retrying the agent's start", async () => {
    report = blockedReport("agent");
    let refreshed;
    refetch.mockReturnValue(
      new Promise((resolve) => {
        refreshed = resolve;
      }),
    );
    const onView = vi.fn();
    render(<ReadyToRunSection envId="env-1" picks={picks} onView={onView} />);
    await userEvent.click(screen.getByRole("button", { name: "Fix" }));
    await userEvent.click(
      screen.getByRole("button", { name: "Retry the agent's start" }),
    );
    const [, options] = retryAgent.mock.calls[0];
    await act(async () => {
      options.onSettled();
    });
    expect(refetch).toHaveBeenCalled();
    expect(onView).toHaveBeenLastCalledWith(
      expect.objectContaining({ canRun: false }),
    );
    await act(async () => refreshed());
    expect(onView).toHaveBeenLastCalledWith(
      expect.objectContaining({ canRun: true }),
    );
  });

  it("names the scenarios behind a count on hover", async () => {
    report = blockedReport("pair");
    render(<ReadyToRunSection envId="env-1" picks={picks} />);
    await userEvent.hover(screen.getByText("4 blocked"));
    expect(await screen.findByText("Refund over limit")).toBeVisible();
  });

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
