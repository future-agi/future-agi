// M10 (W7 integration review SF6): the wizard picker's preview of a decision tree
// says "Decision tree · … · N scores" once. A tree created without a
// description is described by that same line (create-dag/), and the preview
// prints the description under the summary, so the summary leaves its own
// headline out then; a description the author wrote keeps both.
import React from "react";
import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "src/utils/test-utils";
import { dagSummaryOf } from "../dagModel";

const dag = {
  schema: "fi-dag-v1",
  start: "q1",
  nodes: [
    {
      id: "q1",
      kind: "judge",
      question: "Does the reply say a refund was issued?",
      answers: [
        { label: "Yes", next: "l1" },
        { label: "No", next: "l2" },
      ],
    },
    { id: "l1", kind: "leaf", label: "Refund", score: 1 },
    { id: "l2", kind: "leaf", label: "No refund", score: 0 },
  ],
};

vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn(async () => ({ data: { result: { id: "tpl-dag", dag } } })),
  },
  endpoints: { develop: { w7: { dag: (id) => `/dag/${id}/` } } },
}));

import DagPickerSummary from "../DagPickerSummary";

const renderWith = (description) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <DagPickerSummary evalData={{ id: "tpl-dag", description }} />
    </QueryClientProvider>,
  );

describe("decision tree picker summary line", () => {
  it("leaves its headline out when the description is that line", async () => {
    const line = dagSummaryOf(dag);
    renderWith(line);
    await waitFor(() =>
      expect(screen.getByTestId("dag-picker-summary")).toBeTruthy(),
    );
    const box = screen.getByTestId("dag-picker-summary");
    expect(box.textContent).not.toContain(line);
    expect(box.textContent).toContain("Scores: Refund 1.00 · No refund 0.00");
  });

  it("keeps its headline beside a description the author wrote", async () => {
    renderWith("Checks the refund promise");
    await waitFor(() =>
      expect(screen.getByTestId("dag-picker-summary")).toBeTruthy(),
    );
    expect(screen.getByTestId("dag-picker-summary").textContent).toContain(
      dagSummaryOf(dag),
    );
  });
});
