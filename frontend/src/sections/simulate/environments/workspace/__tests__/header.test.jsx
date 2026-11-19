import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import WorkspaceHeader from "../WorkspaceHeader";
import LivePill from "../LivePill";
import SystemBanners from "../SystemBanners";
import VersionBar from "../VersionBar";

// The versions query is stubbed: each test sets the response it wants here,
// or "loading" / "error" / "disabled" for those states. "disabled" is the
// server switching versioning off after the page loaded: the last answer is
// still cached, but the refetch failed with versioning_disabled.
const versionsResponse = { current: undefined };
const refreshCalls = [];
vi.mock("src/api/simulate-environments/versions", async () => {
  const actual = await vi.importActual(
    "src/api/simulate-environments/versions",
  );
  return {
    ...actual,
    useRefreshWhenChecked: vi.fn((envId, versions) =>
      refreshCalls.push({ envId, versions }),
    ),
    useEnvironmentVersions: vi.fn((envId, { enabled } = {}) => {
      const answer = versionsResponse.current;
      if (!enabled || !answer) return { data: undefined, isSuccess: false };
      if (answer === "loading")
        return { data: undefined, isLoading: true, isSuccess: false };
      if (answer === "error")
        return { data: undefined, isError: true, isSuccess: false };
      if (answer.refreshFailed) {
        // An earlier answer is cached; the latest refresh failed.
        return {
          data: actual.toVersions(answer.refreshFailed),
          isError: true,
          isSuccess: false,
          error: { statusCode: 500 },
        };
      }
      if (answer === "disabled") {
        return {
          data: actual.toVersions({ agent: [], environment: [], suite: [] }),
          isError: true,
          isSuccess: false,
          error: { statusCode: 404, error: "versioning_disabled" },
        };
      }
      return { data: actual.toVersions(answer), isSuccess: true };
    }),
  };
});

const ENV = {
  id: "env-1",
  name: "Refund Copilot",
  tagline: "Handles refund conversations",
  surface: "voice",
  buildStatus: "ready",
};
const VERSIONED_ENV = {
  ...ENV,
  versioning: { enabled: true, nudges_enabled: false },
};

const item = (overrides) => ({
  id: overrides.id,
  label: overrides.label,
  status: "active",
  is_default: false,
  created_at: "2026-10-15T10:00:00Z",
  created_by: null,
  origin: "backfill",
  summary: "",
  hash: null,
  parent_id: null,
  ...overrides,
});
const VERSIONS = {
  agent: [item({ id: "a1", label: "v4", is_default: true })],
  environment: [
    item({
      id: "e2",
      label: "v2",
      is_default: true,
      summary: "1 tool added",
      created_by: { id: "u1", name: "Asha" },
    }),
    item({ id: "e1", label: "v1", summary: "First version" }),
  ],
  suite: [
    item({ id: "s3", label: "v3", is_default: true, scenario_count: 12 }),
  ],
};

// WorkspaceHeader now uses a react-query mutation (§2 delete), so wrap in a client.
const withRouter = (ui) => (
  <QueryClientProvider client={new QueryClient()}>
    <MemoryRouter>{ui}</MemoryRouter>
  </QueryClientProvider>
);

describe("LivePill", () => {
  it("shows Ready for a ready environment", () => {
    render(<LivePill env={{ buildStatus: "ready" }} />);
    expect(screen.getByText("Ready")).toBeInTheDocument();
    expect(screen.queryByText("Building")).toBeNull();
  });

  it("shows Building while the environment is still deriving", () => {
    render(<LivePill env={{ buildStatus: "building" }} />);
    expect(screen.getByText("Building")).toBeInTheDocument();
    expect(screen.queryByText("Ready")).toBeNull();
  });

  it("honours an explicit building override", () => {
    render(<LivePill env={{ buildStatus: "ready" }} building />);
    expect(screen.getByText("Building")).toBeInTheDocument();
  });

  it("shows a static Failed for a terminal-failed build (not Building/Ready)", () => {
    render(<LivePill env={{ buildStatus: "failed" }} />);
    expect(screen.getByText("Failed")).toBeInTheDocument();
    expect(screen.queryByText("Ready")).toBeNull();
    expect(screen.queryByText("Building")).toBeNull();
  });

  it.each([
    [{ buildStatus: "building", status: "finalizing" }, "Finalizing"],
    [{ buildStatus: "building", status: "cancelling" }, "Cancelling"],
    [{ buildStatus: "failed", status: "cancelled" }, "Cancelled"],
  ])("labels %o as %s, not Building or Failed", (env, label) => {
    render(<LivePill env={env} />);
    expect(screen.getByText(label)).toBeInTheDocument();
    expect(screen.queryByText("Building")).toBeNull();
    expect(screen.queryByText("Failed")).toBeNull();
  });
});

describe("SystemBanners", () => {
  it("renders the building banner from the env build status", () => {
    render(
      <SystemBanners
        env={{
          ...ENV,
          buildStatus: "building",
          buildProgress: { done: 5, total: 7 },
        }}
      />,
    );
    expect(
      screen.getByText("Environment is still being built"),
    ).toBeInTheDocument();
    expect(screen.getByText(/5 of 7 steps done/)).toBeInTheDocument();
  });

  it("renders nothing on a ready environment", () => {
    const { container } = render(<SystemBanners env={ENV} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("WorkspaceHeader", () => {
  afterEach(() => {
    versionsResponse.current = undefined;
  });

  const baseProps = {
    env: ENV,
    envState: { scenarios: [1, 2, 3], evals: [{ id: "e1" }] },
    canRun: true,
    runBlockedReason: "",
    onFork: vi.fn(),
  };

  it("hides the overflow menu when locked", () => {
    render(withRouter(<WorkspaceHeader {...baseProps} locked />));
    expect(screen.queryByRole("button", { name: "More actions" })).toBeNull();
  });

  it("shows Cancel build (by Run simulation) only while the env is building", () => {
    render(withRouter(<WorkspaceHeader {...baseProps} />));
    expect(screen.queryByRole("button", { name: /Cancel build/ })).toBeNull();

    render(
      withRouter(
        <WorkspaceHeader
          {...baseProps}
          env={{ ...ENV, buildStatus: "building" }}
          canRun={false}
          locked
        />,
      ),
    );
    expect(
      screen.getByRole("button", { name: /Cancel build/ }),
    ).toBeInTheDocument();
  });

  it("hides Cancel build once a cancel is in flight", () => {
    render(
      withRouter(
        <WorkspaceHeader
          {...baseProps}
          env={{ ...ENV, buildStatus: "building", status: "cancelling" }}
          canRun={false}
          locked
        />,
      ),
    );
    expect(screen.queryByRole("button", { name: /Cancel build/ })).toBeNull();
  });

  it("leaves the environment version to the bar and History, not a header pin", () => {
    versionsResponse.current = VERSIONS;
    render(withRouter(<WorkspaceHeader {...baseProps} env={VERSIONED_ENV} />));
    expect(screen.queryByRole("button", { name: /env v2/i })).toBeNull();
  });

  it("shows the rename pencil for a backend-backed env and opens the dialog", async () => {
    const user = userEvent.setup();
    render(withRouter(<WorkspaceHeader {...baseProps} backed />));

    const pencil = screen.getByRole("button", { name: "Rename environment" });
    expect(pencil).toBeInTheDocument();
    await user.click(pencil);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("hides the rename pencil for a non-backed env (no §8 row to PATCH)", () => {
    render(withRouter(<WorkspaceHeader {...baseProps} />));
    expect(
      screen.queryByRole("button", { name: "Rename environment" }),
    ).toBeNull();
  });

  it("hides the rename pencil when locked", () => {
    render(withRouter(<WorkspaceHeader {...baseProps} backed locked />));
    expect(
      screen.queryByRole("button", { name: "Rename environment" }),
    ).toBeNull();
  });

  it("offers Delete in the overflow for a backend-backed env and opens the confirm", async () => {
    const user = userEvent.setup();
    render(withRouter(<WorkspaceHeader {...baseProps} backed />));

    await user.click(screen.getByRole("button", { name: "More actions" }));
    await user.click(
      screen.getByRole("menuitem", { name: /Delete environment/ }),
    );
    expect(screen.getByText("Delete environment?")).toBeInTheDocument();
  });

  it("hides the overflow menu for a non-backed env (Delete absent, Fork disabled → no empty menu)", () => {
    // A non-backed env has nothing to delete on the server and Fork is currently
    // commented out, so the kebab must not render (opening an empty menu).
    render(withRouter(<WorkspaceHeader {...baseProps} />));
    expect(screen.queryByRole("button", { name: "More actions" })).toBeNull();
  });

  // Fork is temporarily commented out in ForkMenu — re-enable this with it.
  it.skip("forks from the overflow menu when unlocked", async () => {
    const user = userEvent.setup();
    const onFork = vi.fn();
    render(
      withRouter(
        <WorkspaceHeader {...baseProps} onFork={onFork} locked={false} />,
      ),
    );

    await user.click(screen.getByRole("button", { name: "More actions" }));
    await user.click(
      screen.getByRole("menuitem", { name: /Fork environment/ }),
    );
    expect(onFork).toHaveBeenCalledTimes(1);
  });

  it("starts a run-all (no ids) directly on Run, with no config dialog", async () => {
    const user = userEvent.setup();
    const onStartRun = vi.fn();
    render(
      withRouter(
        <WorkspaceHeader {...baseProps} canRun onStartRun={onStartRun} />,
      ),
    );

    await user.click(screen.getByRole("button", { name: "Run simulation" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(onStartRun).toHaveBeenCalledWith(undefined, 1);
  });

  it("runs with the repeats picked in the header dropdown", async () => {
    const user = userEvent.setup();
    const onStartRun = vi.fn();
    render(
      withRouter(
        <WorkspaceHeader {...baseProps} canRun onStartRun={onStartRun} />,
      ),
    );

    await user.click(screen.getByText(/^Repeats:/));
    await user.click(screen.getByText("3×"));
    await user.click(screen.getByRole("button", { name: "Run simulation" }));
    expect(onStartRun).toHaveBeenCalledWith(undefined, 3);
  });

  it("counts scenarios from the server count, not the seeded client list", async () => {
    const user = userEvent.setup();
    render(
      withRouter(<WorkspaceHeader {...baseProps} canRun scenarioCount={7} />),
    );

    await user.click(screen.getByText(/^Repeats:/));
    expect(
      screen.getByText(/7 scenarios × 1 repeat = 7 runs/),
    ).toBeInTheDocument();
  });

  it("hides the header Run + Repeats while a scenario selection is active", () => {
    render(
      withRouter(<WorkspaceHeader {...baseProps} canRun selectionActive />),
    );
    expect(screen.queryByRole("button", { name: "Run simulation" })).toBeNull();
    // The Repeats button's accessible name is its tooltip, so match its text.
    expect(screen.queryByText(/^Repeats:/)).toBeNull();
  });

  it("disables Run simulation with a reason tooltip when the env cannot run", async () => {
    const user = userEvent.setup();
    const reason = "Add scenarios on the Scenarios tab";
    render(
      withRouter(
        <WorkspaceHeader
          {...baseProps}
          canRun={false}
          runBlockedReason={reason}
        />,
      ),
    );

    const btn = screen.getByRole("button", { name: "Run simulation" });
    expect(btn).toBeDisabled();
    await user.hover(btn.parentElement);
    expect(await screen.findByText(reason)).toBeInTheDocument();
  });
});

describe("VersionBar", () => {
  afterEach(() => {
    versionsResponse.current = undefined;
  });

  it("states only the scenario count while versioning is off", () => {
    render(<VersionBar env={ENV} envState={{ scenarios: [1, 2, 3, 4] }} />);
    expect(screen.getByText("4 scenarios")).toBeInTheDocument();
    expect(screen.queryByText("×")).toBeNull();
  });

  it("follows agent checks while mounted, the drawer open or not", () => {
    versionsResponse.current = VERSIONS;
    refreshCalls.length = 0;
    render(<VersionBar env={VERSIONED_ENV} envState={{ scenarios: [1, 2] }} />);
    expect(refreshCalls.at(-1).envId).toBe(VERSIONED_ENV.id);
    expect(refreshCalls.at(-1).versions).toHaveLength(1);
  });

  it("states the default agent, environment and scenario versions", () => {
    versionsResponse.current = VERSIONS;
    const { container } = render(
      <VersionBar env={VERSIONED_ENV} envState={{ scenarios: [1, 2] }} />,
    );
    // Agent first, then the environment it runs against, as one line.
    expect(container).toHaveTextContent(
      "Agent v4×environment v2·scenarios v3·12 scenarios",
    );
    expect(screen.getByText("v2")).toBeInTheDocument();
    expect(screen.getByText("v4")).toBeInTheDocument();
    expect(screen.getByText("v3")).toBeInTheDocument();
    // The count is the default suite's, not the client store's.
    expect(screen.getByText("12 scenarios")).toBeInTheDocument();
  });

  it("says before versioning when the environment has no versions yet", () => {
    versionsResponse.current = { agent: [], environment: [], suite: [] };
    render(<VersionBar env={VERSIONED_ENV} envState={{ scenarios: [1] }} />);
    expect(screen.getAllByText("before versioning")).toHaveLength(1);
    expect(screen.getByText("1 scenario")).toBeInTheDocument();
  });

  it("says none, not before versioning, when there is no default agent", () => {
    versionsResponse.current = { ...VERSIONS, agent: [] };
    render(<VersionBar env={VERSIONED_ENV} envState={{ scenarios: [1] }} />);
    expect(screen.getByText("none")).toBeInTheDocument();
    expect(screen.queryByText("before versioning")).toBeNull();
    expect(screen.getByText("×")).toBeInTheDocument();
  });

  it("names an unnumbered default instead of printing null", () => {
    versionsResponse.current = {
      ...VERSIONS,
      environment: [item({ id: "e9", label: null, is_default: true })],
    };
    render(<VersionBar env={VERSIONED_ENV} envState={{ scenarios: [1] }} />);
    expect(screen.getByText("Unnumbered version")).toBeInTheDocument();
    expect(screen.queryByText(/null/)).toBeNull();
  });

  it("drops the pairing when the server has switched versioning off", () => {
    versionsResponse.current = "disabled";
    render(<VersionBar env={VERSIONED_ENV} envState={{ scenarios: [1, 2] }} />);
    expect(screen.queryByText("×")).toBeNull();
    expect(screen.getByText("2 scenarios")).toBeInTheDocument();
  });

  it("calls the agent the environment's default, not the one runs test", async () => {
    const user = userEvent.setup();
    versionsResponse.current = VERSIONS;
    render(<VersionBar env={VERSIONED_ENV} envState={{ scenarios: [1, 2] }} />);
    await user.hover(screen.getByText("v2"));
    const tip = await screen.findByRole("tooltip");
    expect(tip).toHaveTextContent("default agent version is v4");
    expect(tip).not.toHaveTextContent(/Runs test agent/);
  });

  it("says plainly when there is no default agent version", async () => {
    const user = userEvent.setup();
    versionsResponse.current = { ...VERSIONS, agent: [] };
    render(<VersionBar env={VERSIONED_ENV} envState={{ scenarios: [1] }} />);
    await user.hover(screen.getByText("v2"));
    const tip = await screen.findByRole("tooltip");
    expect(tip).toHaveTextContent(
      "No default agent version is set for this environment.",
    );
    expect(tip).not.toHaveTextContent(/is none/);
  });

  it("says plainly when there is no environment version yet", async () => {
    const user = userEvent.setup();
    versionsResponse.current = { ...VERSIONS, environment: [] };
    render(<VersionBar env={VERSIONED_ENV} envState={{ scenarios: [1] }} />);
    await user.hover(screen.getByText("v4"));
    const tip = await screen.findByRole("tooltip");
    expect(tip).toHaveTextContent(
      "This environment has no environment version yet.",
    );
    expect(tip).not.toHaveTextContent(/replay environment before versioning/);
  });

  it("keeps the pairing when a refresh fails after an answer", () => {
    versionsResponse.current = { refreshFailed: VERSIONS };
    render(<VersionBar env={VERSIONED_ENV} envState={{ scenarios: [1, 2] }} />);
    expect(screen.getByText("×")).toBeInTheDocument();
    expect(screen.getByText("v4")).toBeInTheDocument();
  });

  it.each(["loading", "error"])(
    "states no pairing while the versions are %s",
    (answer) => {
      versionsResponse.current = answer;
      render(
        <VersionBar env={VERSIONED_ENV} envState={{ scenarios: [1, 2] }} />,
      );
      expect(screen.queryByText("before versioning")).toBeNull();
      expect(screen.queryByText("×")).toBeNull();
      expect(screen.getByText("2 scenarios")).toBeInTheDocument();
    },
  );

  const SUBJECT = {
    id: "agent-1",
    name: "Single Prompt Agent",
    provider: "retell",
    versions_count: 1,
    active_version: "v1",
  };

  it("names the agent under test and its active version from the API", () => {
    render(
      <VersionBar
        env={{ ...ENV, testSubject: SUBJECT, counts: { scenarios: 3 } }}
        envState={{ scenarios: [1] }}
      />,
    );
    expect(screen.getByText("Single Prompt Agent")).toBeInTheDocument();
    expect(screen.getByText("v1")).toBeInTheDocument();
    expect(screen.queryByText(/^env$/)).toBeNull();
  });

  it('calls an agent with no name on record "Agent", as the Overview card does', () => {
    render(
      <VersionBar
        env={{
          ...ENV,
          testSubject: { ...SUBJECT, name: null },
          counts: { scenarios: 3 },
        }}
        envState={{ scenarios: [1] }}
      />,
    );
    expect(screen.getByText("Agent")).toBeInTheDocument();
    expect(screen.getByText("v1")).toBeInTheDocument();
  });

  it("counts every scenario the environment has, not just the loaded page", () => {
    render(
      <VersionBar
        env={{ ...ENV, testSubject: SUBJECT, counts: { scenarios: 42 } }}
        envState={{ scenarios: [1, 2, 3, 4] }}
      />,
    );
    expect(screen.getByText("42 scenarios")).toBeInTheDocument();
  });

  it("leaves the agent out when no agent is connected yet", () => {
    render(
      <VersionBar
        env={{ ...ENV, testSubject: null, counts: { scenarios: 0 } }}
        envState={{}}
      />,
    );
    expect(screen.queryByText("Test subject")).toBeNull();
    expect(screen.getByText("0 scenarios")).toBeInTheDocument();
  });
});
