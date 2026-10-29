import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import ConfirmRunEvaluations from "../ConfirmRunEvaluations";

const renderDialog = (props = {}) => {
  const defaultProps = {
    open: true,
    onClose: vi.fn(),
    onConfirm: vi.fn(),
    selectedUserEvalList: [],
    loading: false,
    ...props,
  };

  const utils = render(<ConfirmRunEvaluations {...defaultProps} />);

  return { ...utils, ...defaultProps };
};

describe("ConfirmRunEvaluations", () => {
  it("asks before overwriting and says so", () => {
    renderDialog({ selectedUserEvalList: [{ id: "a", name: "A" }] });

    expect(
      screen.getByText(
        /Are you sure you want to run the following evaluation\?/,
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText("This will overwrite previous evaluation results."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/harness/i)).toBeNull();
  });

  it("shows the note only while the eval it is about is still listed", async () => {
    const user = userEvent.setup();
    const getNote = (list) =>
      list.some((e) => e.harness)
        ? "Scores the harness gave will be replaced by the platform's."
        : null;

    renderDialog({
      selectedUserEvalList: [
        { id: "a", name: "A", harness: true },
        { id: "b", name: "B" },
      ],
      getNote,
    });

    expect(
      screen.getByText(
        "Scores the harness gave will be replaced by the platform's.",
      ),
    ).toBeInTheDocument();

    await user.click(screen.getAllByRole("button", { name: "remove-eval" })[0]);

    expect(
      screen.queryByText(
        "Scores the harness gave will be replaced by the platform's.",
      ),
    ).toBeNull();
    expect(screen.getByText("B")).toBeInTheDocument();
  });

  it("confirms with the evals still listed", async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();

    renderDialog({
      selectedUserEvalList: [
        { id: "a", name: "A" },
        { id: "b", name: "B" },
      ],
      onConfirm,
    });

    await user.click(screen.getAllByRole("button", { name: "remove-eval" })[0]);
    await user.click(screen.getByText("Run Evaluations"));

    expect(onConfirm).toHaveBeenCalledWith([{ id: "b", name: "B" }]);
  });
});
