/* eslint-disable react/prop-types */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation, useNavigate } from "react-router-dom";
import { createTheme, ThemeProvider } from "@mui/material/styles";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import DashboardLayout from "./index";
import { useSettingsOpen } from "./states";
import { HelmetProvider } from "react-helmet-async";
import GatewayPageFrame from "src/sections/gateway/components/frame/GatewayPageFrame";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

const h = vi.hoisted(() => ({ width: 390, layout: "vertical" }));
vi.mock("src/hooks/use-responsive", () => ({
  useResponsive: (query, start) => query === "down"
    ? h.width < ({ xs: 0, md: 900, lg: 1200 }[start] ?? 1200)
    : h.width >= ({ xs: 0, md: 900, lg: 1200 }[start] ?? 1200),
}));
vi.mock("src/components/settings", () => ({
  useSettingsContext: () => ({ themeLayout: h.layout }),
}));
vi.mock("src/hooks/useCapabilities", () => ({
  useFeatureAllowed: () => ({ allowed: false }),
  useCapabilities: () => ({ features: {}, isLoading: false }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: { organization_role: "Owner" } }),
}));
vi.mock("src/routes/hooks", () => ({
  usePathname: () => useLocation().pathname,
  useRouter: () => ({ push: useNavigate() }),
}));
vi.mock("src/sections/falcon-ai/FalconAISidebar", () => ({
  default: () => null,
}));
vi.mock("src/sections/falcon-ai/components/FalconAIFab", () => ({
  default: () => null,
}));
vi.mock("src/sections/falcon-ai/store/useFalconStore", () => ({
  default: (select) =>
    select({ pendingNavigation: null, clearPendingNavigation: () => {} }),
}));
vi.mock("./WorkspaceSwitcher/CreateWorkspaceModal", () => ({
  default: () => null,
}));
vi.mock("./WorkspaceSwitcher/WorkspaceSwitcher", () => ({
  default: () => null,
}));
vi.mock("src/components/two-factor-enforcement/TwoFactorBanner", () => ({
  default: () => null,
}));
vi.mock("src/components/logo", () => ({
  default: () => <div>Primary navigation</div>,
}));
vi.mock("src/components/scrollbar", () => ({
  default: ({ children }) => <div>{children}</div>,
}));
vi.mock("src/components/nav-section", () => ({
  NavSectionVertical: () => null,
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/sections/omega/shell/NavBrandRow", () => ({
  default: () => <div>Primary navigation</div>,
}));
vi.mock("./useShellNavData", () => ({ useShellNavData: () => [] }));
vi.mock("./sos-mode-banner", () => ({ default: () => null }));
vi.mock("./nav-mini", () => ({ default: () => <div>Mini navigation</div> }));
vi.mock("./nav-horizontal", () => ({
  default: () => <div>Horizontal navigation</div>,
}));
vi.mock("./config-navigation", () => ({
  useNavData: () => [],
  useNavUpgradeData: () => [],
  useNavSettingsData: () => [],
  useNavDashBoardData: () => [],
  useWorkspaceSettingsNav: () => null,
  useNavGatewayData: () => [
    {
      items: [
        { title: "Models", path: "/dashboard/gateway/models", icon: "catalog" },
        { title: "Request Logs", path: "/dashboard/gateway/logs", icon: "logs" },
      ],
    },
  ],
}));

function Location() {
  const { pathname } = useLocation();
  return (
    <>
      <output data-testid="path">{pathname}</output>
      <GatewayPageFrame title={pathname.endsWith("logs") ? "Request logs" : "Models"} />
    </>
  );
}
function mount(path = "/dashboard/gateway/models") {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <ThemeProvider
      theme={createTheme({ palette: palette("light"), typography })}
    >
      <MemoryRouter initialEntries={[path]}>
        <HelmetProvider>
          <DashboardLayout>
            <Location />
            <div>Page content</div>
          </DashboardLayout>
        </HelmetProvider>
      </MemoryRouter>
    </ThemeProvider>
    </QueryClientProvider>,
  );
}
beforeEach(() => {
  h.width = 390;
  h.layout = "vertical";
  useSettingsOpen.setState({ settingOpen: false });
});
afterEach(cleanup);

describe("gateway mobile shell", () => {
  it.each([
    "/dashboard/gateway/models",
    "/dashboard/gateway/logs",
    "/dashboard/settings/billing",
  ])(
    "removes the desktop minimum width and keeps content shrinkable at 390px on %s",
    (path) => {
      mount(path);
      const main = screen.getByRole("main");
      expect(getComputedStyle(main.parentElement).minWidth).toBe("0");
      expect(getComputedStyle(main).minWidth).toBe("0");
      expect(screen.queryByText("Primary navigation")).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Open navigation" })).toBeVisible();
    },
  );
  it.each(["vertical", "mini", "horizontal"])(
    "opens and closes the primary drawer with %s preferences",
    async (layout) => {
      h.layout = layout;
      mount();
      const menu = screen.getByRole("button", { name: "Open navigation" });
      await userEvent.click(menu);
      expect(await screen.findByText("Primary navigation")).toBeVisible();
      await userEvent.keyboard("{Escape}");
      await waitFor(() =>
        expect(
          screen.queryByText("Primary navigation"),
        ).not.toBeInTheDocument(),
      );
      expect(menu).toHaveFocus();
    },
  );
  it("opens gateway navigation on demand and closes it after a keyboard link selection", async () => {
    mount();
    expect(
      screen.queryByRole("link", { name: "Request Logs" }),
    ).not.toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "Open navigation" }),
    );
    const link = await screen.findByRole("link", { name: "Request Logs" });
    link.focus();
    await userEvent.keyboard("{Enter}");
    await waitFor(() =>
      expect(screen.getByTestId("path")).toHaveTextContent(
        "/dashboard/gateway/logs",
      ),
    );
    await waitFor(() =>
      expect(
        screen.queryByRole("link", { name: "Request Logs" }),
      ).not.toBeInTheDocument(),
    );
    await waitFor(() => expect(screen.getByRole("heading", { name: "Request logs" })).toHaveFocus());
    expect(screen.getByRole("status", { name: "Page navigation" })).toHaveTextContent("Request logs");
  });
  it.each(["/dashboard/gateway/models", "/dashboard/gateway/logs"])(
    "keeps gateway navigation with a saved horizontal preference on %s",
    async (path) => {
      h.width = 1440;
      h.layout = "horizontal";
      mount(path);
      expect(screen.queryByText("Horizontal navigation")).not.toBeInTheDocument();
      expect(await screen.findByRole("link", { name: "Request Logs" })).toBeVisible();
      expect(screen.getByRole("button", { name: /Find a gateway page/ })).toBeVisible();
      expect(h.layout).toBe("horizontal");
    },
  );
  it("preserves the saved horizontal navigation for other products", () => {
    h.width = 1440;
    h.layout = "horizontal";
    mount("/dashboard/datasets");
    expect(screen.getByText("Horizontal navigation")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Find a gateway page/ })).not.toBeInTheDocument();
  });
  it("retains the desktop sidebars at 1440px", async () => {
    h.width = 1440;
    mount();
    expect(screen.getByText("Primary navigation")).toBeInTheDocument();
    expect(await screen.findByText("Request Logs")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Open navigation" }),
    ).not.toBeInTheDocument();
  });
});
