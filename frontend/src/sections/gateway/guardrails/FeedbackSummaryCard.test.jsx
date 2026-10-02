import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";

import FeedbackSummaryCard from "./FeedbackSummaryCard";

vi.mock("./hooks/useGuardrailFeedback", () => ({
  useGuardrailFeedbackSummary: () => ({
    isLoading: false,
    data: [
      {
        check_name: "pii-detection",
        total_feedback: 4,
        correct_count: 2,
        false_positive_count: 1,
        false_negative_count: 1,
      },
    ],
  }),
}));

describe("FeedbackSummaryCard", () => {
  it("renders canonical snake_case guardrail feedback summary fields", () => {
    render(<FeedbackSummaryCard />);

    expect(screen.getByText("pii-detection")).toBeInTheDocument();
    expect(screen.getByText("4")).toBeInTheDocument();
    expect(screen.getByText("50.0%")).toBeInTheDocument();
    expect(screen.getAllByText("1")).toHaveLength(2);
    expect(screen.getByText("2")).toBeInTheDocument();
  });
});
