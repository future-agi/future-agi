import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

// Each hook is stubbed; tests set what the server answered.
const state = {
  versions: undefined,
  versionsError: undefined,
  plans: {},
  members: {},
  drafts: null,
};
const PLAN_OK = {
  warnings: [],
  blocked_tools: [],
  runnable: true,
  held_back: 0,
};
const planCalls = [];
const refetchPlan = vi.fn();
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
      isLoading: enabled && !state.versions && !state.versionsError,
      isError: Boolean(state.versionsError),
      error: state.versionsError,
    })),
    // A plan is its data, `{ loading: true }` or `{ error }`.
    useRunPlan: vi.fn((envId, picks, { enabled, scenarioKeys } = {}) => {
      planCalls.push({ picks, enabled, scenarioKeys });
      const plan = { ...PLAN_OK, ...state.plans[JSON.stringify(picks)] };
      if (!enabled) return { isLoading: false, isError: false };
      if (plan.loading) return { isLoading: true, isError: false };
      if (plan.error) {
        return {
          isLoading: false,
          isError: true,
          error: plan.error,
          refetch: refetchPlan,
        };
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
    state.versionsError = undefined;
  });

  it("asks the plan about the scenarios this run takes", () => {
    renderDialog();
    expect(planCalls.at(-1)).toMatchObject({
      enabled: true,
      scenarioKeys: ["k1", "k2"],
    });
  });

  it("says in one line that a runnable plan's blocked tool has its calls blocked, with its scenarios under Details", async () => {
    const user = userEvent.setup();
    state.plans["{}"] = {
      blocked_tools: [
        {
          tool: "issue_refund",
          scenarios: [{ key: "k1", name: "Refund a late ride" }],
        },
      ],
      held_back: 0,
    };
    renderDialog();

    const line = screen.getByTestId("run-blocked-tool");
    expect(line).toHaveTextContent(
      "issue_refund reaches production, so this test blocks its calls",
    );
    expect(line).not.toHaveTextContent("held back");
    expect(
      within(line).getByText(/Scenarios that use it: Refund a late ride\./),
    ).not.toBeVisible();
    await user.click(within(line).getByRole("button", { name: "Details" }));
    expect(
      within(line).getByText(
        "Blocked: issue_refund reaches production, so this test blocks its calls. Serve it from the agent's tools service or remove it. Scenarios that use it: Refund a late ride.",
      ),
    ).toBeVisible();
    expect(screen.queryByTestId("run-blocked")).toBeNull();
    expect(runButton()).toBeEnabled();
  });

  it("does not start a run the plan says cannot run, and says which tools block it", () => {
    state.plans["{}"] = {
      blocked_tools: [
        { tool: "issue_refund", scenarios: [{ key: "k1", name: "Refund" }] },
      ],
      held_back: 1,
      runnable: false,
      blocked_code: "production_only",
    };
    renderDialog({ scenarioIds: ["k1"] });

    expect(screen.getByTestId("run-blocked")).toHaveTextContent(
      "Every selected scenario needs issue_refund, which this test blocks. Serve it from the agent's tools service or remove it, then run again.",
    );
    expect(runButton()).toBeDisabled();
  });

  it("asks the plan for the blocked tools even with versioning off, and shows them", () => {
    state.versions = undefined;
    state.versionsError = {
      statusCode: 404,
      error: "versioning_disabled",
      message: "versioning is off",
    };
    state.plans["{}"] = {
      blocked_tools: [{ tool: "issue_refund", scenarios: [] }],
      held_back: 0,
    };
    renderDialog({ env: { ...ENV, versioning: { enabled: false } } });

    expect(planCalls.at(-1).enabled).toBe(true);
    expect(screen.getByTestId("run-blocked-tool")).toHaveTextContent(
      "issue_refund reaches production, so this test blocks its calls",
    );
  });

  it("shows no blocked line when the plan blocks nothing", () => {
    renderDialog();
    expect(screen.queryByTestId("run-blocked-tool")).toBeNull();
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
      null,
    );

    await pickOption(user, "Agent version", "v4 (default)");
    await user.click(screen.getByRole("button", { name: "Run" }));
    expect(onSubmit).toHaveBeenLastCalledWith({}, ["k1", "k2"], DEFAULTS, null);
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
    expect(planCalls.at(-1)).toMatchObject({ picks: {}, enabled: true });

    await user.click(screen.getByRole("button", { name: "Run" }));
    expect(onSubmit).toHaveBeenCalledWith({}, ["k1", "k2"], DEFAULTS, null);
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
      null,
    );

    await pickOption(user, "Scenario version", "v2");
    await pickOption(user, "Environment version", "v2 (default)");
    await user.click(screen.getByRole("button", { name: "Run" }));
    expect(onSubmit).toHaveBeenLastCalledWith(
      { suite: "s2" },
      ["k1"],
      resolved({ suite: "s2" }),
      null,
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
      null,
    );
  });

  it("keeps Run off while the versions are being checked", () => {
    state.plans[JSON.stringify({})] = { loading: true };
    renderDialog();
    const region = screen.getByTestId("run-plan");
    expect(region).toHaveAttribute("aria-live", "polite");
    expect(
      within(region).getByText(
        "Checking these versions and what the run would block…",
      ),
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
    expect(screen.queryByTestId("run-plan-failed")).toBeNull();
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

  it("leaves a plan that failed on the server to a note with Try again and lets the run start", async () => {
    const user = userEvent.setup();
    state.plans[JSON.stringify({ environment: "e1" })] = {
      error: { statusCode: 503, message: "busy" },
    };
    const { onSubmit } = renderDialog();

    await pickOption(user, "Environment version", "v1");
    expect(screen.getByTestId("run-plan-failed")).toHaveTextContent(
      "Couldn't check these versions and what the run would block. The run can still start; a blocked one is refused.",
    );
    expect(screen.queryByTestId("run-error")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(refetchPlan).toHaveBeenCalled();
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
      null,
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
    expect(onSubmit).toHaveBeenCalledWith(kept, ["k1"], resolved(kept), null);
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
    expect(onSubmit).toHaveBeenCalledWith(kept, ["k1"], resolved(kept), null);
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
    expect(onSubmit).toHaveBeenCalledWith(kept, ["k1"], resolved(kept), null);
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
    expect(onSubmit).toHaveBeenCalledWith({}, ["k1", "k2"], DEFAULTS, null);
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
    expect(onSubmit).toHaveBeenCalledWith({}, ["k1", "k2"], DEFAULTS, null);

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
    expect(planCalls.at(-1)).toMatchObject({ picks: {}, enabled: true });
    expect(screen.getByTestId("run-error")).toHaveTextContent(
      "These versions haven't been run together yet",
    );
    expect(screen.queryByTestId("run-plan-failed")).toBeNull();
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

  it("asks the plan about a whole-suite run without listing its scenarios", () => {
    renderDialog({ scenarioIds: undefined });
    expect(planCalls.at(-1).scenarioKeys).toBeNull();
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
      null,
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
    expect(onSubmit).toHaveBeenCalledWith({}, undefined, DEFAULTS, null);
  });

  it("runs the default suite's own scenarios with versioning on, even before the drafts are known", async () => {
    const user = userEvent.setup();
    const { onSubmit } = renderDialog({ scenarioIds: undefined });
    expect(screen.getByText("Runs all 3 scenarios in v3.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Run" }));
    expect(onSubmit).toHaveBeenCalledWith(
      {},
      ["k1", "k2", "k3"],
      DEFAULTS,
      null,
    );
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

  it.each([
    [
      "pair_validating",
      "These versions are being checked together. Run them once the check ends.",
    ],
    [
      "pair_failed",
      "These versions failed their check together. Fix them and check again.",
    ],
  ])(
    "explains a %s refusal inline in our words when the server sends none",
    (code, text) => {
      renderDialog({ error: { statusCode: 409, error: code } });
      expect(screen.getByTestId("run-error")).toHaveTextContent(text);
    },
  );

  it.each([
    [
      "pair_validating",
      "These versions are being checked together. Run them again when the check ends, at the latest in 45 minutes.",
    ],
    [
      "pair_failed",
      "These versions failed their check together: the agent did not pass this environment's checks. Fix them, or check again from Ready to run.",
    ],
  ])("shows the server's reason and wait for a %s refusal", (code, message) => {
    renderDialog({ error: { statusCode: 409, error: code, message } });
    expect(screen.getByTestId("run-error")).toHaveTextContent(message);
  });

  it("explains in place that every selected case is held back, with the server's reasons", () => {
    const message =
      "No run was created: every selected case is held back (Controlled tool routing: Called, but the test world never received it: get_deal). Fix it, or select cases it does not hold back.";
    renderDialog({
      error: { statusCode: 409, error: "all_cases_blocked", message },
    });
    expect(screen.getByTestId("run-error")).toHaveTextContent(message);
  });

  it("leaves other failures to the snackbar", () => {
    renderDialog({ error: { statusCode: 500, message: "boom" } });
    expect(screen.queryByTestId("run-error")).toBeNull();
  });

  it("folds the seed to one line, New seed until one is typed, and opens it with Edit", async () => {
    const user = userEvent.setup();
    renderDialog();
    expect(screen.getByTestId("run-seed-line")).toHaveTextContent("New seed");
    expect(screen.getByTestId("run-seed")).not.toBeVisible();
    await user.click(screen.getByRole("button", { name: "Edit" }));
    expect(screen.getByTestId("run-seed")).toBeVisible();
    await user.type(screen.getByTestId("run-seed"), "42");
    expect(screen.getByTestId("run-seed-line")).toHaveTextContent("42");
    await user.click(screen.getByRole("button", { name: "Done" }));
    expect(screen.getByRole("button", { name: "Edit" })).toBeInTheDocument();
  });

  it("sends the seed as typed, with no check of its own", async () => {
    const user = userEvent.setup();
    const { onSubmit } = renderDialog();
    await user.click(screen.getByRole("button", { name: "Edit" }));
    expect(
      screen.getByText(
        "The same seed draws the same personas and variations again",
      ),
    ).toBeInTheDocument();
    await user.type(screen.getByTestId("run-seed"), " 9223372036854775808 ");
    await user.click(runButton());
    expect(onSubmit).toHaveBeenLastCalledWith(
      {},
      ["k1", "k2"],
      DEFAULTS,
      "9223372036854775808",
    );
  });

  it("shows the server's refusal of a seed under the seed, and lets the run go again once the seed changes", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    const refused = {
      statusCode: 400,
      code: "invalid_seed",
      attr: "seed",
      message: "seed: A seed is a whole number from 0 to 2^63 - 1.",
      details: { seed: ["A seed is a whole number from 0 to 2^63 - 1."] },
    };
    render(
      <RunDialog
        open
        env={ENV}
        scenarioIds={["k1", "k2"]}
        trials={1}
        initialSeed="-4"
        onClose={vi.fn()}
        onSubmit={onSubmit}
        error={refused}
      />,
    );
    const seed = screen.getByTestId("run-seed");
    expect(seed).toBeVisible();
    expect(seed).toHaveAccessibleDescription(
      "A seed is a whole number from 0 to 2^63 - 1.",
    );
    // Said once, under the seed, never as the dialog's own error.
    expect(screen.queryByText(/^seed: /)).toBeNull();
    expect(runButton()).toBeDisabled();
    await user.clear(seed);
    await user.type(seed, "42");
    expect(seed).not.toHaveAccessibleDescription(/2\^63/);
    await waitFor(() => expect(runButton()).toBeEnabled());
    await user.click(runButton());
    expect(onSubmit).toHaveBeenLastCalledWith({}, ["k1", "k2"], DEFAULTS, "42");
  });

  it("starts a re-run from its run's seed, exactly, past what a JavaScript number holds", async () => {
    const user = userEvent.setup();
    const { onSubmit } = renderDialog({ initialSeed: "7902065059689103066" });
    expect(screen.getByTestId("run-seed-line")).toHaveTextContent(
      "7902065059689103066",
    );
    expect(screen.getByTestId("run-seed")).toHaveValue("7902065059689103066");
    await user.click(runButton());
    expect(onSubmit).toHaveBeenLastCalledWith(
      {},
      ["k1", "k2"],
      DEFAULTS,
      "7902065059689103066",
    );
  });
});
