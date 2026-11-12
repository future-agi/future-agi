import React from "react";
import { describe, expect, it, vi } from "vitest";

import { fireEvent, render, screen, within } from "src/utils/test-utils";

import CustomDialog from "../CustomDialog";

vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span data-testid="iconify" data-icon={icon} />,
}));

const FOCUSABLE =
  'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';

function renderDialog(props = {}) {
  const onClose = vi.fn();
  render(
    <CustomDialog
      open
      title="Remove Scale?"
      preTitleIcon="mdi:alert-circle-outline"
      actionButton="Remove"
      onClose={onClose}
      onClickAction={() => {}}
      {...props}
    >
      <p>The add-on ends at the end of this billing cycle.</p>
    </CustomDialog>,
  );
  return { onClose, dialog: screen.getByRole("dialog") };
}

describe("CustomDialog", () => {
  it("has no unlabeled focusable control", () => {
    const { dialog } = renderDialog();
    const focusable = Array.from(dialog.querySelectorAll(FOCUSABLE)).filter(
      (el) => !el.matches(":disabled"),
    );
    const named = within(dialog).queryAllByRole("button", { name: /\S/ });
    expect(focusable).toHaveLength(3);
    expect(named).toHaveLength(focusable.length);
    expect(
      named.map((b) => b.getAttribute("aria-label") || b.textContent),
    ).toEqual(["Close", "Cancel", "Remove"]);
  });

  it("renders the title icon as decoration, not a button", () => {
    const { dialog } = renderDialog();
    const icon = within(dialog)
      .getAllByTestId("iconify")
      .find((el) => el.dataset.icon === "mdi:alert-circle-outline");
    expect(icon).toBeTruthy();
    expect(icon.closest("button")).toBeNull();
    expect(icon.closest('[aria-hidden="true"]')).not.toBeNull();
  });

  it("closes from the X button, which is named Close", () => {
    const { onClose } = renderDialog();
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("without a title icon still has only named controls", () => {
    const { dialog } = renderDialog({ preTitleIcon: undefined });
    const focusable = dialog.querySelectorAll(FOCUSABLE);
    const named = within(dialog).queryAllByRole("button", { name: /\S/ });
    expect(focusable).toHaveLength(3);
    expect(named).toHaveLength(3);
  });
});
