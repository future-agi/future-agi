import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import ConfirmDialog from "./ConfirmDialog";

describe("GWUX-02 shared confirmation labels", () => {
  it("keeps Cancel as the default for existing callers", async () => {
    const onClose = vi.fn();
    const onConfirm = vi.fn();
    render(<ConfirmDialog open onClose={onClose} onConfirm={onConfirm} />);
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("uses the explicit non-destructive label without changing the action", async () => {
    const onClose = vi.fn();
    const onConfirm = vi.fn();
    render(
      <ConfirmDialog
        open
        onClose={onClose}
        onConfirm={onConfirm}
        cancelLabel="Keep running"
        confirmLabel="Cancel job"
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Keep running" }));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Cancel job" })).toHaveClass(
      "MuiButton-containedError",
    );
  });
});
