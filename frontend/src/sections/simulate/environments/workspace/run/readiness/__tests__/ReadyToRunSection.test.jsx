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
const saveCredentials = vi.fn();
let agentVersions = [];
let fixRun = null;
const resetFix = vi.fn();
let drafts;
const startFix = vi.fn();

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
  useRereadWhenDone: () => {},
  useRereadWhenDefaultsMove: () => {},
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
  useEnvironmentVersions: () => ({ data: { agent: agentVersions } }),
  useDrafts: () => ({ data: drafts }),
  useChangeCredentials: () => ({
    mutate: saveCredentials,
    isPending: false,
    reset: vi.fn(),
  }),
}));

vi.mock(
  "src/api/simulate-environments/environments",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useUploadSecretFile: () => ({ mutate: vi.fn(), reset: vi.fn() }),
  }),
);

vi.mock("src/api/simulate-environments/readinessFix", () => ({
  FIX_ACTIVE_STATES: new Set([
    "queued",
    "fixing",
    "waiting_on_you",
    "saving",
    "checking",
  ]),
  useFixRun: () => ({ data: fixRun }),
  useFollowFixRun: () => {},
  useStartGroupFix: () => ({
    mutate: startFix,
    isPending: false,
    reset: resetFix,
  }),
}));
vi.mock("../FixChat", () => ({
  default: () => <div data-testid="fix-chat" />,
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

// A report from a server that supports fixes: a line for the user and one
// the builder chat can fix.
const groupedReport = (progress = null) => {
  const grouped = blockedReport("pair");
  grouped.areas.push({
    id: "start_and_message",
    status: "blocked",
    rows: ["start"],
  });
  grouped.rows[0] = {
    ...grouped.rows[0],
    area: "tool_binding",
    status: progress?.state === "fixed" ? "supported" : "blocked",
    fix_group: "builder",
    fix_progress: progress,
  };
  grouped.rows.push({
    id: "start",
    area: "start_and_message",
    status: "blocked",
    measured: true,
    reason: "The agent didn't start",
    affected: { count: 2 },
    fix: { type: "preflight", action: "Add the key", recheck: null },
    fix_group: "needs_you",
  });
  grouped.fix_groups = {
    builder: { message: "Point issue_refund at the test world", rows: [] },
  };
  return grouped;
};

const picks = { agent: "agent-v2" };

beforeEach(() => {
  loading = false;
  recheckPending = false;
  recheckResult = {};
  cases = { names: ["Refund over limit"], isError: false };
  recheck.mockReset();
  refetch.mockReset();
  retryAgent.mockReset();
  saveCredentials.mockReset();
  agentVersions = [];
  fixRun = null;
  drafts = undefined;
  startFix.mockReset();
});

describe("ReadyToRunSection", () => {
  it("closes an open fix when other versions are picked", async () => {
    report = blockedReport("pair");
    const { rerender } = render(
      <ReadyToRunSection envId="env-1" picks={picks} />,
    );
    await userEvent.click(screen.getByRole("button", { name: /^Details for/ }));
    rerender(<ReadyToRunSection envId="env-1" picks={{ agent: "agent-v3" }} />);
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: /^Details for/ }),
      ).toHaveAttribute("aria-expanded", "false"),
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
    const fix = screen.getByRole("button", { name: /^Details for/ });
    expect(fix).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(fix);
    expect(fix).toHaveAttribute("aria-expanded", "true");
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
    await userEvent.click(screen.getByRole("button", { name: /^Details for/ }));
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

    const [first, second] = screen.getAllByRole("button", {
      name: /^Details for/,
    });
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

    await userEvent.click(screen.getByRole("button", { name: /^Details for/ }));
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
    expect(screen.queryByRole("button", { name: /^Details for/ })).toBeNull();
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

    await userEvent.click(screen.getByRole("button", { name: /^Details for/ }));
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

    await userEvent.click(screen.getByRole("button", { name: /^Details for/ }));
    await userEvent.click(
      screen.getByRole("button", { name: "Retry the agent's start" }),
    );
    expect(retryAgent).toHaveBeenCalledWith(
      { versionId: "agent-v2" },
      expect.anything(),
    );
    expect(recheck).not.toHaveBeenCalled();
  });

  it("takes the values the default's start check found missing, in place of Retry", async () => {
    report = blockedReport("agent");
    report.rows[0].fix.type = "preflight";
    agentVersions = [
      {
        id: "agent-v2",
        isDefault: true,
        credentialNames: ["OTHER_KEY"],
        preflightReport: {
          checks: [
            { id: "env", status: "failed", missing: ["RETELL_API_KEY"] },
          ],
        },
      },
    ];
    render(<ReadyToRunSection envId="env-1" picks={{}} />);

    await userEvent.click(screen.getByRole("button", { name: /^Details for/ }));
    expect(screen.getByLabelText("Name 1")).toHaveValue("RETELL_API_KEY");
    // Only the missing names, fixed: nothing to rename, add or drop.
    expect(screen.getByLabelText("Name 1")).toHaveAttribute("readonly");
    expect(screen.queryByLabelText("Name 2")).toBeNull();
    expect(screen.queryByRole("button", { name: /^Don't change/ })).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Retry the agent's start" }),
    ).not.toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Value 1"), "secret");
    await userEvent.click(screen.getByRole("button", { name: "Save values" }));
    expect(saveCredentials).toHaveBeenCalledWith(
      { values: { RETELL_API_KEY: "secret" }, files: undefined },
      expect.anything(),
    );
  });

  it("shows today's flat list when the server doesn't group fixes", () => {
    report = blockedReport("pair");
    render(<ReadyToRunSection envId="env-1" picks={picks} />);
    expect(screen.getByRole("button", { name: /^Details for/ })).toBeVisible();
    expect(screen.queryByText("Needs you")).toBeNull();
    expect(screen.queryByText("Builder chat can fix")).toBeNull();
    expect(screen.queryByRole("checkbox")).toBeNull();
  });

  it("groups the fixes and sends the ticked builder lines to the chat", async () => {
    report = groupedReport();
    render(
      <ReadyToRunSection envId="env-1" picks={picks} env={{ id: "env-1" }} />,
    );
    expect(screen.getByText("Needs you")).toBeVisible();
    expect(screen.getByText("Builder chat can fix")).toBeVisible();
    expect(screen.queryByTestId("fix-chat")).toBeNull();
    await userEvent.click(
      screen.getByRole("button", { name: "Fix 1 with builder chat" }),
    );
    expect(startFix).toHaveBeenCalledWith(
      { group: "builder", rows: ["tool_routing"], picks },
      expect.anything(),
    );
  });

  it("sends nothing once every builder line is unticked", async () => {
    report = groupedReport();
    render(
      <ReadyToRunSection envId="env-1" picks={picks} env={{ id: "env-1" }} />,
    );
    await userEvent.click(
      screen.getByRole("checkbox", {
        name: "Fix tools wired to the test world with the builder chat",
      }),
    );
    expect(
      screen.getByRole("button", { name: "Tick lines to send" }),
    ).toBeDisabled();
  });

  it("adds ticked lines to a running fix instead of holding them", async () => {
    report = groupedReport();
    fixRun = { state: "fixing", rows: [] };
    render(
      <ReadyToRunSection envId="env-1" picks={picks} env={{ id: "env-1" }} />,
    );
    await userEvent.click(
      screen.getByRole("checkbox", {
        name: "Select every line the builder chat can fix",
      }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Add 1 to this fix" }),
    );
    expect(startFix).toHaveBeenCalledWith(
      { group: "builder", rows: ["tool_routing"], picks },
      expect.anything(),
    );
  });

  it("re-checks the pair from the re-check group, with no boxes to tick", async () => {
    report = groupedReport();
    report.areas.push({ id: "reset", status: "stale", rows: ["reset_row"] });
    report.rows.push({
      id: "reset_row",
      status: "stale",
      measured: true,
      reason: "Checked with another version",
      fix: { type: "recheck", recheck: { scope: "pair", path: "/x/" } },
      fix_group: "recheck",
    });
    render(
      <ReadyToRunSection envId="env-1" picks={picks} env={{ id: "env-1" }} />,
    );
    expect(screen.getAllByRole("checkbox")).toHaveLength(2);
    const [, groupButton] = screen.getAllByRole("button", { name: "Re-check" });
    await userEvent.click(groupButton);
    expect(recheck).toHaveBeenCalledWith(picks);
  });

  it("drops a fix refusal once the fix run moves on", () => {
    report = groupedReport();
    fixRun = { state: "fixing" };
    const { rerender } = render(
      <ReadyToRunSection envId="env-1" picks={picks} env={{ id: "env-1" }} />,
    );
    resetFix.mockClear();
    fixRun = { state: "done" };
    rerender(
      <ReadyToRunSection envId="env-1" picks={picks} env={{ id: "env-1" }} />,
    );
    expect(resetFix).toHaveBeenCalled();
  });

  it("shows the chat only while a fix runs", () => {
    report = groupedReport();
    fixRun = { state: "fixing" };
    const { unmount } = render(
      <ReadyToRunSection envId="env-1" picks={picks} env={{ id: "env-1" }} />,
    );
    expect(screen.getByTestId("fix-chat")).toBeInTheDocument();
    unmount();
    fixRun = { state: "done" };
    render(
      <ReadyToRunSection envId="env-1" picks={picks} env={{ id: "env-1" }} />,
    );
    expect(screen.queryByTestId("fix-chat")).toBeNull();
  });

  it("holds sending fixes while there are unsaved changes, and says why", () => {
    report = groupedReport();
    drafts = { any: true };
    render(<ReadyToRunSection envId="env-1" picks={picks} />);
    const send = screen.getByRole("button", {
      name: "Fix 1 with builder chat",
    });
    expect(send).toBeDisabled();
    expect(send).toHaveAccessibleDescription(
      "Save or discard the unsaved changes first, then send fixes to the chat.",
    );
  });

  it.each([
    ["fixing", "Fixing…", null],
    ["waiting_on_you", "Waiting on you", "Answer the chat's question"],
    ["waiting_on_other", "Waiting on starts and replies", null],
    ["fixed", "Fixed", null],
    [
      "could_not_fix",
      "Couldn't fix",
      "The builder chat couldn't make this change.",
    ],
  ])("shows a %s line as %s", (state, text, message) => {
    report = groupedReport({
      state,
      reason: message ? { code: "x", message } : null,
      waiting_on: state === "waiting_on_other" ? "start" : null,
    });
    render(
      <ReadyToRunSection envId="env-1" picks={picks} env={{ id: "env-1" }} />,
    );
    expect(screen.getByText(text)).toBeVisible();
    if (message) expect(screen.getByText(message)).toBeVisible();
  });
});
