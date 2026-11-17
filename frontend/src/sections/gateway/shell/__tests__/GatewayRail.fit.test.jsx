import React from "react";
import { MemoryRouter } from "react-router-dom";
import { render, screen, within } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { describe, expect, it } from "vitest";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

import GatewayRail from "../GatewayRail";
import GatewayRailScroll from "../GatewayRailScroll";
import { gatewayRailGroups } from "../gateway-rail";

// Billing & credits lives in Settings. These tests pin spacing and the
// finder's additional height, not a browser/phone layout guarantee.
//
// The model, from a browser measurement: with 19 items the rail was 782 px in
// a 733 px box, items 28 px each. Without Billing & credits: 754 px. The rail
// also drops what only adds height at its end or inside its group labels,
// never the gap between groups (header 8 px above, group 10 px below, as the
// Future AGI nav):
// - the last group's 10 px bottom margin and the scroller's 4 px bottom
//   padding (14 px);
// - each group label's 2 px padding above and below (2 px each per label).
// That is 716 px for 18 items in 7 groups (6 labels). Integrations adds an
// item (28 px), so MCP tools moves into the Providers group beside it and
// the MCP group goes: 19 items in 6 groups (5 labels), about 711 px, with
// one label (about 15 px), its 8 px above and its group's 10 px below gone.
// The finder now adds a 44 px minimum target plus 4 px below: approximately
// 759 px total, so the existing overflow cue/scroller is still necessary.
// jsdom lays nothing out; a browser walk must measure desktop and phone fit.

const theme = createTheme({ palette: palette("light"), typography });

function renderRail() {
  render(
    <ThemeProvider theme={theme}>
      <MemoryRouter initialEntries={["/dashboard/gateway/logs"]}>
        <GatewayRailScroll>
          <GatewayRail groups={gatewayRailGroups({ integrations: true })} />
        </GatewayRailScroll>
      </MemoryRouter>
    </ThemeProvider>,
  );
  return screen.getByTestId("gateway-rail");
}

describe("GatewayRail spacing contract (browser fit pending)", () => {
  it("includes the finder before navigation with a 44 px minimum target and 4 px gap", () => {
    const rail = renderRail();
    const finder = within(rail).getByRole("button", {
      name: "Find a gateway page",
    });
    expect(rail.firstElementChild).toBe(finder);
    expect(window.getComputedStyle(finder).minHeight).toBe("44px");
    expect(window.getComputedStyle(finder).marginBottom).toBe("4px");
    expect(within(rail).getAllByRole("link")).toHaveLength(19);
  });

  it("keeps the mini finder a labelled 44 px target without adding a navigation link", () => {
    render(
      <ThemeProvider theme={theme}>
        <MemoryRouter>
          <GatewayRail groups={gatewayRailGroups()} collapsed />
        </MemoryRouter>
      </ThemeProvider>,
    );
    const finder = screen.getByRole("button", { name: "Find a gateway page" });
    expect(window.getComputedStyle(finder).width).toBe("44px");
    expect(window.getComputedStyle(finder).height).toBe("44px");
    expect(finder).not.toHaveAttribute("href");
  });
  it("has 19 items in 6 groups, Integrations and MCP tools among them, Billing & credits not", () => {
    const groups = gatewayRailGroups({ integrations: true });
    expect(groups).toHaveLength(6);
    const titles = groups.flatMap((group) => group.items.map((i) => i.title));
    expect(titles).toHaveLength(19);
    expect(titles).toContain("Integrations");
    expect(titles).toContain("MCP tools");
    expect(titles).not.toContain("Billing & credits");
  });

  it("draws group labels with no vertical padding, keeping the gap above them", () => {
    const rail = renderRail();
    const labels = rail.querySelectorAll(".MuiListSubheader-root");
    expect(labels).toHaveLength(5);
    labels.forEach((label) => {
      const style = window.getComputedStyle(label);
      expect(style.paddingTop).toBe("0px");
      expect(style.paddingBottom).toBe("0px");
      expect(style.marginTop).toBe("8px");
    });
  });

  it("ends without a margin after the last group or padding under the rail", () => {
    const rail = renderRail();
    const groups = within(rail).getAllByRole("group");
    const itemsOf = (group) => group.lastElementChild;
    groups
      .slice(0, -1)
      .forEach((group) =>
        expect(window.getComputedStyle(itemsOf(group)).marginBottom).toBe(
          "10px",
        ),
      );
    expect(window.getComputedStyle(itemsOf(groups.at(-1))).marginBottom).toBe(
      "0px",
    );
    const content = document.querySelector(".simplebar-content");
    expect(window.getComputedStyle(content).paddingBottom).toBe("0px");
  });
});
