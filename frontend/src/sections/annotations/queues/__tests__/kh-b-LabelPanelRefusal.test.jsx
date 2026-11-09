// KH-SCOPE §6 B1, mounts M-B2 + M-B3 (ships in kh-review/integration.patch with
// the mounts): a number the rule refused that is still in its field blocks
// Submit & Next and ⌘↵ (the panel's imperative submit), with the reason, the
// label highlighted, and nothing saved; the kept score is never sent in its
// place.
/* eslint-disable react/prop-types */
import React, { createRef } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  fireEvent,
  render,
  screen,
  userEvent,
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
    label_id: "correct",
    name: "Correct?",
    type: "thumbs_up_down",
    settings: {},
  },
  {
    id: "ql-2",
    label_id: "help",
    name: "Helpfulness",
    type: "numeric",
    settings: { min: 0, max: 1, step_size: 0.1, display_type: "slider" },
  },
];
const SAVED = [
  { label_id: "correct", value: { value: "up" }, notes: "" },
  { label_id: "help", value: { value: 0.5 }, notes: "" },
];
const REFUSED =
  "Fix 1 score before submitting: Helpfulness must be between 0 and 1 (you entered 1.4).";

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
  const field = screen.getByRole("spinbutton", { name: "Helpfulness" });
  await waitFor(() => expect(field).toHaveValue(0.5));
  const helpLabel = () =>
    field.closest("[data-review-label-id]") ?? document.body;
  return { onSubmit, ref, field, helpLabel };
};

beforeEach(() => {
  enqueueSnackbar.mockClear();
});

describe("LabelPanel: a refused entry left in its field (KH B1)", () => {
  it("Submit & Next refuses with the reason and highlights the label", async () => {
    const user = userEvent.setup();
    const { onSubmit, field, helpLabel } = await setup();
    act(() => field.focus());
    fireEvent.change(field, { target: { value: "1.4" } });
    expect(field).toHaveValue(1.4);
    // The click moves focus off the field first (blur), as in a browser.
    await user.click(screen.getByRole("button", { name: /submit & next/i }));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(enqueueSnackbar).toHaveBeenCalledWith(REFUSED, {
      variant: "warning",
    });
    expect(within(helpLabel()).getByText("(required)")).toBeInTheDocument();
    expect(within(helpLabel()).getByRole("alert")).toHaveTextContent(
      "Helpfulness must be between 0 and 1 (you entered 1.4).",
    );
  });

  it("⌘↵ from inside the field (no blur) refuses the same way", async () => {
    const { onSubmit, ref, field, helpLabel } = await setup();
    act(() => field.focus());
    fireEvent.change(field, { target: { value: "1.4" } });
    act(() => ref.current.submit());
    expect(onSubmit).not.toHaveBeenCalled();
    expect(enqueueSnackbar).toHaveBeenCalledWith(REFUSED, {
      variant: "warning",
    });
    expect(field).toHaveValue(1.4);
    expect(within(helpLabel()).getByText("(required)")).toBeInTheDocument();
  });

  it("an off-step entry is named with its own reason", async () => {
    const { onSubmit, ref, field } = await setup();
    fireEvent.change(field, { target: { value: "0.35" } });
    act(() => ref.current.submit());
    expect(onSubmit).not.toHaveBeenCalled();
    expect(enqueueSnackbar).toHaveBeenCalledWith(
      "Fix 1 score before submitting: Helpfulness moves in steps of 0.1 (you entered 0.35).",
      { variant: "warning" },
    );
  });

  it("a valid entry clears the refusal and is the value saved", async () => {
    const user = userEvent.setup();
    const { onSubmit, field, helpLabel } = await setup();
    fireEvent.change(field, { target: { value: "1.4" } });
    fireEvent.blur(field);
    fireEvent.change(field, { target: { value: "0.7" } });
    expect(within(helpLabel()).queryByRole("alert")).toBeNull();
    await user.click(screen.getByRole("button", { name: /submit & next/i }));
    expect(enqueueSnackbar).not.toHaveBeenCalled();
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit.mock.calls[0][0].annotations).toEqual(
      expect.arrayContaining([{ label_id: "help", value: { value: 0.7 } }]),
    );
  });

  it("with no refusal pending, the kept score submits as before", async () => {
    const { onSubmit, ref } = await setup();
    act(() => ref.current.submit());
    expect(enqueueSnackbar).not.toHaveBeenCalled();
    expect(onSubmit.mock.calls[0][0].annotations).toEqual([
      { label_id: "correct", value: { value: "up" } },
      { label_id: "help", value: { value: 0.5 } },
    ]);
  });
});
