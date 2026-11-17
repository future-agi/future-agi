import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import ConfirmDialog from "../ConfirmDialog";

const TITLE = "Delete the Golden dataset?";

function renderDialog(props = {}) {
  const onConfirm = vi.fn();
  const onClose = vi.fn();
  render(
    <ConfirmDialog
      open
      title={TITLE}
      body="Its 120 rows are deleted."
      onConfirm={onConfirm}
      onClose={onClose}
      {...props}
    />,
  );
  return { onConfirm, onClose };
}

describe("ConfirmDialog and the Enter key", () => {
  it("does not confirm on Enter when there is no name to type", () => {
    const { onConfirm, onClose } = renderDialog();
    const dialog = screen.getByRole("dialog", { name: TITLE });

    fireEvent.keyDown(dialog, { key: "Enter" });

    expect(onConfirm).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("confirms on Enter once the typed name matches", () => {
    const { onConfirm } = renderDialog({ typedConfirm: "Golden" });
    const input = screen.getByRole("textbox", {
      name: "Type Golden to confirm",
    });

    fireEvent.change(input, { target: { value: "Gold" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onConfirm).not.toHaveBeenCalled();

    fireEvent.change(input, { target: { value: "Golden" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });
});
