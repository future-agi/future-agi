/* eslint-disable react/prop-types */
// Restore row 8: Dataset > Summary offers the Prompt sub-tab on every
// dataset, as production does, including one with no Run Prompt column.
// Human review stays conditional (shown only when there is review to show).
import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

vi.mock("src/api/develop/develop-detail", () => ({
  // A dataset whose columns include no Run Prompt column.
  useGetDatasetDetail: () => ({
    data: {
      column_config: [
        { id: "c1", name: "question", origin_type: "others" },
        { id: "c2", name: "answer", origin_type: "others" },
      ],
    },
  }),
}));
vi.mock("src/sections/develop-detail/DatasetSummaryTab/summaryApi", () => ({
  useEvalStats: () => ({ data: [] }),
  flattenEvalStats: () => [],
  useSummarySplits: () => [],
  // Loaded, with nothing reviewed and no queue.
  useAnnotationSummary: () => ({
    data: { review: { reviewed_rows: 0 }, judge_agreement: [] },
    isLoading: false,
  }),
}));
vi.mock(
  "src/sections/develop-detail/DatasetSummaryTab/EvalsCard/EvalsCard",
  () => ({ default: () => <div data-testid="summary-evals" /> }),
);
vi.mock("src/sections/develop-detail/DatasetSummaryTab/PromptCard", () => ({
  default: () => <div data-testid="summary-prompt" />,
}));
vi.mock("src/sections/develop-detail/DatasetSummaryTab/HumanReviewTab", () => ({
  default: () => <div data-testid="summary-human" />,
}));
vi.mock(
  "src/sections/develop-detail/DatasetSummaryTab/DatasetVerdictCard",
  () => ({ default: () => null }),
);
vi.mock("src/sections/develop-detail/DatasetSummaryTab/SplitSelect", () => ({
  default: () => null,
}));
vi.mock("src/sections/develop-detail/DatasetSummaryTab/FilterSummary", () => ({
  default: () => null,
}));

import DatasetSummaryTab from "src/sections/develop-detail/DatasetSummaryTab/DatasetSummaryTab";

const renderSummary = () =>
  render(
    <MemoryRouter initialEntries={["/dashboard/develop/d1?tab=summary"]}>
      <Routes>
        <Route
          path="/dashboard/develop/:dataset"
          element={<DatasetSummaryTab setCurrentTabs={() => {}} />}
        />
      </Routes>
    </MemoryRouter>,
  );

describe("Dataset Summary: Prompt sub-tab (restore row 8)", () => {
  it("lists Prompt on a dataset with no Run Prompt column and opens it", () => {
    renderSummary();

    expect(screen.getByRole("tab", { name: "Evals" })).toBeInTheDocument();
    const prompt = screen.getByRole("tab", { name: "Prompt" });
    // Human review stays conditional: nothing reviewed, so not listed.
    expect(screen.queryByRole("tab", { name: "Human review" })).toBeNull();

    expect(screen.getByTestId("summary-evals")).toBeInTheDocument();
    expect(screen.queryByTestId("summary-prompt")).toBeNull();

    fireEvent.click(prompt);
    expect(screen.getByRole("tab", { name: "Prompt" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByTestId("summary-prompt")).toBeInTheDocument();
    expect(screen.queryByTestId("summary-evals")).toBeNull();
  });
});
