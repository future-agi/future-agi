import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import TaskConfirmDialog from "../TaskConfirmBox";

const renderDialog = (props) =>
  render(
    <TaskConfirmDialog
      title="Update Task"
      content="Select one of the options"
      open
      onClose={vi.fn()}
      {...props}
    />,
  );

describe("TaskConfirmDialog", () => {
  it("keeps past results unless the user picks to delete them", () => {
    const onConfirm = vi.fn();
    renderDialog({ onConfirm });

    expect(
      screen.getByRole("radio", { name: /Edit & Re-run Existing Evals/ }),
    ).toBeChecked();
    expect(
      screen.getByRole("radio", { name: /Delete Past Data & Start Fresh/ }),
    ).not.toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: /run task/i }));

    expect(onConfirm).toHaveBeenCalledWith("edit_rerun");
  });

  it("deletes past data only when the user picks that option", () => {
    const onConfirm = vi.fn();
    renderDialog({ onConfirm });

    fireEvent.click(screen.getByText("Delete Past Data & Start Fresh"));
    fireEvent.click(screen.getByRole("button", { name: /run task/i }));

    expect(onConfirm).toHaveBeenCalledWith("fresh_run");
  });

  it("resets to keeping past results when reopened, so a delete picked last time is not preselected", () => {
    const onConfirm = vi.fn();
    const { rerender } = renderDialog({ onConfirm });

    fireEvent.click(screen.getByText("Delete Past Data & Start Fresh"));

    rerender(
      <TaskConfirmDialog
        title="Update Task"
        content="Select one of the options"
        open={false}
        onClose={vi.fn()}
        onConfirm={onConfirm}
      />,
    );
    rerender(
      <TaskConfirmDialog
        title="Update Task"
        content="Select one of the options"
        open
        onClose={vi.fn()}
        onConfirm={onConfirm}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /run task/i }));

    expect(onConfirm).toHaveBeenCalledWith("edit_rerun");
  });
});
