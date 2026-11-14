import React from "react";
import { act, render, screen, userEvent } from "src/utils/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ClippedColumnLabel from "../ClippedColumnLabel";

let widths;
let observers;
beforeEach(() => {
  widths = { scroll: 80, client: 108 };
  observers = [];
  vi.spyOn(HTMLElement.prototype, "scrollWidth", "get").mockImplementation(
    () => widths.scroll,
  );
  vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockImplementation(
    () => widths.client,
  );
  vi.stubGlobal(
    "ResizeObserver",
    class {
      constructor(callback) {
        this.callback = callback;
        observers.push(this);
      }
      observe() {}
      disconnect() {}
    },
  );
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("ClippedColumnLabel", () => {
  it("does not add a tab stop or tooltip when its complete label fits", async () => {
    const user = userEvent.setup();
    render(
      <>
        <ClippedColumnLabel label="tenant" />
        <button type="button">Next</button>
      </>,
    );
    const label = screen.getByText("tenant");
    expect(label).not.toHaveAttribute("tabindex");
    await user.tab();
    expect(screen.getByRole("button", { name: "Next" })).toHaveFocus();
    await user.hover(label);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("makes clipped text focusable and shows its full label on keyboard focus", async () => {
    widths.scroll = 300;
    const user = userEvent.setup();
    const name = "billing_reconciliation_account";
    render(<ClippedColumnLabel label={name} />);
    const label = screen.getByText(name);
    expect(label).toHaveAttribute("tabindex", "0");
    await user.tab();
    expect(label).toHaveFocus();
    expect(await screen.findByRole("tooltip")).toHaveTextContent(name);
  });

  it("updates focusability on resize without replacing the focused text node", async () => {
    const user = userEvent.setup();
    render(<ClippedColumnLabel label="tenant" />);
    const label = screen.getByText("tenant");
    expect(label).not.toHaveAttribute("tabindex");
    widths.client = 20;
    act(() => observers.forEach((observer) => observer.callback()));
    expect(label).toHaveAttribute("tabindex", "0");
    await user.tab();
    expect(label).toHaveFocus();
    widths.client = 200;
    act(() => observers.forEach((observer) => observer.callback()));
    expect(screen.getByText("tenant")).toBe(label);
    expect(label).not.toHaveAttribute("tabindex");
    expect(label).toHaveFocus();
  });

  it("remeasures changed text even if the container width did not change", () => {
    const { rerender } = render(<ClippedColumnLabel label="tenant" />);
    expect(screen.getByText("tenant")).not.toHaveAttribute("tabindex");
    widths.scroll = 300;
    rerender(<ClippedColumnLabel label="billing_reconciliation_account" />);
    expect(screen.getByText("billing_reconciliation_account")).toHaveAttribute(
      "tabindex",
      "0",
    );
  });
});
