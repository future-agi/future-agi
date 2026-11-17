/* eslint-env node */
/* eslint-disable react/prop-types */
// REGISTRY-CENSUS §A/§B: the classic vehicle must not activate DSX children.
import React from "react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fireEvent, render, screen } from "@testing-library/react";
import { useForm } from "react-hook-form";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { madeRealSurfaceIds } from "../madeReal";
import {
  isDatasetTabShown,
  isSurfaceShown,
  setLaunchOverrideForTest,
} from "../launchSwitch";
import { surfacesOfOption } from "../launchSurfaces";

// Both switch states, the way launch-gates.test.jsx pins them: the override
// launchSwitch reads and the export the product modules read. The census
// cases below are the build with unreleased parts hidden.
const unreleased = vi.hoisted(() => ({ shown: false }));
vi.mock("../launchSwitch", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    get SHOW_UNRELEASED_PARTS() {
      return unreleased.shown;
    },
  };
});

vi.mock("src/sections/common/RunSources/SourcePicker", () => ({
  default: () => <div data-testid="new-source-picker" />,
}));
vi.mock("src/sections/develop-detail/Experiment/RunTargetPicker", () => ({
  default: ({ anchorEl, onPick }) =>
    anchorEl ? (
      <button
        onClick={() =>
          onPick({ id: "p1", name: "Saved prompt", model: "model-1" })
        }
      >
        Saved prompt
      </button>
    ) : null,
}));
vi.mock(
  "src/sections/develop-detail/Experiment/StepperComponentExperiment/AgentPromptRenderer",
  () => ({
    default: ({ prompt, onRemove }) => (
      <div>
        {prompt.name}
        <button onClick={onRemove}>Remove prompt</button>
      </div>
    ),
  }),
);
vi.mock("src/sections/common/Datasets/datasetKind", () => ({
  useDatasetKind: () => ({ kind: "dataset" }),
}));
vi.mock("src/api/develop/r2", () => ({
  useRunConfigs: () => ({ data: null }),
}));
vi.mock("src/sections/develop-detail/Experiment/reservedIds", () => ({
  useReservedIds: () => ({ data: null }),
}));
vi.mock("src/api/develop/prompt", () => ({
  usePromptExecutions: () => ({ data: null }),
}));
vi.mock("src/api/develop/develop-detail", () => ({
  useGetDatasetDetail: () => ({ data: { column_config: [] } }),
}));
vi.mock("src/sections/develop-detail/DatasetSummaryTab/summaryApi", () => ({
  useEvalStats: () => ({ data: [] }),
  flattenEvalStats: () => [],
  useSummarySplits: vi.fn(() => []),
  useAnnotationSummary: vi.fn(() => ({ data: {} })),
}));
vi.mock(
  "src/sections/develop-detail/DatasetSummaryTab/EvalsCard/EvalsCard",
  () => ({ default: () => <div>Classic evaluation scores</div> }),
);
vi.mock("src/sections/develop-detail/DatasetSummaryTab/PromptCard", () => ({
  default: () => <div>Classic prompt scores</div>,
}));
vi.mock("src/sections/develop-detail/DatasetSummaryTab/HumanReviewTab", () => ({
  default: () => <div data-testid="new-human-review" />,
}));
vi.mock(
  "src/sections/develop-detail/DatasetSummaryTab/DatasetVerdictCard",
  () => ({ default: () => <div data-testid="new-verdict" /> }),
);
vi.mock("src/sections/develop-detail/DatasetSummaryTab/SplitSelect", () => ({
  default: () => <div data-testid="new-summary-split" />,
}));
vi.mock("src/sections/develop-detail/DatasetSummaryTab/FilterSummary", () => ({
  default: () => null,
}));

import ConfigureStepLLM from "src/sections/develop-detail/Experiment/StepperComponentExperiment/ConfigureStepLLM";
import DatasetSummaryTab from "src/sections/develop-detail/DatasetSummaryTab/DatasetSummaryTab";
import {
  useSummarySplits,
  useAnnotationSummary,
} from "src/sections/develop-detail/DatasetSummaryTab/summaryApi";
// Read source, not Vite's client asset-URL glob (which misses nested paths).
const read = (p) => readFileSync(resolve(process.cwd(), "src", p), "utf8");
function Form() {
  const form = useForm({ defaultValues: { promptConfig: [] } });
  return <ConfigureStepLLM {...form} />;
}

beforeEach(() => {
  vi.clearAllMocks();
  unreleased.shown = false;
  setLaunchOverrideForTest({
    launch: true,
    showUnreleased: false,
    experimentsBasic: false,
    madeReal: madeRealSurfaceIds(),
  });
});
afterEach(() => setLaunchOverrideForTest(null));

describe("census classic capability restoration", () => {
  it("keeps both dataset tabs enabled without claiming any NEW experiment surface", () => {
    expect(isDatasetTabShown("summary")).toBe(true);
    expect(isDatasetTabShown("experiments")).toBe(true);
    for (const id of [
      "exp.new",
      "exp.new.kindMenu",
      "exp.new.record",
      "exp.runControl",
      "exp.rerun",
      "exp.compare",
      "exp.results.summary",
      "exp.results.matrix",
      "dataset.tab.summary",
    ])
      expect(isSurfaceShown(id), id).toBe(false);
    for (const option of [
      "card:prompt",
      "knob:prompt:models",
      "knob:prompt:version",
      "knob:prompt:model-params",
      "exp:name",
      "wiz:add-evaluation",
      "wiz:card-edit",
      "wiz:card-remove",
    ])
      expect(
        surfacesOfOption(option).every((s) => isSurfaceShown(s.id)),
        option,
      ).toBe(true);
    for (const option of [
      "picker:recommended",
      "picker:retest-strip",
      "test:card",
      "review:planned-ledger",
      "action:create-toast-undo",
      "knob:prompt:tool-modes",
      "more:carry-over",
    ])
      expect(
        surfacesOfOption(option).some((s) => !isSurfaceShown(s.id)),
        option,
      ).toBe(true);
  });

  it("adds and removes a saved prompt without mounting SourcePicker or enabling Agent graph", () => {
    render(
      <MemoryRouter>
        <Form />
      </MemoryRouter>,
    );
    expect(screen.queryByTestId("new-source-picker")).toBeNull();
    expect(screen.getByRole("button", { name: "Agent graph" })).toBeDisabled();
    expect(
      screen.getByText(
        "Agent graph experiments are not available in this release.",
      ),
    ).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Select prompt" }));
    fireEvent.click(screen.getByRole("button", { name: "Saved prompt" }));
    expect(screen.getByText("Saved prompt")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Remove prompt" }));
    expect(screen.queryByText("Saved prompt")).toBeNull();
  });

  it("renders the working dataset Evals and Prompt summaries without new reads or verdicts", () => {
    render(
      <MemoryRouter initialEntries={["/dashboard/develop/d1?tab=summary"]}>
        <Routes>
          <Route
            path="/dashboard/develop/:dataset"
            element={<DatasetSummaryTab />}
          />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByText("Classic evaluation scores")).toBeVisible();
    fireEvent.click(screen.getByRole("tab", { name: "Prompt" }));
    expect(screen.getByText("Classic prompt scores")).toBeVisible();
    for (const id of ["new-verdict", "new-human-review", "new-summary-split"])
      expect(screen.queryByTestId(id)).toBeNull();
    expect(useSummarySplits).not.toHaveBeenCalled();
    expect(useAnnotationSummary).not.toHaveBeenCalled();
  });

  it("separates legacy type selection, input mapping and prompt configuration from new children", () => {
    const configure = read(
      "sections/develop-detail/Experiment/StepperComponentExperiment/ConfigureStepExperimentCreation.jsx",
    );
    expect(configure).toMatch(
      /!isSurfaceShown\("exp\.new"\)[\s\S]*?<ExperimentTypeSelect/,
    );
    // Both wizards offer every experiment type: no filter drops TTS or STT.
    expect(configure).not.toMatch(/\["tts", "stt"\]\.includes/);
    const card = read(
      "sections/develop-detail/Experiment/StepperComponentExperiment/AgentPromptRenderer.jsx",
    );
    expect(card).toMatch(/isSurfaceShown\("exp\.new"\) && !readOnlyTarget/);
    expect(card).toMatch(
      /isSurfaceShown\("exp\.new"\) &&\s*\(?\s*<ToolModesBlock/,
    );
    const data = read(
      "sections/develop-detail/Experiment/StepperComponentExperiment/DataStep.jsx",
    );
    expect(data).toMatch(
      /isSurfaceShown\("exp\.new"\)[\s\S]*?<ClassicExperimentData/,
    );
    const classic = read(
      "sections/develop-detail/Experiment/ClassicExperimentData.jsx",
    );
    expect(classic).not.toMatch(
      /SourcePicker|TryOnOneRow|VariableMappingTable|ReadsOnSampleTable|SplitSelect|useSelectedSampleContext/,
    );
    expect(classic).toContain("variableMapping");
  });
});

// As shipped (unreleased parts shown) both dataset tabs stay: the Experiments
// tab shows the runs view and, under it, the experiments the runs view cannot
// show in full (ExperimentTabClassic/__tests__/other-experiments-tab.test.jsx).
describe("census classic capability restoration as shipped", () => {
  beforeEach(() => {
    unreleased.shown = true;
    setLaunchOverrideForTest({
      launch: true,
      experimentsBasic: true,
      madeReal: madeRealSurfaceIds(),
    });
  });

  it("keeps both dataset tabs enabled", () => {
    expect(isDatasetTabShown("summary")).toBe(true);
    expect(isDatasetTabShown("experiments")).toBe(true);
  });
});
