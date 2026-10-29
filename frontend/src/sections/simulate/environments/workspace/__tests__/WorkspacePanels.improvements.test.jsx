import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("../overview/OverviewPanel", () => ({
  default: () => <div>overview-body</div>,
}));
vi.mock("../contract/RlContractPanel", () => ({
  default: () => <div>contract-body</div>,
}));
vi.mock("../scenarios/ScenariosStep", () => ({
  default: () => <div>scenarios-body</div>,
}));
vi.mock("../evals/EvalsStep", () => ({ default: () => <div>evals-body</div> }));
vi.mock("../runs/summary/RunsSummary", () => ({
  default: () => <div>runs-body</div>,
}));
vi.mock("../versions/VersionsPanel", () => ({
  default: () => <div>versions-body</div>,
}));
vi.mock("../improvements/ImprovementsPanel", () => ({
  default: () => <div>improvements-body</div>,
}));
vi.mock("src/api/simulate-environments/runs", async (importOriginal) => ({
  ...(await importOriginal()),
  useEnvironmentRuns: () => ({
    runs: [{ id: "r1" }],
    count: 1,
    isLoading: false,
  }),
}));

let fixesValue;
vi.mock("src/api/simulate-environments/fixRuns", () => ({
  useEnvironmentFixRuns: () => fixesValue,
}));

const { default: WorkspacePanels } = await import("../WorkspacePanels");

const ENV = {
  id: "env-1",
  name: "Refund Copilot",
  versioning: { enabled: true },
};

function renderPanels(tab = "overview") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <WorkspacePanels
          env={ENV}
          envState={{ scenarios: [], evals: [], runs: [] }}
          patch={vi.fn()}
          tab={tab}
          onTabChange={vi.fn()}
          counts={{ scenarios: 0, evals: 0 }}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Improvements tab", () => {
  beforeEach(() => {
    fixesValue = { fixes: [], count: 0, isLoading: false };
  });

  it("is hidden until the environment has a fix", () => {
    renderPanels();
    expect(screen.queryByRole("tab", { name: /Improvements/ })).toBeNull();
  });

  it("shows with its count once there are fixes", () => {
    fixesValue = { fixes: [{}, {}, {}], count: 3, isLoading: false };
    renderPanels();
    const tab = screen.getByRole("tab", { name: /Improvements/ });
    expect(within(tab).getByText("3")).toBeInTheDocument();
  });

  it("sits directly after Runs", () => {
    fixesValue = { fixes: [{}], count: 1, isLoading: false };
    renderPanels();
    const labels = screen.getAllByRole("tab").map((t) => t.textContent);
    const runs = labels.findIndex((l) => l.startsWith("Runs"));
    expect(labels[runs + 1]).toMatch(/^Improvements/);
  });

  it("keeps the tab for ?tab=improvements while the list loads", () => {
    fixesValue = { fixes: [], count: 0, isLoading: true };
    renderPanels("improvements");
    expect(screen.getByRole("tab", { name: /Improvements/ })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByText("improvements-body")).toBeInTheDocument();
  });

  it("renders the Improvements panel on its tab", () => {
    fixesValue = { fixes: [{}], count: 1, isLoading: false };
    renderPanels("improvements");
    expect(screen.getByText("improvements-body")).toBeInTheDocument();
  });
});
