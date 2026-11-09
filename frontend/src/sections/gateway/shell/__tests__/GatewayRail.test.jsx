import React from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

import GatewayRailScroll from "../GatewayRailScroll";

// jsdom lays nothing out, so every scroll size is 0: stub the three the cue
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

// At 1440x960 the rail scrolls by 49 px under the
// Resources footer and nothing shows that it does ("Budgets" cut in half,
// "Billing & credits" hidden).
describe("GatewayRail overflow cue", () => {
  it("shows the overflow cue when scrollHeight > clientHeight", () => {
    box.scrollHeight = 812;
    box.clientHeight = 763;
    const area = renderScroll();
    expect(area).toHaveAttribute("data-more-below", "true");
    expect(
      window.getComputedStyle(area).maskImage || area.style.maskImage,
    ).toMatch(/linear-gradient/);
  });

  it("drops the cue once the rail is scrolled to its end", () => {
    box.scrollHeight = 812;
    box.clientHeight = 763;
    const area = renderScroll();
    const scroller = area.querySelector("[data-rail-scroller]");
    act(() => {
      scroller.scrollTop = 49;
      fireEvent.scroll(scroller);
    });
    expect(area).toHaveAttribute("data-more-below", "false");
  });

  it("shows no cue when the rail fits", () => {
    box.scrollHeight = 700;
    box.clientHeight = 763;
    expect(renderScroll()).toHaveAttribute("data-more-below", "false");
  });
});
