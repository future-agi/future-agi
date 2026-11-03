// Regression tests for the experiment snake_case hotfix: Data tab toolbar
// (Show Diff and the Display menu's "View all prompts").
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import {
  EXPERIMENT_ID,
  experimentDetailAgent,
  experimentDetailLlm,
} from "./experiment-snake-case.fixtures";

vi.mock("react-router", () => ({
  useParams: () => ({ experimentId: EXPERIMENT_ID }),
}));
vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  endpoints: { develop: { experiment: { downloadExperiment: () => "/x/" } } },
}));
// The edit drawer is a separate flow; it is not under test here.
vi.mock("src/sections/develop-detail/Experiment/RunExperiment", () => ({
  default: () => null,
}));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/components/iconify", () => ({ default: () => null }));

import { shouldShowDiffModeButton } from "../ExperimentData/Common";
import ExperimentBarDataRightSection from "../ExperimentBarRightSection/ExperimentBarDataRightSection";

const renderToolbar = (experimentData, outputFormat = "string") =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ExperimentBarDataRightSection
        gridApiRef={{ current: null }}
        columns={[]}
        outputFormat={outputFormat}
        experimentData={experimentData}
      />
    </QueryClientProvider>,
  );

describe("Data tab toolbar reads the experiment detail's snake_case keys", () => {
  it("D3: offers Show Diff for an LLM experiment with a baseline column and string output", () => {
    // ExperimentDetailV2Serializer sends column_id / experiment_type /
    // agent_configs / prompt_configs[].output_format
    // (serializers/experiments.py:796-818, 681-710).
    expect(shouldShowDiffModeButton(experimentDetailLlm)).toBe(true);
    expect(shouldShowDiffModeButton(experimentDetailAgent)).toBe(true);
    expect(
      shouldShowDiffModeButton({ ...experimentDetailLlm, column_id: null }),
    ).toBe(false);
    expect(
      shouldShowDiffModeButton({
        ...experimentDetailLlm,
        prompt_configs: experimentDetailLlm.prompt_configs.map((c) => ({
          ...c,
          output_format: "json",
        })),
      }),
    ).toBe(false);

    renderToolbar(experimentDetailLlm);
    expect(screen.getByText("Show Diff")).toBeInTheDocument();
  });

  it("D5: shows 'View all prompts' in the Display menu for an agent experiment", () => {
    // agent_configs: serializers/experiments.py:837-848 (snake key)
    renderToolbar(experimentDetailAgent);
    fireEvent.click(screen.getByText("Display"));
    expect(screen.getByText("View all prompts")).toBeInTheDocument();
  });
});
