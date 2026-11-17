// An answer saved before its label's limits changed: the panel shows why it
// does not fit as a quiet line, lets the annotator save the item's other
// labels, and refuses only when the old answer is edited to another value that
// still breaks the limits. A refused filled field names its problem; only an
// empty field is marked "(required)".
/* eslint-disable react/prop-types */
import React, { createRef } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";
import { enqueueSnackbar } from "notistack";
import LabelPanel from "../annotate/label-panel";

vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/utils/format-time", () => ({
  fDateTime: () => "Jan 1, 2025 12:00",
  fToNowStrict: () => "5 minutes ago",
}));
vi.mock("src/sections/common/CellMarkdown", () => ({
  default: ({ text }) => <div>{text}</div>,
}));
const { mutation } = vi.hoisted(() => ({
  mutation: () => ({
    mutate: () => {},
    isPending: false,
    variables: undefined,
  }),
}));
vi.mock("src/api/annotation-queues/annotation-queues", () => ({
  useItemAnnotations: () => ({ data: [] }),
  useCreateDiscussionComment: mutation,
  useUpdateDiscussionComment: mutation,
  useDeleteDiscussionComment: mutation,
  useResolveDiscussionThread: mutation,
  useReopenDiscussionThread: mutation,
  useToggleDiscussionReaction: mutation,
}));

const LABELS = [
  {
    id: "ql-1",
    label_id: "comment",
    name: "Comment",
    type: "text",
    settings: { min_length: 50, max_length: 1000 },
  },
  {
    id: "ql-2",
    label_id: "correct",
    name: "Correct?",
    type: "thumbs_up_down",
    settings: {},
  },
  {
    id: "ql-3",
    label_id: "accuracy",
    name: "Accuracy",
    type: "numeric",
    settings: { min: 0, max: 5, step_size: 1, display_type: "slider" },
  },
];
const SAVED = [
  { label_id: "comment", value: { text: "short note" }, notes: "" },
  { label_id: "correct", value: { value: "up" }, notes: "" },
  { label_id: "accuracy", value: { value: 8 }, notes: "" },
];
const OLD_COMMENT_LINE = "Comment needs at least 50 characters (you wrote 10).";
const OLD_NUMBER_LINE = "Accuracy must be between 0 and 5 (you entered 8).";

const labelOf = (node) => node.closest("[data-review-label-id]");
const labelBox = (labelId) =>
  document.querySelector(`[data-review-label-id="${labelId}"]`);

const setup = async () => {
  const onSubmit = vi.fn();
  const ref = createRef();
  render(
    <LabelPanel
      ref={ref}
      labels={LABELS}
      annotations={SAVED}
      onSubmit={onSubmit}
      queueId="queue-1"
      itemId="item-1"
    />,
  );
  const comment = within(labelBox("comment")).getByRole("textbox");
  await waitFor(() => expect(comment).toHaveValue("short note"));
  const accuracy = screen.getByRole("spinbutton", { name: "Accuracy" });
  await waitFor(() => expect(accuracy).toHaveValue(8));
  return { onSubmit, ref, comment, accuracy };
};

beforeEach(() => {
  enqueueSnackbar.mockClear();
});

describe("LabelPanel: an answer saved before its label's limits changed", () => {
  it("shows why the old text does not fit as a quiet line, not an error", async () => {
    const { comment } = await setup();
    const box = within(labelOf(comment));
    const line = box.getByText(OLD_COMMENT_LINE);
    expect(line).not.toHaveAttribute("role", "alert");
    expect(box.queryByRole("alert")).toBeNull();
    expect(box.queryByText("(required)")).toBeNull();
  });

  it("shows why the old number does not fit, and leaving the field keeps it quiet", async () => {
    const { accuracy } = await setup();
    const box = within(labelOf(accuracy));
    expect(box.getByTestId("numeric-rule")).toHaveTextContent(OLD_NUMBER_LINE);
    act(() => accuracy.focus());
    fireEvent.blur(accuracy);
    expect(box.getByTestId("numeric-rule")).toHaveTextContent(OLD_NUMBER_LINE);
    expect(box.queryByRole("alert")).toBeNull();
    expect(accuracy).toHaveValue(8);
  });

  it("Submit sends the other label's change with the old answers as they were", async () => {
    const { onSubmit, ref } = await setup();
    fireEvent.click(within(labelBox("correct")).getByText("No"));
    act(() => ref.current.submit());
    expect(enqueueSnackbar).not.toHaveBeenCalled();
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit.mock.calls[0][0].annotations).toEqual([
      { label_id: "comment", value: { text: "short note" } },
      { label_id: "correct", value: { value: "down" } },
      { label_id: "accuracy", value: { value: 8 } },
    ]);
  });

  it("editing the old text to another text that breaks the limit shows the error and blocks", async () => {
    const { onSubmit, ref, comment } = await setup();
    fireEvent.change(comment, { target: { value: "still far too short." } });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 350));
    });
    act(() => ref.current.submit());
    expect(onSubmit).not.toHaveBeenCalled();
    expect(enqueueSnackbar).toHaveBeenCalledWith(
      "Fix 1 score before submitting: Comment needs at least 50 characters (you wrote 20).",
      { variant: "warning" },
    );
    expect(within(labelOf(comment)).getByRole("alert")).toHaveTextContent(
      "Comment needs at least 50 characters (you wrote 20).",
    );
    expect(within(labelOf(comment)).queryByText("(required)")).toBeNull();
  });

  it("an emptied required field is the one marked (required)", async () => {
    const { onSubmit, ref, comment } = await setup();
    fireEvent.change(comment, { target: { value: "" } });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 350));
    });
    act(() => ref.current.submit());
    expect(onSubmit).not.toHaveBeenCalled();
    expect(
      within(labelOf(comment)).getByText("(required)"),
    ).toBeInTheDocument();
  });
});

const PAIRWISE_LABELS = [
  {
    id: "ql-p",
    label_id: "preference",
    name: "Overall preference",
    type: "categorical",
    settings: {
      kind: "pairwise",
      options: [{ label: "A" }, { label: "B" }],
      multi_choice: false,
    },
  },
  {
    id: "ql-2",
    label_id: "correct",
    name: "Correct?",
    type: "thumbs_up_down",
    settings: {},
  },
];
const PAIRWISE_SAVED = [
  { label_id: "preference", value: { selected: ["Tie"] }, notes: "" },
  { label_id: "correct", value: { value: "up" }, notes: "" },
];

describe("LabelPanel: a pairwise answer saved before its choices changed", () => {
  it("does not block the submit of the other labels", async () => {
    const onSubmit = vi.fn();
    const ref = createRef();
    render(
      <LabelPanel
        ref={ref}
        labels={PAIRWISE_LABELS}
        annotations={PAIRWISE_SAVED}
        onSubmit={onSubmit}
        queueId="queue-1"
        itemId="item-1"
      />,
    );
    await waitFor(() => expect(labelBox("correct")).not.toBeNull());
    fireEvent.click(within(labelBox("correct")).getByText("No"));
    act(() => ref.current.submit());
    expect(enqueueSnackbar).not.toHaveBeenCalled();
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit.mock.calls[0][0].annotations).toEqual([
      { label_id: "preference", value: { selected: ["Tie"] } },
      { label_id: "correct", value: { value: "down" } },
    ]);
  });
});
