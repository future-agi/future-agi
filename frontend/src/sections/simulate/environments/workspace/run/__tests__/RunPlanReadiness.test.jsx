import { describe, it, expect, vi, beforeEach } from "vitest";
import { useEffect } from "react";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

// The versions hooks answer one environment; the Ready to run section is
// stubbed to hand the popup a view, and records what it was asked for.
const state = { view: null };
const sectionCalls = [];
vi.mock("src/api/simulate-environments/versions", async () => {
  const actual = await vi.importActual(
    "src/api/simulate-environments/versions",
  );
  const item = (overrides) => ({
    status: "active",
    is_default: false,
    ...overrides,
  });
  const versions = actual.toVersions({
    agent: [
      item({ id: "a4", label: "v4", is_default: true, pickable: true }),
      item({ id: "a3", label: "v3", pickable: true }),
    ],
    environment: [item({ id: "e2", label: "v2", is_default: true })],
    suite: [item({ id: "s3", label: "v3", is_default: true })],
  });
  return {
    ...actual,
    useEnvironmentVersions: () => ({ data: versions, isLoading: false }),
    useRunPlan: () => ({ data: { warnings: [] }, isLoading: false }),
    useDrafts: () => ({ data: undefined }),
    useSuiteMembers: () => ({
      data: [{ scenario_key: "k1" }, { scenario_key: "k2" }],
      isLoading: false,
    }),
  };
});
vi.mock("../readiness/ReadyToRunSection", () => ({
  default: function Section({ picks, keys, onView }) {
    sectionCalls.push({ picks, keys });
    useEffect(() => onView(state.view), [onView]);
    return null;
  },
}));

const { default: RunDialog } = await import("../RunDialog");

const view = ({ requested = 10, blocked = 0, canRun = true } = {}) => ({
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

const renderDialog = () =>
  render(
    <RunDialog
      open
      env={{ id: "env-1", name: "Refunds desk", versioning: { enabled: true } }}
      scenarioIds={["k1", "k2"]}
      onClose={vi.fn()}
      onSubmit={vi.fn()}
    />,
  );
const runButton = () => screen.getByRole("button", { name: /^Run/ });

beforeEach(() => {
  state.view = null;
  sectionCalls.length = 0;
});

describe("RunDialog with Ready to run", () => {
  it("reads Run 6 of 10 when 4 of the selected scenarios are held back", () => {
    state.view = view({ requested: 10, blocked: 4 });
    renderDialog();
    expect(runButton()).toHaveTextContent("Run 6 of 10");
    expect(runButton()).toBeEnabled();
    expect(
      screen.getByText(/10 selected · 6 will run · 4 held back/),
    ).toBeInTheDocument();
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
      "Tools wired to the test world",
    );
  });

  it("asks for the readiness of the agent version the user picks", async () => {
    state.view = view();
    const user = userEvent.setup();
    renderDialog();
    await user.click(screen.getByRole("combobox", { name: "Agent version" }));
    await user.click(screen.getByRole("option", { name: "v3" }));
    expect(sectionCalls.at(-1)).toEqual({
      picks: { agent: "a3" },
      keys: ["k1", "k2"],
    });
  });
});
