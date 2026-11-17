import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import ConfirmDialog from "./ConfirmDialog";

const MESSAGE = "This revokes the key. It cannot be undone.";

describe("ConfirmDialog names and describes itself", () => {
  it("is named by its title and described by its message", () => {
    render(
      <ConfirmDialog
        open
        onClose={vi.fn()}
        onConfirm={vi.fn()}
        title="Revoke ci?"
        message={MESSAGE}
      />,
    );
    const dialog = screen.getByRole("dialog", { name: "Revoke ci?" });
    expect(dialog).toHaveAccessibleDescription(MESSAGE);
  });

  it("points at no description when it has no message", () => {
    render(<ConfirmDialog open onClose={vi.fn()} onConfirm={vi.fn()} />);
    expect(screen.getByRole("dialog")).not.toHaveAttribute("aria-describedby");
  });
});

describe("ConfirmDialog close button", () => {
  it("is offered on request and closes without confirming", async () => {
    const onClose = vi.fn();
    const onConfirm = vi.fn();
    render(
      <ConfirmDialog
        open
        onClose={onClose}
        onConfirm={onConfirm}
        title="Revoke ci?"
        message={MESSAGE}
        showClose
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("cannot close while the action is processing", () => {
    render(
      <ConfirmDialog
        open
        onClose={vi.fn()}
        onConfirm={vi.fn()}
        message={MESSAGE}
        showClose
        isLoading
      />,
    );
    expect(screen.getByRole("button", { name: "Close" })).toBeDisabled();
  });

  it("is left out for callers that do not ask for it", () => {
    render(
      <ConfirmDialog
        open
        onClose={vi.fn()}
        onConfirm={vi.fn()}
        message={MESSAGE}
      />,
    );
    expect(screen.queryByRole("button", { name: "Close" })).toBeNull();
  });
});
