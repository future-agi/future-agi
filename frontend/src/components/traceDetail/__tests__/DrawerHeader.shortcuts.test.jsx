import React from "react";
import { Dialog } from "@mui/material";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import DrawerHeader from "../DrawerHeader";

vi.mock("src/components/iconify", () => ({ default: () => <span /> }));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

const callbacks = () => ({
  onClose: vi.fn(),
  onNext: vi.fn(),
  onPrev: vi.fn(),
});

describe("trace drawer header keyboard shortcuts", () => {
  it("navigates with J/K and closes with Esc while open", () => {
    const handlers = callbacks();
    render(<DrawerHeader open {...handlers} />);
    fireEvent.keyDown(document, { key: "j" });
    fireEvent.keyDown(document, { key: "k" });
    fireEvent.keyDown(document, { key: "Escape" });
    expect(handlers.onNext).toHaveBeenCalledOnce();
    expect(handlers.onPrev).toHaveBeenCalledOnce();
    expect(handlers.onClose).toHaveBeenCalledOnce();
  });

  it("ignores every shortcut while the persistent drawer is closed", () => {
    const handlers = callbacks();
    const { rerender } = render(<DrawerHeader open {...handlers} />);
    rerender(<DrawerHeader open={false} {...handlers} />);
    for (const key of ["j", "J", "k", "K", "Escape"]) {
      fireEvent.keyDown(document, { key });
    }
    expect(handlers.onNext).not.toHaveBeenCalled();
    expect(handlers.onPrev).not.toHaveBeenCalled();
    expect(handlers.onClose).not.toHaveBeenCalled();

    rerender(<DrawerHeader open {...handlers} />);
    fireEvent.keyDown(document, { key: "j" });
    expect(handlers.onNext).toHaveBeenCalledOnce();
  });

  it.each([
    ["Cmd", { metaKey: true }],
    ["Ctrl", { ctrlKey: true }],
    ["Alt", { altKey: true }],
    ["Shift", { shiftKey: true }],
  ])("leaves %s+key to the browser", (_name, modifier) => {
    const handlers = callbacks();
    render(<DrawerHeader open {...handlers} />);
    for (const key of ["j", "J", "k", "K", "Escape"]) {
      fireEvent.keyDown(document, { key, ...modifier });
    }
    expect(handlers.onNext).not.toHaveBeenCalled();
    expect(handlers.onPrev).not.toHaveBeenCalled();
    expect(handlers.onClose).not.toHaveBeenCalled();
  });

  it("leaves keys typed into an input to the input, Escape included", () => {
    const handlers = callbacks();
    render(
      <>
        <DrawerHeader open {...handlers} />
        <input aria-label="span search" />
        <textarea aria-label="note" />
      </>,
    );
    for (const field of [
      screen.getByRole("textbox", { name: "span search" }),
      screen.getByRole("textbox", { name: "note" }),
    ]) {
      for (const key of ["j", "k", "Escape"]) {
        fireEvent.keyDown(field, { key });
      }
    }
    expect(handlers.onNext).not.toHaveBeenCalled();
    expect(handlers.onPrev).not.toHaveBeenCalled();
    expect(handlers.onClose).not.toHaveBeenCalled();
  });

  it("does not act on a key a nested handler already consumed", () => {
    const handlers = callbacks();
    render(<DrawerHeader open {...handlers} />);
    const event = new KeyboardEvent("keydown", {
      key: "Escape",
      bubbles: true,
      cancelable: true,
    });
    event.preventDefault();
    document.dispatchEvent(event);
    expect(handlers.onClose).not.toHaveBeenCalled();
  });

  it("lets a nested dialog close on Esc without closing the trace", () => {
    const handlers = callbacks();
    const onDialogClose = vi.fn();
    render(
      <>
        <DrawerHeader open {...handlers} />
        <Dialog open onClose={onDialogClose}>
          <button type="button">Nested action</button>
        </Dialog>
      </>,
    );
    fireEvent.keyDown(screen.getByRole("button", { name: "Nested action" }), {
      key: "Escape",
    });
    expect(onDialogClose).toHaveBeenCalledOnce();
    expect(handlers.onClose).not.toHaveBeenCalled();
  });

  it("does not submit an enclosing form from its buttons", () => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: vi.fn(() => Promise.resolve()) },
    });
    const handlers = callbacks();
    const onSubmit = vi.fn((event) => event.preventDefault());
    const { container } = render(
      <form onSubmit={onSubmit}>
        <DrawerHeader
          open
          traceId="trace-1"
          {...handlers}
          onFullscreen={vi.fn()}
          onOpenNewTab={vi.fn()}
          onDownload={vi.fn()}
          onShare={vi.fn()}
        />
      </form>,
    );
    const buttons = container.querySelectorAll("button");
    expect(buttons.length).toBeGreaterThan(0);
    for (const button of buttons) {
      expect(button).toHaveAttribute("type", "button");
      fireEvent.click(button);
    }
    expect(onSubmit).not.toHaveBeenCalled();
    expect(handlers.onClose).toHaveBeenCalledOnce();
  });
});
