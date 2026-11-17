import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import FeedbackWidget from "./FeedbackWidget";

let saved;
const refetch = vi.fn();
const create = vi.fn();
const update = vi.fn();
const answer = { id: "review-1", feedback: "correct", comment: "Saved comment" };
vi.mock("./hooks/useGuardrailFeedback", () => ({
  useMyGuardrailFeedback: () => ({ ...saved, refetch }),
  useCreateGuardrailFeedback: () => ({ mutate: create }),
  useUpdateGuardrailFeedback: () => ({ mutate: update }),
}));

beforeEach(() => {
  saved = { isLoading: false, isError: false, answer };
  vi.clearAllMocks();
});

describe("R3-CONTROLS-05 review lookup failures", () => {
  it("blocks a cold failed lookup instead of exposing a new upsert form", async () => {
    saved = { isLoading: false, isError: true };
    const view = render(<FeedbackWidget requestLogId="log-1" checkName="pii" />);
    expect(screen.getByRole("alert")).toHaveTextContent(/could not be loaded/i);
    expect(screen.queryByRole("button", { name: "Correct" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save review" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retry review" }));
    expect(refetch).toHaveBeenCalledTimes(1);
    saved = { isLoading: false, isError: false, answer };
    view.rerender(<FeedbackWidget requestLogId="log-1" checkName="pii" />);
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    expect(screen.getByLabelText("Comment")).toHaveValue("Saved comment");
    expect(create).not.toHaveBeenCalled();
  });

  it("keeps an unsaved comment through a failed read and retry", async () => {
    const view = render(<FeedbackWidget requestLogId="log-1" checkName="pii" />);
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    await userEvent.clear(screen.getByLabelText("Comment"));
    await userEvent.type(screen.getByLabelText("Comment"), "Unsaved note");
    saved = { isLoading: false, isError: true };
    view.rerender(<FeedbackWidget requestLogId="log-1" checkName="pii" />);
    expect(screen.queryByRole("button", { name: "Save review" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retry review" }));
    saved = { isLoading: false, isError: false, answer };
    view.rerender(<FeedbackWidget requestLogId="log-1" checkName="pii" />);
    expect(screen.getByLabelText("Comment")).toHaveValue("Unsaved note");
    await userEvent.click(screen.getByRole("button", { name: "Save review" }));
    expect(update).toHaveBeenCalledWith({ id: "review-1", feedback: "correct", comment: "Unsaved note" }, expect.any(Object));
    expect(create).not.toHaveBeenCalled();
  });
});
