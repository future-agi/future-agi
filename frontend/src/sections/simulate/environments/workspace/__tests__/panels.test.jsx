import { describe, it, expect, vi } from "vitest";
import PropTypes from "prop-types";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useSearchParams } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import WorkspacePanels from "../WorkspacePanels";
import NextStepsChecklist from "../overview/NextStepsChecklist";
import useWorkspaceTab from "../helpers/useWorkspaceTab";
import { gapsByTab, counts } from "../helpers/workspaceGaps";

// The five tab bodies are exercised in their own suites — here they are markers
// so the switch is what's under test. The two that read the eval set echo the
// names they were handed, because which state each one gets is the wiring
// under test: a backed environment's real applied evals overlaid on the store,
// not the raw store.
function echoEvals(label, envState) {
  return `${label}:${(envState?.evals || []).map((e) => e.name || e.id).join(",")}`;
}
// The Overview stub mounts the REAL next-steps checklist, because the
// checklist's "Add evaluations" step and its CTA count are the thing that
// regressed when this panel was handed the raw store: a stub echoing the prop
// would pass against a checklist that read something else entirely.
function OverviewPanelStub({ envState, onGo }) {
  return (
    <div>
      {echoEvals("overview-body", envState)}
      <NextStepsChecklist env={ENV} envState={envState} onGo={onGo} />
    </div>
  );
}
OverviewPanelStub.propTypes = { envState: PropTypes.object, onGo: PropTypes.func };
vi.mock("../overview/OverviewPanel", () => ({ default: OverviewPanelStub }));
vi.mock("../contract/RlContractPanel", () => ({ default: () => <div>contract-body</div> }));
vi.mock("../scenarios/ScenariosStep", () => ({ default: () => <div>scenarios-body</div> }));
vi.mock("../evals/EvalsStep", () => ({ default: () => <div>evals-body</div> }));
function RunsPanelStub({ envState }) {
  return <div>{echoEvals("runs-body", envState)}</div>;
}
RunsPanelStub.propTypes = { envState: PropTypes.object };
vi.mock("../runs/RunsPanel", () => ({ default: RunsPanelStub }));

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
  it("renders all workspace tabs with Overview first and Settings last", () => {
    renderPanels();
    ["Overview", "Contract", "Scenarios", "Evaluations", "Runs", "Settings"].forEach((label) => {
      expect(screen.getByRole("tab", { name: new RegExp(label) })).toBeInTheDocument();
    });
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

  it("hands the Runs panel the badge state, so the pre-flight count matches the tab", () => {
    renderPanels({
      tab: "runs",
      envState: baseEnvState({ evals: [{ id: "stale-store-eval" }] }),
      serverEnvState: baseEnvState({
        evals: [{ id: "cfg-1", name: "no_misselling" }],
      }),
    });
    expect(screen.getByText("runs-body:no_misselling")).toBeInTheDocument();
  });

  it("falls back to envState when no badge state is passed (the build page)", () => {
    renderPanels({ tab: "runs", envState: baseEnvState({ evals: [{ id: "e1" }] }) });
    expect(screen.getByText("runs-body:e1")).toBeInTheDocument();
  });

  // The Overview's next-steps checklist counts applied evals for its "Add
  // evaluations" step. On a backed environment that set is the server's, so
  // handing this panel the raw store let the checklist still ask for
  // evaluations, and count the remaining steps as though there were none,
  // while the Evaluations tab listed the server's two.
  it("hands the Overview the server's eval set too, so its checklist counts what the Evaluations tab lists", () => {
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
    // The step is satisfied by the server's evals: no "Add evaluations" ask,
    // and it counts towards the remaining-steps line.
    expect(screen.queryByRole("button", { name: "Add evaluations" })).toBeNull();
    expect(
      screen.getByText("3 of 5 complete, 2 left to run your first simulation"),
    ).toBeInTheDocument();
  });

  // The same render with only the store, to show the case above is a genuine
  // difference and not something the checklist does regardless.
  it("still asks for evaluations when neither the store nor the server has any", () => {
    renderPanels({
      tab: "overview",
      envState: baseEnvState({ evals: [] }),
      serverEnvState: baseEnvState({ evals: [] }),
    });

    expect(screen.getByRole("button", { name: "Add evaluations" })).toBeInTheDocument();
    expect(
      screen.getByText("2 of 5 complete, 3 left to run your first simulation"),
    ).toBeInTheDocument();
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
