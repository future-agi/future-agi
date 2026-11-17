import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, userEvent, fireEvent } from "src/utils/test-utils";
import ConfirmDialog from "./ConfirmDialog";

// MUI treats a click on its full-screen dialog container as a backdrop click.
function clickBackdrop() {
  const container = screen.getByRole("dialog").parentElement;
  fireEvent.mouseDown(container);
  fireEvent.click(container);
}

describe("processing confirmations", () => {
  it("keeps Processing visible and ignores Escape, backdrop, Cancel and repeated confirm clicks", async () => {
    const onClose = vi.fn();
    const onConfirm = vi.fn();
    const props = { open: true, onClose, onConfirm };
    const { rerender } = render(<ConfirmDialog {...props} />);
    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    rerender(<ConfirmDialog {...props} isLoading />);
    await userEvent.keyboard("{Escape}");
    clickBackdrop();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await userEvent.dblClick(
      screen.getByRole("button", { name: "Processing..." }),
    );
    expect(onClose).not.toHaveBeenCalled();
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("dialog")).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Processing..." }),
    ).toBeDisabled();
  });

  it.each(["Escape", "backdrop", "Cancel"])(
    "allows %s dismissal again after a failed operation settles",
    async (action) => {
      const onClose = vi.fn();
      const onConfirm = vi.fn();
      const props = { open: true, onClose, onConfirm };
      const { rerender } = render(<ConfirmDialog {...props} isLoading />);
      rerender(
        <ConfirmDialog
          {...props}
          isLoading={false}
          message="Could not remove the provider. Try again."
        />,
      );
      expect(screen.getByRole("button", { name: "Confirm" })).toBeEnabled();
      if (action === "Escape") await userEvent.keyboard("{Escape}");
      else if (action === "backdrop") clickBackdrop();
      else
        await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
      expect(onClose).toHaveBeenCalledTimes(1);
      expect(onConfirm).not.toHaveBeenCalled();
    },
  );

  it("allows an explicit retry after failure, not while the first attempt is processing", async () => {
    const onConfirm = vi.fn();
    const props = { open: true, onClose: vi.fn(), onConfirm };
    const { rerender } = render(<ConfirmDialog {...props} />);
    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));
    rerender(<ConfirmDialog {...props} isLoading />);
    await userEvent.click(
      screen.getByRole("button", { name: "Processing..." }),
    );
    expect(onConfirm).toHaveBeenCalledTimes(1);
    rerender(<ConfirmDialog {...props} isLoading={false} />);
    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(onConfirm).toHaveBeenCalledTimes(2);
  });

  it("resets typed confirmation on idle dismissal and requires it again after reopening", async () => {
    const onClose = vi.fn();
    const props = { onClose, onConfirm: vi.fn(), typeToConfirm: "remove" };
    const { rerender } = render(<ConfirmDialog {...props} open />);
    expect(screen.getByRole("button", { name: "Confirm" })).toBeDisabled();
    await userEvent.type(screen.getByRole("textbox"), "remove");
    expect(screen.getByRole("button", { name: "Confirm" })).toBeEnabled();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onClose).toHaveBeenCalledTimes(1);
    rerender(<ConfirmDialog {...props} open={false} />);
    rerender(<ConfirmDialog {...props} open />);
    expect(screen.getByRole("textbox")).toHaveValue("");
    expect(screen.getByRole("button", { name: "Confirm" })).toBeDisabled();
  });
});

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
