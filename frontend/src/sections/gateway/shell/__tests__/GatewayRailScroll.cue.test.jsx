import React from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

import GatewayRailScroll from "../GatewayRailScroll";

// jsdom lays nothing out, so every scroll size is 0: stub the two the cue
// reads, on every element, for the length of a test.
const box = { scrollHeight: 0, clientHeight: 0 };
const stubbed = ["scrollHeight", "clientHeight"];
let saved;

beforeEach(() => {
  saved = stubbed.map((name) => [
    name,
    Object.getOwnPropertyDescriptor(HTMLElement.prototype, name),
  ]);
  stubbed.forEach((name) =>
    Object.defineProperty(HTMLElement.prototype, name, {
      configurable: true,
      get: () => box[name],
    }),
  );
});

afterEach(() => {
  cleanup();
  saved.forEach(([name, descriptor]) =>
    descriptor
      ? Object.defineProperty(HTMLElement.prototype, name, descriptor)
      : delete HTMLElement.prototype[name],
  );
});

function renderScroll() {
  render(
    <ThemeProvider
      theme={createTheme({ palette: palette("light"), typography })}
    >
      <GatewayRailScroll>
        <nav>rail</nav>
      </GatewayRailScroll>
    </ThemeProvider>,
  );
  return screen.getByTestId("gateway-rail-scroll");
}

const fadeOf = (area) => {
  const mask = window.getComputedStyle(area).maskImage || area.style.maskImage;
  return Number(/calc\(100% - (\d+)px\)/.exec(mask || "")?.[1] ?? 0);
};

// At 1024x768 and 1280x800 the rail is cut off, and the cut
// fell on the gap between items, so the 16 px fade covered empty space and
// the rail looked complete. The fade is taller and a chevron says "more".
describe("GatewayRail 'more below' cue (layout)", () => {
  it("fades a band tall enough to cover part of an item", () => {
    box.scrollHeight = 812;
    box.clientHeight = 620;
    expect(fadeOf(renderScroll())).toBeGreaterThanOrEqual(32);
  });

  it("shows a chevron while items are below, and none at the end", () => {
    box.scrollHeight = 812;
    box.clientHeight = 620;
    const area = renderScroll();
    expect(screen.getByTestId("gateway-rail-more")).toBeInTheDocument();
    const scroller = area.querySelector("[data-rail-scroller]");
    act(() => {
      scroller.scrollTop = 192;
      fireEvent.scroll(scroller);
    });
    expect(screen.queryByTestId("gateway-rail-more")).toBeNull();
  });

  it("shows no chevron when the rail fits", () => {
    box.scrollHeight = 600;
    box.clientHeight = 620;
    renderScroll();
    expect(screen.queryByTestId("gateway-rail-more")).toBeNull();
  });

  it("scrolls the rail down when the chevron is clicked", () => {
    box.scrollHeight = 812;
    box.clientHeight = 620;
    const area = renderScroll();
    const scroller = area.querySelector("[data-rail-scroller]");
    scroller.scrollBy = vi.fn();
    fireEvent.click(screen.getByTestId("gateway-rail-more"));
    expect(scroller.scrollBy).toHaveBeenCalledTimes(1);
    expect(scroller.scrollBy.mock.calls[0][0].top).toBeGreaterThan(0);
  });
});
