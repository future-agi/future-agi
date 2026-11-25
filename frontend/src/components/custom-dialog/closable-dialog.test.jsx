import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import ClosableDialog from "./closable-dialog";

describe("ClosableDialog close action", () => {
  it.each([undefined, "Close image"])(
    "preserves the default or contextual close label: %s",
    (closeLabel) => {
      const close = vi.fn();
      render(
        <ClosableDialog open onClose={close} closeLabel={closeLabel}>
          <p>Image evidence</p>
        </ClosableDialog>,
      );
      fireEvent.click(
        screen.getByRole("button", {
          name: closeLabel || "Close",
          exact: true,
        }),
      );
      expect(close).toHaveBeenCalledOnce();
      expect(close.mock.calls[0][1]).toBe("closeButton");
      expect(screen.getByRole("dialog")).not.toHaveAttribute("closeLabel");
    },
  );
});
