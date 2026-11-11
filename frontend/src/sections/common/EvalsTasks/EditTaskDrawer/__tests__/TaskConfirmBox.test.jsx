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
  it("reports the option the user picked", () => {
    const onConfirm = vi.fn();
    renderDialog({ onConfirm });

    fireEvent.click(screen.getByText("Edit & Re-run Existing Evals"));
    fireEvent.click(screen.getByRole("button", { name: /run task/i }));

    expect(onConfirm).toHaveBeenCalledWith("edit_rerun");
  });

  it("resets to the safe default when reopened, so a past pick cannot leak into the next run", () => {
    const onConfirm = vi.fn();
    const { rerender } = renderDialog({ onConfirm });

    fireEvent.click(screen.getByText("Edit & Re-run Existing Evals"));

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

    expect(onConfirm).toHaveBeenCalledWith("fresh_run");
  });
});
