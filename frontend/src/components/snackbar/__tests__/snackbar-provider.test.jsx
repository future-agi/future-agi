import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";

import SnackbarProvider from "../snackbar-provider";

// Toasts sit on the right (bottom-right of the page content),
// as in production. notistack is stubbed so the test reads the props the
// provider gives it; the provider's own GlobalStyles still render.
const seen = vi.hoisted(() => ({ props: null }));

vi.mock("notistack", async () => {
  const { Component, Fragment, createElement, forwardRef } =
    await import("react");
  class NotistackStub extends Component {
    render() {
      seen.props = this.props;
      return createElement(Fragment, null, seen.props.children);
    }
  }
  return {
    closeSnackbar: () => {},
    SnackbarProvider: NotistackStub,
    MaterialDesignContent: forwardRef(function ContentStub(_props, ref) {
      return createElement("div", { ref });
    }),
  };
});
vi.mock("../../settings", () => ({
  useSettingsContext: () => ({ themeDirection: "ltr" }),
}));
vi.mock("../../iconify", () => ({ default: () => null }));

describe("toast provider (toasts on the right)", () => {
  it("anchors toasts bottom-right; max 2, dedupe and 4 s unchanged", () => {
    render(
      <SnackbarProvider>
        <span>page</span>
      </SnackbarProvider>,
    );
    expect(seen.props.anchorOrigin).toEqual({
      vertical: "bottom",
      horizontal: "right",
    });
    // The measured placement styles the bottom-right container.
    expect(seen.props.classes).toEqual({
      containerAnchorOriginBottomRight: "fa-toasts",
    });
    expect(seen.props.maxSnack).toBe(2);
    expect(seen.props.preventDuplicate).toBe(true);
    expect(seen.props.autoHideDuration).toBe(4000);
  });

  it("places the container by the measured right inset, not a left one", () => {
    render(
      <SnackbarProvider>
        <span>page</span>
      </SnackbarProvider>,
    );
    const css = Array.from(document.querySelectorAll("style"))
      .map((s) => s.textContent)
      .join("\n");
    expect(css).toContain("--fa-toast-right");
    expect(css).not.toContain("--fa-toast-left");
  });
});
