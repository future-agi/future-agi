import { describe, test, expect, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import QueueAgreementTab from "../queue-agreement-tab";

// Mock the API hook so the component receives controlled data.
vi.mock("src/api/annotation-queues/annotation-queues", () => ({
  useQueueAgreement: vi.fn(),
}));

import { useQueueAgreement } from "src/api/annotation-queues/annotation-queues";

function mockAgreement(overrides = {}) {
  useQueueAgreement.mockReturnValue({
    data: {
      overall_agreement: 0.85,
      labels: {},
      annotator_pairs: [],
      judge_vs_human: null,
      ...overrides,
    },
    isLoading: false,
  });
}

describe("QueueAgreementTab — judge vs human section", () => {
  test("shows empty-state prompt when no evaluator is linked", () => {
    mockAgreement();

    render(<QueueAgreementTab queueId="q-1" />);

    expect(screen.getByText("Judge vs Human Agreement")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Link an evaluator to this queue to compare judge scores against human labels.",
      ),
    ).toBeInTheDocument();
  });

  test("renders judge-vs-human overall agreement when data is present", () => {
    mockAgreement({
      judge_vs_human: {
        evaluator_name: "Safety Eval v2",
        overall_agreement: 0.72,
        total_comparisons: 45,
        labels: {},
      },
    });

    render(<QueueAgreementTab queueId="q-1" />);

    // The evaluator name is prefixed with "Evaluator: " in the Typography.
    expect(screen.getByText("Evaluator: Safety Eval v2")).toBeInTheDocument();
    // 72.0% appears as the Judge-Human Overall Agreement in <h2>.
    expect(screen.getByText("72.0%")).toBeInTheDocument();
  });

  test("renders per-label agreement rows", () => {
    mockAgreement({
      judge_vs_human: {
        evaluator_name: "Safety Eval v2",
        overall_agreement: 0.67,
        total_comparisons: 30,
        labels: {
          "label-1": {
            label_name: "PII Leak",
            label_type: "categorical",
            judge_human_agreement: 0.68,
            total_comparisons: 20,
          },
          "label-2": {
            label_name: "Safe",
            label_type: "categorical",
            judge_human_agreement: 0.85,
            total_comparisons: 10,
          },
        },
      },
    });

    render(<QueueAgreementTab queueId="q-1" />);

    // Labels should appear in the table.
    expect(screen.getByText("PII Leak")).toBeInTheDocument();
    expect(screen.getByText("Safe")).toBeInTheDocument();
    // Agreement percentages (overall_agreement is 0.85 -> "85.0%" in
    // Overall card, and label-2 has 0.85 -> "85.0%" in the per-label
    // table — so "85.0%" appears twice and "68.0%" once).
    expect(screen.getByText("68.0%")).toBeInTheDocument();
    expect(screen.getAllByText("85.0%").length).toBe(2);
  });

  test("shows N/A when overall agreement is null", () => {
    mockAgreement({
      judge_vs_human: {
        evaluator_name: "Score Eval",
        overall_agreement: null,
        total_comparisons: 0,
        labels: {},
      },
    });

    render(<QueueAgreementTab queueId="q-1" />);

    expect(screen.getByText("N/A")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Not enough overlapping items with both judge scores and human labels to calculate agreement",
      ),
    ).toBeInTheDocument();
  });

  test("shows N/A for label agreement when null", () => {
    mockAgreement({
      judge_vs_human: {
        evaluator_name: "Safety Eval",
        overall_agreement: null,
        total_comparisons: 0,
        labels: {
          "label-1": {
            label_name: "Toxicity",
            label_type: "categorical",
            judge_human_agreement: null,
            total_comparisons: 0,
          },
        },
      },
    });

    render(<QueueAgreementTab queueId="q-1" />);

    // The label name appears, but the agreement percentage shows N/A.
    expect(screen.getByText("Toxicity")).toBeInTheDocument();
    // "N/A" appears twice: once for overall and once for the label.
    const naElements = screen.getAllByText("N/A");
    expect(naElements.length).toBe(2);
  });

  test("does not render per-label table when labels are empty", () => {
    mockAgreement({
      judge_vs_human: {
        evaluator_name: "Safety Eval",
        overall_agreement: 0.5,
        total_comparisons: 10,
        labels: {},
      },
    });

    render(<QueueAgreementTab queueId="q-1" />);

    // The evaluator name is prefixed with "Evaluator: ".
    expect(screen.getByText("Evaluator: Safety Eval")).toBeInTheDocument();
    // Per-Label table headers should not appear.
    expect(screen.queryByText("PII Leak")).not.toBeInTheDocument();
  });
});
