import { describe, it, expect, vi } from "vitest";
import { render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import WorkspacePanels from "../WorkspacePanels";
import { counts } from "../helpers/workspaceGaps";

// The tab bodies are not under test here; only the tab rail is.
vi.mock("../overview/OverviewPanel", () => ({ default: () => <div>overview-body</div> }));
vi.mock("../contract/RlContractPanel", () => ({ default: () => <div>contract-body</div> }));
vi.mock("../scenarios/ScenariosStep", () => ({ default: () => <div>scenarios-body</div> }));
vi.mock("../evals/EvalsStep", () => ({ default: () => <div>evals-body</div> }));
vi.mock("../runs/summary/RunsSummary", () => ({ default: () => <div>runs-body</div> }));
vi.mock("../versions/VersionsPanel", () => ({ default: () => <div>versions-body</div> }));
vi.mock("../settings/SettingsPanel", () => ({ default: () => <div>settings-body</div> }));

// Versioning on, so the Versions tab (shown only in some states) is in the rail.
const ENV = { id: "env-1", name: "Refund Copilot", surface: "voice", versioning: { enabled: true } };

// One scenario, four evals and two runs: the report's "Scenarios 1",
// "Evaluations 4", "Runs 2".
const envState = {
  agent: { name: "Support agent" },
  scenarios: [{ id: "s1" }],
  evals: [{ id: "e1" }, { id: "e2" }, { id: "e3" }, { id: "e4" }],
  runs: [{ id: "r1" }, { id: "r2" }],
};

function renderPanels(props = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <WorkspacePanels
          env={ENV}
          envState={envState}
          patch={vi.fn()}
          tab="overview"
          onTabChange={vi.fn()}
          counts={counts(envState)}
          {...props}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// Longer than the tooltip's 400ms enter delay.
const waitPastEnterDelay = () =>
  act(() => new Promise((resolve) => setTimeout(resolve, 700)));

const TAB_NAMES = [
  ["Overview", "Overview"],
  ["Contract", "Contract"],
  ["Scenarios", "Scenarios 1"],
  ["Evaluations", "Evaluations 4"],
  ["Runs", "Runs 2"],
  ["Versions", "Versions"],
  ["Settings", "Settings"],
];

describe("workspace tab rail tooltips", () => {
  it("keeps every tab's label and count", () => {
    renderPanels();
    TAB_NAMES.forEach(([, name]) => {
      expect(screen.getByRole("tab", { name })).toBeInTheDocument();
    });
  });

  it.each(TAB_NAMES)("shows no tooltip when hovering the %s tab", async (label) => {
    const user = userEvent.setup();
    renderPanels();
    await user.hover(screen.getByText(label));
    await waitPastEnterDelay();
    expect(screen.queryByRole("tooltip")).toBeNull();
    expect(screen.queryByText(/What this environment is made of/)).toBeNull();
  });

  it("shows no tooltip when a tab gets keyboard focus", async () => {
    const user = userEvent.setup();
    renderPanels();
    await user.tab();
    await user.keyboard("{ArrowRight}");
    await waitPastEnterDelay();
    expect(screen.getByRole("tab", { name: "Contract" })).toHaveFocus();
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("shows no tooltip on a tab that carries a setup-gap dot", async () => {
    const user = userEvent.setup();
    renderPanels({
      gapsByTab: { evals: [{ id: "no-evals", title: "No evaluations added" }] },
    });
    await user.hover(screen.getByText("Evaluations"));
    await waitPastEnterDelay();
    expect(screen.queryByRole("tooltip")).toBeNull();
    expect(screen.queryByText(/These decide whether each task passed/)).toBeNull();
  });
});
