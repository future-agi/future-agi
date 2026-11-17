/* eslint-disable react/prop-types */
import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import {
  BrowserRouter,
  MemoryRouter,
  Route,
  Routes,
  useLocation,
} from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  isSurfaceShown,
  setLaunchOverrideForTest,
} from "src/launch/launchSwitch";

const h = vi.hoisted(() => ({
  runs: [],
  pinned: "a",
  analysis: vi.fn(),
  pin: vi.fn(),
  unpin: vi.fn(),
  options: [],
  commands: vi.fn(),
}));
vi.mock("src/sections/common/CommandPalette/commandRegistry", () => ({
  useRegisterCommands: h.commands,
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("src/api/develop/runs", () => ({
  useDatasetRuns: () => ({
    data: { results: h.runs, pinned_run_id: h.pinned },
  }),
  useExperimentDetail: () => ({
    data: { id: "e1", dataset_id: "d1", runs: h.runs },
  }),
  usePinBaseline: () => ({ mutate: h.pin }),
  useUnpinBaseline: () => ({ mutate: h.unpin }),
}));
vi.mock("src/api/develop/r2", () => ({
  useRunConfigs: () => ({ data: { results: h.runs } }),
}));
vi.mock("src/api/develop/develop-detail", () => ({
  useDevelopDatasetList: () => ({
    data: [{ datasetId: "d1", name: "Example dataset" }],
  }),
}));
vi.mock("src/api/develop/analysis", () => ({
  useCandidateRunId: () => "b",
  useRunAnalysis: (...args) => {
    h.analysis(...args);
    return { data: undefined, isLoading: false };
  },
}));
vi.mock("src/api/develop/experiment-stats", () => ({
  useExperimentStats: () => ({ data: undefined, isPending: true }),
}));
vi.mock("../ExperimentSummary/ChooseWinnerDrawer", () => ({
  default: () => null,
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("../ExperimentContextProvider", () => ({
  default: ({ children }) => children,
}));
// The real header's primary and menu doors are covered in linkedRunsHeader.
vi.mock("../ExperimentRow", () => ({ default: () => null }));
vi.mock("../ExperimentBar", () => ({ default: () => null }));
vi.mock("../results/RunResultsView", () => ({ default: () => null }));
vi.mock("../matrix/MatrixView", () => ({ default: () => null }));
vi.mock("../paths/PathsView", () => ({ default: () => null }));
vi.mock("../iterate/LineageRibbon", () => ({ default: () => null }));
vi.mock("../iterate/IterateButton", () => ({
  default: () => <button>Test a fix</button>,
}));
// A sentinel mounts even for a supplied iterate URL. The host must never
// mount it, rather than relying on a collapsed panel to hide a keyboard door.
vi.mock("../iterate/IterateDock", () => ({
  default: () => <div data-testid="iterate-dock" />,
}));
vi.mock("../iterate/IterateResultBanner", () => ({
  default: () => <div data-testid="iterate-banner" />,
}));
vi.mock("src/sections/develop-detail/Experiment/RunActionConfirmHost", () => ({
  default: () => null,
}));
vi.mock(
  "src/sections/develop-detail/Experiment/ExternalRunWaitingBanner",
  () => ({
    default: () => null,
  }),
);
vi.mock("src/sections/common/Access/DatasetAccessGate", () => ({
  DatasetDeniedPage: () => null,
}));
vi.mock("src/sections/common/Runs/ConfigDiffDetail", () => ({
  default: ({ chip, a, b }) => (
    <output data-testid="config-diff-detail">
      {`${a.run_id},${b.run_id}:${chip.axis}`}
    </output>
  ),
}));
vi.mock("src/sections/common/Runs/configDiff", async (importOriginal) => ({
  ...(await importOriginal()),
  configDiff: () => [{ axis: "model", label: "Model A → B", text: "A → B" }],
  configDiffSentence: () => "Changed model",
}));
vi.mock("src/sections/common/Runs/isolate", () => ({
  isolateOptions: () => ({ options: h.options }),
}));

import CompareWrapperView from "../CompareWrapperView";
import ExperimentWrapperView from "../ExperimentWrapperView";
import ExperimentAnalysisView from "../ExperimentSummary/Analysis/ExperimentAnalysisView";
import BaselinePicker from "src/sections/common/Runs/BaselinePicker";

function LocationProbe() {
  const { pathname, search } = useLocation();
  return (
    <output data-testid="location">
      {pathname}
      {search}
    </output>
  );
}

const query = "?runs=a,b&baseline=a&cand=b&iterate=b";
function mount(host) {
  const path =
    host === "compare"
      ? "/dashboard/develop/d1/compare/summary"
      : "/dashboard/develop/experiment/e1/summary";
  // Results URL writes merge with browser history, as in the app host.
  window.history.replaceState({}, "", `${path}${query}`);
  return render(
    <BrowserRouter>
      <Routes>
        {["summary", "data"].map((tab) => (
          <Route
            key={tab}
            path={`/dashboard/develop/:dataset/compare/${tab}`}
            element={<CompareWrapperView tab={tab} />}
          />
        ))}
        <Route
          path="/dashboard/develop/experiment/:experimentId"
          element={<ExperimentWrapperView />}
        >
          <Route
            path="summary"
            element={
              <ExperimentAnalysisView
                datasetId="d1"
                runIds={["a", "b"]}
                baselineId="a"
                mode="experiment"
              />
            }
          />
        </Route>
      </Routes>
      <LocationProbe />
    </BrowserRouter>,
  );
}

let previousLocation;
beforeEach(() => {
  previousLocation = {
    href: window.location.href,
    state: window.history.state,
  };
  setLaunchOverrideForTest({
    launch: true,
    experimentsBasic: true,
    showUnreleased: false,
  });
  h.analysis.mockClear();
  h.commands.mockClear();
  h.pin.mockClear();
  h.unpin.mockClear();
  h.pinned = "a";
  h.runs = ["a", "b"].map((id, index) => ({
    run_id: id,
    dataset_id: "d1",
    experiment_id: "e1",
    label: `#${index + 1}`,
    name: `Prompt ${id}`,
    status: "Completed",
    config: { kind: "prompt", model: { id } },
  }));
  h.options = [
    { axis: "model", action: "run", text: "Model only: not run yet" },
  ];
});
afterEach(() => {
  cleanup();
  window.history.replaceState(
    previousLocation.state,
    "",
    previousLocation.href,
  );
  setLaunchOverrideForTest(null);
});

describe("accepted Results hosts with unclaimed mutations", () => {
  it("never registers the unclaimed Iterate command from another host", async () => {
    const { default: IterateButton } = await vi.importActual(
      "../iterate/IterateButton",
    );
    render(
      <MemoryRouter
        initialEntries={[`/dashboard/develop/d1/compare/summary${query}`]}
      >
        <IterateButton datasetId="d1" runIds={["a", "b"]} baselineId="a" />
      </MemoryRouter>,
    );
    expect(screen.queryByTestId("iterate-button")).toBeNull();
    expect(h.commands).not.toHaveBeenCalled();
  });

  it.each(["compare", "experiment"])(
    "keeps %s Summary readable without Iterate doors or unsupported views",
    (host) => {
      expect(isSurfaceShown("exp.results.summary")).toBe(true);
      expect(isSurfaceShown("exp.results.addToDataset")).toBe(true);
      expect(isSurfaceShown("exp.results.iterateDock")).toBe(false);
      expect(isSurfaceShown("exp.results.matrix")).toBe(false);
      mount(host);
      expect(screen.getByRole("heading", { name: "Summary" })).toBeTruthy();
      expect(h.analysis.mock.calls.at(-1)).toEqual([
        "d1",
        {
          runIds: ["a", "b"],
          baselineId: "a",
          filters: [],
          groupBy: undefined,
          bmetric: undefined,
          cand: "b",
          wmetric: undefined,
          include: "root_causes",
          enabled: true,
          errorHandled: true,
        },
      ]);
      expect(
        screen
          .getByRole("link", { name: "Open Results data" })
          .getAttribute("href"),
      ).toBe(
        screen.getByTestId("location").textContent.replace("/summary", "/data"),
      );
      expect(screen.getByTestId("config-diff-strip")).toBeTruthy();
      // The tooltip supplies the accessible name; the action keeps its copy.
      const whatDiffers = screen.getByRole("button", {
        name: "Every setting that differs between #1 and #2",
      });
      expect(whatDiffers.textContent).toBe("What differs");
      expect(screen.queryByRole("button", { name: "Isolate" })).toBeNull();
      expect(
        screen.queryByRole("button", { name: /Test a fix|Run it/ }),
      ).toBeNull();
      expect(screen.queryByTestId("iterate-dock")).toBeNull();
      expect(screen.queryByTestId("iterate-banner")).toBeNull();
      fireEvent.keyDown(document, { key: "i" });
      expect(screen.queryByTestId("iterate-dock")).toBeNull();
      expect(
        screen.queryByText(/Classification|Derived metrics|Run metrics/i),
      ).toBeNull();
      expect(h.pin).not.toHaveBeenCalled();
      expect(h.unpin).not.toHaveBeenCalled();
      fireEvent.click(whatDiffers);
      expect(screen.getByTestId("config-diff-detail").textContent).toBe(
        "a,b:model",
      );
    },
  );

  it.each(["compare", "experiment"])(
    "retains read-only isolate comparison in the %s host",
    (host) => {
      h.options.push({
        axis: "prompt",
        action: "compare",
        text: "Prompt alone",
        run: h.runs[1],
      });
      mount(host);
      fireEvent.click(screen.getByRole("button", { name: "Isolate" }));
      expect(screen.queryByRole("button", { name: "Run it" })).toBeNull();
      fireEvent.click(screen.getByRole("button", { name: "Compare" }));
      const [path, search] = screen
        .getByTestId("location")
        .textContent.split("?");
      expect(path).toBe(
        host === "compare"
          ? "/dashboard/develop/d1/compare/summary"
          : "/dashboard/develop/d1/compare/data",
      );
      const params = new URLSearchParams(search);
      expect(params.get("runs")).toBe("a,b");
      expect(params.get("baseline")).toBe("a");
      // Isolate clears the old candidate; the compare host writes its new
      // default back into the URL. Mount that destination for both doors.
      expect(params.get("cand")).toBe("b");
      expect(screen.queryByTestId("iterate-dock")).toBeNull();
      expect(screen.queryByTestId("iterate-banner")).toBeNull();
    },
  );

  it.each(["a", "b"])(
    "hides baseline pin and unpin for selected run %s without hiding view selection",
    (value) => {
      const onChange = vi.fn();
      render(
        <BaselinePicker
          datasetId="d1"
          value={value}
          pinnedRunId="a"
          runsInView={h.runs}
          restrictTo={["a", "b"]}
          allowNone
          onChange={onChange}
        />,
      );
      fireEvent.click(screen.getByRole("button", { name: /Change baseline/ }));
      expect(
        screen.queryByRole("button", {
          name: /Pin .*dataset baseline|Unpin .*dataset baseline/,
        }),
      ).toBeNull();
      fireEvent.click(screen.getByRole("button", { name: /None/ }));
      expect(onChange).toHaveBeenCalledWith("none", { scope: "view" });
      expect(h.pin).not.toHaveBeenCalled();
      expect(h.unpin).not.toHaveBeenCalled();
    },
  );
});
