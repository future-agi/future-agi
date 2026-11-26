import { describe, it, expect, vi, beforeEach } from "vitest";
import PropTypes from "prop-types";
import { render, screen, waitFor } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

// The report the server answered, and what the buttons asked for.
let report;
let recheckResult = {};
let cases = [];
const recheck = vi.fn();
const reset = vi.fn();
const retryAgent = vi.fn();
const saveCredentials = vi.fn();
let agentVersions = [];
let fixRun = null;
let canEdit = true;
const resetFix = vi.fn();
const startFix = vi.fn();

vi.mock("src/api/simulate-environments/readiness", () => ({
  useRecheck: () => ({ mutate: recheck, reset, ...recheckResult }),
  useReadinessCases: (envId, { enabled }) => ({
    data: enabled
      ? cases.map((name, at) => ({ key: `k${at}`, name }))
      : undefined,
  }),
}));
vi.mock("src/api/simulate-environments/versions", async (importOriginal) => ({
  ...(await importOriginal()),
  useRetryAgentVersion: () => ({ mutate: retryAgent, reset }),
  useEnvironmentVersions: () => ({ data: { agent: agentVersions } }),
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
  fixIsActive: (run) => Boolean(run?.active),
  useFixRun: () => ({ data: fixRun }),
  useStartGroupFix: () => ({
    mutate: startFix,
    isPending: false,
    reset: resetFix,
  }),
}));

vi.mock("../../../../hooks/useSimulationAccess", () => ({
  default: () => ({ canEdit, canRun: true }),
}));

const { default: ReadyToRunSection } = await import("../ReadyToRunSection");
const { readinessView } = await import("../readinessView");

// As the run plan renders it: it owns the report.
function Section({ error, refetch, ...props }) {
  return (
    <ReadyToRunSection
      envId="env-1"
      picks={{}}
      readiness={{
        data: error ? undefined : report,
        isError: !!error,
        error,
        refetch,
      }}
      view={error ? null : readinessView(report)}
      {...props}
    />
  );
}
Section.propTypes = { error: PropTypes.object, refetch: PropTypes.func };
const details = (name = /^Details for/) => screen.getByRole("button", { name });

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
const groupedReport = (progress = null, held = null) => {
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
    fix_action: {
      available: !progress || progress.state === "could_not_fix",
      held,
    },
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
  grouped.fix_groups = { builder: { rows: [] } };
  return grouped;
};

const picks = { agent: "agent-v2" };

beforeEach(() => {
  recheckResult = {};
  cases = [];
  agentVersions = [];
  fixRun = null;
  canEdit = true;
  vi.clearAllMocks();
});

describe("ReadyToRunSection", () => {
  it("says it waits for a saved version instead of failing", () => {
    render(
      <Section error={{ statusCode: 404, error: "versioning_disabled" }} />,
    );
    expect(screen.getByTestId("ready-to-run-no-versions")).toBeInTheDocument();
    expect(screen.queryByText(/Couldn't check/)).toBeNull();
  });

  it("shows a re-check as running from the click, with nothing to press on the last results", () => {
    report = blockedReport("pair");
    recheckResult = { isPending: true };
    const onSendingChange = vi.fn();
    render(<Section onSendingChange={onSendingChange} />);
    expect(screen.getByRole("button", { name: /Checking/ })).toBeDisabled();
    expect(
      screen.getByText(/Checking these versions · runs wait/),
    ).toHaveAttribute("aria-live", "polite");
    expect(screen.queryByRole("button", { name: /^Details for/ })).toBeNull();
    expect(onSendingChange).toHaveBeenLastCalledWith(true);
  });

  it("closes an open fix when other versions are picked", async () => {
    report = blockedReport("pair");
    const { rerender } = render(<Section picks={picks} />);
    await userEvent.click(screen.getByRole("button", { name: /^Details for/ }));
    rerender(<Section picks={{ agent: "agent-v3" }} />);
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: /^Details for/ }),
      ).toHaveAttribute("aria-expanded", "false"),
    );
  });

  it("explains a refused re-check with the server's words", () => {
    report = blockedReport("pair");
    recheckResult = {
      isError: true,
      error: { message: "Usage limit reached", statusCode: 402 },
    };
    render(<Section picks={picks} />);
    expect(screen.getByText(/Usage limit reached/)).toBeVisible();
  });

  it("says when these versions can't be checked", () => {
    report = blockedReport("pair");
    recheckResult = { data: { validation: { state: "unavailable" } } };
    render(<Section picks={picks} />);
    expect(screen.getByText(/can't be checked/)).toBeVisible();
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
    render(<Section picks={picks} />);

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
    render(<Section picks={picks} />);
    expect(screen.getByText(/Not checked yet/)).toBeVisible();
    expect(screen.queryByText(/ready: /i)).toBeNull();
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
    report.rows[0].checked_at = new Date().toISOString();
    render(<Section picks={picks} />);

    await userEvent.click(screen.getByRole("button", { name: /^Details for/ }));
    expect(screen.getByText("Last check")).toBeVisible();
    expect(
      screen.getByText("Check again before trusting these cases"),
    ).toBeVisible();
    await userEvent.click(
      screen.getByRole("button", { name: "Re-check this pair" }),
    );
    expect(recheck).toHaveBeenCalledWith(picks);
  });

  it("opens a fix with the server's instruction and re-checks the pair", async () => {
    report = blockedReport("pair");
    cases = ["A", "B", "C", "D"];
    render(<Section picks={picks} />);
    expect(screen.getByText("· 1 blocker to fix")).toBeVisible();

    await userEvent.click(screen.getByRole("button", { name: /^Details for/ }));
    expect(
      screen.getByText("Point issue_refund at the test world"),
    ).toBeVisible();
    // The scenarios this line holds back: the first few, then the rest counted.
    expect(screen.getByText("A, B, C and 1 more")).toBeVisible();
    await userEvent.click(
      screen.getByRole("button", { name: "Re-check this pair" }),
    );
    expect(recheck).toHaveBeenCalledWith(picks);
  });

  it("retries the agent version when the fix is the agent's own startup, holding Run until it reads again", async () => {
    report = blockedReport("agent");
    let reread;
    const refetch = vi.fn(
      () =>
        new Promise((resolve) => {
          reread = resolve;
        }),
    );
    const onSendingChange = vi.fn();
    render(
      <Section
        picks={picks}
        refetch={refetch}
        onSendingChange={onSendingChange}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: /^Details for/ }));
    await userEvent.click(
      screen.getByRole("button", { name: "Retry the agent's start" }),
    );
    const [args, { onSettled }] = retryAgent.mock.lastCall;
    expect(args).toEqual({ versionId: "agent-v2" });
    expect(recheck).not.toHaveBeenCalled();

    onSettled();
    expect(refetch).toHaveBeenCalled();
    expect(onSendingChange).toHaveBeenLastCalledWith(true);
    reread();
    await waitFor(() =>
      expect(onSendingChange).toHaveBeenLastCalledWith(false),
    );
  });

  it("takes the values the default's start check found missing, in place of Retry, only while Fix is offered", async () => {
    report = blockedReport("agent");
    report.rows[0].fix.type = "preflight";
    report.fix_groups = {};
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
    const onOpenAgentVersions = vi.fn();
    const { rerender } = render(
      <Section onOpenAgentVersions={onOpenAgentVersions} />,
    );

    await userEvent.click(details());
    expect(
      screen.queryByRole("button", { name: "Open agent versions" }),
    ).toBeNull();
    expect(screen.getByLabelText("Name 1")).toHaveValue("RETELL_API_KEY");
    // Only the missing names, fixed: nothing to rename, add or drop.
    expect(screen.getByLabelText("Name 1")).toHaveAttribute("readonly");
    expect(screen.queryByLabelText("Name 2")).toBeNull();
    expect(screen.queryByRole("button", { name: /^Don't change/ })).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Retry the agent's start" }),
    ).not.toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Value 1"), "secret");
    await userEvent.click(
      screen.getByRole("button", { name: "Save and re-check" }),
    );
    expect(saveCredentials).toHaveBeenCalledWith(
      { values: { RETELL_API_KEY: "secret" }, files: undefined },
      expect.anything(),
    );

    report = { ...report, fix_groups: undefined };
    rerender(<Section onOpenAgentVersions={onOpenAgentVersions} />);
    expect(screen.queryByLabelText("Name 1")).toBeNull();
    expect(
      screen.getByRole("button", { name: "Open agent versions" }),
    ).toBeVisible();
  });

  it("shows today's flat list when the server doesn't group fixes", () => {
    report = blockedReport("pair");
    render(<Section picks={picks} />);
    expect(screen.getByRole("button", { name: /^Details for/ })).toBeVisible();
    expect(screen.queryByText("Needs you")).toBeNull();
    expect(screen.queryByText("Builder chat can fix")).toBeNull();
    expect(screen.queryByRole("checkbox")).toBeNull();
  });

  it("groups the fixes and sends the ticked builder lines to the chat", async () => {
    report = groupedReport();
    render(<Section />);
    expect(screen.getByText("Needs you")).toBeVisible();
    expect(screen.getByText("Builder chat can fix")).toBeVisible();
    expect(screen.queryByTestId("fix-chat")).toBeNull();
    await userEvent.dblClick(
      screen.getByRole("button", { name: "Fix with builder chat" }),
    );
    expect(startFix).toHaveBeenCalledTimes(1);
    expect(startFix).toHaveBeenCalledWith(
      { group: "builder", rows: ["tool_routing"], picks: {} },
      expect.anything(),
    );
  });

  it("says where a fix saved from these versions put its changes", () => {
    report = groupedReport();
    report.rows[0].fix_action = { available: false, held: null };
    report.rows[0].fix_result = {
      state: "fixed",
      environment: { id: "e3", label: "v3" },
      suite: null,
    };
    render(<Section />);
    expect(screen.getByText("Fixed in environment v3")).toBeVisible();
  });

  it("keeps sending to the chat from a role that can't edit", () => {
    report = groupedReport();
    canEdit = false;
    render(<Section />);
    expect(
      screen.getByRole("button", { name: "Fix with builder chat" }),
    ).toBeDisabled();
    screen
      .getAllByRole("checkbox")
      .forEach((box) => expect(box).toBeDisabled());
  });

  it("sends nothing once every builder line is unticked", async () => {
    report = groupedReport();
    render(<Section />);
    await userEvent.click(
      screen.getByRole("checkbox", {
        name: "Fix tools wired to the test world with the builder chat",
      }),
    );
    expect(
      screen.getByRole("button", { name: "Fix with builder chat" }),
    ).toBeDisabled();
  });

  it("adds ticked lines to a running fix", async () => {
    report = groupedReport();
    fixRun = { state: "fixing", active: true, rows: [] };
    render(<Section />);
    expect(screen.getByRole("button", { name: "Fixing…" })).toBeDisabled();
    await userEvent.click(
      screen.getByRole("checkbox", {
        name: "Select every line the builder chat can fix",
      }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Add to this fix" }),
    );
    expect(startFix).toHaveBeenCalledWith(
      { group: "builder", rows: ["tool_routing"], picks: {} },
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
    render(<Section picks={picks} />);
    expect(screen.getAllByRole("checkbox")).toHaveLength(2);
    const [, groupButton] = screen.getAllByRole("button", { name: "Re-check" });
    await userEvent.click(groupButton);
    expect(recheck).toHaveBeenCalledWith(picks);
  });

  it("drops a fix refusal once the fix run moves on", () => {
    report = groupedReport();
    fixRun = { state: "fixing", active: true };
    const { rerender } = render(<Section picks={picks} />);
    resetFix.mockClear();
    fixRun = { state: "done" };
    rerender(<Section picks={picks} />);
    expect(resetFix).toHaveBeenCalled();
  });

  it("points to the builder chat only while a fix runs", () => {
    report = groupedReport();
    fixRun = { state: "fixing", active: true, rows: ["tool_routing"] };
    const { unmount } = render(<Section picks={picks} />);
    expect(screen.getByTestId("fix-chat-note")).toHaveTextContent(
      "Fixing: Tools wired to the test world · follow it in the builder chat",
    );
    unmount();
    fixRun = { state: "done" };
    render(<Section picks={picks} />);
    expect(screen.queryByTestId("fix-chat-note")).toBeNull();
  });

  it.each([
    [
      "unsaved changes",
      "unsaved_changes",
      {},
      "Save or discard the unsaved changes first, then try again.",
    ],
    [
      "non-default picks",
      "fix_needs_defaults",
      picks,
      "Fix works on the default versions. Pick the defaults to fix.",
    ],
  ])("holds sending fixes with %s, and says why", (_, held, picked, why) => {
    report = groupedReport(null, held);
    render(<Section picks={picked} />);
    const send = screen.getByRole("button", {
      name: "Fix with builder chat",
    });
    expect(send).toBeDisabled();
    expect(send).toHaveAccessibleDescription(why);
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
    render(<Section picks={picks} />);
    expect(screen.getByText(text)).toBeVisible();
    if (message) expect(screen.getByText(message)).toBeVisible();
  });
});
