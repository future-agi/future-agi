import { createRef } from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import SvgColor from "./svg-color";

// Importing an icon must not initialize template parsing or shared utilities.
vi.mock("src/utils/utils", () => {
  throw new Error("SvgColor must use the standalone ref helper");
});

afterEach(cleanup);

describe("SvgColor", () => {
  it("forwards and clears an object ref without changing its markup", () => {
    const ref = createRef<HTMLSpanElement>();
    const { unmount } = render(
      <SvgColor
        ref={ref}
        src="/favicon/logo.svg"
        className="test-icon"
        data-testid="icon"
      />,
    );
    const icon = screen.getByTestId("icon");
    expect(icon.tagName).toBe("SPAN");
    expect(icon).toHaveClass("svg-color", "test-icon");
    expect(ref.current).toBe(icon);
    unmount();
    expect(ref.current).toBeNull();
  });

  it("forwards a callback ref and preserves the image-load refresh", () => {
    const images: { onload: (() => void) | null; src: string }[] = [];
    vi.stubGlobal(
      "Image",
      class {
        onload = null;
        src = "";
        constructor() {
          images.push(this);
        }
      },
    );
    try {
      const ref = vi.fn();
      const { unmount } = render(
        <SvgColor ref={ref} src="/favicon/logo.svg" data-testid="icon" />,
      );
      const icon = screen.getByTestId("icon");
      expect(ref).toHaveBeenLastCalledWith(icon);
      expect(images[0].src).toBe("/favicon/logo.svg");
      images[0].onload?.();
      expect(icon.style.display).toBe("");
      unmount();
      expect(ref).toHaveBeenLastCalledWith(null);
      expect(images[0].onload).toBeNull();
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
