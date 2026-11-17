import React from "react";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { afterEach, describe, expect, it, vi } from "vitest";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

import FileViewer from "../components/FileViewer";

// The code pane of the Files tab and of a review's Files, at any width. A
// line keeps to one line as written and the pane scrolls sideways for it.
// While a line runs past the pane's right edge, a fade there shows that it
// goes on, so a cut line is never silent; scrolled to its end, the pane has
// nothing more to the right and the fade goes. The fade only decorates: a
// screen reader skips it and a pointer passes through it, and it is drawn
// in the pane's own fill in either theme. A flagged line still wraps inside
// the pane, its note under it, both drawn above the fade. A pane that
// scrolls, sideways or down, can be reached by keyboard, so it can be
// scrolled without a pointer, and its focus ring is drawn above the fade,
// so no side of it is lost under the fade.

const LONG =
  "const answer = await runAgent({ model, tools: [getWeather], instructions: 'Answer from the tool result only.' });";
const FLAGGED =
  "writeFileSync(join(outputDir, 'weather.json'), JSON.stringify(answer));";
const TEXT = ["// The weather agent's last step.", LONG, FLAGGED].join("\n");
const MARKS = [
  {
    line: 3,
    kind: "finding",
    rule: "writes-code",
    text: "writeFileSync() writes a computed path",
  },
];
const PANE_NAME = { name: "Contents of main.mjs" };

// The pane as a browser lays it out: lines `scrollWidth` wide and
// `scrollHeight` high in a box `clientWidth` wide and `clientHeight` high,
// scrolled `scrollLeft` from its start. jsdom lays nothing out, so these are
// given here, for the pane only.
const UNLAID = {
  scrollWidth: 0,
  clientWidth: 0,
  scrollHeight: 0,
  clientHeight: 0,
  scrollLeft: 0,
};
const layout = { ...UNLAID };
// This file's own spies, restored after each test (and nothing else).
const spies = [];

function isPane(node) {
  return node.getAttribute?.("data-testid") === "hub-file-pane";
}

function layOutPane(sizes) {
  Object.assign(layout, sizes);
  for (const name of Object.keys(layout)) {
    const spy = vi.spyOn(Element.prototype, name, "get");
    spy.mockImplementation(function () {
      return isPane(this) ? layout[name] : 0;
    });
    spies.push(spy);
  }
}

function show({ mode = "light", ...props } = {}) {
  const theme = createTheme({ palette: palette(mode), typography });
  return render(
    <ThemeProvider theme={theme}>
      <FileViewer path="main.mjs" text={TEXT} marks={MARKS} {...props} />
    </ThemeProvider>,
  );
}

const fade = () => screen.queryByTestId("hub-file-fade");

// A selector jsdom cannot match (a pseudo-class or element) never applies.
function matches(node, selector) {
  try {
    return node.matches(selector);
  } catch {
    return false;
  }
}

// What the page's style rules declare for `node`: `property` as written, or
// "". With `state`, only the rules for `node` in that state, that is, those
// whose selector is one for `node` followed by `state` (such as `:has(` or
// `:focus-visible`, which jsdom does not match itself). jsdom keeps no
// gradient in a computed style, so a fill is read here.
function declared(node, property, state = "") {
  let value = "";
  for (const sheet of Array.from(document.styleSheets)) {
    for (const rule of Array.from(sheet.cssRules || [])) {
      const selector = rule.selectorText || "";
      const at = state ? selector.indexOf(state) : selector.length;
      if (at > 0 && matches(node, selector.slice(0, at))) {
        value = rule.style.getPropertyValue(property) || value;
      }
    }
  }
  return value;
}

afterEach(() => {
  for (const spy of spies.splice(0)) spy.mockRestore();
  vi.unstubAllGlobals();
  Object.assign(layout, UNLAID);
});

describe("Code pane: a line wider than the pane", () => {
  it("keeps the line on one line and shows a fade at the pane's right edge, which a screen reader and a pointer pass over", () => {
    layOutPane({ scrollWidth: 900, clientWidth: 400 });
    show();
    const edge = screen.getByTestId("hub-file-fade");
    const viewer = screen.getByTestId("hub-file-viewer");
    expect(viewer).toContainElement(edge);
    expect(edge).toHaveAttribute("aria-hidden", "true");
    expect(edge).toBeEmptyDOMElement();
    expect(getComputedStyle(edge).position).toBe("absolute");
    expect(getComputedStyle(edge).pointerEvents).toBe("none");
    const line = within(viewer).getByText(LONG);
    expect(getComputedStyle(line).whiteSpace).toBe("pre");
  });

  it("keeps a flagged line wrapping inside the pane, its note under it, both drawn above the fade", () => {
    layOutPane({ scrollWidth: 900, clientWidth: 400 });
    show();
    expect(fade()).not.toBeNull();
    const viewer = screen.getByTestId("hub-file-viewer");
    const flagged = within(viewer).getByText(FLAGGED);
    expect(getComputedStyle(flagged).whiteSpace).toBe("pre-wrap");
    const row = flagged.parentElement;
    const note = row.nextElementSibling;
    expect(note).toHaveTextContent(
      "writes-code: writeFileSync() writes a computed path",
    );
    for (const node of [row, note]) {
      expect(declared(node, "position")).toBe("relative");
      expect(declared(node, "z-index")).toBe("1");
    }
  });

  it("shows no fade when every line fits", () => {
    layOutPane({ scrollWidth: 400, clientWidth: 400 });
    show();
    expect(screen.getByTestId("hub-file-viewer")).toHaveTextContent(LONG);
    expect(fade()).toBeNull();
  });

  it("takes the fade away once the pane is scrolled to its end, and brings it back when scrolled back", () => {
    layOutPane({ scrollWidth: 900, clientWidth: 400 });
    show();
    expect(fade()).not.toBeNull();
    const pane = screen.getByTestId("hub-file-pane");
    layout.scrollLeft = 500;
    fireEvent.scroll(pane);
    expect(fade()).toBeNull();
    layout.scrollLeft = 120;
    fireEvent.scroll(pane);
    expect(fade()).not.toBeNull();
  });

  it("adds the fade when the pane narrows past its longest line, and takes it away when the pane widens again", () => {
    // Each observer of the pane hears when the pane's box changes size.
    const watchers = [];
    vi.stubGlobal(
      "ResizeObserver",
      class {
        constructor(onResize) {
          this.onResize = onResize;
          watchers.push(this);
        }

        observe() {}

        unobserve() {}

        disconnect() {}
      },
    );
    layOutPane({ scrollWidth: 900, clientWidth: 900 });
    show();
    expect(fade()).toBeNull();
    const resizeTo = (width) => {
      layout.clientWidth = width;
      act(() => {
        for (const watcher of watchers) watcher.onResize([]);
      });
    };
    resizeTo(400);
    expect(fade()).not.toBeNull();
    resizeTo(900);
    expect(fade()).toBeNull();
  });

  it("can be reached by keyboard while it scrolls sideways, named for its file, and not while every line fits", () => {
    layOutPane({ scrollWidth: 900, clientWidth: 400 });
    const { unmount } = show();
    const pane = screen.getByRole("region", PANE_NAME);
    expect(pane).toHaveAttribute("tabindex", "0");
    unmount();
    layout.clientWidth = 900;
    show();
    expect(screen.getByTestId("hub-file-viewer")).toHaveTextContent(LONG);
    expect(screen.queryByRole("region", PANE_NAME)).toBeNull();
  });

  it("draws its focus ring above the fade, on the viewer's frame, and none on the pane itself, where the fade would cover it", () => {
    layOutPane({ scrollWidth: 900, clientWidth: 400 });
    show();
    expect(fade()).not.toBeNull();
    const pane = screen.getByRole("region", PANE_NAME);
    const frame = screen.getByTestId("hub-file-viewer");
    expect(pane).toHaveClass("hub-file-pane");
    expect(declared(pane, "outline", ":focus-visible")).toBe("none");
    // The frame's rule for a focused pane draws a 2 px ring over all of it.
    expect(declared(frame, "outline", ":has(")).toMatch(/^2px solid /);
    expect(declared(frame, "z-index", ":has(")).toBe("2");
    expect(declared(frame, "pointer-events", ":has(")).toBe("none");
  });
});

describe("Code pane: a file longer than the pane", () => {
  it("can be reached by keyboard while it scrolls down, named for its file, with no fade while every line fits across", () => {
    layOutPane({
      scrollWidth: 400,
      clientWidth: 400,
      scrollHeight: 1200,
      clientHeight: 560,
    });
    show();
    const pane = screen.getByRole("region", PANE_NAME);
    expect(pane).toHaveAttribute("tabindex", "0");
    expect(fade()).toBeNull();
  });
});

describe.each(["light", "dark"])("Code pane in the %s theme", (mode) => {
  it("fades from clear to the pane's own fill at its right edge", () => {
    layOutPane({ scrollWidth: 900, clientWidth: 400 });
    show({ mode });
    const edge = screen.getByTestId("hub-file-fade");
    const image = declared(edge, "background-image");
    expect(image).toMatch(/^linear-gradient\(to right,/);
    expect(image).toContain(palette(mode).background.neutral);
  });
});
