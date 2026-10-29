import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import VerticalNavList from "../vertical/nav-list";
import MiniNavList from "../mini/nav-list";

const agentsItem = {
  title: "Agents",
  path: "/dashboard/agents/connected",
  activePath: "/dashboard/agents",
  icon: <span />,
};

const renderAt = (NavList, url, data = agentsItem) =>
  render(
    <ThemeProvider theme={createTheme()}>
      <MemoryRouter initialEntries={[url]}>
        <NavList data={data} depth={1} slotProps={{}} />
      </MemoryRouter>
    </ThemeProvider>,
  );

const link = () => screen.getByRole("link");
// NavItem puts the active class on the button inside the link.
const isActive = () =>
  link().classList.contains("active") || !!link().querySelector(".active");

describe.each([
  ["vertical", VerticalNavList],
  ["mini", MiniNavList],
])("%s nav item with activePath", (_, NavList) => {
  it("links to its own path", () => {
    renderAt(NavList, "/dashboard/agents/connected");
    expect(link()).toHaveAttribute("href", "/dashboard/agents/connected");
  });

  it("stays active on a sibling route under activePath", () => {
    renderAt(NavList, "/dashboard/agents/playground");
    expect(isActive()).toBe(true);
  });

  it("stays active on a deep route under activePath", () => {
    renderAt(NavList, "/dashboard/agents/playground/abc/build");
    expect(isActive()).toBe(true);
  });

  it("is not active outside activePath", () => {
    renderAt(NavList, "/dashboard/prompt");
    expect(isActive()).toBe(false);
  });

  it("falls back to path when there is no activePath", () => {
    renderAt(NavList, "/dashboard/prompt/abc", {
      title: "Prompts",
      path: "/dashboard/prompt",
      icon: <span />,
    });
    expect(isActive()).toBe(true);
  });
});
