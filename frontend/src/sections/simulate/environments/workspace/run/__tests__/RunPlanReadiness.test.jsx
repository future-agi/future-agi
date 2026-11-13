import { describe, it, expect, vi, beforeEach } from "vitest";
import { useEffect } from "react";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

// The versions hooks answer one environment; the Ready to run section is
// stubbed to hand the popup a view, and records what it was asked for.
const state = {
  view: null,
  versionsLoading: false,
  defaultEnv: "e2",
  drafts: undefined,
  fixRun: null,
};
const sectionCalls = [];
const save = vi.fn();
const readinessCalls = [];
vi.mock("src/api/simulate-environments/versions", async () => {
  const actual = await vi.importActual(
    "src/api/simulate-environments/versions",
  );
  const item = (overrides) => ({
    status: "active",
    is_default: false,
    ...overrides,
  });
  const versions = () =>
    actual.toVersions({
      agent: [
        item({ id: "a4", label: "v4", is_default: true, pickable: true }),
        item({ id: "a3", label: "v3", pickable: true }),
      ],
      environment: [
        item({ id: "e4", label: "v4", created_at: "2026-04-01" }),
        item({ id: "e3", label: "v3", created_at: "2026-03-01" }),
        item({ id: "e2", label: "v2", created_at: "2026-02-01" }),
        item({ id: "e1", label: "v1", created_at: "2026-01-01" }),
      ].map((one) => ({ ...one, is_default: one.id === state.defaultEnv })),
      suite: [item({ id: "s3", label: "v3", is_default: true })],
    });
  return {
    ...actual,
    useEnvironmentVersions: () =>
      state.versionsLoading
        ? { data: undefined, isLoading: true }
        : { data: versions(), isLoading: false },
    useRunPlan: () => ({ data: { warnings: [] }, isLoading: false }),
    useDrafts: () => ({ data: state.drafts }),
    useSaveDrafts: () => ({ mutate: save, isPending: false, error: null }),
    useSuiteMembers: () => ({
      data: [{ scenario_key: "k1" }, { scenario_key: "k2" }],
      isLoading: false,
    }),
  };
});
vi.mock("src/api/simulate-environments/readiness", () => ({
  MAX_CASE_KEYS: 200,
  useReadiness: (envId, picks) => {
    readinessCalls.push(picks);
    return { data: { selection: { requested: 10, eligible: 7 } } };
  },
  useReadinessCases: () => ({}),
}));
vi.mock("src/api/simulate-environments/readinessFix", () => ({
  useFixRun: () => ({ data: state.fixRun }),
  FIX_ACTIVE_STATES: new Set([
    "queued",
    "fixing",
    "waiting_on_you",
    "saving",
    "checking",
  ]),
}));
vi.mock("../readiness/ReadyToRunSection", () => ({
  default: function Section({ picks, keys, onView }) {
    sectionCalls.push({ picks, keys });
    useEffect(() => onView(state.view), [onView]);
    return null;
  },
}));

const { default: RunDialog } = await import("../RunDialog");

const view = ({ requested = 10, blocked = 0, canRun = true } = {}) => ({
  grouped: true,
  state: blocked ? "blocked" : "ready",
  canRun,
  summary: {
    requested,
    eligible: requested - blocked,
    blocked,
    stale: 0,
    untested: 0,
  },
  open: blocked
    ? [
        {
          area: "tool_binding",
          status: "blocked",
          reason: "x",
          affected: blocked,
        },
      ]
    : [],
  limits: [],
  ready: [],
  grading: { notGradable: 0 },
});

const dialog = () => (
  <RunDialog
    open
    env={{ id: "env-1", name: "Refunds desk", versioning: { enabled: true } }}
    scenarioIds={["k1", "k2"]}
    onClose={vi.fn()}
    onSubmit={vi.fn()}
  />
);
const renderDialog = () => render(dialog());
const runButton = () => screen.getByRole("button", { name: /^Run/ });

beforeEach(() => {
  state.view = null;
  state.versionsLoading = false;
  state.defaultEnv = "e2";
  state.drafts = undefined;
  state.fixRun = null;
  sectionCalls.length = 0;
  readinessCalls.length = 0;
  save.mockClear();
});

const openDrafts = (updatedAt = "2026-11-12T10:00:00Z") => ({
  any: true,
  stale: false,
  pieces: ["environment"],
  environment: { parentId: "e2", revision: 3, updatedAt },
});
const envPicker = () => screen.getByRole("combobox", { name: "Environment" });

describe("RunDialog with Ready to run", () => {
  it("asks for the whole suite when running every scenario", () => {
    state.view = view();
    render(
      <RunDialog
        open
        env={{
          id: "env-1",
          name: "Refunds desk",
          versioning: { enabled: true },
        }}
        onClose={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );
    expect(sectionCalls.length).toBeGreaterThan(0);
    expect(sectionCalls.every((call) => call.keys === null)).toBe(true);
  });

  it("keeps the plan's shape with a placeholder while the versions load", () => {
    state.versionsLoading = true;
    renderDialog();
    expect(
      screen.getByRole("progressbar", { name: "Loading versions…" }),
    ).toBeInTheDocument();
    expect(runButton()).toBeDisabled();
  });

  it("reads Run 6 of 10 when 4 of the selected scenarios are held back", () => {
    state.view = view({ requested: 10, blocked: 4 });
    renderDialog();
    expect(runButton()).toHaveTextContent("Run 6 of 10");
    expect(runButton()).toBeEnabled();
    expect(
      screen.getByText(/10 selected · 6 will run · 4 held back/),
    ).toBeInTheDocument();
    expect(screen.getByTestId("run-counts")).toHaveTextContent(
      "6 of 10 eligible · 4 blocked",
    );
    expect(screen.getByText("2 selected scenarios")).toBeInTheDocument();
  });

  it("turns Run off while the versions are being checked together", () => {
    state.view = view({ canRun: false });
    renderDialog();
    expect(runButton()).toBeDisabled();
  });

  it("turns Run off and says why when every selected scenario is held back", () => {
    state.view = view({ requested: 2, blocked: 2 });
    renderDialog();
    expect(runButton()).toBeDisabled();
    expect(screen.getByTestId("run-all-held")).toHaveTextContent(
      "Every selected scenario waits on a fix: tools wired to the test world",
    );
  });

  it("asks for the readiness of the agent version the user picks", async () => {
    state.view = view();
    const user = userEvent.setup();
    renderDialog();
    await user.click(screen.getByRole("combobox", { name: "Agent" }));
    await user.click(screen.getByRole("option", { name: "v3" }));
    expect(sectionCalls.at(-1)).toEqual({
      picks: { agent: "a3" },
      keys: ["k1", "k2"],
    });
  });

  it("saves open drafts from the note with their parents and revisions", async () => {
    state.view = view();
    state.drafts = openDrafts();
    const user = userEvent.setup();
    renderDialog();
    await user.click(screen.getByRole("button", { name: "Save to run" }));
    expect(save).toHaveBeenCalledWith([
      { piece: "environment", parentId: "e2", revision: 3 },
    ]);
  });

  it("says the fixes are in unsaved changes only when the run left them", () => {
    state.view = view();
    state.drafts = openDrafts("2026-11-12T10:00:00Z");
    state.fixRun = {
      state: "done",
      draft: true,
      finished_at: "2026-11-12T10:01:00Z",
    };
    const { unmount } = renderDialog();
    expect(screen.getByTestId("run-drafts-note")).toHaveTextContent(
      "The fixes are in unsaved changes",
    );
    unmount();
    state.drafts = openDrafts("2026-11-12T10:05:00Z");
    renderDialog();
    expect(screen.getByTestId("run-drafts-note")).toHaveTextContent(
      "Unsaved changes aren't part of this run",
    );
  });

  it("offers the saved environment version only over an older hand pick", async () => {
    state.view = view();
    state.fixRun = {
      state: "done",
      saved: { environment: { id: "e3", label: "v3" }, suite: null },
    };
    const user = userEvent.setup();
    renderDialog();
    await user.click(envPicker());
    await user.click(screen.getByRole("option", { name: "v1" }));
    expect(screen.getByTestId("run-switch-environment")).toHaveTextContent(
      "The fixes are in environment v3. 7 of 10 eligible there.",
    );
    await user.click(screen.getByRole("button", { name: "Switch to v3" }));
    expect(envPicker()).toHaveTextContent("v3");
    expect(screen.queryByTestId("run-switch-environment")).toBeNull();

    await user.click(envPicker());
    await user.click(screen.getByRole("option", { name: "v4" }));
    expect(screen.queryByTestId("run-switch-environment")).toBeNull();
  });

  it("follows the default when the picker is on it, also after picking it by hand", async () => {
    state.view = view();
    const user = userEvent.setup();
    const { rerender } = renderDialog();
    expect(envPicker()).toHaveTextContent("v2 (default)");
    state.defaultEnv = "e3";
    rerender(dialog());
    expect(envPicker()).toHaveTextContent("v3 (default)");

    await user.click(envPicker());
    await user.click(screen.getByRole("option", { name: "v1" }));
    await user.click(envPicker());
    await user.click(screen.getByRole("option", { name: "v3 (default)" }));
    state.defaultEnv = "e4";
    rerender(dialog());
    expect(envPicker()).toHaveTextContent("v4 (default)");
    expect(screen.queryByTestId("run-switch-environment")).toBeNull();
  });
});
