import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import ExperimentReviewSummary from "../ExperimentReviewSummary";

// integration (RV-09 4, 5): the pairwise summary says what its number is (ties
// count half), the judge line comes from one count, your own picks are
// labelled yours, and the low-sample note never glues onto the sentence.
vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span data-icon={icon} />,
}));
vi.mock("../CorrectionsBlock", () => ({ default: () => null }));
vi.mock("../OutsideReviewersSection", () => ({ default: () => null }));
const summary = {
  mode: "pairwise",
  run_a: { run_id: "a", label: "#3A" },
  run_b: { run_id: "b", label: "#4A" },
  total_items: 41,
  completed_items: 3,
  wins: 2,
  ties: 1,
  losses: 0,
  judged: 3,
  win_rate: 0.833,
  ci: [0.3, 0.98],
  low_sample: true,
  judge: { agreement: { n: 41, agree: 33, pct: 33 / 41 } },
  reveal: [
    { row_id: "r1", row_no: 5, status: "completed", agree: false },
    { row_id: "r2", row_no: 9, status: "completed", agree: true },
  ],
};
vi.mock("src/api/annotation-queues/annotation-queues", () => ({
  useAnnotationQueueDetail: () => ({
    data: { metadata: { dataset_id: "ds-1" }, review_summary: summary },
    isLoading: false,
  }),
}));

describe("ExperimentReviewSummary pairwise wording", () => {
  it("headline, agreement and your picks say one thing each", () => {
    const { container } = render(<ExperimentReviewSummary queueId="q-1" />);
    expect(
      screen.getByText("#4A win rate 83% (ties count half) · 3 rows"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("review-summary-agreement")).toHaveTextContent(
      "LLM judge agrees with reviewers on 33 of 41 rows (80%) · differs on 8",
    );
    expect(screen.getByTestId("review-summary-mine")).toHaveTextContent(
      "Your picks differ from the judge on 1 of 2 rows",
    );
    const t = container.textContent;
    expect(t).not.toMatch(/preferred on/);
    expect(t).not.toMatch(/#4A\.n=/);
    expect(t).toMatch(/for #4A\. · low sample \(3 rows\)/);
  });
});
