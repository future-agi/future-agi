import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import {
  agentItem,
  mutations,
  resetMutations,
  useFakeMutation,
} from "./agentVersionsTestUtils";

// Each hook is stubbed; tests set what the server answered.
const state = { versions: undefined, loadError: null, nudges: [] };
vi.mock("src/api/simulate-environments/versions", async () => {
  const actual = await vi.importActual(
    "src/api/simulate-environments/versions",
  );
  const seed = (mutation) => {
    if (mutation.data?.agent) state.versions = mutation.data;
  };
  return {
    ...actual,
    useEnvironmentVersions: vi.fn((envId, { enabled } = {}) => {
      if (!enabled) return { data: undefined, isLoading: false };
      if (state.loadError) {
        return { data: undefined, isLoading: false, error: state.loadError };
      }
      return {
        data: state.versions ? actual.toVersions(state.versions) : undefined,
        isLoading: !state.versions,
      };
    }),
    useMoveDefault: vi.fn(() => useFakeMutation("move", seed)),
    useDiscardAgentVersion: vi.fn(() => useFakeMutation("discard", seed)),
    useRetryAgentVersion: vi.fn(() => useFakeMutation("retry", seed)),
    useChangeCredentials: vi.fn(() => useFakeMutation("change")),
    useCreateAgentVersion: vi.fn(() => useFakeMutation("create")),
    useNudges: vi.fn((envId, { enabled } = {}) => ({
      data: enabled ? actual.toNudges(state.nudges) : undefined,
    })),
    useAnswerNudge: vi.fn(() => useFakeMutation("answer")),
    useStopNudge: vi.fn(() => useFakeMutation("stop")),
  };
});
vi.mock("src/api/simulate-environments/environments", () => ({
  useUploadSecretFile: vi.fn(() => useFakeMutation("upload")),
}));

const { default: AgentVersionsPanel } = await import("../AgentVersionsPanel");

const ENV = { id: "env-1", versioning: { enabled: true } };
const OLD = "2026-10-15T10:00:00Z";
const REPORT = {
  checks: [
    {
      id: "source",
      label: "Source reachable",
      status: "passed",
      missing: [],
    },
    {
      id: "credentials_present",
      label: "Target credentials",
      status: "failed",
      missing: ["API_KEY"],
    },
  ],
  missing: ["API_KEY"],
  invalid: ["REGION"],
  error: null,
};
const VERSIONS = {
  agent: [
    agentItem({
      id: "a7",
      label: "v7",
      status: "draft",
      origin: "credentials",
      preflight_status: "failed",
      preflight_report: REPORT,
      summary: "API_KEY changed",
      requested_here: true,
      discardable: true,
    }),
    agentItem({
      id: "a6",
      label: "v6",
      origin: "credentials",
      preflight_status: "passed",
      activation_outcome: "environment_busy",
      requested_here: true,
      discardable: true,
    }),
    agentItem({
      id: "a5",
      label: "v5",
      preflight_status: "passed",
      activation_outcome: "rollback_unavailable",
    }),
    agentItem({
      id: "a4",
      label: "v4",
      is_default: true,
      preflight_status: "legacy",
      origin: "backfill",
      credential_names: ["API_KEY"],
      created_by: { id: "u1", name: "Asha" },
    }),
    agentItem({
      id: "a3",
      label: "v3",
      status: "discarded",
      preflight_status: "discarded",
    }),
    agentItem({ id: "a2", label: "v2" }),
  ],
  environment: [],
  suite: [],
};

const rowOf = (id) => screen.getByTestId(`agent-version-${id}`);
const button = (id, name) => within(rowOf(id)).queryByRole("button", { name });

beforeEach(() => {
  state.versions = VERSIONS;
  state.loadError = null;
  state.nudges = [];
  resetMutations();
});

describe("AgentVersionsPanel", () => {
  it("renders nothing while versioning is off", () => {
    const { container } = render(<AgentVersionsPanel env={{ id: "env-1" }} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("says when the versions could not be loaded or versioning was switched off", () => {
    state.versions = undefined;
    state.loadError = { statusCode: 500 };
    const { rerender } = render(<AgentVersionsPanel env={ENV} />);
    expect(
      screen.getByText(
        "Couldn't load the agent versions. Try again in a moment.",
      ),
    ).toBeInTheDocument();

    state.loadError = { statusCode: 404, error: "versioning_disabled" };
    rerender(<AgentVersionsPanel env={ENV} />);
    expect(
      screen.getByText("Versioning is switched off for this environment."),
    ).toBeInTheDocument();
  });

  it("lists every agent version with its default, status, trigger, summary and check", () => {
    render(<AgentVersionsPanel env={ENV} />);

    const failed = rowOf("a7");
    expect(failed).toHaveTextContent("v7");
    expect(failed).toHaveTextContent("draft");
    expect(failed).toHaveTextContent("Check failed");
    expect(failed).toHaveTextContent("Credentials changed");
    expect(failed).toHaveTextContent("API_KEY changed");

    const current = rowOf("a4");
    expect(current).toHaveTextContent("default");
    expect(current).toHaveTextContent("Recorded from before versioning");
    expect(current).toHaveTextContent("by Asha");
    expect(current).not.toHaveTextContent(/Check/);

    expect(rowOf("a3")).toHaveTextContent("discarded");
    expect(rowOf("a6")).toHaveTextContent("Check passed");
  });

  it("names a pushed version's commit and shows what the default is built from", () => {
    state.versions = {
      ...VERSIONS,
      agent: [
        agentItem({
          id: "a8",
          label: "v8",
          origin: "push",
          source: {
            kind: "github",
            repository: "acme/agent",
            ref: "main",
            commit_sha: "a1b2c3d4e5f6a7b8c9d0a1b2c3d4e5f6a7b8c9d0",
            visibility: "public",
          },
        }),
        ...VERSIONS.agent.map((item) =>
          item.is_default
            ? {
                ...item,
                source: {
                  kind: "github",
                  repository: "acme/agent",
                  ref: "main",
                },
              }
            : item,
        ),
      ],
    };
    render(<AgentVersionsPanel env={ENV} />);

    expect(rowOf("a8")).toHaveTextContent("Pushed · a1b2c3d");
    expect(rowOf("a8")).not.toHaveTextContent("a1b2c3d4");
    expect(screen.getByTestId("agent-source-change")).toHaveTextContent(
      "Watching acme/agent @ main",
    );
  });

  it("shows a failed check's steps and the names it found missing or rejected", () => {
    render(<AgentVersionsPanel env={ENV} />);
    const report = within(rowOf("a7")).getByTestId("preflight-report");
    expect(report).toHaveTextContent("Source reachable: passed");
    expect(report).toHaveTextContent(
      "Target credentials: failed · Missing: API_KEY",
    );
    expect(report).toHaveTextContent("Rejected by the provider: REGION");
  });

  it("explains in plain words why a passed version isn't the default", () => {
    state.versions = {
      ...VERSIONS,
      agent: [
        ...VERSIONS.agent,
        agentItem({
          id: "a1",
          label: "v1",
          preflight_status: "passed",
          activation_outcome: "something_new",
        }),
        agentItem({
          id: "a0",
          label: "v0",
          preflight_status: "passed",
          activation_outcome: "rerun_only",
        }),
        agentItem({
          id: "a9",
          label: "v9",
          preflight_status: "passed",
          activation_outcome: "default_moved_by_hand",
        }),
      ],
    };
    render(<AgentVersionsPanel env={ENV} />);
    expect(rowOf("a9")).toHaveTextContent(
      "Not made default: the default was changed by hand while it was checked",
    );
    expect(rowOf("a6")).toHaveTextContent(
      "Not made default: the environment was busy",
    );
    expect(rowOf("a5")).toHaveTextContent(
      "Not made default: this combination hasn't been prepared yet",
    );
    expect(rowOf("a1")).toHaveTextContent("Not made default (something_new)");
    expect(rowOf("a0")).toHaveTextContent(
      "Made for a single rerun; not used as the default",
    );
    expect(rowOf("a4")).not.toHaveTextContent(/Not made default/);
  });

  it("says nothing about activation once the version is the default", () => {
    state.versions = {
      ...VERSIONS,
      agent: [
        agentItem({
          id: "a6",
          label: "v6",
          is_default: true,
          preflight_status: "passed",
          activation_outcome: "environment_busy",
        }),
      ],
    };
    render(<AgentVersionsPanel env={ENV} />);
    expect(rowOf("a6")).not.toHaveTextContent(/Not made default/);
  });

  it("offers each action only where it applies", () => {
    render(<AgentVersionsPanel env={ENV} />);
    // Make default: active, pickable here and not the default.
    expect(button("a6", "Make v6 the default")).toBeInTheDocument();
    expect(button("a2", "Make v2 the default")).toBeNull();
    expect(button("a4", "Make v4 the default")).toBeNull();
    expect(button("a7", "Make v7 the default")).toBeNull();
    expect(button("a3", "Make v3 the default")).toBeNull();
    // Discard: where the server says this environment can.
    expect(button("a7", "Discard v7")).toBeInTheDocument();
    expect(button("a6", "Discard v6")).toBeInTheDocument();
    expect(button("a5", "Discard v5")).toBeNull();
    expect(button("a4", "Discard v4")).toBeNull();
    expect(button("a3", "Discard v3")).toBeNull();
    expect(button("a2", "Discard v2")).toBeNull();
    // Check again: its own failed check.
    expect(button("a7", "Check v7 again")).toBeInTheDocument();
    expect(button("a6", "Check v6 again")).toBeNull();
  });

  it("offers discard and check again only on its own changes and its default's lineage", () => {
    state.versions = {
      ...VERSIONS,
      agent: [
        agentItem({
          id: "o2",
          label: "v12",
          status: "draft",
          preflight_status: "failed",
        }),
        agentItem({
          id: "d2",
          label: "v11",
          is_default: true,
          parent_id: "d1",
        }),
        agentItem({
          id: "d1",
          label: "v10",
          preflight_status: "passed",
          parent_id: "x1",
          discardable: true,
        }),
        agentItem({ id: "x1", label: "v9", preflight_status: "passed" }),
      ],
    };
    render(<AgentVersionsPanel env={ENV} />);
    // Another environment's failed change: the server would refuse both.
    expect(button("o2", "Discard v12")).toBeNull();
    expect(button("o2", "Check v12 again")).toBeNull();
    // An ancestor of this environment's default.
    expect(button("d1", "Discard v10")).toBeInTheDocument();
    // An ancestor another environment made: the server keeps it.
    expect(button("x1", "Discard v9")).toBeNull();
  });

  it("never discards a legacy version and moves only to a pickable one", () => {
    state.versions = {
      ...VERSIONS,
      agent: [
        agentItem({
          id: "d2",
          label: "v11",
          is_default: true,
          preflight_status: "passed",
          parent_id: "d1",
        }),
        agentItem({
          id: "d1",
          label: "v10",
          preflight_status: "legacy",
          pickable: true,
        }),
        agentItem({ id: "o1", label: "v9", preflight_status: "legacy" }),
      ],
    };
    render(<AgentVersionsPanel env={ENV} />);
    // Its own pre-change version: it can roll back to it, never set it aside.
    expect(button("d1", "Make v10 the default")).toBeInTheDocument();
    expect(button("d1", "Discard v10")).toBeNull();
    // An older legacy version outside its lineage.
    expect(button("o1", "Make v9 the default")).toBeNull();
    expect(button("o1", "Discard v9")).toBeNull();
  });

  it("offers to check again a version stuck checking, not one just saved", () => {
    state.versions = {
      ...VERSIONS,
      agent: [
        agentItem({
          id: "a9",
          label: "v9",
          status: "draft",
          preflight_status: "pending",
          created_at: new Date().toISOString(),
          requested_here: true,
        }),
        agentItem({
          id: "a8",
          label: "v8",
          status: "draft",
          preflight_status: "pending",
          created_at: OLD,
          requested_here: true,
        }),
        ...VERSIONS.agent,
      ],
    };
    render(<AgentVersionsPanel env={ENV} />);
    expect(rowOf("a9")).toHaveTextContent("Checking…");
    expect(button("a9", "Check v9 again")).toBeNull();
    expect(button("a8", "Check v8 again")).toBeInTheDocument();
  });

  it("dates a stuck check from its last start, not the version's creation", async () => {
    const user = userEvent.setup();
    const now = new Date().toISOString();
    state.versions = {
      ...VERSIONS,
      agent: [
        agentItem({
          id: "a8",
          label: "v8",
          status: "draft",
          preflight_status: "pending",
          created_at: OLD,
          check_started_at: now,
          requested_here: true,
        }),
        agentItem({
          id: "a10",
          label: "v10",
          status: "draft",
          preflight_status: "pending",
          created_at: OLD,
          check_started_at: OLD,
          requested_here: true,
        }),
        { ...VERSIONS.agent[0], preflighted_at: OLD },
      ],
    };
    mutations.retry.data = {
      ...state.versions,
      agent: state.versions.agent.map((one) =>
        one.id === "a7"
          ? { ...one, preflight_status: "pending", check_started_at: now }
          : one,
      ),
    };
    const { unmount } = render(<AgentVersionsPanel env={ENV} />);
    // Asked for again lately, though made long ago; the control, long ago.
    expect(button("a8", "Check v8 again")).toBeNull();
    expect(button("a10", "Check v10 again")).toBeInTheDocument();

    await user.click(button("a7", "Check v7 again"));
    // The drawer closes and opens again: the server dates the restart.
    unmount();
    render(<AgentVersionsPanel env={ENV} />);

    // Its last check ended long ago, but it was just asked for again.
    expect(rowOf("a7")).toHaveTextContent("Checking…");
    expect(button("a7", "Check v7 again")).toBeNull();
  });

  it("announces a check that starts or lands", () => {
    state.versions = {
      ...VERSIONS,
      agent: [
        agentItem({
          id: "a8",
          label: "v8",
          status: "draft",
          preflight_status: "pending",
        }),
      ],
    };
    const { rerender } = render(<AgentVersionsPanel env={ENV} />);
    const region = screen.getByRole("status");
    expect(region).toHaveAttribute("aria-live", "polite");
    expect(region).toHaveTextContent("");

    state.versions = {
      ...state.versions,
      agent: [{ ...state.versions.agent[0], preflight_status: "passed" }],
    };
    rerender(<AgentVersionsPanel env={ENV} />);
    expect(screen.getByRole("status")).toHaveTextContent("v8: Check passed");
  });

  it("makes a version the default after asking", async () => {
    const user = userEvent.setup();
    mutations.move.data = {
      ...VERSIONS,
      agent: VERSIONS.agent.map((one) => ({
        ...one,
        is_default: one.id === "a6",
      })),
    };
    const { rerender } = render(<AgentVersionsPanel env={ENV} />);

    await user.click(button("a6", "Make v6 the default"));
    const dialog = screen.getByRole("dialog", {
      name: "Make v6 the default agent version?",
    });
    await user.click(
      within(dialog).getByRole("button", { name: "Make default" }),
    );

    expect(mutations.move.calls).toEqual([
      { piece: "agent", versionId: "a6", expectedDefaultId: "a4" },
    ]);
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    rerender(<AgentVersionsPanel env={ENV} />);
    expect(rowOf("a6")).toHaveTextContent("default");
    expect(button("a6", "Make v6 the default")).toBeNull();
  });

  it("ignores a confirm click while the dialog fades out", async () => {
    const user = userEvent.setup();
    render(<AgentVersionsPanel env={ENV} />);
    await user.click(button("a6", "Make v6 the default"));
    const confirm = within(screen.getByRole("dialog")).getByRole("button", {
      name: "Make default",
    });
    await user.click(confirm);
    expect(() => fireEvent.click(confirm)).not.toThrow();
    expect(mutations.move.calls).toHaveLength(1);
  });

  it("keeps the confirmation busy while the move is pending", async () => {
    const user = userEvent.setup();
    mutations.move.answer = "pending";
    render(<AgentVersionsPanel env={ENV} />);
    await user.click(button("a6", "Make v6 the default"));
    const dialog = screen.getByRole("dialog");
    await user.click(
      within(dialog).getByRole("button", { name: "Make default" }),
    );
    expect(
      within(dialog).getByRole("button", { name: "Making default…" }),
    ).toBeDisabled();
    for (const one of within(dialog).getAllByRole("button")) {
      expect(one).toBeDisabled();
    }
  });

  it.each([
    [
      "the default moved meanwhile",
      {
        statusCode: 409,
        error: "version_conflict",
        message: "The default changed to v8 since you opened this; pick again",
      },
      "The default changed to v8 since you opened this; pick again",
    ],
    [
      "the combination isn't prepared",
      {
        statusCode: 422,
        error: "rollback_unavailable",
        message: "This combination hasn't been prepared yet",
      },
      "This combination hasn't been prepared yet",
    ],
    [
      "the server gave no words",
      { statusCode: 500 },
      "Couldn't change the default. Try again in a moment.",
    ],
  ])("explains a refused move when %s", async (_, error, text) => {
    const user = userEvent.setup();
    mutations.move.answer = { error };
    render(<AgentVersionsPanel env={ENV} />);
    await user.click(button("a6", "Make v6 the default"));
    await user.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Make default",
      }),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(text);
    // Shown under the version it was about.
    expect(rowOf("a6").parentElement).toContainElement(alert);
  });

  it("discards a version after asking", async () => {
    const user = userEvent.setup();
    mutations.discard.data = {
      ...VERSIONS,
      agent: VERSIONS.agent.map((one) =>
        one.id === "a7"
          ? {
              ...one,
              status: "discarded",
              preflight_status: "discarded",
              discardable: false,
            }
          : one,
      ),
    };
    const { rerender } = render(<AgentVersionsPanel env={ENV} />);

    await user.click(button("a7", "Discard v7"));
    const dialog = screen.getByRole("dialog", { name: "Discard v7?" });
    expect(dialog).toHaveTextContent(
      "It stays in the history but can no longer be run or made the default.",
    );
    // Cancel asks nothing.
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(mutations.discard.calls).toEqual([]);
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    await user.click(button("a7", "Discard v7"));
    await user.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Discard",
      }),
    );
    expect(mutations.discard.calls).toEqual([{ versionId: "a7" }]);
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    rerender(<AgentVersionsPanel env={ENV} />);
    expect(rowOf("a7")).toHaveTextContent("discarded");
    expect(button("a7", "Discard v7")).toBeNull();
  });

  it("explains a refused discard", async () => {
    const user = userEvent.setup();
    const message =
      "This agent version is an environment's default; make another one the default first";
    mutations.discard.answer = {
      error: { statusCode: 422, error: "agent_version_in_use", message },
    };
    render(<AgentVersionsPanel env={ENV} />);
    await user.click(button("a6", "Discard v6"));
    await user.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Discard",
      }),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.getByRole("alert")).toHaveTextContent(message);
  });

  it("checks a failed version again and explains a refusal", async () => {
    const user = userEvent.setup();
    render(<AgentVersionsPanel env={ENV} />);
    await user.click(button("a7", "Check v7 again"));
    expect(mutations.retry.calls).toEqual([{ versionId: "a7" }]);
    expect(screen.queryByRole("alert")).toBeNull();

    mutations.retry.answer = {
      error: { statusCode: 422, error: "version_not_retryable" },
    };
    await user.click(button("a7", "Check v7 again"));
    expect(screen.getByRole("alert")).toHaveTextContent(
      "version_not_retryable",
    );
  });

  it("says when the agent has no versions", () => {
    state.versions = { agent: [], environment: [], suite: [] };
    render(<AgentVersionsPanel env={ENV} />);
    expect(
      screen.getByText("This environment's agent has no versions yet."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("There's no default agent version yet."),
    ).toBeInTheDocument();
  });
});

describe("AgentVersionsPanel nudges", () => {
  const nudge = (over) => ({
    id: "n1",
    target: "environment",
    state: "pending",
    facts: {
      changed_files: [{ path: "tools/a.py", status: "modified" }],
      variables: { added: [], removed: [], changed: ["API_KEY"], unknown: [] },
      prompt_changed: true,
    },
    trigger_agent_version: { id: "a6", label: "v6" },
    ...over,
  });
  // The nudge sits under the version's row, beside its refusals.
  const actionOf = (id) =>
    within(rowOf(id).parentElement).queryByTestId(/^rebuild-action-/);

  it("puts Rebuild and Skip on the row of the version that raised the nudge only", () => {
    state.nudges = [nudge()];
    render(<AgentVersionsPanel env={ENV} />);
    const action = actionOf("a6");
    expect(action).toHaveTextContent(
      "1 file changed · 1 variable changed · prompt changed",
    );
    expect(
      within(action).getByRole("button", { name: "Rebuild: v6" }),
    ).toBeEnabled();
    ["a7", "a5", "a4"].forEach((id) => expect(actionOf(id)).toBeNull());
  });

  it("rebuilds through the answer endpoint, then shows it waiting with Stop", async () => {
    state.nudges = [nudge()];
    const user = userEvent.setup();
    const { rerender } = render(<AgentVersionsPanel env={ENV} />);
    await user.click(
      within(actionOf("a6")).getByRole("button", { name: "Rebuild: v6" }),
    );
    expect(mutations.answer.calls).toEqual([{ nudgeId: "n1", answer: "yes" }]);
    state.nudges = [nudge({ state: "queued", can_stop: true })];
    rerender(<AgentVersionsPanel env={ENV} />);
    const action = actionOf("a6");
    expect(within(action).getByRole("status")).toHaveTextContent(
      "Waiting for the chat to finish",
    );
    expect(within(action).getByRole("status")).toHaveFocus();
    expect(action).not.toHaveTextContent("1 file changed");
    await user.click(
      within(action).getByRole("button", { name: "Stop the update: v6" }),
    );
    expect(mutations.stop.calls).toEqual([{ nudgeId: "n1" }]);
  });

  it("explains a refused Stop", async () => {
    state.nudges = [nudge({ state: "running", can_stop: true })];
    mutations.stop.answer = {
      error: { statusCode: 409, error: "nudge_closed", state: "applied" },
    };
    const user = userEvent.setup();
    render(<AgentVersionsPanel env={ENV} />);
    expect(within(actionOf("a6")).getByRole("status")).toHaveTextContent(
      "Updating…",
    );
    await user.click(
      within(actionOf("a6")).getByRole("button", {
        name: "Stop the update: v6",
      }),
    );
    expect(within(actionOf("a6")).getByRole("alert")).toHaveTextContent(
      "This update was already answered or closed.",
    );
  });

  it("offers Rebuild again on a failed update", async () => {
    state.nudges = [
      nudge({ state: "failed", error: { code: "stopped_by_user" } }),
    ];
    const user = userEvent.setup();
    render(<AgentVersionsPanel env={ENV} />);
    expect(within(actionOf("a6")).getByRole("status")).toHaveTextContent(
      "Stopped",
    );
    await user.click(
      within(actionOf("a6")).getByRole("button", {
        name: "Rebuild again: v6",
      }),
    );
    expect(mutations.answer.calls).toEqual([{ nudgeId: "n1", answer: "yes" }]);
  });

  it("keeps the row's state line once the rebuild lands, with no actions", () => {
    state.nudges = [nudge({ state: "running", can_stop: true })];
    const { rerender } = render(<AgentVersionsPanel env={ENV} />);
    state.nudges = [
      nudge({
        state: "applied",
        summary: {
          piece: "environment",
          label: "v4",
          agent_not_moved: "other",
        },
      }),
    ];
    rerender(<AgentVersionsPanel env={ENV} />);
    const action = actionOf("a6");
    expect(within(action).getByRole("status")).toHaveTextContent(
      "Rebuilt: environment v4",
    );
    expect(action).toHaveTextContent(
      "Agent v6 didn't become the default. Make it the default from the Agents panel.",
    );
    expect(within(action).queryByRole("button")).toBeNull();
  });

  it("says a rebuild found nothing to change", () => {
    state.nudges = [nudge({ state: "no_change" })];
    render(<AgentVersionsPanel env={ENV} />);
    expect(within(actionOf("a6")).getByRole("status")).toHaveTextContent(
      "No change needed",
    );
    expect(within(actionOf("a6")).queryByRole("button")).toBeNull();
  });

  it("skips through the answer endpoint", async () => {
    state.nudges = [nudge()];
    const user = userEvent.setup();
    render(<AgentVersionsPanel env={ENV} />);
    await user.click(
      within(actionOf("a6")).getByRole("button", { name: "Skip: v6" }),
    );
    expect(mutations.answer.calls).toEqual([{ nudgeId: "n1", answer: "no" }]);
  });

  it("explains a refused skip", async () => {
    state.nudges = [nudge()];
    mutations.answer.answer = {
      error: { statusCode: 409, error: "nudge_closed", state: "replaced" },
    };
    const user = userEvent.setup();
    render(<AgentVersionsPanel env={ENV} />);
    await user.click(
      within(actionOf("a6")).getByRole("button", { name: "Skip: v6" }),
    );
    expect(within(actionOf("a6")).getByRole("alert")).toHaveTextContent(
      "This update was already answered or closed.",
    );
  });

  it("keeps a refused skip off the nudge that replaced it", async () => {
    state.nudges = [nudge()];
    mutations.answer.answer = {
      error: { statusCode: 409, error: "nudge_closed", state: "replaced" },
    };
    const user = userEvent.setup();
    const { rerender } = render(<AgentVersionsPanel env={ENV} />);
    await user.click(
      within(actionOf("a6")).getByRole("button", { name: "Skip: v6" }),
    );
    state.nudges = [nudge({ id: "n2" })];
    rerender(<AgentVersionsPanel env={ENV} />);
    expect(within(actionOf("a6")).queryByRole("alert")).toBeNull();
    expect(
      within(actionOf("a6")).getByRole("button", { name: "Skip: v6" }),
    ).toBeEnabled();
  });

  it("keeps a skip in flight off the nudge that replaced it", async () => {
    state.nudges = [nudge()];
    mutations.answer.answer = "pending";
    const user = userEvent.setup();
    const { rerender } = render(<AgentVersionsPanel env={ENV} />);
    await user.click(
      within(actionOf("a6")).getByRole("button", { name: "Skip: v6" }),
    );
    state.nudges = [nudge({ id: "n2" })];
    rerender(<AgentVersionsPanel env={ENV} />);
    expect(
      within(actionOf("a6")).getByRole("button", { name: "Skip: v6" }),
    ).toBeEnabled();
  });

  it("moves focus to the skipped line after a Skip here", async () => {
    state.nudges = [nudge()];
    const user = userEvent.setup();
    const { rerender } = render(<AgentVersionsPanel env={ENV} />);
    await user.click(
      within(actionOf("a6")).getByRole("button", { name: "Skip: v6" }),
    );
    state.nudges = [nudge({ state: "answered_no" })];
    rerender(<AgentVersionsPanel env={ENV} />);
    const line = within(rowOf("a6").parentElement).getByRole("status");
    expect(line).toHaveTextContent("Update skipped");
    expect(line).toHaveFocus();
  });

  it("says a skipped update was skipped, with no actions", () => {
    state.nudges = [nudge({ state: "answered_no" })];
    render(<AgentVersionsPanel env={ENV} />);
    expect(actionOf("a6")).toBeNull();
    expect(
      within(rowOf("a6").parentElement).getByText("Update skipped"),
    ).toBeInTheDocument();
  });

  it("shows nothing without a nudge, or for a suite nudge", () => {
    state.nudges = [
      nudge({ id: "n2", target: "suite", trigger_agent_version: null }),
    ];
    render(<AgentVersionsPanel env={ENV} />);
    expect(screen.queryByTestId(/^rebuild-action-/)).toBeNull();
    expect(screen.queryByText("Update skipped")).toBeNull();
  });
});
