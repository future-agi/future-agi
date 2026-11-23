import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import { fDateTime } from "src/utils/format-time";

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
// The last comparison the panel actually asked for (a switched-off call asks
// nothing).
const lastAsked = () => diffCalls.filter((call) => call.enabled).at(-1);
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
        variables: status.variables,
        reset: () => setStatus({}),
        mutate: (variables, { onSettled } = {}) => {
          move.calls.push(variables);
          if (move.answer === "pending") {
            setStatus({ pending: true, variables });
            return;
          }
          if (move.answer?.versions) state.versions = move.answer.versions;
          setStatus(
            move.answer?.error ? { error: move.answer.error, variables } : {},
          );
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
        // A comparison still loading keeps the previous answer, as
        // placeholderData does.
        if (state.diffStale)
          return {
            data: state.diffs[`${id}:`],
            isLoading: false,
            isError: false,
            isPlaceholderData: Boolean(against),
          };
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
const { default: useSimulationAccess } = await import(
  "../../../hooks/useSimulationAccess"
);
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
      rules: {
        added: ["no refunds over $1,000", "ask for the order number"],
        removed: ["no refunds over $500"],
      },
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
  state.diffStale = false;
  state.membersError = false;
  state.versionsRefreshFailed = false;
  state.diffRefreshFailed = false;
  state.membersRefreshFailed = false;
  diffCalls.length = 0;
  move.answer = undefined;
  move.calls.length = 0;
});

const PIECE_TITLE = {
  agent: "Agent",
  environment: "Environment",
  suite: "Scenarios",
};
// The timeline shows one kind at a time; pick the kind's filter first.
const showKind = (piece) => {
  const filter = screen.getByRole("button", { name: PIECE_TITLE[piece] });
  if (filter.getAttribute("aria-pressed") !== "true") fireEvent.click(filter);
};
// A version row's accessible name starts with its kind and label.
const row = (piece, label) => {
  showKind(piece);
  return screen.getByRole("button", {
    name: new RegExp(`^${PIECE_TITLE[piece]} ${label}\\b`),
  });
};
const entryOf = (piece, label) =>
  row(piece, label).closest('[data-testid^="history-entry-"]');
const openActions = async (user, piece, label) => {
  await user.click(
    within(entryOf(piece, label)).getByRole("button", {
      name: `Version actions, ${PIECE_TITLE[piece]} ${label}`,
    }),
  );
  return screen.getByRole("menu");
};

describe("VersionsPanel", () => {
  it("renders nothing while versioning is off", () => {
    const { container } = render(<VersionsPanel env={{ id: "env-1" }} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("gives the exact time a version was made on hover", async () => {
    const user = userEvent.setup();
    render(<VersionsPanel env={ENV} />);
    await user.hover(within(entryOf("environment", "v2")).getByText(/ ago$/));
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      fDateTime(VERSIONS.environment[0].created_at),
    );
  });

  it("lists agent, environment and scenario versions with their badges and origin", () => {
    render(<VersionsPanel env={ENV} />);

    expect(row("agent", "v4")).toHaveTextContent(
      "Recorded from before versioning",
    );
    expect(row("agent", "v4")).toHaveTextContent("being tested");
    expect(row("environment", "v2")).toHaveTextContent("Saved from chat");
    expect(row("environment", "v2")).toHaveTextContent(/by Asha/);
    expect(row("environment", "v2")).toHaveTextContent("runs use this");
    expect(row("environment", "v1")).not.toHaveTextContent("runs use this");
    expect(row("suite", "v1")).toHaveTextContent("discarded");
    expect(row("suite", "v2")).toHaveTextContent("runs use this");
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

  it("opens nothing until a row is picked, then shows what changed in it", async () => {
    const user = userEvent.setup();
    render(<VersionsPanel env={ENV} />);
    expect(screen.queryByTestId("version-detail")).toBeNull();
    await user.click(row("environment", "v2"));
    const detail = screen.getByTestId("version-detail");

    // The row above already names the version and says its summary.
    expect(within(detail).getByText("What changed")).toBeInTheDocument();
    expect(
      within(detail).queryByText("1 tool added, 1 world file changed"),
    ).toBeNull();
    expect(within(detail).getByText("issue_refund")).toBeInTheDocument();
    // A rule reads as its wording, one per line: the old one struck through,
    // the new one marked added.
    expect(
      within(detail).getByText("no refunds over $500").closest("del"),
    ).not.toBeNull();
    expect(
      within(detail).getByText("no refunds over $1,000").closest("ins"),
    ).not.toBeNull();
    expect(
      within(detail).getByText("ask for the order number"),
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
    await userEvent.setup().click(row("environment", "v2"));
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
        changed: {
          "refund-request": ["instruction", "persona"],
          "wrong-item": ["instruction", "persona"],
        },
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
    expect(within(detail).getByText("What changed")).toBeInTheDocument();
    expect(
      within(detail).getByText("late-delivery", { selector: "span" }),
    ).toBeInTheDocument();
    expect(
      within(detail).getByText(/removed by manual edit/),
    ).toBeInTheDocument();
    // Scenarios that changed the same way share one line, in the scenario
    // editor's words, not the server's keys.
    expect(
      within(detail).getAllByText(/What the caller wants, Persona/),
    ).toHaveLength(1);
    expect(
      within(detail).getByText("What the caller wants, Persona · 2 scenarios"),
    ).toBeInTheDocument();
    expect(within(detail).getByText("wrong-item")).toBeInTheDocument();
    // The list under the changes is the whole version, not the comparison.
    expect(
      within(detail).getByText("All 2 scenarios in v2"),
    ).toBeInTheDocument();
    expect(within(detail).getByText("Refund request")).toBeInTheDocument();
    expect(within(detail).getByRole("table")).toHaveTextContent("billing");
  });

  it("works out the change from the previous version when none was saved", async () => {
    const user = userEvent.setup();
    state.diffs["e2:"] = { summary: "", diff: null };
    state.diffs["e2:e1"] = ENV_DIFF;
    render(<VersionsPanel env={ENV} />);
    await user.click(row("environment", "v2"));

    const detail = screen.getByTestId("version-detail");
    expect(lastAsked()).toMatchObject({ id: "e2", against: "e1" });
    expect(within(detail).getByText("issue_refund")).toBeInTheDocument();
    expect(
      within(detail).queryByText("No change record for this version."),
    ).toBeNull();
    expect(
      screen.getByRole("combobox", { name: "Compared with" }),
    ).toHaveTextContent("Its previous version (v1)");
  });

  it("compares against another version of the same piece", async () => {
    const user = userEvent.setup();
    state.versions = {
      ...VERSIONS,
      environment: [
        item({ id: "ed", label: null, status: "draft", parent_id: "e2" }),
        ...VERSIONS.environment,
        item({ id: "e0", label: "v0" }),
      ],
    };
    state.diffs["e2:e0"] = {
      summary: "No contract, sub-goal or world change",
      diff: {
        contract: { tools: {}, rules: {}, use_cases: {}, fields: [] },
        sub_goals: {},
        files: {},
      },
    };
    render(<VersionsPanel env={ENV} />);
    await user.click(row("environment", "v2"));

    await user.click(screen.getByRole("combobox", { name: "Compared with" }));
    // The previous version is named once, and a draft says it's unsaved.
    expect(
      screen.getAllByRole("option").map((option) => option.textContent),
    ).toEqual(["Its previous version (v1)", "Unsaved draft", "v0"]);
    await user.click(screen.getByRole("option", { name: "v0" }));

    expect(lastAsked()).toEqual({
      piece: "environment",
      id: "e2",
      against: "e0",
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

  it("lists the actors added and changed when only the cast changed", async () => {
    const user = userEvent.setup();
    state.diffs["e2:"] = {
      summary: "2 actors added, 1 actor changed",
      diff: {
        contract: { tools: {}, rules: {}, use_cases: {}, fields: [] },
        sub_goals: {},
        files: {},
        actors: { added: ["someone_1", "colleague_2"], changed: ["colleague"] },
      },
    };
    render(<VersionsPanel env={ENV} />);
    await user.click(row("environment", "v2"));

    expect(screen.queryByText("Nothing changed.")).toBeNull();
    expect(screen.getByTestId("diff-group-Actors")).toHaveTextContent(
      "someone_1, colleague_2",
    );
    expect(screen.getByTestId("diff-group-Actors")).toHaveTextContent(
      "colleague",
    );
  });

  it("names each actor the server sends by id and name, never as an object", async () => {
    const user = userEvent.setup();
    state.diffs["e2:"] = {
      summary: "1 actor added, 1 actor changed",
      diff: {
        contract: { tools: {}, rules: {}, use_cases: {}, fields: [] },
        sub_goals: {},
        files: {},
        actors: {
          added: [{ id: "lead", name: "Sam Rivera" }],
          removed: [],
          changed: [{ id: "manager", name: "" }],
        },
      },
    };
    render(<VersionsPanel env={ENV} />);
    await user.click(row("environment", "v2"));

    const group = screen.getByTestId("diff-group-Actors");
    expect(group).toHaveTextContent("Added: Sam Rivera");
    expect(group).toHaveTextContent("Changed: manager");
    expect(group).not.toHaveTextContent("[object Object]");
  });

  it("says the actor changes are unknown when a version's cast cannot be read, not that nothing changed", async () => {
    const user = userEvent.setup();
    state.diffs["e2:"] = {
      summary: "",
      diff: {
        contract: { tools: {}, rules: {}, use_cases: {}, fields: [] },
        sub_goals: {},
        files: {},
        actors: null,
      },
    };
    render(<VersionsPanel env={ENV} />);
    await user.click(row("environment", "v2"));

    expect(
      screen.getByText(/Actors of one of these versions aren't known/),
    ).toBeInTheDocument();
    expect(screen.queryByText("Nothing changed.")).toBeNull();
    expect(screen.queryByTestId("diff-group-Actors")).toBeNull();
  });

  it("keeps the last comparison readable while another one loads", async () => {
    const user = userEvent.setup();
    state.versions = {
      ...VERSIONS,
      environment: [...VERSIONS.environment, item({ id: "e0", label: "v0" })],
    };
    state.diffStale = true;
    render(<VersionsPanel env={ENV} />);
    await user.click(row("environment", "v2"));
    await user.click(screen.getByRole("combobox", { name: "Compared with" }));
    await user.click(screen.getByRole("option", { name: "v0" }));

    const detail = screen.getByTestId("version-detail");
    // A thin line says it's updating; the old answer stays, not faded out.
    expect(
      within(detail).getByRole("progressbar", { name: "Loading the changes" }),
    ).toBeInTheDocument();
    expect(within(detail).getByText("issue_refund")).toBeVisible();
  });

  it("shows a skeleton while the changes load and an error if they fail", async () => {
    const user = userEvent.setup();
    state.diffLoading = true;
    const { unmount } = render(<VersionsPanel env={ENV} />);
    await user.click(row("environment", "v2"));
    const detail = screen.getByTestId("version-detail");
    // A skeleton shaped like the changes, not a spinner.
    expect(within(detail).getByRole("status")).toHaveTextContent(
      "Loading the changes",
    );
    expect(within(detail).queryByRole("progressbar")).toBeNull();
    unmount();

    state.diffLoading = false;
    state.diffs = { error: true };
    render(<VersionsPanel env={ENV} />);
    await user.click(row("environment", "v2"));
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

  it("opens one row at a time and marks it expanded", async () => {
    const user = userEvent.setup();
    render(<VersionsPanel env={ENV} />);
    expect(row("environment", "v2")).toHaveAttribute("aria-expanded", "false");
    await user.click(row("environment", "v2"));
    expect(row("environment", "v2")).toHaveAttribute("aria-expanded", "true");
    expect(row("environment", "v1")).toHaveAttribute("aria-expanded", "false");

    await user.click(row("environment", "v1"));
    expect(row("environment", "v1")).toHaveAttribute("aria-expanded", "true");
    expect(row("environment", "v2")).toHaveAttribute("aria-expanded", "false");
    expect(screen.getAllByTestId("version-detail")).toHaveLength(1);

    await user.click(row("environment", "v1"));
    expect(screen.queryByTestId("version-detail")).toBeNull();
  });

  it("expands one row by piece and id when two agent versions share a label", async () => {
    const user = userEvent.setup();
    state.versions = {
      ...VERSIONS,
      agent: [
        item({ id: "a3", label: "fix-live-test" }),
        item({ id: "a2", label: "fix-live-test" }),
        ...VERSIONS.agent,
      ],
    };
    render(<VersionsPanel env={ENV} />);
    showKind("agent");
    const a3 = screen.getByTestId("history-entry-agent:a3");
    await user.click(within(a3).getByRole("button", { expanded: false }));
    expect(within(a3).getByTestId("version-detail")).toBeInTheDocument();
    expect(
      within(screen.getByTestId("history-entry-agent:a2")).queryByTestId(
        "version-detail",
      ),
    ).toBeNull();
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
    expect(lastAsked()).toMatchObject({ id: "e2", enabled: true });
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
    state.versions = {
      ...VERSIONS,
      environment: [...VERSIONS.environment, item({ id: "e0", label: "v0" })],
    };
    state.diffs["e2:e0"] = ENV_DIFF;
    const { rerender } = render(<VersionsPanel env={ENV} />);
    await user.click(row("environment", "v2"));
    await user.click(screen.getByRole("combobox", { name: "Compared with" }));
    await user.click(screen.getByRole("option", { name: "v0" }));
    expect(lastAsked()).toMatchObject({ id: "e2", against: "e0" });

    state.versions = VERSIONS;
    rerender(<VersionsPanel env={ENV} />);

    expect(lastAsked()).toMatchObject({ id: "e2", against: undefined });
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
        "aria-expanded",
        "true",
      );
      expect(row("environment", "v2")).toHaveAttribute(
        "aria-expanded",
        "false",
      );
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
        "aria-expanded",
        "true",
      );
    } finally {
      window.history.pushState({}, "", "/");
    }
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
      screen.getByText("The 1 scenario in Unnumbered version"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/null/)).toBeNull();
  });

  it("shows a skeleton of the History tab while the versions load", () => {
    state.versions = undefined;
    render(<VersionsPanel env={ENV} />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading versions");
    expect(screen.queryByRole("progressbar")).toBeNull();
  });

  it("filters the timeline to scenario versions", async () => {
    const user = userEvent.setup();
    render(<VersionsPanel env={ENV} />);
    // Opens on environment versions.
    expect(screen.getByRole("button", { name: "Environment" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.queryAllByTestId(/^history-entry-suite:/)).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "Scenarios" }));
    expect(screen.queryAllByTestId(/^history-entry-agent:/)).toHaveLength(0);
    expect(screen.getAllByTestId(/^history-entry-suite:/)).toHaveLength(2);
    // One kind at a time: no "All", and the picked one can't be un-picked.
    expect(screen.queryByRole("button", { name: "All" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Scenarios" }));
    expect(screen.getAllByTestId(/^history-entry-suite:/)).toHaveLength(2);
  });

  it("says a filter is empty instead of showing a blank card", async () => {
    const user = userEvent.setup();
    state.versions = { ...VERSIONS, suite: [] };
    render(<VersionsPanel env={ENV} />);
    await user.click(screen.getByRole("button", { name: "Scenarios" }));
    expect(screen.getByText("No scenario versions yet.")).toBeInTheDocument();
  });

  it("scrolls to the row a link names", () => {
    const scrolled = vi.fn();
    Element.prototype.scrollIntoView = scrolled;
    window.history.pushState({}, "", "/?tab=versions&version=suite:s1");
    try {
      render(<VersionsPanel env={ENV} />);
      expect(
        within(screen.getByTestId("history-entry-suite:s1")).getByTestId(
          "version-detail",
        ),
      ).toBeInTheDocument();
      expect(scrolled).toHaveBeenCalled();
    } finally {
      window.history.pushState({}, "", "/");
      delete Element.prototype.scrollIntoView;
    }
  });

  it("shows the kind a link names, and lets the reader pick another", async () => {
    const user = userEvent.setup();
    window.history.pushState({}, "", "/?tab=versions&kind=agent");
    try {
      render(<VersionsPanel env={ENV} />);
      const agent = screen.getByRole("button", { name: "Agent" });
      expect(agent).toHaveAttribute("aria-pressed", "true");
      // Used once, so the reader's own pick isn't undone.
      expect(new URLSearchParams(window.location.search).has("kind")).toBe(
        false,
      );
      await user.click(screen.getByRole("button", { name: "Scenarios" }));
      expect(agent).toHaveAttribute("aria-pressed", "false");
    } finally {
      window.history.pushState({}, "", "/");
    }
  });

  it("lets a bar link's kind win over an older version link still in the URL", () => {
    // Left over from a View changes link, then the bar's Agent was clicked.
    window.history.pushState(
      {},
      "",
      "/?tab=versions&version=suite:s1&kind=agent",
    );
    try {
      render(<VersionsPanel env={ENV} />);
      expect(screen.getByRole("button", { name: "Agent" })).toHaveAttribute(
        "aria-pressed",
        "true",
      );
      expect(screen.queryByTestId("version-detail")).toBeNull();
      expect(new URLSearchParams(window.location.search).has("version")).toBe(
        false,
      );
    } finally {
      window.history.pushState({}, "", "/");
    }
  });

  it("ignores a link to a version that is no longer listed", () => {
    window.history.pushState({}, "", "/?tab=versions&version=suite:gone");
    try {
      render(<VersionsPanel env={ENV} />);
      expect(screen.queryByTestId("version-detail")).toBeNull();
      expect(row("suite", "v2")).toBeInTheDocument();
    } finally {
      window.history.pushState({}, "", "/");
    }
  });

  it("opens a linked row even when the filter hides its kind", async () => {
    const user = userEvent.setup();
    const Link = () => {
      const { setTab } = useWorkspaceTab();
      return (
        <button
          type="button"
          onClick={() => setTab("versions", { version: "suite:s1" })}
        >
          View changes
        </button>
      );
    };
    render(
      <>
        <Link />
        <VersionsPanel env={ENV} />
      </>,
    );
    await user.click(screen.getByRole("button", { name: "Agent" }));
    await user.click(screen.getByRole("button", { name: "View changes" }));
    expect(
      within(screen.getByTestId("history-entry-suite:s1")).getByTestId(
        "version-detail",
      ),
    ).toBeInTheDocument();
  });

  it("ignores a hand-edited link that names no piece", () => {
    window.history.pushState({}, "", "/?tab=versions&version=defaults:x");
    try {
      render(<VersionsPanel env={ENV} />);
      expect(screen.queryByTestId("version-detail")).toBeNull();
      expect(row("suite", "v2")).toBeInTheDocument();
    } finally {
      window.history.pushState({}, "", "/");
    }
  });

  it("says when a newer environment version exists than the one runs use", () => {
    state.versions = {
      ...VERSIONS,
      environment: [
        { ...VERSIONS.environment[0], is_default: false },
        { ...VERSIONS.environment[1], is_default: true },
      ],
    };
    render(<VersionsPanel env={ENV} />);
    expect(screen.getByTestId("history-tile-environment")).toHaveTextContent(
      "v1runs use this · a newer one exists",
    );
  });

  it("states what runs use now in the strip, with no side panel", () => {
    render(<VersionsPanel env={{ ...ENV, counts: { evaluations: 5 } }} />);
    expect(screen.getByTestId("history-tile-environment")).toHaveTextContent(
      "v2latest · runs use this",
    );
    expect(screen.getByTestId("history-tile-agent")).toHaveTextContent("v4");
    expect(screen.getByTestId("history-tile-scenarios")).toHaveTextContent(
      "v22 scenarios in this version",
    );
    expect(screen.getByTestId("history-tile-evaluations")).toHaveTextContent(
      "5score each run",
    );
    expect(screen.queryByText("What runs use now")).toBeNull();
  });

  describe("making a version the default", () => {
    // Each row's actions menu says Roll back for an environment or scenario
    // version, Make default for an agent version.
    const makeDefault = async (user, piece, label, name = "Make default") => {
      const menu = await openActions(user, piece, label);
      await user.click(within(menu).getByRole("menuitem", { name }));
    };
    const rollBack = (user, piece, label) =>
      makeDefault(
        user,
        piece,
        label,
        `Roll back to ${piece === "suite" ? "scenarios" : piece} ${label}`,
      );
    const noMakeDefault = async (user, piece, label) => {
      const menu = await openActions(user, piece, label);
      expect(
        within(menu).queryByRole("menuitem", {
          name: /^(Make default|Roll back)/,
        }),
      ).toBeNull();
      await user.keyboard("{Escape}");
    };
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
      await noMakeDefault(user, "environment", "v2");

      await rollBack(user, "environment", "v1");
      const dialog = confirmDialog("Roll back to environment v1?");
      expect(dialog).toHaveTextContent(
        "New runs use it. If this combination hasn't run before, the next run prepares it first.",
      );
      await user.click(
        within(dialog).getByRole("button", { name: "Roll back to env v1" }),
      );

      expect(move.calls).toEqual([
        { piece: "environment", versionId: "e1", expectedDefaultId: "e2" },
      ]);
      await waitFor(() =>
        expect(confirmDialog("Roll back to environment v1?")).toBeNull(),
      );
      // The versions query now answers with the moved default.
      rerender(<VersionsPanel env={ENV} />);
      expect(row("environment", "v1")).toHaveTextContent("runs use this");
      expect(row("environment", "v2")).not.toHaveTextContent("runs use this");
      await noMakeDefault(user, "environment", "v1");
      // Any other version reads as a roll back, newer ones too, as in the design.
      const menu = await openActions(user, "environment", "v2");
      expect(
        within(menu).getByRole("menuitem", {
          name: "Roll back to environment v2",
        }),
      ).toBeInTheDocument();
    });

    it("checks against the default seen when the roll back was opened", async () => {
      const user = userEvent.setup();
      const { rerender } = render(<VersionsPanel env={ENV} />);
      await rollBack(user, "environment", "v1");
      const dialog = confirmDialog("Roll back to environment v1?");

      // Someone else moves the default while the dialog is open.
      state.versions = {
        ...VERSIONS,
        environment: [
          item({ id: "e3", label: "v3", is_default: true }),
          { ...VERSIONS.environment[0], is_default: false },
          VERSIONS.environment[1],
        ],
      };
      rerender(<VersionsPanel env={ENV} />);
      await user.click(
        within(dialog).getByRole("button", { name: "Roll back to env v1" }),
      );

      // The server can then refuse it instead of moving a default the reader
      // never saw.
      expect(move.calls).toEqual([
        { piece: "environment", versionId: "e1", expectedDefaultId: "e2" },
      ]);
    });

    it("makes another scenario version the default", async () => {
      const user = userEvent.setup();
      state.versions = {
        ...VERSIONS,
        suite: [...VERSIONS.suite, item({ id: "s0", label: "v0" })],
      };
      render(<VersionsPanel env={ENV} />);

      await rollBack(user, "suite", "v0");
      const dialog = confirmDialog("Roll back to scenarios v0?");
      await user.click(
        within(dialog).getByRole("button", {
          name: "Roll back to scenarios v0",
        }),
      );

      expect(move.calls).toEqual([
        { piece: "suite", versionId: "s0", expectedDefaultId: "s2" },
      ]);
    });

    it("shows a Viewer the roll back but doesn't let them use it", async () => {
      const user = userEvent.setup();
      useSimulationAccess.mockReturnValue({ canEdit: false, canRun: true });
      try {
        render(<VersionsPanel env={ENV} />);
        const menu = await openActions(user, "environment", "v1");
        expect(
          within(menu).getByRole("menuitem", {
            name: "Roll back to environment v1",
          }),
        ).toHaveAttribute("aria-disabled", "true");
      } finally {
        useSimulationAccess.mockReturnValue({ canEdit: true, canRun: true });
      }
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

      await noMakeDefault(user, "suite", "v1");
      await noMakeDefault(user, "agent", "v5");
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

      await makeDefault(user, "agent", "v3");
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
      await rollBack(user, "environment", "v1");
      const dialog = confirmDialog("Roll back to environment v1?");
      await user.click(
        within(dialog).getByRole("button", { name: "Roll back to env v1" }),
      );

      expect(
        within(dialog).getByRole("button", { name: "Rolling back…" }),
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
      await rollBack(user, "environment", "v1");
      const dialog = confirmDialog("Roll back to environment v1?");
      await user.click(
        within(dialog).getByRole("button", { name: "Roll back to env v1" }),
      );
      await waitFor(() =>
        expect(confirmDialog("Roll back to environment v1?")).toBeNull(),
      );

      rerender(<VersionsPanel env={ENV} />);
      // Shown in the row, which stays closed.
      expect(
        within(entryOf("environment", "v1")).getByRole("alert"),
      ).toHaveTextContent(message);
      expect(screen.queryByTestId("version-detail")).toBeNull();
      await noMakeDefault(user, "environment", "v1");

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
      await rollBack(user, "environment", "v1");
      const dialog = confirmDialog("Roll back to environment v1?");
      await user.click(
        within(dialog).getByRole("button", { name: "Roll back to env v1" }),
      );

      await waitFor(() =>
        expect(confirmDialog("Roll back to environment v1?")).toBeNull(),
      );
      expect(
        within(entryOf("environment", "v1")).getByRole("alert"),
      ).toHaveTextContent(text);
      expect(screen.queryByTestId("version-detail")).toBeNull();
    });
  });
});
