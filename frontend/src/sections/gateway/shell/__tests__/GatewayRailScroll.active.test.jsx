import React from "react";
import { act, cleanup, render, waitFor } from "@testing-library/react";
import { MemoryRouter, useNavigate } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

import GatewayRail from "../GatewayRail";
import GatewayRailScroll from "../GatewayRailScroll";
import { gatewayRailGroups } from "../gateway-rail";

const groups = gatewayRailGroups({ integrations: true });
const pathOf = (title) =>
  groups.flatMap((g) => g.items).find((item) => item.title === title).path;

// jsdom has no scrollIntoView: record each call with the element it ran on.
let calls;
let saved;
beforeEach(() => {
  calls = [];
  saved = Element.prototype.scrollIntoView;
  Element.prototype.scrollIntoView = function scrollIntoView(options) {
    calls.push({ el: this, options });
  };
});
afterEach(() => {
  cleanup();
  Element.prototype.scrollIntoView = saved;
});

let navigate;
function Navigator() {
  navigate = useNavigate();
  return null;
}

function renderRailAt(path) {
  return render(
    <ThemeProvider
      theme={createTheme({ palette: palette("light"), typography })}
    >
      <MemoryRouter initialEntries={[path]}>
        <Navigator />
        <GatewayRailScroll>
          <GatewayRail groups={groups} />
        </GatewayRailScroll>
      </MemoryRouter>
    </ThemeProvider>,
  );
}

const current = (container) => container.querySelector("[aria-current='page']");

// At 1024x768 and 1280x800 the rail scrolls, but on
// Webhooks or Gateway settings the highlighted item sat below the fold, so
// the person could not see where they were. The rail brings the current
// page's item into view on load and on every route change.
describe("GatewayRail keeps the current page in view (layout)", () => {
  it("scrolls the current page's item into view when the rail mounts", () => {
    const { container } = renderRailAt(pathOf("Webhooks"));
    const active = current(container);
    expect(active).toHaveTextContent("Webhooks");
    const call = calls.find((c) => c.el === active);
    expect(call).toBeDefined();
    expect(call.options).toMatchObject({ block: "nearest" });
  });

  it("scrolls the new page's item into view when the route changes", async () => {
    const { container } = renderRailAt(pathOf("Overview"));
    act(() => navigate(pathOf("Gateway settings")));
    const active = current(container);
    expect(active).toHaveTextContent("Gateway settings");
    await waitFor(() => expect(calls.some((c) => c.el === active)).toBe(true));
  });
});
