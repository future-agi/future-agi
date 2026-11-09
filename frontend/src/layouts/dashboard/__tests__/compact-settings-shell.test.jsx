import React from "react";
import PropTypes from "prop-types";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { act, fireEvent, render, screen } from "src/utils/test-utils";

import DashboardLayout from "../index";
import NavVertical from "../nav-vertical";

const state = {
  pathname: "/dashboard/settings/mcp-server",
  belowMd: true,
  falconAllowed: false,
};

vi.mock("src/routes/hooks", () => ({
  usePathname: () => state.pathname,
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("src/hooks/use-responsive", () => ({
  useResponsive: (query, start) =>
    start === "md"
      ? query === "down" ? state.belowMd : !state.belowMd
      : true,
}));

vi.mock("src/components/settings", () => ({
  useSettingsContext: () => ({ themeLayout: "vertical" }),
}));

vi.mock("src/hooks/useCapabilities", async (importOriginal) => ({
  .../** @type {typeof import("src/hooks/useCapabilities")} */ (
    await importOriginal()
  ),
  useFeatureAllowed: () => ({ allowed: state.falconAllowed }),
  useCapabilities: () => ({
    data: { features: {} },
    isLoading: false,
    isError: false,
  }),
  useFeatureLocked: () => ({ locked: true, isLoading: false, isError: false }),
}));

vi.mock("src/sections/falcon-ai/FalconAISidebar", () => ({
  default: ({ variant }) => (
    <div data-testid="falcon-sidebar" data-variant={variant || "docked"} />
  ),
}));
vi.mock("src/sections/falcon-ai/components/FalconAIFab", () => ({
  default: () => null,
}));
vi.mock("src/sections/falcon-ai/store/useFalconStore", () => ({
  default: (select) =>
    select({ isSidebarOpen: state.falconAllowed, pendingNavigation: null, clearPendingNavigation: () => {} }),
}));
vi.mock("../WorkspaceSwitcher/CreateWorkspaceModal", () => ({
  default: () => null,
}));
vi.mock("src/components/two-factor-enforcement/TwoFactorBanner", () => ({
  default: () => null,
}));
vi.mock("../nav-mini", () => ({ default: () => null }));
vi.mock("../nav-horizontal", () => ({ default: () => null }));
vi.mock("../NavGatewayPanel", () => ({
  default: () => <div data-testid="gateway-panel" />,
}));

// NavVertical's own children, so the real component can render in jsdom.
vi.mock("src/auth/hooks", () => ({ useAuthContext: () => ({ user: null }) }));
vi.mock("src/components/logo", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/components/scrollbar", () => {
  function Scrollbar({ children }) {
    return <div>{children}</div>;
  }
  Scrollbar.propTypes = { children: PropTypes.node };
  return { default: Scrollbar };
});
vi.mock("src/components/nav-section", () => ({
  NavSectionVertical: () => <div data-testid="nav-section" />,
}));
vi.mock("../WorkspaceSwitcher/WorkspaceSwitcher", () => ({
  default: () => null,
}));
vi.mock("../config-navigation", () => ({
  useNavData: () => [],
  useNavGatewayData: () => [],
  useNavUpgradeData: () => [],
  useNavSettingsData: () => [],
  useNavDashBoardData: () => [],
  useWorkspaceSettingsNav: () => null,
}));
vi.mock("../states", () => ({
  syncSettingsOpen: vi.fn(),
  useSettingsOpen: () => ({ settingOpen: true, setSettingOpen: () => {} }),
  useGatewayOpen: () => ({ gatewayOpen: false, setGatewayOpen: () => {} }),
}));

function renderLayout() {
  return render(
    <DashboardLayout>
      <p>page body</p>
    </DashboardLayout>,
  );
}

const shellOf = () => screen.getByRole("main").parentElement;
const drawerOpen = () => !!document.querySelector(".MuiDrawer-paper");

describe("Settings shell below md", () => {
  beforeEach(() => {
    state.pathname = "/dashboard/settings/mcp-server";
    state.belowMd = true;
    state.falconAllowed = false;
  });

  it("drops the 1200 px desktop width and opens navigation from a menu button", () => {
    renderLayout();
    const shell = screen.getByRole("main").closest("[data-shell='compact']");
    expect(shell).not.toBeNull();
    expect(getComputedStyle(shell).minWidth).toMatch(/^0(px)?$/);
    expect(shellOf()).not.toHaveStyle("min-width: 1200px");
    expect(screen.queryByTestId("gateway-panel")).toBeNull();

    expect(drawerOpen()).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Open navigation" }));
    expect(drawerOpen()).toBe(true);
    expect(screen.getByRole("button", { name: "Back to app" })).toBeVisible();
  });

  it("keeps the desktop shell on Settings at md and wider", () => {
    state.belowMd = false;
    renderLayout();
    expect(getComputedStyle(shellOf()).minWidth).toMatch(/^0(px)?$/);
    expect(
      screen.queryByRole("button", { name: "Open navigation" }),
    ).toBeNull();
    // v2 renders Settings inside NavVertical, not the removed gateway panel.
    expect(screen.getByRole("button", { name: "Back to app" })).toBeVisible();
    expect(screen.getAllByTestId("nav-section").length).toBeGreaterThan(0);
    expect(drawerOpen()).toBe(false);
  });

  it("gives every other section the phone menu and a shrinkable frame at phone width", () => {
    state.pathname = "/dashboard/develop";
    renderLayout();
    expect(getComputedStyle(shellOf()).minWidth).toMatch(/^0(px)?$/);
    expect(
      screen.getByRole("button", { name: "Open navigation" }),
    ).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Open navigation" }));
    expect(drawerOpen()).toBe(true);
  });
});

describe("Falcon AI sidebar in the Settings shell", () => {
  beforeEach(() => {
    state.pathname = "/dashboard/settings/profile-settings";
    state.falconAllowed = true;
  });

  it("opens over the page below md, so a 420 px column cannot push it sideways", async () => {
    state.belowMd = true;
    renderLayout();
    const sidebar = await screen.findByTestId("falcon-sidebar");
    expect(sidebar).toHaveAttribute("data-variant", "overlay");
    // Nothing is docked next to <main> in the compact shell's row.
    expect(screen.getByRole("main").parentElement).not.toContainElement(
      sidebar,
    );
  });

  it("stays docked beside the page at md and wider", async () => {
    state.belowMd = false;
    renderLayout();
    const sidebar = await screen.findByTestId("falcon-sidebar");
    expect(sidebar).toHaveAttribute("data-variant", "docked");
    expect(shellOf()).toContainElement(sidebar);
  });

  it("stays docked on every other section at phone width", async () => {
    state.belowMd = true;
    state.pathname = "/dashboard/develop";
    renderLayout();
    expect(await screen.findByTestId("falcon-sidebar")).toHaveAttribute(
      "data-variant",
      "docked",
    );
  });
});

describe("NavVertical as a temporary drawer", () => {
  beforeEach(() => {
    state.pathname = "/dashboard/settings/mcp-server";
  });

  it("stays open once opened and closes after navigating", () => {
    const onCloseNav = vi.fn();
    const { rerender } = render(
      <NavVertical temporary openNav onCloseNav={onCloseNav} />,
    );
    expect(drawerOpen()).toBe(true);
    expect(onCloseNav).not.toHaveBeenCalled();

    state.pathname = "/dashboard/settings/profile-settings";
    act(() => {
      rerender(<NavVertical temporary openNav onCloseNav={onCloseNav} />);
    });
    expect(onCloseNav).toHaveBeenCalledTimes(1);
  });

  it("reserves no width while closed", () => {
    const { container } = render(
      <NavVertical temporary openNav={false} onCloseNav={() => {}} />,
    );
    expect(container).toBeEmptyDOMElement();
    expect(drawerOpen()).toBe(false);
  });
});
