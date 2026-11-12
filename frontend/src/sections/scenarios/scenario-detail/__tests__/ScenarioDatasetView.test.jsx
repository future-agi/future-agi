import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within, fireEvent } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import ScenarioDatasetView from "../ScenarioDatasetView";

const mocks = vi.hoisted(() => ({
  useGetScenarioDetail: vi.fn(),
  role: "Owner",
}));

vi.mock("src/api/scenarios/scenarios", () => ({
  useGetScenarioDetail: (...args) => mocks.useGetScenarioDetail(...args),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: mocks.role }),
}));
vi.mock("src/components/loading-screen", () => ({
  LoadingScreen: () => null,
}));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock(
  "src/components/scenarios/CustomCellRenderers/ChipCellRenderer",
  () => ({
    getChipConfig: (value) => ({ label: value ?? "-", icon: null }),
  }),
);
// The rows table and its dialogs are their own screens; this page only
// decides whether they show.
vi.mock("src/sections/develop-detail/DataTab/DevelopDataV2", () => ({
  default: () => <div data-testid="scenario-rows" />,
}));
vi.mock("src/sections/develop-detail/DevelopDetailProvider", () => ({
  default: ({ children }) => <>{children}</>,
}));
vi.mock("../GraphPreview", () => ({ default: () => null }));
vi.mock("../PromptPreview", () => ({ default: () => null }));
vi.mock("../AddRowScenario", () => ({ default: () => null }));
vi.mock("../AddColumnScenario", () => ({ default: () => null }));
vi.mock("../ScenarioDetailRightSection", () => ({ default: () => null }));

// What the walk saw: a prompt-source scenario whose generation failed before
// any rows were made, from before failures recorded a reason.
const failedScenario = {
  id: "scn-1",
  name: "Refund calls",
  status: "Failed",
  scenario_type: "dataset",
  agent_type: "prompt",
  dataset: null,
  dataset_id: null,
  dataset_rows: 0,
  created_at: null,
  graph: {},
  prompts: [],
};

const showScenario = (scenario) =>
  mocks.useGetScenarioDetail.mockReturnValue({
    data: scenario,
    isLoading: false,
    error: null,
  });

const renderView = () =>
  render(
    <MemoryRouter initialEntries={["/dashboard/simulate/scenarios/scn-1"]}>
      <Routes>
        <Route
          path="/dashboard/simulate/scenarios/create"
          element={<p>Create scenario page</p>}
        />
        <Route
          path="/dashboard/simulate/scenarios/:scenarioId"
          element={<ScenarioDatasetView />}
        />
      </Routes>
    </MemoryRouter>,
  );

beforeEach(() => {
  mocks.useGetScenarioDetail.mockReset();
  mocks.role = "Owner";
});

describe("ScenarioDatasetView: a scenario whose generation failed", () => {
  it("says what happened in plain words instead of a generic error", () => {
    showScenario(failedScenario);
    renderView();

    const notice = screen.getByRole("alert");
    expect(
      within(notice).getByText("Scenario generation failed"),
    ).toBeInTheDocument();
    // With no recorded reason the page says so rather than inventing one.
    expect(
      within(notice).getByText(/No reason was recorded for this failure\./),
    ).toBeInTheDocument();
    expect(
      within(notice).getByText(
        /Create the scenario again\. If it keeps failing, try a different agent or prompt version\./,
      ),
    ).toBeInTheDocument();

    expect(
      screen.getByText("No scenarios were generated."),
    ).toBeInTheDocument();
    expect(screen.getByText("0 (generation failed)")).toBeInTheDocument();
    expect(screen.queryByText(/There was an error generating/)).toBeNull();
  });

  it("offers Create again, which opens the create scenario page", () => {
    showScenario(failedScenario);
    renderView();

    const button = within(screen.getByRole("alert")).getByRole("button", {
      name: "Create again",
    });
    expect(button).toBeEnabled();
    expect(
      screen.queryByText(
        "Your role can't create scenarios, so ask a teammate who can.",
      ),
    ).toBeNull();

    fireEvent.click(button);
    expect(screen.getByText("Create scenario page")).toBeInTheDocument();
  });

  it("keeps Create again visible but disabled, with one line saying why, for a role that cannot create", () => {
    mocks.role = "Viewer";
    showScenario(failedScenario);
    renderView();

    const notice = screen.getByRole("alert");
    expect(
      within(notice).getByRole("button", { name: "Create again" }),
    ).toBeDisabled();
    expect(
      within(notice).getByText(
        "Your role can't create scenarios, so ask a teammate who can.",
      ),
    ).toBeInTheDocument();
  });

  it("still explains the failure when some rows were made before it stopped", () => {
    showScenario({
      ...failedScenario,
      dataset: "ds-1",
      dataset_id: "ds-1",
      dataset_rows: 3,
    });
    renderView();

    expect(
      within(screen.getByRole("alert")).getByText("Scenario generation failed"),
    ).toBeInTheDocument();
    expect(screen.getByText("3 (generation failed)")).toBeInTheDocument();
    expect(screen.getByTestId("scenario-rows")).toBeInTheDocument();
  });
});

describe("ScenarioDatasetView: a failed scenario with a recorded reason", () => {
  const reason =
    "No working model provider is set up for scenario generation on this " +
    "deployment. Ask your administrator to add one, then create the scenario " +
    "again.";

  it("says why generation failed, in place of the no-reason line and the generic retry advice", () => {
    showScenario({ ...failedScenario, failure_reason: reason });
    renderView();

    const notice = screen.getByRole("alert");
    expect(
      within(notice).getByText("Scenario generation failed"),
    ).toBeInTheDocument();
    expect(within(notice).getByText(reason)).toBeInTheDocument();
    expect(
      within(notice).getByText(
        /Generation stopped before it finished, so the graph, prompt or rows below may be missing\./,
      ),
    ).toBeInTheDocument();
    expect(
      within(notice).queryByText(/No reason was recorded for this failure\./),
    ).toBeNull();
    expect(within(notice).queryByText(/If it keeps failing/)).toBeNull();
    expect(
      within(notice).getByRole("button", { name: "Create again" }),
    ).toBeEnabled();
  });

  it("keeps Create again disabled with the role line when the role cannot create", () => {
    mocks.role = "Viewer";
    showScenario({ ...failedScenario, failure_reason: reason });
    renderView();

    const notice = screen.getByRole("alert");
    expect(within(notice).getByText(reason)).toBeInTheDocument();
    expect(
      within(notice).getByRole("button", { name: "Create again" }),
    ).toBeDisabled();
    expect(
      within(notice).getByText(
        "Your role can't create scenarios, so ask a teammate who can.",
      ),
    ).toBeInTheDocument();
  });

  it("falls back to the no-reason line when the API returns a null reason", () => {
    showScenario({ ...failedScenario, failure_reason: null });
    renderView();

    expect(
      within(screen.getByRole("alert")).getByText(
        /No reason was recorded for this failure\./,
      ),
    ).toBeInTheDocument();
  });
});

describe("ScenarioDatasetView: a scenario that generated", () => {
  it("shows no failure notice and a plain row count", () => {
    showScenario({
      ...failedScenario,
      status: "Completed",
      dataset: "ds-1",
      dataset_id: "ds-1",
      dataset_rows: 12,
    });
    renderView();

    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByText("Scenario generation failed")).toBeNull();
    expect(screen.getByText("12")).toBeInTheDocument();
  });
});
