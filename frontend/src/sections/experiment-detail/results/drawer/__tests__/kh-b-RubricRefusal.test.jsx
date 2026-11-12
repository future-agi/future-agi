// KH-SCOPE §6 B1, mounts M-B6 + M-B7 (ships in kh-review/integration.patch with
// the mounts): the results drawer's rubric card refuses ⌘↵ and Submit & next
// with the one rule's words while a refused number is still in its field (or
// a label has no score), highlights the label, and saves nothing; a valid
// entry saves the number shown.
/* eslint-disable react/prop-types */
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  fireEvent,
  render,
  screen,
  userEvent,
  within,
} from "src/utils/test-utils";
import notify from "src/sections/common/ux/toast";
import ReviewTab from "../ReviewTab";

const { mutate } = vi.hoisted(() => ({ mutate: vi.fn() }));
vi.mock("src/sections/common/ux/toast", () => ({ default: vi.fn() }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/sections/common/ValueViewer/ValueViewer", () => ({
  default: ({ value }) => <div>{String(value)}</div>,
}));
vi.mock("src/sections/common/ValueViewer/useValueViewMode", () => ({
  default: () => ["text"],
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: { id: "user-1" } }),
}));
vi.mock("src/sections/common/Runs/runColors", () => ({
  useRunColors: () => ({}),
}));
vi.mock("src/sections/common/Datasets/datasetKind", () => ({
  useDatasetKind: () => ({ kind: "dataset" }),
  rowLabel: (_, n) => `Row ${n}`,
}));
// M8: W4 C's cards under the rubric (merged in M6) read their own queries;
// this test is about the rubric card's rule, so they render nothing here.
vi.mock("src/sections/common/Collab/RunCommentsSection", () => ({
  default: () => null,
}));
vi.mock("../CorrectAnswerCard", () => ({ default: () => null }));
const LABELS = [
  {
    id: "correct",
    name: "Correct?",
    type: "thumbs_up_down",
    settings: {},
  },
  {
    id: "help",
    name: "Helpfulness",
    type: "numeric",
    settings: { min: 0, max: 1, step_size: 0.1, display_type: "slider" },
  },
];
vi.mock("src/api/annotation-labels/annotation-labels", () => ({
  useAnnotationLabelsList: () => ({
    data: { results: LABELS },
    isLoading: false,
  }),
}));
vi.mock("src/api/develop/review", () => ({
  useReviewQueueFor: (_, { mode }) => ({
    queue:
      mode === "rubric"
        ? {
            name: "Quality · #2B",
            labels: [{ label_id: "correct" }, { label_id: "help" }],
          }
        : null,
  }),
  useSubmitResultReview: () => ({ mutate, isPending: false }),
}));

const RUN = { run_id: "run-2b", label: "#2B", name: "Support bot v2" };
const ROW = {
  example_id: "ex-12",
  row_order: 12,
  results: { "run-2b": { status: "ok", output: "Refund issued" } },
};

const renderTab = () => {
  render(
    <ReviewTab
      datasetId="ds-1"
      row={ROW}
      runs={[RUN]}
      baselineId="run-2b"
      focusedRunId="run-2b"
    />,
  );
  return { field: screen.getByRole("spinbutton", { name: "Helpfulness" }) };
};
const cmdEnter = (el) =>
  fireEvent.keyDown(el, { key: "Enter", ctrlKey: true, bubbles: true });

beforeEach(() => {
  mutate.mockClear();
  notify.mockClear();
});

describe("ReviewTab rubric card: the one rule at submit (KH B1)", () => {
  it("⌘↵ with a label unscored names it (no silent no-op)", () => {
    const { field } = renderTab();
    cmdEnter(field);
    expect(mutate).not.toHaveBeenCalled();
    expect(notify).toHaveBeenCalledWith(
      "Fix 2 scores before submitting: Correct? needs a score. Helpfulness needs a score.",
      expect.objectContaining({ variant: "warning" }),
    );
  });

  it("⌘↵ from inside the field with a refused 1.4 refuses, highlights, saves nothing", async () => {
    const user = userEvent.setup();
    const { field } = renderTab();
    await user.click(screen.getByText("Yes"));
    fireEvent.change(field, { target: { value: "0.5" } });
    act(() => field.focus());
    fireEvent.change(field, { target: { value: "1.4" } });
    expect(field).toHaveValue(1.4);
    cmdEnter(field);
    expect(mutate).not.toHaveBeenCalled();
    expect(notify).toHaveBeenCalledWith(
      "Fix 1 score before submitting: Helpfulness must be between 0 and 1 (you entered 1.4).",
      expect.objectContaining({ variant: "warning" }),
    );
    expect(screen.getAllByText("(required)")).toHaveLength(1);
    expect(screen.getByRole("alert")).toHaveTextContent("(you entered 1.4)");
  });

  it("Submit & next (the click blurs the field first) refuses the same way", async () => {
    const user = userEvent.setup();
    const { field } = renderTab();
    await user.click(screen.getByText("Yes"));
    fireEvent.change(field, { target: { value: "0.5" } });
    act(() => field.focus());
    fireEvent.change(field, { target: { value: "-0.2" } });
    await user.click(screen.getByRole("button", { name: /submit & next/i }));
    expect(mutate).not.toHaveBeenCalled();
    expect(notify).toHaveBeenCalledWith(
      "Fix 1 score before submitting: Helpfulness must be between 0 and 1 (you entered -0.2).",
      expect.objectContaining({ variant: "warning" }),
    );
  });

  it("a valid entry clears the refusal and saves the number shown", async () => {
    const user = userEvent.setup();
    const { field } = renderTab();
    await user.click(screen.getByText("Yes"));
    fireEvent.change(field, { target: { value: "1.4" } });
    cmdEnter(field);
    expect(screen.getAllByText("(required)")).toHaveLength(1);
    fireEvent.change(field, { target: { value: "0.7" } });
    expect(screen.queryByText("(required)")).toBeNull();
    notify.mockClear();
    cmdEnter(field);
    expect(notify).not.toHaveBeenCalled();
    expect(mutate).toHaveBeenCalledTimes(1);
    expect(mutate.mock.calls[0][0]).toMatchObject({
      example_id: "ex-12",
      mode: "rubric",
      run_id: "run-2b",
      values: [
        { label_id: "correct", value: { value: "up" } },
        { label_id: "help", value: { value: 0.7 } },
      ],
    });
    expect(within(document.body).queryByRole("alert")).toBeNull();
  });
});
