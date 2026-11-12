import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "src/utils/test-utils";
import ExperimentReviewSummary from "../ExperimentReviewSummary";

// W4 C3 review fix M1: outside answers no longer complete the creator's
// items, so the rubric headline reads the server's rated rows (internal
// completions plus rows with an outside answer) and says how many rows
// outside reviewers rated.
vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span data-icon={icon} />,
}));
vi.mock("../CorrectionsBlock", () => ({ default: () => null }));
vi.mock("../OutsideReviewersSection", () => ({ default: () => null }));
let summary = null;
vi.mock("src/api/annotation-queues/annotation-queues", () => ({
  useAnnotationQueueDetail: () => ({
    data: { metadata: { dataset_id: "ds-1" }, review_summary: summary },
    isLoading: false,
  }),
}));

const rubric = (extra) => ({
  mode: "rubric",
  run: { run_id: "run-1", label: "#4A" },
  experiment_id: null,
  baseline: null,
  total_items: 248,
  completed_items: 1,
  labels: [],
  ...extra,
});

describe("ExperimentReviewSummary rated rows", () => {
  beforeEach(() => {
    summary = null;
  });

  it("counts rows outside reviewers rated and names them", () => {
    summary = rubric({ rated_items: 5, outside_items: 4 });
    render(<ExperimentReviewSummary queueId="q-1" />);
    expect(screen.getByText("#4A rated on 5 of 248 rows")).toBeInTheDocument();
    expect(
      screen.getByTestId("review-summary-outside-rated"),
    ).toHaveTextContent("Outside reviewers rated 4 of these rows.");
  });

  it("shows no outside line when nobody outside answered", () => {
    summary = rubric({ rated_items: 1, outside_items: 0 });
    render(<ExperimentReviewSummary queueId="q-1" />);
    expect(screen.getByText("#4A rated on 1 of 248 rows")).toBeInTheDocument();
    expect(
      screen.queryByTestId("review-summary-outside-rated"),
    ).not.toBeInTheDocument();
  });
});
