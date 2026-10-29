import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

// Each hook is stubbed; tests set what the server answered.
const state = { versions: undefined, plans: {}, members: {}, drafts: null };
const planCalls = [];
vi.mock("src/api/simulate-environments/versions", async () => {
  const actual = await vi.importActual(
    "src/api/simulate-environments/versions",
  );
  return {
    ...actual,
    useEnvironmentVersions: vi.fn((envId, { enabled } = {}) => ({
      data:
        enabled && state.versions
          ? actual.toVersions(state.versions)
          : undefined,
      isLoading: enabled && !state.versions,
      isError: false,
    })),
    // A plan is its data, `{ loading: true }` or `{ error }`.
    useRunPlan: vi.fn((envId, picks, { enabled } = {}) => {
      planCalls.push({ picks, enabled });
      const plan = state.plans[JSON.stringify(picks)] || { warnings: [] };
      if (!enabled) return { isLoading: false, isError: false };
      if (plan.loading) return { isLoading: true, isError: false };
      if (plan.error) {
        return { isLoading: false, isError: true, error: plan.error };
      }
      return { data: plan, isLoading: false, isError: false };
    }),
    useDrafts: vi.fn((envId, { enabled } = {}) => ({
      data: enabled && state.drafts ? actual.toDrafts(state.drafts) : undefined,
    })),
    useSuiteMembers: vi.fn((envId, id) => ({
      data: state.members[id],
      isLoading: !state.members[id],
      isError: false,
    })),
  };
});

const { default: RunDialog } = await import("../RunDialog");

const ENV = { id: "env-1", versioning: { enabled: true } };
const item = (overrides) => ({
  status: "active",
  is_default: false,
  ...overrides,
});
const VERSIONS = {
  agent: [
    item({ id: "a5", label: "v5", status: "draft" }),
    item({ id: "a4", label: "v4", is_default: true, pickable: true }),
    item({ id: "a3", label: "v3", pickable: true }),
    item({ id: "a2", label: "v2", status: "discarded" }),
    // Older than versioning: listed as active, but the server won't run it.
    item({ id: "a1", label: "v1", preflight_status: "legacy" }),
  ],
  environment: [
    item({ id: "e2", label: "v2", is_default: true }),
    item({ id: "e1", label: "v1" }),
    item({ id: "e0", label: "v0", status: "discarded" }),
  ],
  suite: [
    item({ id: "s3", label: "v3", is_default: true }),
    item({ id: "s2", label: "v2" }),
  ],
};
const members = (...keys) => keys.map((key) => ({ scenario_key: key }));
// Every version the run resolves to, defaults included.
const DEFAULTS = { agent: "a4", environment: "e2", suite: "s3" };
const resolved = (picks = {}) => ({ ...DEFAULTS, ...picks });
const refusal = (error, message, extra = {}) => ({
  statusCode: 422,
  error,
  message,
  ...extra,
});

const renderDialog = (props = {}) => {
  const onSubmit = vi.fn();
  render(
    <RunDialog
      open
      env={ENV}
      scenarioIds={["k1", "k2"]}
      trials={1}
      onClose={vi.fn()}
      onSubmit={onSubmit}
      {...props}
    />,
  );
  return { onSubmit };
};

const pickOption = async (user, picker, option) => {
  await user.click(screen.getByRole("combobox", { name: picker }));
  await user.click(screen.getByRole("option", { name: option }));
};
const runButton = () => screen.getByRole("button", { name: /^Run/ });
// The field a picker sits in, helper text included.
const field = (name) =>
  screen.getByRole("combobox", { name }).closest(".MuiFormControl-root");

describe("RunDialog", () => {
  beforeEach(() => {
    state.versions = VERSIONS;
    state.plans = {};
    state.members = { s3: members("k1", "k2", "k3"), s2: members("k1", "k9") };
    planCalls.length = 0;
    state.drafts = null;
  });

  it("pre-selects the defaults and offers only active versions", async () => {
    const user = userEvent.setup();
    renderDialog();

    expect(
      screen.getByRole("combobox", { name: "Environment version" }),
    ).toHaveTextContent("v2 (default)");
    expect(
      screen.getByRole("combobox", { name: "Scenario version" }),
    ).toHaveTextContent("v3 (default)");

    await user.click(
      screen.getByRole("combobox", { name: "Environment version" }),
    );
    const options = within(screen.getByRole("listbox")).getAllByRole("option");
    expect(options.map((one) => one.textContent)).toEqual([
      "v2 (default)",
      "v1",
    ]);
  });

  it("offers the agent versions that can run, the default pre-selected", async () => {
    const user = userEvent.setup();
    renderDialog();
    const agent = screen.getByRole("combobox", { name: "Agent version" });
    expect(agent).toHaveTextContent("v4 (default)");

    await user.click(agent);
    const options = within(screen.getByRole("listbox")).getAllByRole("option");
    // A version still being checked (draft), discarded or older than
    // versioning can't run.
    expect(options.map((one) => one.textContent)).toEqual([
      "v4 (default)",
      "v3",
    ]);
  });

  it("sends the agent version only when the user changed it", async () => {
    const user = userEvent.setup();
    const { onSubmit } = renderDialog();

    await pickOption(user, "Agent version", "v3");
    expect(planCalls.at(-1).picks).toEqual({ agent: "a3" });
    await user.click(screen.getByRole("button", { name: "Run" }));
    expect(onSubmit).toHaveBeenLastCalledWith(
      { agent: "a3" },
      ["k1", "k2"],
      resolved({ agent: "a3" }),
    );

    await pickOption(user, "Agent version", "v4 (default)");
    await user.click(screen.getByRole("button", { name: "Run" }));
    expect(onSubmit).toHaveBeenLastCalledWith({}, ["k1", "k2"], DEFAULTS);
  });

  it("pre-selects a re-run's agent version", () => {
    const { onSubmit } = renderDialog({ initialPicks: { agent: "a3" } });
    expect(
      screen.getByRole("combobox", { name: "Agent version" }),
    ).toHaveTextContent("v3");
    expect(planCalls.at(-1).picks).toEqual({ agent: "a3" });
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("sends nothing when the defaults are kept", async () => {
    const user = userEvent.setup();
    const { onSubmit } = renderDialog();
    expect(planCalls.at(-1)).toEqual({ picks: {}, enabled: true });

    await user.click(screen.getByRole("button", { name: "Run" }));
    expect(onSubmit).toHaveBeenCalledWith({}, ["k1", "k2"], DEFAULTS);
  });

  it("sends only the versions the user changed", async () => {
    const user = userEvent.setup();
    const { onSubmit } = renderDialog();

    await pickOption(user, "Environment version", "v1");
    expect(planCalls.at(-1).picks).toEqual({ environment: "e1" });
    await user.click(screen.getByRole("button", { name: "Run" }));
    expect(onSubmit).toHaveBeenLastCalledWith(
      { environment: "e1" },
      ["k1", "k2"],
      resolved({ environment: "e1" }),
    );

    await pickOption(user, "Scenario version", "v2");
    await pickOption(user, "Environment version", "v2 (default)");
    await user.click(screen.getByRole("button", { name: "Run" }));
    expect(onSubmit).toHaveBeenLastCalledWith(
      { suite: "s2" },
      ["k1"],
      resolved({ suite: "s2" }),
    );
  });

  it("shows the warnings and offers Run anyway", async () => {
    const user = userEvent.setup();
    state.plans[JSON.stringify({ environment: "e1" })] = {
      warnings: [
        {
          code: "suite_unproved",
          message: "Suite v3 hasn't been proved against environment v1",
          piece: "suite",
        },
        {
          code: "bundle_cache_miss",
          message: "The first run of this combination prepares it",
          piece: null,
        },
      ],
    };
    const { onSubmit } = renderDialog();
    expect(screen.getByRole("button", { name: "Run" })).toBeInTheDocument();
    expect(screen.queryByTestId("run-warning")).toBeNull();

    await pickOption(user, "Environment version", "v1");
    expect(screen.getAllByTestId("run-warning")).toHaveLength(2);
    expect(
      screen.getByText("Suite v3 hasn't been proved against environment v1"),
    ).toBeInTheDocument();

    // Warnings never block.
    await user.click(screen.getByRole("button", { name: "Run anyway" }));
    expect(onSubmit).toHaveBeenCalledWith(
      { environment: "e1" },
      ["k1", "k2"],
      resolved({ environment: "e1" }),
    );
  });

  it("keeps Run off while the versions are being checked", () => {
    state.plans[JSON.stringify({})] = { loading: true };
    renderDialog();
    const region = screen.getByTestId("run-plan");
    expect(region).toHaveAttribute("aria-live", "polite");
    expect(
      within(region).getByText("Checking these versions…"),
    ).toBeInTheDocument();
    expect(runButton()).toBeDisabled();
  });

  it("explains a refused plan inline and keeps Run off", async () => {
    const user = userEvent.setup();
    state.plans[JSON.stringify({ environment: "e1" })] = {
      error: refusal(
        "version_not_runnable",
        "These versions haven't been run together yet",
      ),
    };
    renderDialog();

    await pickOption(user, "Environment version", "v1");
    expect(screen.getByTestId("run-error")).toHaveTextContent(
      "These versions haven't been run together yet",
    );
    expect(
      screen.queryByText("Couldn't check these versions for warnings."),
    ).toBeNull();
    expect(runButton()).toBeDisabled();

    // Back at the defaults the plan resolves again.
    await pickOption(user, "Environment version", "v2 (default)");
    expect(screen.queryByTestId("run-error")).toBeNull();
    expect(runButton()).toBeEnabled();
  });

  it("explains a conflicting plan inline", async () => {
    const user = userEvent.setup();
    state.plans[JSON.stringify({ suite: "s2" })] = {
      error: { statusCode: 409, message: "This suite version is changing" },
    };
    renderDialog();

    await pickOption(user, "Scenario version", "v2");
    expect(screen.getByTestId("run-error")).toHaveTextContent(
      "This suite version is changing",
    );
    expect(runButton()).toBeDisabled();
  });

  it("shows a refused pick under the picker the refusal names", async () => {
    const user = userEvent.setup();
    const message = "This environment version can't be picked";
    state.plans[JSON.stringify({ environment: "e1" })] = {
      error: refusal("version_not_pickable", message, {
        piece: "environment",
      }),
    };
    renderDialog();

    await pickOption(user, "Environment version", "v1");
    expect(
      within(field("Environment version")).getByText(message),
    ).toBeInTheDocument();
    expect(within(field("Scenario version")).queryByText(message)).toBeNull();
    expect(screen.queryByTestId("run-error")).toBeNull();
    expect(runButton()).toBeDisabled();
  });

  it("leaves a plan that failed on the server to a note and lets the run start", async () => {
    const user = userEvent.setup();
    state.plans[JSON.stringify({ environment: "e1" })] = {
      error: { statusCode: 503, message: "busy" },
    };
    const { onSubmit } = renderDialog();

    await pickOption(user, "Environment version", "v1");
    expect(
      screen.getByText("Couldn't check these versions for warnings."),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("run-error")).toBeNull();
    await user.click(runButton());
    expect(onSubmit).toHaveBeenCalled();
  });

  it("pre-selects a re-run's versions and counts only those off the default", async () => {
    const user = userEvent.setup();
    const { onSubmit } = renderDialog({
      initialPicks: { environment: "e1", suite: "s3" },
    });

    expect(
      screen.getByRole("combobox", { name: "Environment version" }),
    ).toHaveTextContent("v1");
    await user.click(screen.getByRole("button", { name: "Run" }));
    expect(onSubmit).toHaveBeenCalledWith(
      { environment: "e1" },
      ["k1", "k2"],
      resolved({ environment: "e1" }),
    );
    expect(screen.queryByTestId("rerun-fallback")).toBeNull();
  });

  it("keeps a re-run's versions when their plan failed on the server", () => {
    const rerunPicks = { environment: "e1", suite: "s2" };
    const key = JSON.stringify(rerunPicks);
    state.plans[key] = { error: { statusCode: 503, message: "busy" } };
    const props = {
      open: true,
      env: ENV,
      scenarioIds: ["k1"],
      initialPicks: rerunPicks,
      onClose: vi.fn(),
      onSubmit: vi.fn(),
    };
    const { rerender } = render(<RunDialog {...props} />);

    const environment = () =>
      screen.getByRole("combobox", { name: "Environment version" });
    expect(environment()).toHaveTextContent("v1");
    expect(screen.queryByTestId("rerun-fallback")).toBeNull();
    expect(runButton()).toBeEnabled();

    // No longer checking: a later refusal is explained, not swapped for the
    // defaults.
    state.plans[key] = {
      error: refusal("version_not_runnable", "Not run together yet"),
    };
    rerender(<RunDialog {...props} />);
    expect(screen.getByTestId("run-error")).toHaveTextContent(
      "Not run together yet",
    );
    expect(environment()).toHaveTextContent("v1");
    expect(screen.queryByTestId("rerun-fallback")).toBeNull();
  });

  it("falls back to the defaults for a re-run version no longer active", () => {
    renderDialog({ initialPicks: { environment: "e0", suite: "s2" } });
    expect(
      screen.getByRole("combobox", { name: "Environment version" }),
    ).toHaveTextContent("v2 (default)");
    expect(
      screen.getByRole("combobox", { name: "Scenario version" }),
    ).toHaveTextContent("v3 (default)");
    expect(planCalls.at(-1).picks).toEqual({});
    expect(screen.getByTestId("rerun-fallback")).toHaveTextContent(
      "That run's versions can't run now; using the current defaults.",
    );
  });

  it("falls back to the default agent only for a re-run agent version no longer active", async () => {
    const user = userEvent.setup();
    const { onSubmit } = renderDialog({
      initialPicks: { agent: "a2", environment: "e1", suite: "s2" },
    });
    expect(
      screen.getByRole("combobox", { name: "Agent version" }),
    ).toHaveTextContent("v4 (default)");
    // The run's environment and scenario versions are still valid: kept.
    expect(
      screen.getByRole("combobox", { name: "Environment version" }),
    ).toHaveTextContent("v1");
    expect(
      screen.getByRole("combobox", { name: "Scenario version" }),
    ).toHaveTextContent("v2");
    expect(planCalls.at(-1).picks).toEqual({ environment: "e1", suite: "s2" });
    expect(screen.getByTestId("rerun-fallback")).toHaveTextContent(
      "That run's agent version can't run now; using the current default agent.",
    );
    await user.click(runButton());
    const kept = { environment: "e1", suite: "s2" };
    expect(onSubmit).toHaveBeenCalledWith(kept, ["k1"], resolved(kept));
  });

  it("keeps a pre-change run's other versions when its agent can't be picked", async () => {
    const user = userEvent.setup();
    const { onSubmit } = renderDialog({
      initialPicks: { agent: "a1", environment: "e1", suite: "s2" },
    });
    expect(
      screen.getByRole("combobox", { name: "Agent version" }),
    ).toHaveTextContent("v4 (default)");
    expect(
      screen.getByRole("combobox", { name: "Environment version" }),
    ).toHaveTextContent("v1");
    expect(screen.getByTestId("rerun-fallback")).toHaveTextContent(
      "That run's agent version can't run now; using the current default agent.",
    );
    await user.click(runButton());
    const kept = { environment: "e1", suite: "s2" };
    expect(onSubmit).toHaveBeenCalledWith(kept, ["k1"], resolved(kept));
  });

  it("drops only the agent when the server refuses a re-run's agent pick", async () => {
    const user = userEvent.setup();
    const rerunPicks = { agent: "a3", environment: "e1", suite: "s2" };
    state.plans[JSON.stringify(rerunPicks)] = {
      error: refusal(
        "version_not_pickable",
        "This older agent version can't be run on its own",
        { piece: "agent" },
      ),
    };
    const { onSubmit } = renderDialog({ initialPicks: rerunPicks });

    expect(
      screen.getByRole("combobox", { name: "Agent version" }),
    ).toHaveTextContent("v4 (default)");
    expect(
      screen.getByRole("combobox", { name: "Scenario version" }),
    ).toHaveTextContent("v2");
    expect(planCalls.at(-1).picks).toEqual({ environment: "e1", suite: "s2" });
    expect(screen.getByTestId("rerun-fallback")).toHaveTextContent(
      "That run's agent version can't run now; using the current default agent.",
    );
    expect(screen.queryByTestId("run-error")).toBeNull();
    await user.click(runButton());
    const kept = { environment: "e1", suite: "s2" };
    expect(onSubmit).toHaveBeenCalledWith(kept, ["k1"], resolved(kept));
  });

  it("falls back to every default when the kept versions are refused too", async () => {
    const user = userEvent.setup();
    state.plans[JSON.stringify({ environment: "e1", suite: "s2" })] = {
      error: refusal("version_not_runnable", "Not run together yet"),
    };
    const { onSubmit } = renderDialog({
      initialPicks: { agent: "a2", environment: "e1", suite: "s2" },
    });
    expect(
      screen.getByRole("combobox", { name: "Environment version" }),
    ).toHaveTextContent("v2 (default)");
    expect(screen.getByTestId("rerun-fallback")).toHaveTextContent(
      "That run's versions can't run now; using the current defaults.",
    );
    expect(screen.queryByTestId("run-error")).toBeNull();
    await user.click(runButton());
    expect(onSubmit).toHaveBeenCalledWith({}, ["k1", "k2"], DEFAULTS);
  });

  it("falls back to the defaults when a re-run's versions are refused", async () => {
    const user = userEvent.setup();
    state.plans[JSON.stringify({ environment: "e1", suite: "s2" })] = {
      error: refusal("version_not_runnable", "Not run together yet"),
    };
    const { onSubmit } = renderDialog({
      initialPicks: { environment: "e1", suite: "s2" },
    });

    expect(
      screen.getByRole("combobox", { name: "Environment version" }),
    ).toHaveTextContent("v2 (default)");
    expect(screen.getByTestId("rerun-fallback")).toBeInTheDocument();
    expect(screen.queryByTestId("run-error")).toBeNull();
    await user.click(runButton());
    expect(onSubmit).toHaveBeenCalledWith({}, ["k1", "k2"], DEFAULTS);

    // Picking again drops the note.
    await pickOption(user, "Environment version", "v1");
    expect(screen.queryByTestId("rerun-fallback")).toBeNull();
  });

  it("explains on first open that the defaults can't run and keeps Run off", () => {
    // An unsealed default pair while building is off.
    state.plans[JSON.stringify({})] = {
      error: refusal(
        "version_not_runnable",
        "These versions haven't been run together yet",
      ),
    };
    renderDialog();
    expect(planCalls.at(-1)).toEqual({ picks: {}, enabled: true });
    expect(screen.getByTestId("run-error")).toHaveTextContent(
      "These versions haven't been run together yet",
    );
    expect(
      screen.queryByText("Couldn't check these versions for warnings."),
    ).toBeNull();
    expect(runButton()).toBeDisabled();
  });

  it("shows a refusal of the defaults for a re-run that pinned nothing", () => {
    state.plans[JSON.stringify({})] = {
      error: refusal("version_not_runnable", "Not run together yet"),
    };
    renderDialog({
      initialPicks: { environment: undefined, suite: undefined },
    });
    expect(screen.getByTestId("run-error")).toHaveTextContent(
      "Not run together yet",
    );
    expect(screen.queryByTestId("rerun-fallback")).toBeNull();
    expect(runButton()).toBeDisabled();
  });

  it("shows a refusal that carries only a detail", async () => {
    const user = userEvent.setup();
    state.plans[JSON.stringify({ suite: "s2" })] = {
      error: { statusCode: 409, detail: "Environment is busy" },
    };
    renderDialog();
    await pickOption(user, "Scenario version", "v2");
    expect(screen.getByTestId("run-error")).toHaveTextContent(
      "Environment is busy",
    );
  });

  it("runs all of another suite's scenarios when that suite is picked", async () => {
    const user = userEvent.setup();
    const { onSubmit } = renderDialog({ scenarioIds: undefined });
    expect(screen.getByText("Runs all 3 scenarios in v3.")).toBeInTheDocument();

    await pickOption(user, "Scenario version", "v2");
    expect(screen.getByText("Runs all 2 scenarios in v2.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Run" }));
    expect(onSubmit).toHaveBeenCalledWith(
      { suite: "s2" },
      ["k1", "k9"],
      resolved({ suite: "s2" }),
    );
  });

  it("keeps run-all on the server's list for the default suite with versioning off", async () => {
    const user = userEvent.setup();
    const { onSubmit } = renderDialog({
      scenarioIds: undefined,
      env: { id: "env-1" },
    });
    // The default suite runs the server's current list, so no count is promised.
    expect(screen.getByText("Runs all scenarios in v3.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Run" }));
    expect(onSubmit).toHaveBeenCalledWith({}, undefined, DEFAULTS);
  });

  it("runs the default suite's own scenarios with versioning on, even before the drafts are known", async () => {
    const user = userEvent.setup();
    const { onSubmit } = renderDialog({ scenarioIds: undefined });
    expect(screen.getByText("Runs all 3 scenarios in v3.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Run" }));
    expect(onSubmit).toHaveBeenCalledWith({}, ["k1", "k2", "k3"], DEFAULTS);
  });

  it("says unsaved changes aren't part of the run", () => {
    state.drafts = { environment: { id: "ed" }, suite: null, set_aside: [] };
    renderDialog();
    expect(screen.getByTestId("run-drafts-note")).toHaveTextContent(
      "Unsaved changes aren't part of this run. Save them first to include them.",
    );
  });

  it("says nothing about unsaved changes when there are none", () => {
    state.drafts = { environment: null, suite: null, set_aside: [] };
    renderDialog();
    expect(screen.queryByTestId("run-drafts-note")).toBeNull();
  });

  it("says why Run is off when the picked suite has no scenarios", async () => {
    const user = userEvent.setup();
    state.members.s2 = [];
    renderDialog({ scenarioIds: undefined });
    await pickOption(user, "Scenario version", "v2");
    expect(screen.getByTestId("run-blocked")).toHaveTextContent(
      "v2 has no scenarios to run.",
    );
    expect(runButton()).toBeDisabled();
  });

  it("waits for another suite's scenarios before running all of them", async () => {
    const user = userEvent.setup();
    delete state.members.s2;
    renderDialog({ scenarioIds: undefined });
    await pickOption(user, "Scenario version", "v2");
    expect(screen.getByRole("button", { name: "Run" })).toBeDisabled();
  });

  it("warns when selected scenarios are not in the picked suite", async () => {
    const user = userEvent.setup();
    renderDialog();
    expect(screen.queryByText(/isn't in|aren't in/)).toBeNull();

    await pickOption(user, "Scenario version", "v2");
    expect(
      screen.getByText(
        "1 selected scenario isn't in v2 and will be skipped: k2",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("Runs 1 selected scenario.")).toBeInTheDocument();
  });

  it("keeps Run off when none of the selected scenarios are in the picked suite", async () => {
    const user = userEvent.setup();
    renderDialog({ scenarioIds: ["k2", "k3"] });
    await pickOption(user, "Scenario version", "v2");
    expect(screen.getByTestId("run-blocked")).toHaveTextContent(
      "None of the selected scenarios are in v2.",
    );
    expect(runButton()).toBeDisabled();
  });

  it("waits for the picked suite's scenarios before running a selection", () => {
    delete state.members.s3;
    renderDialog();
    expect(runButton()).toBeDisabled();
  });

  it("explains a refused run inline", () => {
    renderDialog({
      error: {
        statusCode: 422,
        error: "version_not_runnable",
        message: "These versions haven't been run together yet",
      },
    });
    expect(screen.getByTestId("run-error")).toHaveTextContent(
      "These versions haven't been run together yet",
    );
  });

  it("shows a version that can't be picked at the picker it names", async () => {
    const user = userEvent.setup();
    const error = refusal(
      "version_not_pickable",
      "This suite version can't be picked for this environment",
      { piece: "suite" },
    );
    renderDialog({ error });
    expect(screen.queryByTestId("run-error")).toBeNull();
    expect(
      within(field("Scenario version")).getByText(
        "This suite version can't be picked for this environment",
      ),
    ).toBeInTheDocument();
    expect(
      within(field("Environment version")).queryByText(/can't be picked/),
    ).toBeNull();

    // Changing a pick clears it.
    await pickOption(user, "Scenario version", "v2");
    expect(
      screen.queryByText(
        "This suite version can't be picked for this environment",
      ),
    ).toBeNull();
  });

  it("shows an agent version that can't be picked at the agent field", () => {
    renderDialog({
      error: refusal(
        "version_not_pickable",
        "Only the environment's current agent version can be picked for now",
        { piece: "agent" },
      ),
    });
    expect(
      within(field("Agent version")).getByText(
        "Only the environment's current agent version can be picked for now",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("run-error")).toBeNull();
  });

  it("explains a refusal naming no picker inline", () => {
    renderDialog({
      error: refusal(
        "version_not_pickable",
        "Versions can't be picked for this environment",
        { piece: null },
      ),
    });
    expect(screen.getByTestId("run-error")).toHaveTextContent(
      "Versions can't be picked for this environment",
    );
  });

  it("explains a reused request key inline", () => {
    renderDialog({
      error: {
        statusCode: 409,
        error: "idempotency_key_reused",
        message: "This request was already sent with other versions",
      },
    });
    expect(screen.getByTestId("run-error")).toHaveTextContent(
      "This request was already sent with other versions",
    );
  });

  it("leaves other failures to the snackbar", () => {
    renderDialog({ error: { statusCode: 500, message: "boom" } });
    expect(screen.queryByTestId("run-error")).toBeNull();
  });
});
