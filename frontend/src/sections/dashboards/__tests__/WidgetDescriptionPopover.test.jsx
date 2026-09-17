import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";
import { createEvent } from "@testing-library/react";
import WidgetDescriptionPopover from "../WidgetDescriptionPopover";

const setup = (props = {}) => {
  const onChange = vi.fn();
  const onClose = vi.fn();
  const anchor = document.createElement("div");
  document.body.appendChild(anchor);
  const ui = (extra) => (
    <WidgetDescriptionPopover
      open
      anchorEl={anchor}
      value=""
      onChange={onChange}
      onClose={onClose}
      {...props}
      {...extra}
    />
  );
  const view = render(ui());
  return { onChange, onClose, rerender: (extra) => view.rerender(ui(extra)) };
};

describe("WidgetDescriptionPopover", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows the current description", () => {
    setup({ value: "p95 latency across production calls" });
    expect(
      screen.getByDisplayValue("p95 latency across production calls"),
    ).toBeInTheDocument();
  });

  it("holds edits until Done, then reports a plain string, not an event", () => {
    const { onChange } = setup();
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "Error rate by model" },
    });
    // Typing must not reach the widget — the toolbar preview would follow it.
    expect(onChange).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    expect(onChange).toHaveBeenCalledWith("Error rate by model");
  });

  it("closes on Done", () => {
    const { onClose } = setup({ value: "Anything" });
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("discards the edit when dismissed without Done", () => {
    const { onChange, onClose } = setup({ value: "Original" });
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "Half-typed thought" },
    });

    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Escape" });

    expect(onClose).toHaveBeenCalled();
    expect(onChange).not.toHaveBeenCalled();
  });

  it("reopens on the widget's value, not the discarded draft", () => {
    const { rerender } = setup({ value: "Original" });
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "Half-typed thought" },
    });

    rerender({ open: false });
    rerender({ open: true });

    expect(screen.getByRole("textbox")).toHaveValue("Original");
  });

  it("closes on modifier+Enter but not on a bare Enter, which inserts a line break", () => {
    const { onClose } = setup({ value: "Line one" });
    const field = screen.getByRole("textbox");

    const bare = createEvent.keyDown(field, { key: "Enter" });
    fireEvent(field, bare);
    expect(onClose).not.toHaveBeenCalled();
    // The line break is the whole point of a bare Enter — leave it alone.
    expect(bare.defaultPrevented).toBe(false);

    const shortcut = createEvent.keyDown(field, {
      key: "Enter",
      metaKey: true,
    });
    fireEvent(field, shortcut);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("commits the edit on modifier+Enter, as Done does", () => {
    const { onChange } = setup({ value: "Line one" });
    const field = screen.getByRole("textbox");
    fireEvent.change(field, { target: { value: "Line one and two" } });

    fireEvent.keyDown(field, { key: "Enter", metaKey: true });

    expect(onChange).toHaveBeenCalledWith("Line one and two");
  });

  it("does not let the close shortcut insert a line break of its own", () => {
    setup({ value: "Line one" });
    const field = screen.getByRole("textbox");

    // Ctrl+Enter still inserts a newline on Windows/Linux unless suppressed.
    const shortcut = createEvent.keyDown(field, {
      key: "Enter",
      ctrlKey: true,
    });
    fireEvent(field, shortcut);
    expect(shortcut.defaultPrevented).toBe(true);
  });

  it("renders nothing while closed", () => {
    setup({ open: false, value: "Hidden" });
    expect(screen.queryByRole("textbox")).toBeNull();
  });
});
