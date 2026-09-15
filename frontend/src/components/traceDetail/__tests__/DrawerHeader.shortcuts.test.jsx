import React from "react";
import { Dialog } from "@mui/material";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import DrawerHeader from "../DrawerHeader";

vi.mock("src/components/iconify", () => ({ default: () => <span /> }));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
afterEach(cleanup);

function header(props = {}) {
  const callbacks = { onClose: vi.fn(), onNext: vi.fn(), onPrev: vi.fn() };
  const view = render(<DrawerHeader open {...callbacks} {...props} />);
  return { ...callbacks, ...view };
}

describe("trace drawer keyboard ownership", () => {
  it("closes with the advertised Escape shortcut, including from an input", () => {
    const { onClose } = header();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
    const input = document.createElement("input");
    document.body.append(input);
    fireEvent.keyDown(input, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(2);
    input.remove();
  });

  it("releases shortcuts when the persistent drawer closes", () => {
    const callbacks = { onClose: vi.fn(), onNext: vi.fn(), onPrev: vi.fn() };
    const { rerender } = render(<DrawerHeader open {...callbacks} />);
    fireEvent.keyDown(document, { key: "j" });
    expect(callbacks.onNext).toHaveBeenCalledTimes(1);
    rerender(<DrawerHeader open={false} {...callbacks} />);
    for (const key of ["j", "k", "Escape"])
      fireEvent.keyDown(document, { key });
    expect(callbacks.onNext).toHaveBeenCalledTimes(1);
    expect(callbacks.onPrev).not.toHaveBeenCalled();
    expect(callbacks.onClose).not.toHaveBeenCalled();
  });

  it("does not consume shortcuts already handled by a child", () => {
    const { onClose } = header();
    const event = new KeyboardEvent("keydown", {
      key: "Escape",
      bubbles: true,
      cancelable: true,
    });
    event.preventDefault();
    document.dispatchEvent(event);
    expect(onClose).not.toHaveBeenCalled();
  });

  it("lets a nested modal consume Escape before closing the trace", () => {
    const onClose = vi.fn();
    const onModalClose = vi.fn();
    render(
      <>
        <DrawerHeader open onClose={onClose} />
        <Dialog open onClose={onModalClose}>
          <button type="button">Nested action</button>
        </Dialog>
      </>,
    );
    fireEvent.keyDown(screen.getByRole("button", { name: "Nested action" }), {
      key: "Escape",
    });
    expect(onModalClose).toHaveBeenCalledTimes(1);
    expect(onClose).not.toHaveBeenCalled();
  });

  it("keeps typing and modified navigation keys out of trace navigation", () => {
    const { onNext, onPrev } = header();
    render(<input aria-label="edit trace" />);
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "j" });
    fireEvent.keyDown(document, { key: "j", ctrlKey: true });
    fireEvent.keyDown(document, { key: "k", metaKey: true });
    expect(onNext).not.toHaveBeenCalled();
    expect(onPrev).not.toHaveBeenCalled();
  });

  it("closes by button without submitting an enclosing form", () => {
    const onClose = vi.fn();
    const onSubmit = vi.fn((event) => event.preventDefault());
    render(
      <form onSubmit={onSubmit}>
        <DrawerHeader open onClose={onClose} />
      </form>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Close (Esc)" }));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onSubmit).not.toHaveBeenCalled();
  });
});
