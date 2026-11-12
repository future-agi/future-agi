import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import EvalFeedbackTab from "../EvalFeedbackTab";

// W10-EVALS-U06: the feedback list is always returned newest first and the
// tab cannot re-sort it, so no header may offer a sort control, and the
// order is stated in words instead.

const h = vi.hoisted(() => ({ feedback: {} }));

vi.mock("../../hooks/useEvalFeedback", () => ({
  useEvalFeedbackList: () => h.feedback,
}));
vi.mock("@tanstack/react-query", async (importOriginal) => ({
  ...(await importOriginal()),
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}));
vi.mock("src/sections/develop-detail/DataTab/common", () => ({
  normalizeEvalCellValue: (value) => value,
}));
vi.mock(
  "src/sections/evals/EvalDetails/EvalsFeedback/AddEvalsFeedbackDrawer",
  () => ({
    default: () => null,
  }),
);
vi.mock("src/components/FormSearchField/FormSearchField", () => ({
  default: () => null,
}));
vi.mock("@monaco-editor/react", () => ({ default: () => null }));
vi.mock("src/hooks/use-debounce", () => ({ useDebounce: (value) => value }));

const FEEDBACK_ROWS = [
  {
    id: "fb-1",
    value: "passed",
    explanation: "Matches the reference answer",
    action_type: "retune",
    source: "eval_playground",
    user_name: "Ana",
    created_at: "2026-11-02T10:00:00Z",
  },
  {
    id: "fb-2",
    value: "failed",
    explanation: "Missed the requested tone",
    action_type: "recalculate",
    source: "dataset",
    user_name: "Ben",
    created_at: "2026-11-01T09:00:00Z",
  },
];

const HEADERS = [
  "Feedback",
  "Improvement Note",
  "Action",
  "Source",
  "By",
  "Date",
];

const renderTab = () =>
  render(
    <div style={{ height: 600, width: 1200 }}>
      <EvalFeedbackTab templateId="eval-1" />
    </div>,
  );

describe("EvalFeedbackTab sort affordance (W10-EVALS-U06)", () => {
  beforeEach(() => {
    h.feedback = {
      data: { items: FEEDBACK_ROWS, total: FEEDBACK_ROWS.length },
      isLoading: false,
      isFetching: false,
    };
  });

  it("W10-EVALS-U06 shows no sort control on the feedback headers", async () => {
    renderTab();

    expect(
      await screen.findByRole("columnheader", { name: "Date" }),
    ).toBeInTheDocument();
    HEADERS.forEach((label) => {
      const header = screen.getByRole("columnheader", { name: label });
      expect(
        within(header).queryByRole("button", { name: "Sort", hidden: true }),
      ).toBeNull();
    });
    expect(
      screen.queryAllByRole("button", { name: "Sort", hidden: true }),
    ).toHaveLength(0);
  });

  it("W10-EVALS-U06 says the feedback list is newest first", () => {
    renderTab();

    expect(screen.getByText("Newest first")).toBeInTheDocument();
  });
});
