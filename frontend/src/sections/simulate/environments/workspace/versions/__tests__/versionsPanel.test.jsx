import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

// Each hook is stubbed; tests set what the server answered.
const state = {
  versions: undefined,
  diffs: {},
  members: {},
  versionsError: false,
  versionsDisabled: false,
  diffLoading: false,
  membersError: false,
  // A refresh that failed after an earlier answer: the data stays cached.
  versionsRefreshFailed: false,
  diffRefreshFailed: false,
  membersRefreshFailed: false,
};
const diffCalls = [];
// What a move of the default answers: "pending", { error } or { versions }.
const move = { answer: undefined, calls: [] };
vi.mock("src/api/simulate-environments/versions", async () => {
  const actual = await vi.importActual(
    "src/api/simulate-environments/versions",
  );
  const { useState } = await import("react");
  return {
    ...actual,
    useMoveDefault: vi.fn(() => {
      const [status, setStatus] = useState({});
      return {
        isPending: Boolean(status.pending),
        isError: Boolean(status.error),
        error: status.error || null,
        reset: () => setStatus({}),
        mutate: (variables, { onSettled } = {}) => {
          move.calls.push(variables);
          if (move.answer === "pending") {
            setStatus({ pending: true });
            return;
          }
          if (move.answer?.versions) state.versions = move.answer.versions;
          setStatus(move.answer?.error ? { error: move.answer.error } : {});
          onSettled?.();
        },
      };
    }),
    useEnvironmentVersions: vi.fn((envId, { enabled } = {}) => {
      if (!enabled)
        return { data: undefined, isLoading: false, isError: false };
      if (state.versionsError)
        return { data: undefined, isLoading: false, isError: true };
      if (state.versionsRefreshFailed) {
        return {
          data: actual.toVersions(state.versions),
          isLoading: false,
          isError: true,
          error: { statusCode: 500 },
        };
      }
      if (state.versionsDisabled) {
        // The last answer is still cached; the refetch said versioning is off.
        return {
          data: actual.toVersions(state.versions),
          isLoading: false,
          isError: true,
          error: { statusCode: 404, error: "versioning_disabled" },
        };
      }
      return {
        data: state.versions ? actual.toVersions(state.versions) : undefined,
        isLoading: !state.versions,
        isError: false,
      };
    }),
    useVersionDiff: vi.fn(
      (envId, piece, id, against, { enabled = true } = {}) => {
        diffCalls.push({ piece, id, against, enabled });
        if (!enabled)
          return { data: undefined, isLoading: false, isError: false };
        if (state.diffLoading)
          return { data: undefined, isLoading: true, isError: false };
        const data = state.diffs[`${id}:${against || ""}`];
        return {
          data,
          isLoading: false,
          isError:
            (!data && state.diffs.error === true) || state.diffRefreshFailed,
        };
      },
    ),
    useSuiteMembers: vi.fn((envId, id) => ({
      data: state.membersError ? undefined : state.members[id] || [],
      isLoading: false,
      isError: state.membersError || state.membersRefreshFailed,
    })),
  };
});

const { default: VersionsPanel } = await import("../VersionsPanel");
const { default: useWorkspaceTab } = await import(
  "../../helpers/useWorkspaceTab"
);

const ENV = {
  id: "env-1",
  versioning: { enabled: true, nudges_enabled: false },
};

const item = (overrides) => ({
  status: "active",
  is_default: false,
  created_at: "2026-10-15T10:00:00Z",
  created_by: null,
  origin: null,
  summary: "",
  hash: null,
  parent_id: null,
  ...overrides,
});

const VERSIONS = {
  agent: [
    item({ id: "a1", label: "v4", is_default: true, origin: "backfill" }),
  ],
  environment: [
    item({
      id: "e2",
      label: "v2",
      is_default: true,
      origin: "chat",
      summary: "1 tool added, 1 world file changed",
      parent_id: "e1",
      created_by: { id: "u1", name: "Asha" },
    }),
    item({
      id: "e1",
      label: "v1",
      origin: "backfill",
      summary: "First version",
    }),
  ],
  suite: [
    item({
      id: "s2",
      label: "v2",
      is_default: true,
      origin: "manual",
      parent_id: "s1",
      scenario_count: 2,
    }),
    item({
      id: "s1",
      label: "v1",
      status: "discarded",
      origin: "backfill",
      scenario_count: 3,
    }),
  ],
};

const ENV_DIFF = {
  summary: "1 tool added, 1 world file changed",
  diff: {
    contract: {
      tools: { added: ["issue_refund"], removed: [], changed: [] },
      rules: { added: [], removed: ["no refunds over $500"] },
      use_cases: { added: [], removed: [] },
      fields: ["persona"],
      modality: { from: "text", to: "voice" },
    },
    sub_goals: { added: [], removed: [], changed: ["refunded"] },
    files: { added: [], removed: [], changed: ["world.sqlite"] },
  },
};

beforeEach(() => {
  state.versions = VERSIONS;
  state.versionsError = false;
  state.versionsDisabled = false;
  state.diffs = { "e2:": ENV_DIFF };
  state.members = {};
  state.diffLoading = false;
  state.membersError = false;
  state.versionsRefreshFailed = false;
  state.diffRefreshFailed = false;
  state.membersRefreshFailed = false;
  diffCalls.length = 0;
  move.answer = undefined;
  move.calls.length = 0;
});

// A version row's accessible name starts with its label.
const row = (piece, label) =>
  within(screen.getByTestId(`versions-${piece}`)).getByRole("button", {
    name: new RegExp(`^${label}\\b`),
  });

describe("VersionsPanel", () => {
  it("renders nothing while versioning is off", () => {
    const { container } = render(<VersionsPanel env={{ id: "env-1" }} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("lists agent, environment and scenario versions with defaults, status and origin", () => {
    render(<VersionsPanel env={ENV} />);

    const agent = screen.getByTestId("versions-agent");
    expect(within(agent).getByText("v4")).toBeInTheDocument();
    expect(
      within(agent).getByText("Recorded from before versioning"),
    ).toBeInTheDocument();

    const environment = screen.getByTestId("versions-environment");
    expect(within(environment).getAllByRole("button")).toHaveLength(2);
    expect(
      within(environment).getByText("Saved from chat"),
    ).toBeInTheDocument();
    expect(within(environment).getByText(/by Asha/)).toBeInTheDocument();

    const suite = screen.getByTestId("versions-suite");
    expect(within(suite).getByText("discarded")).toBeInTheDocument();
    expect(within(suite).getAllByText("default")).toHaveLength(1);
  });

  it("shows an agent version's check and its report", async () => {
    const user = userEvent.setup();
    state.versions = {
      ...VERSIONS,
      agent: [
        item({
          id: "a5",
          label: "v5",
          status: "draft",
          origin: "credentials",
          preflight_status: "failed",
          preflight_report: {
            checks: [
              {
                id: "credentials_present",
                label: "Target credentials",
                status: "failed",
                missing: ["API_KEY"],
              },
            ],
            missing: ["API_KEY"],
            invalid: [],
            error: null,
          },
        }),
        item({
          id: "a6",
          label: "v6",
          status: "draft",
          preflight_status: "pending",
        }),
        ...VERSIONS.agent,
      ],
    };
    render(<VersionsPanel env={ENV} />);

    expect(row("agent", "v5")).toHaveTextContent("Check failed");
    expect(row("agent", "v6")).toHaveTextContent("Checking…");
    // A version from before versioning has no check to show.
    expect(row("agent", "v4")).not.toHaveTextContent(/Check/);

    await user.click(row("agent", "v5"));
    const report = within(screen.getByTestId("version-detail")).getByTestId(
      "preflight-report",
    );
    expect(report).toHaveTextContent(
      "Target credentials: failed · Missing: API_KEY",
    );
  });

  it("opens on the default environment version and shows what changed", () => {
    render(<VersionsPanel env={ENV} />);
    const detail = screen.getByTestId("version-detail");

    expect(
      within(detail).getByText("What changed in Environment v2"),
    ).toBeInTheDocument();
    expect(within(detail).getByText("issue_refund")).toBeInTheDocument();
    expect(
      within(detail).getByText("no refunds over $500"),
    ).toBeInTheDocument();
    expect(within(detail).getByText("persona")).toBeInTheDocument();
    expect(within(detail).getByText("text → voice")).toBeInTheDocument();
    expect(within(detail).getByText("refunded")).toBeInTheDocument();
    expect(within(detail).getByText("world.sqlite")).toBeInTheDocument();
  });

  it("says when the earlier version's world files are unknown", async () => {
    state.diffs["e2:"] = {
      summary: "1 tool added",
      diff: { ...ENV_DIFF.diff, files: null },
    };
    render(<VersionsPanel env={ENV} />);
    expect(
      screen.getByText(/World files of the earlier version aren't known/),
    ).toBeInTheDocument();
    expect(screen.queryByText("world.sqlite")).toBeNull();
  });

  it("calls the oldest version the first, without asking for its diff", async () => {
    const user = userEvent.setup();
    render(<VersionsPanel env={ENV} />);
    await user.click(row("environment", "v1"));
    expect(screen.getByText(/This is the first version/)).toBeInTheDocument();
    expect(diffCalls.at(-1)).toMatchObject({ id: "e1", enabled: false });
  });

  it("shows a suite version's changes and its scenarios", async () => {
    const user = userEvent.setup();
    state.diffs["s2:"] = {
      summary: "1 scenario added, 1 scenario removed, 1 scenario changed",
      diff: {
        added: ["late-delivery"],
        removed: [{ key: "old-refund", reason: "removed by manual edit" }],
        changed: { "refund-request": ["instruction", "persona"] },
      },
    };
    state.members.s2 = [
      {
        scenario_key: "refund-request",
        number: 1,
        name: "Refund request",
        use_case: "billing",
      },
      { scenario_key: "late-delivery", number: 2, name: "", use_case: "" },
    ];
    render(<VersionsPanel env={ENV} />);

    await user.click(row("suite", "v2"));

    const detail = screen.getByTestId("version-detail");
    expect(
      within(detail).getByText("What changed in Scenarios v2"),
    ).toBeInTheDocument();
    expect(
      within(detail).getByText("late-delivery", { selector: "span" }),
    ).toBeInTheDocument();
    expect(
      within(detail).getByText(/removed by manual edit/),
    ).toBeInTheDocument();
    expect(
      within(detail).getByText(": instruction, persona"),
    ).toBeInTheDocument();
    expect(within(detail).getByText("2 scenarios in v2")).toBeInTheDocument();
    expect(within(detail).getByText("Refund request")).toBeInTheDocument();
    expect(within(detail).getByRole("table")).toHaveTextContent("billing");
  });

  it("compares against another version of the same piece", async () => {
    const user = userEvent.setup();
    state.diffs["e2:e1"] = {
      summary: "No contract, sub-goal or world change",
      diff: {
        contract: { tools: {}, rules: {}, use_cases: {}, fields: [] },
        sub_goals: {},
        files: {},
      },
    };
    render(<VersionsPanel env={ENV} />);

    await user.click(screen.getByRole("combobox", { name: "Compared with" }));
    await user.click(screen.getByRole("option", { name: "v1" }));

    expect(diffCalls.at(-1)).toEqual({
      piece: "environment",
      id: "e2",
      against: "e1",
      enabled: true,
    });
    expect(screen.getByText("Nothing changed.")).toBeInTheDocument();
    // The server's "no change" summary is not repeated above it.
    expect(
      screen.queryByText("No contract, sub-goal or world change"),
    ).toBeNull();
  });

  it("shows an empty state when the environment has no versions", () => {
    state.versions = { agent: [], environment: [], suite: [] };
    render(<VersionsPanel env={ENV} />);
    expect(
      screen.getByText("This environment has no versions yet"),
    ).toBeInTheDocument();
  });

  it("says when the versions could not be loaded", () => {
    state.versionsError = true;
    render(<VersionsPanel env={ENV} />);
    expect(screen.getByText(/Couldn't load the versions/)).toBeInTheDocument();
  });

  it("renders an agent version's changes when it has a change record", async () => {
    const user = userEvent.setup();
    state.diffs["a2:"] = {
      summary: "",
      diff: {
        source: true,
        prompt: false,
        variables: { added: ["API_TOKEN"], removed: [], changed: [] },
      },
    };
    state.versions = {
      ...VERSIONS,
      agent: [
        item({ id: "a2", label: "v5", is_default: true }),
        item({ id: "a1", label: "v4" }),
      ],
    };
    render(<VersionsPanel env={ENV} />);
    await user.click(row("agent", "v5"));
    expect(screen.getByText("Source changed")).toBeInTheDocument();
    expect(screen.getByText("API_TOKEN")).toBeInTheDocument();
  });

  it("is honest about agent versions from before versioning", async () => {
    const user = userEvent.setup();
    // What the server sends today: no parent, no diff, no origin.
    state.diffs["a2:"] = { summary: "", diff: null };
    state.versions = {
      ...VERSIONS,
      agent: [
        item({ id: "a2", label: "v5", is_default: true }),
        item({ id: "a1", label: "v4" }),
      ],
    };
    render(<VersionsPanel env={ENV} />);

    await user.click(row("agent", "v5"));
    expect(
      screen.getByText("No change record for this version."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/This is the first version/)).toBeNull();
    expect(screen.queryByText("Nothing changed.")).toBeNull();

    await user.click(row("agent", "v4"));
    expect(screen.getByText(/This is the first version/)).toBeInTheDocument();
  });

  it("shows a spinner while the changes load and an error if they fail", async () => {
    state.diffLoading = true;
    const { unmount } = render(<VersionsPanel env={ENV} />);
    expect(
      within(screen.getByTestId("version-detail")).getByRole("progressbar"),
    ).toBeInTheDocument();
    unmount();

    state.diffLoading = false;
    state.diffs = { error: true };
    render(<VersionsPanel env={ENV} />);
    expect(screen.getByText("Couldn't load the changes.")).toBeInTheDocument();
  });

  it("says when a suite version's scenarios could not be loaded", async () => {
    const user = userEvent.setup();
    state.membersError = true;
    state.diffs["s2:"] = {
      summary: "",
      diff: { added: ["x"], removed: [], changed: {} },
    };
    render(<VersionsPanel env={ENV} />);
    await user.click(row("suite", "v2"));
    expect(
      screen.getByText("Couldn't load the scenarios in this version."),
    ).toBeInTheDocument();
  });

  it("labels each list by its heading and marks the selected version", async () => {
    const user = userEvent.setup();
    render(<VersionsPanel env={ENV} />);
    expect(
      screen.getByRole("list", { name: "Environment" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Scenarios" })).toBeInTheDocument();
    expect(row("environment", "v2")).toHaveAttribute("aria-current", "true");
    expect(row("environment", "v1")).not.toHaveAttribute("aria-current");
    // Every row sits in a list item, so each list is a valid list.
    expect(row("environment", "v2").closest("li")).not.toBeNull();

    await user.click(row("suite", "v1"));
    expect(row("suite", "v1")).toHaveAttribute("aria-current", "true");
    expect(row("environment", "v2")).not.toHaveAttribute("aria-current");
  });

  it("keeps showing the versions when a refresh fails", () => {
    state.versionsRefreshFailed = true;
    render(<VersionsPanel env={ENV} />);
    expect(row("environment", "v2")).toBeInTheDocument();
    expect(screen.queryByText(/Couldn't load the versions/)).toBeNull();
  });

  it("keeps a diff and a suite's scenarios when their refresh fails", async () => {
    const user = userEvent.setup();
    state.diffRefreshFailed = true;
    state.membersRefreshFailed = true;
    state.diffs["s2:"] = {
      summary: "1 scenario added",
      diff: { added: ["late-delivery"], removed: [], changed: {} },
    };
    state.members.s2 = [
      { scenario_key: "late-delivery", number: 1, name: "Late", use_case: "" },
    ];
    render(<VersionsPanel env={ENV} />);
    expect(screen.queryByText("Couldn't load the changes.")).toBeNull();
    await user.click(row("suite", "v2"));
    const detail = screen.getByTestId("version-detail");
    expect(within(detail).getAllByText("late-delivery").length).toBeGreaterThan(
      0,
    );
    expect(
      screen.queryByText("Couldn't load the scenarios in this version."),
    ).toBeNull();
  });

  it("does not call the oldest listed version first when it has a parent", async () => {
    const user = userEvent.setup();
    // The real first version was deleted; v2 still has its stored diff.
    state.versions = {
      ...VERSIONS,
      environment: [VERSIONS.environment[0]],
    };
    render(<VersionsPanel env={ENV} />);
    await user.click(row("environment", "v2"));
    expect(screen.queryByText(/This is the first version/)).toBeNull();
    expect(diffCalls.at(-1)).toMatchObject({ id: "e2", enabled: true });
  });

  it("names an unnumbered version instead of showing its id", () => {
    state.versions = {
      ...VERSIONS,
      environment: [
        item({ id: "e9", label: null, status: "draft" }),
        ...VERSIONS.environment,
      ],
    };
    render(<VersionsPanel env={ENV} />);
    expect(row("environment", "Unnumbered version")).toBeInTheDocument();
    expect(screen.queryByText("e9")).toBeNull();
  });

  it("says two agent versions can't be compared when the server has no comparison", async () => {
    const user = userEvent.setup();
    state.versions = {
      ...VERSIONS,
      agent: [
        item({ id: "a5", label: "v5", is_default: true }),
        item({ id: "a4", label: "v4" }),
        item({ id: "a3", label: "v3" }),
      ],
    };
    state.diffs["a5:a3"] = { summary: "", diff: null };
    render(<VersionsPanel env={ENV} />);
    await user.click(row("agent", "v5"));
    await user.click(screen.getByRole("combobox", { name: "Compared with" }));
    await user.click(screen.getByRole("option", { name: "v3" }));

    expect(
      screen.getByText("These two versions can't be compared."),
    ).toBeInTheDocument();
    expect(screen.queryByText("No change record for this version.")).toBeNull();
  });

  it("shows versioning as off when the server switched it off, not a retry message", () => {
    state.versionsDisabled = true;
    render(<VersionsPanel env={ENV} />);
    expect(
      screen.getByText("Versioning is switched off for this environment."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Try again/)).toBeNull();
  });

  it("falls back to the previous version when the compared one disappears", async () => {
    const user = userEvent.setup();
    state.diffs["e2:e1"] = ENV_DIFF;
    const { rerender } = render(<VersionsPanel env={ENV} />);
    await user.click(screen.getByRole("combobox", { name: "Compared with" }));
    await user.click(screen.getByRole("option", { name: "v1" }));
    expect(diffCalls.at(-1)).toMatchObject({ id: "e2", against: "e1" });

    state.versions = {
      ...VERSIONS,
      environment: [VERSIONS.environment[0], item({ id: "e0", label: "v0" })],
    };
    rerender(<VersionsPanel env={ENV} />);

    expect(diffCalls.at(-1)).toMatchObject({ id: "e2", against: undefined });
    expect(
      screen.getByRole("combobox", { name: "Compared with" }),
    ).toHaveTextContent("Its previous version");
  });

  it("opens on the version a link names, such as the unsaved draft", () => {
    state.versions = {
      ...VERSIONS,
      suite: [
        item({ id: "sd", label: null, status: "draft", parent_id: "s2" }),
        ...VERSIONS.suite,
      ],
    };
    window.history.pushState({}, "", "/?tab=versions&version=suite:sd");
    try {
      render(<VersionsPanel env={ENV} />);
      expect(row("suite", "Unnumbered version")).toHaveAttribute(
        "aria-current",
        "true",
      );
      expect(row("environment", "v2")).not.toHaveAttribute("aria-current");
    } finally {
      window.history.pushState({}, "", "/");
    }
  });

  it("opens the linked version again after the reader picked another one", async () => {
    const user = userEvent.setup();
    state.versions = {
      ...VERSIONS,
      suite: [
        item({ id: "sd", label: null, status: "draft", parent_id: "s2" }),
        ...VERSIONS.suite,
      ],
    };
    const Link = () => {
      const { setTab } = useWorkspaceTab();
      return (
        <button
          type="button"
          onClick={() => setTab("versions", { version: "suite:sd" })}
        >
          View changes
        </button>
      );
    };
    window.history.pushState({}, "", "/?tab=versions&version=suite:sd");
    try {
      render(
        <>
          <Link />
          <VersionsPanel env={ENV} />
        </>,
      );
      await user.click(row("environment", "v1"));
      expect(window.location.search).not.toContain("version=");
      await user.click(screen.getByRole("button", { name: "View changes" }));
      expect(row("suite", "Unnumbered version")).toHaveAttribute(
        "aria-current",
        "true",
      );
    } finally {
      window.history.pushState({}, "", "/");
    }
  });

  it("opens on the newest version that is not a draft when a piece has no default", () => {
    state.versions = {
      agent: [
        item({ id: "a9", label: null, status: "draft" }),
        item({ id: "a8", label: "v3" }),
      ],
      environment: [],
      suite: [],
    };
    render(<VersionsPanel env={ENV} />);
    expect(screen.getByText("What changed in Agent v3")).toBeInTheDocument();
  });

  it("keeps the oldest version's stored summary next to the first-version note", async () => {
    const user = userEvent.setup();
    state.versions = {
      ...VERSIONS,
      environment: [
        VERSIONS.environment[0],
        item({
          id: "e1",
          label: "v1",
          summary:
            "Recorded from the environment as it stood before versioning",
        }),
      ],
    };
    render(<VersionsPanel env={ENV} />);
    await user.click(row("environment", "v1"));
    const detail = screen.getByTestId("version-detail");
    expect(
      within(detail).getByText(/This is the first version/),
    ).toBeInTheDocument();
    expect(
      within(detail).getByText(
        "Recorded from the environment as it stood before versioning",
      ),
    ).toBeInTheDocument();
  });

  it("names an unnumbered suite version in its scenarios line", async () => {
    const user = userEvent.setup();
    state.versions = {
      ...VERSIONS,
      suite: [
        item({ id: "s9", label: null, status: "draft" }),
        ...VERSIONS.suite,
      ],
    };
    state.members.s9 = [
      { scenario_key: "late-delivery", number: 1, name: "Late", use_case: "" },
    ];
    render(<VersionsPanel env={ENV} />);
    await user.click(row("suite", "Unnumbered version"));
    expect(
      screen.getByText("1 scenario in Unnumbered version"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/null/)).toBeNull();
  });

  describe("making a version the default", () => {
    const makeDefault = () =>
      screen.getByRole("button", { name: "Make default" });
    const confirmDialog = (name) => screen.queryByRole("dialog", { name });

    it("makes an earlier environment version the default after asking", async () => {
      const user = userEvent.setup();
      move.answer = {
        versions: {
          ...VERSIONS,
          environment: [
            { ...VERSIONS.environment[0], is_default: false },
            { ...VERSIONS.environment[1], is_default: true },
          ],
        },
      };
      const { rerender } = render(<VersionsPanel env={ENV} />);
      // The default itself offers nothing.
      expect(
        within(screen.getByTestId("version-detail")).queryByRole("button", {
          name: "Make default",
        }),
      ).toBeNull();

      await user.click(row("environment", "v1"));
      await user.click(makeDefault());
      const dialog = confirmDialog("Make v1 the default environment?");
      expect(dialog).toHaveTextContent(
        "New runs use it. If this combination hasn't run before, the next run prepares it first.",
      );
      await user.click(
        within(dialog).getByRole("button", { name: "Make default" }),
      );

      expect(move.calls).toEqual([
        { piece: "environment", versionId: "e1", expectedDefaultId: "e2" },
      ]);
      await waitFor(() =>
        expect(confirmDialog("Make v1 the default environment?")).toBeNull(),
      );
      // The versions query now answers with the moved default.
      rerender(<VersionsPanel env={ENV} />);
      const environment = screen.getByTestId("versions-environment");
      expect(row("environment", "v1")).toHaveTextContent("default");
      expect(within(environment).getAllByText("default")).toHaveLength(1);
      expect(screen.queryByRole("button", { name: "Make default" })).toBeNull();
    });

    it("makes another scenario version the default", async () => {
      const user = userEvent.setup();
      state.versions = {
        ...VERSIONS,
        suite: [...VERSIONS.suite, item({ id: "s0", label: "v0" })],
      };
      render(<VersionsPanel env={ENV} />);

      await user.click(row("suite", "v0"));
      await user.click(makeDefault());
      const dialog = confirmDialog("Make v0 the default scenario version?");
      await user.click(
        within(dialog).getByRole("button", { name: "Make default" }),
      );

      expect(move.calls).toEqual([
        { piece: "suite", versionId: "s0", expectedDefaultId: "s2" },
      ]);
    });

    it("offers nothing on a discarded version or an agent version still being checked", async () => {
      const user = userEvent.setup();
      state.versions = {
        ...VERSIONS,
        agent: [
          item({
            id: "a5",
            label: "v5",
            status: "draft",
            preflight_status: "pending",
          }),
          ...VERSIONS.agent,
        ],
      };
      render(<VersionsPanel env={ENV} />);

      await user.click(row("suite", "v1"));
      expect(screen.queryByRole("button", { name: "Make default" })).toBeNull();
      await user.click(row("agent", "v5"));
      expect(screen.queryByRole("button", { name: "Make default" })).toBeNull();
    });

    it("makes an earlier agent version the default after asking", async () => {
      const user = userEvent.setup();
      state.versions = {
        ...VERSIONS,
        agent: [
          ...VERSIONS.agent,
          item({
            id: "a0",
            label: "v3",
            preflight_status: "passed",
            pickable: true,
          }),
        ],
      };
      render(<VersionsPanel env={ENV} />);

      await user.click(row("agent", "v3"));
      await user.click(makeDefault());
      const dialog = confirmDialog("Make v3 the default agent version?");
      await user.click(
        within(dialog).getByRole("button", { name: "Make default" }),
      );

      expect(move.calls).toEqual([
        { piece: "agent", versionId: "a0", expectedDefaultId: "a1" },
      ]);
    });

    it("keeps the confirmation busy while the move is pending", async () => {
      const user = userEvent.setup();
      move.answer = "pending";
      render(<VersionsPanel env={ENV} />);
      await user.click(row("environment", "v1"));
      await user.click(makeDefault());
      const dialog = confirmDialog("Make v1 the default environment?");
      await user.click(
        within(dialog).getByRole("button", { name: "Make default" }),
      );

      expect(
        within(dialog).getByRole("button", { name: "Making default…" }),
      ).toBeDisabled();
      // Cancel and the close X are visibly off too, not just ignored.
      const buttons = within(dialog).getAllByRole("button");
      expect(buttons).toHaveLength(3);
      for (const button of buttons) {
        expect(button).toBeDisabled();
      }
    });

    it("keeps the conflict in view after the refresh removes the action", async () => {
      const user = userEvent.setup();
      const message =
        "The default changed to v1 since you opened this; pick again";
      move.answer = {
        error: { statusCode: 409, error: "version_conflict", message },
        // Someone else made v1 the default meanwhile.
        versions: {
          ...VERSIONS,
          environment: [
            { ...VERSIONS.environment[0], is_default: false },
            { ...VERSIONS.environment[1], is_default: true },
          ],
        },
      };
      const { rerender } = render(<VersionsPanel env={ENV} />);
      await user.click(row("environment", "v1"));
      await user.click(makeDefault());
      const dialog = confirmDialog("Make v1 the default environment?");
      await user.click(
        within(dialog).getByRole("button", { name: "Make default" }),
      );

      rerender(<VersionsPanel env={ENV} />);
      const detail = screen.getByTestId("version-detail");
      expect(
        within(detail).queryByRole("button", { name: "Make default" }),
      ).toBeNull();
      expect(within(detail).getByRole("alert")).toHaveTextContent(message);

      // Picking a version again clears it.
      await user.click(row("environment", "v2"));
      expect(screen.queryByRole("alert")).toBeNull();
    });

    it.each([
      [
        "the default moved meanwhile",
        {
          statusCode: 409,
          error: "version_conflict",
          message:
            "The default changed to v3 since you opened this; pick again",
          piece: "environment",
        },
        "The default changed to v3 since you opened this; pick again",
      ],
      [
        "the combination isn't prepared",
        {
          statusCode: 422,
          error: "rollback_unavailable",
          message: "This combination hasn't been prepared yet",
          piece: "environment",
        },
        "This combination hasn't been prepared yet",
      ],
      [
        "the environment is being built or extended",
        {
          statusCode: 409,
          error: "environment_busy",
          message:
            "The environment is being built or extended; try again when it finishes",
        },
        "The environment is being built or extended; try again when it finishes",
      ],
    ])("says why when %s", async (_, error, text) => {
      const user = userEvent.setup();
      move.answer = { error };
      render(<VersionsPanel env={ENV} />);
      await user.click(row("environment", "v1"));
      await user.click(makeDefault());
      const dialog = confirmDialog("Make v1 the default environment?");
      await user.click(
        within(dialog).getByRole("button", { name: "Make default" }),
      );

      await waitFor(() =>
        expect(confirmDialog("Make v1 the default environment?")).toBeNull(),
      );
      expect(
        within(screen.getByTestId("version-detail")).getByRole("alert"),
      ).toHaveTextContent(text);
    });
  });
});
