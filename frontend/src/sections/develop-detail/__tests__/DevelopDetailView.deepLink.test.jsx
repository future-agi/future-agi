import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";
import { afterEach, describe, expect, it, vi } from "vitest";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { madeRealSurfaceIds } from "src/launch/madeReal";

// Critic r3 #1: a cold `?tab=experiments` link must stay on Experiments. The
// page renders for real (DevelopDetailView + use-url-state + the compare
// header's mount effect); every heavy child is a stub.
const { Null } = vi.hoisted(() => ({ Null: () => null }));

vi.mock("../DevelopBar/DevelopBar", () => ({
  default: function DevelopBarProbe({ currentTab }) {
    return <div data-testid="current-tab">{currentTab}</div>;
  },
}));
vi.mock("../DevelopBar/DatasetHeaderBar", () => ({ default: Null }));
// Compare mode: the real CompareDatasetsTop, as DatasetsSelectRow mounts it.
vi.mock("../DatasetsSelectRow", async () => {
  const { default: CompareDatasetsTop } = await import("../CompareDatasetsTop");
  return {
    default: function DatasetsSelectRowStub(props) {
      if (!props.isCompareDataset) return null;
      return (
        <CompareDatasetsTop
          datasetOptions={[]}
          setIsCompareDataset={props.setIsCompareDataset}
          setSelectedDatasetData={props.setSelectedDatasetData}
          setSelectedDatasetsValuesInParent={
            props.setSelectedDatasetsValuesInParent
          }
          setBaseColumn={props.setBaseColumnInParent}
          setIsChooseWinnerSelected={props.setIsChooseWinnerSelected}
          selectedDatasetData={props.selectedDatasetData}
          setCurrentTab={props.setCurrentTab}
          setCompareFromOutSide={props.setCompareFromOutSide}
          setIsChooseWinnerButtonVisible={props.setIsChooseWinnerButtonVisible}
          setIsCommonColumn={props.setIsCommonColumn}
        />
      );
    },
  };
});
vi.mock("../CompareDatasetDrawer", () => ({ default: Null }));
vi.mock("../BaseColumnsDrawer", () => ({ default: Null }));
vi.mock("../RunPrompt/RunPrompt", () => ({ default: Null }));
vi.mock("../Optimization/RunOptimization", () => ({ default: Null }));
vi.mock("../OptimizationTab/OptimizeTab", () => ({ default: Null }));
vi.mock("../Evaluation/EditEvaluation", () => ({ default: Null }));
vi.mock("../ExperimentTab/ExperimentTab", () => ({ default: Null }));
vi.mock("../ExperimentTabClassic/ExperimentTabSwitch", () => ({
  default: Null,
}));
vi.mock("../ExperimentTabClassic/ExperimentTabHeaderSwitch", () => ({
  default: Null,
}));
vi.mock("../ExperimentTab/ExperimentTabHeader", () => ({ default: Null }));
vi.mock("../DevelopBarRightSection/DevelopDataRightSection", () => ({
  default: Null,
}));
vi.mock("../DevelopBarRightSection/DevelopExperimentRightSection", () => ({
  default: Null,
}));
vi.mock("../DevelopBarRightSection/CompareSummaryRightSection", () => ({
  default: Null,
}));
vi.mock("../DevelopBarRightSection/CompareDataRightSection", () => ({
  default: Null,
}));
vi.mock("../DevelopBarRightSection/DevelopBarLeftSection", () => ({
  default: Null,
}));
vi.mock("../DatasetSummaryTab/DatasetSummaryTab", () => ({ default: Null }));
vi.mock("../DatasetSummaryTab/CompareSummaryTab", () => ({ default: Null }));
vi.mock("../AddColumn/AddColumnApiCall/AddColumnApiCall", () => ({
  default: Null,
}));
vi.mock("../AddColumn/ExtractEntities/ExtractEntities", () => ({
  default: Null,
}));
vi.mock("../AddColumn/Classification/Classification", () => ({
  default: Null,
}));
vi.mock("../AddColumn/ExecuteCode/ExecuteCode", () => ({ default: Null }));
vi.mock("../AddColumn/ExtractJsonKey/ExtractJsonKey", () => ({
  default: Null,
}));
vi.mock("../AddColumn/Retrieval/Retrieval", () => ({ default: Null }));
vi.mock("../AddColumn/ConditionalNode/ConditionalNodeV2", () => ({
  default: Null,
}));
vi.mock("../DataTab/CompareData", () => ({ default: Null }));
vi.mock("../DataTab/DevelopDataV2", () => ({ default: Null }));
vi.mock("../DataTab/DevelopEvaluationDrawer", () => ({ default: Null }));
vi.mock("../DataTab/Versions/PendingChangesGate", () => ({ default: Null }));
vi.mock("../Evaluation/RunCompareEvaluation", () => ({ default: Null }));
vi.mock("../Experiment/RunExperiment", () => ({
  default: () => <div data-testid="prompt-create-host" />,
}));
vi.mock("../DevelopDetailProvider", () => ({
  default: ({ children }) => children,
}));
vi.mock("../Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({ refreshGrid: vi.fn() }),
}));
// The linked dataset is in the loaded dataset list (normalized items, as
// useDevelopDatasetList selects them), so the page's availability check
// admits it without a separate read.
vi.mock("src/api/develop/develop-detail", () => ({
  useDevelopDatasetList: () => ({
    data: [{ datasetId: "ds-1", name: "Golden set" }],
    isLoading: false,
  }),
}));
vi.mock("src/api/develop/dataset-compare", () => ({
  useDeleteCompare: () => ({ mutate: vi.fn() }),
}));
vi.mock("src/utils/Mixpanel", () => ({
  Events: {},
  PropertyName: {},
  trackEvent: vi.fn(),
}));
vi.mock("src/utils/axios", () => ({
  default: { post: vi.fn(), get: vi.fn() },
  endpoints: { annotation: {} },
}));

const { default: DevelopDetailView } = await import("../DevelopDetailView");

function LocationProbe() {
  const { search } = useLocation();
  return <div data-testid="search">{search}</div>;
}

function renderAt(url, state) {
  const client = new QueryClient();
  return render(
    <HelmetProvider>
      <QueryClientProvider client={client}>
        <MemoryRouter
          initialEntries={[
            {
              pathname: url.split("?")[0],
              search: `?${url.split("?")[1] ?? ""}`,
              state,
            },
          ]}
        >
          <Routes>
            <Route
              path="/dashboard/develop/:dataset"
              element={
                <>
                  <DevelopDetailView />
                  <LocationProbe />
                </>
              }
            />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    </HelmetProvider>,
  );
}

const settle = () =>
  act(async () => {
    await new Promise((r) => setTimeout(r, 50));
  });

afterEach(() => setLaunchOverrideForTest(null));

describe("DevelopDetailView deep links (critic r3 #1)", () => {
  it("keeps the classic tab and prompt-create host mounted under launch defaults", async () => {
    setLaunchOverrideForTest({
      launch: true,
      experimentsBasic: false,
      madeReal: madeRealSurfaceIds(),
    });
    renderAt("/dashboard/develop/ds-1?tab=experiments&new_experiment=1");
    await settle();
    expect(screen.getByTestId("current-tab")).toHaveTextContent("experiments");
    expect(screen.getByTestId("prompt-create-host")).toBeInTheDocument();
    expect(screen.getByTestId("search")).toHaveTextContent("new_experiment=1");
  });
  it("a cold ?tab=experiments link stays on Experiments", async () => {
    renderAt("/dashboard/develop/ds-1?tab=experiments");
    await settle();
    expect(screen.getByTestId("current-tab").textContent).toBe("experiments");
    expect(screen.getByTestId("search").textContent).toContain(
      "tab=experiments",
    );
  });

  it("an invalid compare state on mount leaves compare without moving the tab", async () => {
    renderAt("/dashboard/develop/ds-1?tab=summary", {
      isCompare: true,
      selectedDatasetsValues: [],
    });
    await settle();
    expect(screen.getByTestId("current-tab").textContent).toBe("summary");
    expect(screen.getByTestId("search").textContent).toContain("tab=summary");
  });

  it("removing datasets down to one still leaves compare for Data", async () => {
    renderAt("/dashboard/develop/ds-1?tab=summary", {
      isCompare: true,
      selectedDatasetsValues: ["Golden set", "Prod sample"],
    });
    await settle();
    expect(screen.getByTestId("current-tab").textContent).toBe("summary");
    fireEvent.click(
      screen.getByRole("button", {
        name: "Remove Prod sample from the comparison",
      }),
    );
    await settle();
    expect(screen.getByTestId("current-tab").textContent).toBe("data");
    expect(screen.getByTestId("search").textContent).toContain("tab=data");
  });
});
