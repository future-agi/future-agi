import React from "react";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { palette } from "src/theme/palette";
import GatewayRail from "../GatewayRail";

const state = vi.hoisted(() => ({ pathname: "/dashboard/gateway/providers" }));
vi.mock("src/routes/hooks", () => ({ usePathname: () => state.pathname }));

const groups = [{
  id: "gateway",
  subheader: "AI Gateway",
  items: [
    { title: "Overview", path: "/dashboard/gateway", icon: "overview" },
    { title: "Providers", path: "/dashboard/gateway/providers", icon: "providers" },
    { title: "Logs", path: "/dashboard/gateway/logs", icon: "logs" },
  ],
}];

function rail(collapsed) {
  return (
    <MemoryRouter>
      <ThemeProvider theme={createTheme({ palette: palette("light") })}>
        <GatewayRail groups={groups} collapsed={collapsed} />
      </ThemeProvider>
    </MemoryRouter>
  );
}

function expectCurrent(path) {
  const nav = screen.getByRole("navigation", { name: "AI Gateway" });
  const links = [...nav.querySelectorAll("a[href]")];
  expect(links.map((link) => link.getAttribute("href"))).toEqual(
    groups[0].items.map((item) => item.path),
  );
  for (const link of links) {
    if (link.getAttribute("href") === path) {
      expect(link).toHaveAttribute("aria-current", "page");
    } else {
      expect(link).not.toHaveAttribute("aria-current");
    }
  }
  // The state belongs to the navigable anchor, not its nested visual control.
  expect([...nav.querySelectorAll('[aria-current="page"]')]).toEqual(
    path ? links.filter((link) => link.getAttribute("href") === path) : [],
  );
}

beforeEach(() => { state.pathname = "/dashboard/gateway/providers"; });

describe.each([false, true])("gateway anchor semantics (collapsed=%s)", (collapsed) => {
  it("marks only the active router anchor and updates it after navigation", () => {
    const { rerender } = render(rail(collapsed));
    expectCurrent("/dashboard/gateway/providers");
    state.pathname = "/dashboard/gateway/logs/request-id";
    rerender(rail(collapsed));
    expectCurrent("/dashboard/gateway/logs");
  });

  it("does not mark a route outside the gateway as current", () => {
    state.pathname = "/dashboard/settings/billing";
    render(rail(collapsed));
    expectCurrent(null);
  });
});
