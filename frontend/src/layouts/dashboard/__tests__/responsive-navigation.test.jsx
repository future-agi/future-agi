import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { createTheme, ThemeProvider } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import NavVertical from "../nav-vertical";

const state = vi.hoisted(() => ({
  pathname: "/dashboard/develop",
  width: 390,
  settingOpen: false,
}));
vi.mock("src/routes/hooks", () => ({ usePathname: () => state.pathname }));
vi.mock("src/hooks/use-responsive", () => ({
  useResponsive: (_query, breakpoint) =>
    state.width >= { xs: 0, md: 900, lg: 1200 }[breakpoint],
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: { role: "Owner" } }),
}));
vi.mock("src/components/logo", () => ({ default: () => <span>App</span> }));
vi.mock("src/sections/omega/shell/NavBrandRow", () => ({
  default: () => <span>App</span>,
}));
vi.mock("../useShellNavData", () => ({ useShellNavData: () => [] }));
vi.mock("src/components/scrollbar", () => ({
  default: ({ children }) => <div>{children}</div>,
}));
vi.mock("src/components/nav-section", () => ({
  NavSectionVertical: () => <a href="/dashboard/develop">Datasets</a>,
}));
vi.mock("../WorkspaceSwitcher/WorkspaceSwitcher", () => ({
  default: () => <button>Workspace</button>,
}));
vi.mock("../config-navigation", () => ({
  useNavData: () => [],
  useNavUpgradeData: () => [],
  useNavSettingsData: () => [],
  useNavDashBoardData: () => [],
  useWorkspaceSettingsNav: () => null,
  useNavGatewayData: () => [
    {
      id: "providers",
      subheader: "Providers",
      items: [
        { title: "Providers", path: "/dashboard/gateway/providers", icon: "providers" },
        { title: "Logs", path: "/dashboard/gateway/logs", icon: "logs" },
      ],
    },
  ],
}));
vi.mock("../states", () => ({
  syncSettingsOpen: (pathname) => { state.settingOpen = pathname.startsWith("/dashboard/settings"); },
  useSettingsOpen: () => ({
    settingOpen: state.settingOpen,
    setSettingOpen: (value) => {
      state.settingOpen = value;
    },
  }),
}));
const wrap = (node) => (
  <MemoryRouter initialEntries={[state.pathname]}>
    <ThemeProvider theme={createTheme({ palette: palette("light") })}>
      {node}
    </ThemeProvider>
  </MemoryRouter>
);

beforeEach(() => {
  state.pathname = "/dashboard/develop";
  state.width = 390;
  state.settingOpen = false;
});
describe("responsive navigation lifecycle", () => {
  it.each([390, 768])(
    "does not close a newly opened navigation drawer at %i px",
    (width) => {
      state.width = width;
      const close = vi.fn();
      const { rerender } = render(
        wrap(<NavVertical openNav={false} onCloseNav={close} />),
      );
      close.mockClear();
      rerender(wrap(<NavVertical openNav onCloseNav={close} />));
      expect(
        screen.getByRole("navigation", { name: "Main navigation" }),
      ).toBeTruthy();
      expect(close).not.toHaveBeenCalled();
      state.pathname = "/dashboard/observe";
      rerender(wrap(<NavVertical openNav onCloseNav={close} />));
      expect(close).toHaveBeenCalledTimes(1);
    },
  );
  it("has no mobile navigation in the document flow while closed", () => {
    render(wrap(<NavVertical openNav={false} onCloseNav={vi.fn()} />));
    expect(
      screen.queryByRole("navigation", { name: "Main navigation" }),
    ).toBeNull();
  });
  it("keeps desktop navigation visible without an open drawer", () => {
    state.width = 1440;
    render(wrap(<NavVertical openNav={false} onCloseNav={vi.fn()} />));
    expect(
      screen.getByRole("navigation", { name: "Main navigation" }),
    ).toBeTruthy();
  });
  it("keeps gateway navigation in a dismissible mobile drawer with real links", () => {
    state.pathname = "/dashboard/gateway/providers";
    const close = vi.fn();
    render(wrap(<NavVertical openNav onCloseNav={close} />));
    expect(screen.getByRole("navigation", { name: "AI Gateway" })).toBeTruthy();
    const providers = screen.getByRole("link", { name: "Providers" });
    expect(providers.getAttribute("href")).toBe("/dashboard/gateway/providers");
    expect(providers.getAttribute("aria-current")).toBe("page");
    expect(close).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Close navigation" }));
    expect(close).toHaveBeenCalledTimes(1);
  });
  it("does not render a closed desktop gateway panel as keyboard reachable", () => {
    // The shared frame has no off-route gateway panel or hidden links.
    state.width = 1440;
    render(wrap(<NavVertical openNav={false} onCloseNav={vi.fn()} />));
    expect(screen.queryByRole("link", { name: "Providers" })).toBeNull();
    expect(screen.queryByRole("navigation", { name: "AI Gateway" })).toBeNull();
    expect(screen.getByRole("navigation", { name: "Main navigation" })).toBeTruthy();
  });
  it("closes the gateway drawer after navigation, not when it opens", () => {
    state.pathname = "/dashboard/gateway/providers";
    const close = vi.fn();
    const { rerender } = render(
      wrap(<NavVertical openNav={false} onCloseNav={close} />),
    );
    rerender(wrap(<NavVertical openNav onCloseNav={close} />));
    expect(close).not.toHaveBeenCalled();
    const logs = screen.getByRole("link", { name: "Logs" });
    expect(logs.getAttribute("href")).toBe("/dashboard/gateway/logs");
    fireEvent.click(logs);
    state.pathname = "/dashboard/gateway/logs";
    rerender(wrap(<NavVertical openNav onCloseNav={close} />));
    expect(close).toHaveBeenCalledTimes(1);
  });
  it("keeps the desktop gateway rail visible without an open drawer", () => {
    state.width = 1440;
    state.pathname = "/dashboard/gateway/providers";
    render(wrap(<NavVertical openNav={false} onCloseNav={vi.fn()} />));
    expect(screen.getByRole("navigation", { name: "AI Gateway" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Providers" }).getAttribute("href"))
      .toBe("/dashboard/gateway/providers");
    expect(screen.queryByRole("button", { name: "Close navigation" })).toBeNull();
  });
});
