import { describe, it, expect, vi, afterEach } from "vitest";
import PropTypes from "prop-types";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useSearchParams } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import WorkspacePanels from "../WorkspacePanels";
import useWorkspaceTab from "../helpers/useWorkspaceTab";
import { gapsByTab, counts } from "../helpers/workspaceGaps";

// The five tab bodies are exercised in their own suites — here they are markers
// so the switch is what's under test. The Overview echoes the eval names it was
// handed, because which state it gets is the wiring under test: a backed
// environment's real applied evals overlaid on the store, not the raw store.
function echoEvals(label, envState) {
  return `${label}:${(envState?.evals || []).map((e) => e.name || e.id).join(",")}`;
}
// The Overview stub echoes the eval names it was handed: a backed
// environment's real applied evals overlaid on the store, not the raw store.
function OverviewPanelStub({ envState }) {
  return <div>{echoEvals("overview-body", envState)}</div>;
}
OverviewPanelStub.propTypes = { envState: PropTypes.object };
vi.mock("../overview/OverviewPanel", () => ({ default: OverviewPanelStub }));
vi.mock("../contract/RlContractPanel", () => ({ default: () => <div>contract-body</div> }));
vi.mock("../scenarios/ScenariosStep", () => ({ default: () => <div>scenarios-body</div> }));
vi.mock("../evals/EvalsStep", () => ({
  // eslint-disable-next-line react/prop-types
  default: ({ grading }) => <div>{`evals-body:grading=${grading}`}</div>,
}));
vi.mock("../runs/summary/RunsSummary", () => ({ default: () => <div>runs-body</div> }));
vi.mock("../world/WorldPanel", () => ({ default: () => <div>world-body</div> }));
vi.mock("../versions/VersionsPanel", () => ({ default: () => <div>versions-body</div> }));

// The real runs hook by default; the loading cases override it.
vi.mock("src/api/simulate-environments/runs", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, useEnvironmentRuns: vi.fn(actual.useEnvironmentRuns) };
});
const runsApi = await import("src/api/simulate-environments/runs");
const { useEnvironmentRuns: realUseEnvironmentRuns } = await vi.importActual(
  "src/api/simulate-environments/runs",
);

const ENV = { id: "env-1", name: "Refund Copilot", surface: "voice" };

const baseEnvState = (overrides = {}) => ({
  agent: { name: "Support agent" },
  scenarios: [{ id: "s1" }, { id: "s2" }],
  evals: [{ id: "e1" }],
  runs: [],
  ...overrides,
});

function renderPanels(props = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const merged = {
    env: ENV,
    envState: baseEnvState(),
    patch: vi.fn(),
    tab: "overview",
    onTabChange: vi.fn(),
    ...props,
  };
  const utils = render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <WorkspacePanels {...merged} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...utils, props: merged };
}

describe("WorkspacePanels", () => {
  afterEach(() => runsApi.useEnvironmentRuns.mockImplementation(realUseEnvironmentRuns));

  it("renders the seven tabs in Vel's order, Runs included before any run", () => {
    renderPanels();
    expect(screen.getAllByRole("tab").map((t) => t.textContent.replace(/\d+$/, ""))).toEqual([
      "Overview",
      "World",
      "Contract",
      "Scenarios",
      "Evaluations",
      "Runs",
      "Settings",
    ]);
  });

  it("opens the World panel for ?tab=world", () => {
    renderPanels({ tab: "world" });
    expect(screen.getByRole("tab", { name: "World" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("world-body")).toBeInTheDocument();
  });

  it.each([
    ["while a run is being graded", "Evaluating", true],
    ["while no run is being graded", "Running", false],
  ])("tells the Evaluations tab %s", (_label, status, grading) => {
    runsApi.useEnvironmentRuns.mockReturnValue({
      runs: [
        runsApi.executionToRun({ id: "r1", status: "Completed" }),
        runsApi.executionToRun({ id: "r2", status }),
      ],
      isLoading: false,
    });
    renderPanels({ tab: "evals" });
    expect(
      screen.getByText(`evals-body:grading=${grading}`),
    ).toBeInTheDocument();
  });

  it("shows the Runs empty state with Run baseline when there are no runs", async () => {
    const user = userEvent.setup();
    const onStartRun = vi.fn();
    runsApi.useEnvironmentRuns.mockReturnValue({ runs: [], count: 0, isLoading: false });
    renderPanels({ tab: "runs", onStartRun, canRun: true });
    expect(screen.getByText("No runs yet")).toBeInTheDocument();
    expect(
      screen.getByText("The first run is the baseline improvements are measured against."),
    ).toBeInTheDocument();
    expect(screen.queryByText("runs-body")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Run baseline" }));
    expect(onStartRun).toHaveBeenCalledWith(undefined, 3);
  });

  it("disables Run baseline when the environment can't run yet", () => {
    runsApi.useEnvironmentRuns.mockReturnValue({ runs: [], count: 0, isLoading: false });
    renderPanels({ tab: "runs", canRun: false, runBlockedReason: "Still building" });
    expect(screen.getByRole("button", { name: "Run baseline" })).toBeDisabled();
  });

  it("shows the runs summary, not the empty state, while the runs are loading", () => {
    runsApi.useEnvironmentRuns.mockReturnValue({ runs: [], isLoading: true });
    renderPanels({ tab: "runs" });
    expect(screen.getByText("runs-body")).toBeInTheDocument();
    expect(screen.queryByText("No runs yet")).toBeNull();
  });

  it("badges scenarios/evals from counts and runs from the live executions", () => {
    renderPanels({
      envState: baseEnvState({ runs: [{ id: "r1" }, { id: "r2" }, { id: "r3" }] }),
      counts: { scenarios: 5, evals: 2 },
    });
    expect(screen.getByRole("tab", { name: /Scenarios 5/ })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Evaluations 2/ })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Runs 3/ })).toBeInTheDocument();
  });

  // The runs list's total is what the tab lists; the environment's own count
  // arrives first and stands in only until the list loads.
  it("badges runs from the runs list's total once it loads, not the first page", () => {
    runsApi.useEnvironmentRuns.mockReturnValue({
      runs: Array.from({ length: 10 }, (_, i) => ({ id: `r${i}` })),
      count: 23,
      isLoading: false,
    });
    renderPanels({
      counts: { scenarios: 5, evals: 2 },
      overviewCounts: { runs: 23 },
    });
    expect(screen.getByRole("tab", { name: /Runs 23/ })).toBeInTheDocument();
  });

  // A deleted run leaves its job behind, so the environment's count can run
  // higher than the runs the tab lists.
  it("follows the runs list over a higher environment count once it loads", () => {
    runsApi.useEnvironmentRuns.mockReturnValue({
      runs: [{ id: "r1" }, { id: "r2" }],
      count: 2,
      isLoading: false,
    });
    renderPanels({
      counts: { scenarios: 5, evals: 2 },
      overviewCounts: { runs: 5 },
    });
    expect(screen.getByRole("tab", { name: /Runs 2/ })).toBeInTheDocument();
  });

  it("shows the Runs tab from the environment's run count while the runs list loads", () => {
    runsApi.useEnvironmentRuns.mockReturnValue({ runs: [], isLoading: true });
    renderPanels({
      counts: { scenarios: 5, evals: 2 },
      overviewCounts: { runs: 4 },
    });
    expect(screen.getByRole("tab", { name: /Runs 4/ })).toBeInTheDocument();
  });

  it("shows no numeric badges when counts is omitted (builder still streaming)", () => {
    renderPanels({
      envState: baseEnvState({ runs: [{ id: "r1" }] }),
      counts: null,
    });
    expect(screen.getByRole("tab", { name: "Scenarios" })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: /Scenarios \d/ })).toBeNull();
    expect(screen.queryByRole("tab", { name: /Runs \d/ })).toBeNull();
  });

  it("shows a gap badge whose tooltip lists the missing pieces", async () => {
    const user = userEvent.setup();
    renderPanels({
      gapsByTab: { evals: [{ id: "no-evals", title: "No evaluations added" }] },
      counts: { scenarios: 2, evals: 0 },
    });

    await user.hover(screen.getByText("Evaluations"));
    expect(await screen.findByText("Needs your input before you can run:")).toBeInTheDocument();
    expect(screen.getByText(/No evaluations added/)).toBeInTheDocument();
  });

  it("uses Vel's hints for World and Runs, without em dashes", async () => {
    const user = userEvent.setup();
    renderPanels();
    await user.hover(screen.getByText("World"));
    const world = await screen.findByText(/What your agent talks to during a run/);
    expect(world.textContent).not.toMatch(/\u2014/);
    await user.unhover(screen.getByText("World"));
    await user.hover(screen.getByText("Runs"));
    expect(await screen.findByText(/Every simulation of this environment and how it scored/)).toBeInTheDocument();
  });

  it("explains a tab on hover instead of repeating it as a heading", async () => {
    const user = userEvent.setup();
    renderPanels();
    await user.hover(screen.getByText("Contract"));
    expect(await screen.findByText(/The rules the agent holds to and what it's for/)).toBeInTheDocument();
    // The hint describes the tab; it never replaces the tab's name.
    expect(screen.getByRole("tab", { name: "Contract" })).toBeInTheDocument();
  });

  it("keeps the gap list in the tooltip alongside the tab's explanation", async () => {
    const user = userEvent.setup();
    renderPanels({
      gapsByTab: { evals: [{ id: "no-evals", title: "No evaluations added" }] },
      counts: { scenarios: 2, evals: 0 },
    });
    await user.hover(screen.getByText("Evaluations"));
    expect(await screen.findByText(/Sub-goals decide pass or fail/)).toBeInTheDocument();
    expect(screen.getByText("Needs your input before you can run:")).toBeInTheDocument();
  });

  it("falls back to the Overview body for an unknown tab", () => {
    renderPanels({ tab: "not-a-tab" });
    expect(screen.getByText(/^overview-body:/)).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Overview" })).toHaveAttribute("aria-selected", "true");
  });

  it("renders the body for the active tab", () => {
    renderPanels({ tab: "scenarios" });
    expect(screen.getByText("scenarios-body")).toBeInTheDocument();
    expect(screen.queryByText(/^overview-body:/)).toBeNull();
  });

  it("fires onTabChange when a tab is clicked", async () => {
    const user = userEvent.setup();
    const onTabChange = vi.fn();
    renderPanels({ onTabChange });

    await user.click(screen.getByRole("tab", { name: /Contract/ }));
    expect(onTabChange).toHaveBeenCalledWith("contract");
  });

  it("hands the Overview the server's eval set too, the same one the Evaluations tab lists", () => {
    const serverEvals = [
      { id: "cfg-1", name: "no_misselling" },
      { id: "cfg-2", name: "no_pii_leak" },
    ];
    renderPanels({
      tab: "overview",
      envState: baseEnvState({ evals: [] }),
      serverEnvState: baseEnvState({ evals: serverEvals }),
    });

    expect(screen.getByText("overview-body:no_misselling,no_pii_leak")).toBeInTheDocument();
  });

  it("falls back to envState for the Overview when no server state is passed (the build page)", () => {
    renderPanels({ tab: "overview", envState: baseEnvState({ evals: [{ id: "e1" }] }) });
    expect(screen.getByText("overview-body:e1")).toBeInTheDocument();
  });
});

const GAP_ENV = {
  tools: [{ name: "issue_refund", desc: "Issues a refund to a customer." }],
  rules: ["Never refund twice", "Verify identity first"],
};

describe("gapsByTab", () => {
  it("buckets only blocking gaps — missing evals is no longer one, and assumed gaps are dropped", () => {
    const byTab = gapsByTab(GAP_ENV, { evals: [] });
    // "no evaluations" used to be a blocking gap that badged the Evaluations tab;
    // it isn't anymore (a run is allowed without evals), and the remaining gaps
    // (stub/manifest/prompt-only) are all "assumed", which never badge a tab.
    expect(byTab.evals).toBeUndefined();
    expect(byTab.contract).toBeUndefined();
    expect(byTab).toEqual({});
  });

  it("has no gaps once an evaluation is added", () => {
    expect(gapsByTab(GAP_ENV, { evals: [{ id: "e1" }] })).toEqual({});
  });
});

describe("counts", () => {
  it("counts the scenarios and evals slices", () => {
    expect(counts({ scenarios: [1, 2, 3], evals: [1] })).toEqual({ scenarios: 3, evals: 1 });
  });

  it("is zero-safe on an empty state", () => {
    expect(counts({})).toEqual({ scenarios: 0, evals: 0 });
  });
});

describe("useWorkspaceTab", () => {
  function Probe() {
    const { tab, setTab } = useWorkspaceTab();
    const [params] = useSearchParams();
    return (
      <>
        <span data-testid="tab">{tab}</span>
        <span data-testid="param">{params.get("tab") || ""}</span>
        <button type="button" onClick={() => setTab("runs")}>
          go runs
        </button>
      </>
    );
  }

  const renderProbe = (entry) =>
    render(
      <MemoryRouter initialEntries={[entry]}>
        <Probe />
      </MemoryRouter>,
    );

  it("reads a valid ?tab=", () => {
    renderProbe("/env?tab=scenarios");
    expect(screen.getByTestId("tab")).toHaveTextContent("scenarios");
  });

  it("falls back to overview for an unknown ?tab=", () => {
    renderProbe("/env?tab=bogus");
    expect(screen.getByTestId("tab")).toHaveTextContent("overview");
  });

  it("writes the tab into the query on setTab", async () => {
    const user = userEvent.setup();
    renderProbe("/env");
    await user.click(screen.getByRole("button", { name: "go runs" }));
    expect(screen.getByTestId("param")).toHaveTextContent("runs");
  });
});

describe("Versions tab", () => {
  it("is hidden while versioning is off", () => {
    renderPanels();
    expect(screen.queryByRole("tab", { name: /Versions/ })).toBeNull();
  });

  it("is shown and renders the versions body when versioning is on", () => {
    renderPanels({
      env: { ...ENV, versioning: { enabled: true, nudges_enabled: false } },
      tab: "versions",
    });
    expect(screen.getByRole("tab", { name: /Versions/ })).toBeInTheDocument();
    expect(screen.getByText("versions-body")).toBeInTheDocument();
  });

  it("falls back to the Overview when versions is deep-linked with versioning off", () => {
    renderPanels({ tab: "versions" });
    expect(screen.queryByText("versions-body")).toBeNull();
    expect(screen.getByText(/overview-body/)).toBeInTheDocument();
  });
});

