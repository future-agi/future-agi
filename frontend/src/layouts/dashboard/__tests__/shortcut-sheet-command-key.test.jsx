// The "?" sheet's Cmd/Ctrl+K row is true for the page it is opened on. On a
// dashboard page the Falcon AI launcher is mounted and takes the key: the row
// reads "Ask Falcon AI". On the Hub no launcher is mounted and the command
// palette takes the key: the row reads "Command palette". Each case also
// presses the key, so the row and what the key does agree.
import React from "react";
import { MemoryRouter, Outlet, useRoutes } from "react-router-dom";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ShellTestProviders,
  ensureWebStorage,
  installViewport,
} from "src/sections/omega/shell/__tests__/test-providers";
import { resetHostPathForTests } from "src/sections/omega/shell/products";
import { activePaletteHost } from "src/sections/common/CommandPalette/shortcutOwner";
import { useShortcutStore } from "src/sections/common/ux/useShortcuts";
import { MOD_KEY_LABEL } from "src/utils/keymap";

import DashboardLayout from "..";

const shell = vi.hoisted(() => ({
  falcon: {
    isSidebarOpen: false,
    toggleSidebar: () => {},
    pendingNavigation: null,
    clearPendingNavigation: () => {},
  },
}));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    user: {
      id: "u1",
      role: "Owner",
      organization_role: "Owner",
      getStartedCompleted: true,
      ws_enabled: false,
    },
  }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceRole: null }),
}));
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({
    isOSS: false,
    isCloud: true,
    isEE: false,
    isLoading: false,
  }),
}));
vi.mock("src/hooks/useCapabilities", async (original) => {
  const { capabilitiesAnswer } = await import(
    "src/sections/omega/shell/__tests__/rollout-answer"
  );
  return {
    .../** @type {typeof import("src/hooks/useCapabilities")} */ (
      await original()
    ),
    useFeatureLocked: () => ({
      locked: true,
      isLoading: false,
      isError: false,
    }),
    CAPABILITIES_QUERY_KEY: ["capabilities"],
    useFeatureAllowed: () => ({ allowed: true }),
    useCapabilities: () => ({
      data: capabilitiesAnswer(),
      isLoading: false,
      isError: false,
    }),
  };
});
vi.mock("src/api/workspaces/list", () => ({
  useWorkspacesList: () => ({ data: [] }),
  useWorkspaceFromList: () => ({ workspace: null }),
}));
vi.mock("src/utils/Mixpanel", () => {
  const names = new Proxy({}, { get: (_, key) => String(key) });
  return { trackEvent: () => {}, Events: names, PropertyName: names };
});
vi.mock("src/layouts/dashboard/WorkspaceSwitcher/WorkspaceSwitcher", () => ({
  default: () => <div data-testid="workspace-switcher" />,
}));
vi.mock("src/layouts/dashboard/WorkspaceSwitcher/CreateWorkspaceModal", () => ({
  default: () => null,
}));
vi.mock("src/layouts/dashboard/NavGatewayPanel", () => ({
  default: () => null,
}));
vi.mock("src/components/two-factor-enforcement/TwoFactorBanner", () => ({
  default: () => null,
}));
// The sidebar opens a socket; this file tests the sheet's row.
vi.mock("src/sections/falcon-ai/FalconAISidebar", () => ({
  default: () => null,
}));
vi.mock("src/sections/falcon-ai/store/useFalconStore", () => {
  const store = (selector) => selector(shell.falcon);
  // The command palette (mounted by the layout) subscribes to Falcon resets.
  store.getState = () => shell.falcon;
  store.subscribe = () => () => {};
  return { default: store };
});
vi.mock("src/components/settings", () => ({
  useSettingsContext: () => ({ themeLayout: "vertical", onUpdate: () => {} }),
}));

// The layout reads only the path; stand-in pages keep the test on the shell.
const page = (id) => <div data-testid={id}>page</div>;
function App() {
  return useRoutes([
    {
      path: "/dashboard",
      element: <DashboardLayoutOutlet />,
      children: [
        { path: "develop/:dataset", element: page("dataset-page") },
        { path: "hub", element: page("hub-page") },
      ],
    },
  ]);
}

function DashboardLayoutOutlet() {
  return (
    <DashboardLayout>
      <Outlet />
    </DashboardLayout>
  );
}

function renderAt(path) {
  render(
    <ShellTestProviders>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </ShellTestProviders>,
  );
}

const COMMAND_K_CAP = MOD_KEY_LABEL === "⌘" ? "⌘K" : "Ctrl+K";
const SHEET = /^Keyboard shortcuts/;

const capsOf = (element) =>
  Array.from(element.querySelectorAll("kbd"), (kbd) => kbd.textContent);

// The sheet and the palette load in the layout's own chunks after the shell
// paints (one boundary): once the palette is mounted, the sheet is too.
async function openPage(path, testId) {
  installViewport(1280);
  renderAt(path);
  await screen.findByTestId(testId);
  await waitFor(() => expect(activePaletteHost()).not.toBeNull(), {
    timeout: 25000,
  });
}

// Opens the "?" sheet on its Global section; returns the Cmd/Ctrl+K row.
async function openSheetRow() {
  act(() => {
    useShortcutStore.getState().openSheet({ surface: "global" });
  });
  const sheet = await screen.findByRole("dialog", { name: SHEET });
  const commandKey = sheet.querySelector('[data-shortcut="assistant"]');
  expect(commandKey).not.toBeNull();
  return commandKey;
}

async function closeSheet() {
  act(() => {
    useShortcutStore.getState().closeSheet();
  });
  await waitFor(() =>
    expect(screen.queryByRole("dialog", { name: SHEET })).toBeNull(),
  );
}

describe("the ? sheet's Cmd/Ctrl+K row on the page it is opened on", () => {
  const originalMatchMedia = window.matchMedia;
  let toggleSidebar;
  beforeEach(() => {
    vi.stubEnv("VITE_OMEGA_CONSOLE", "1");
    ensureWebStorage();
    toggleSidebar = vi.fn();
    shell.falcon = {
      isSidebarOpen: false,
      toggleSidebar,
      pendingNavigation: null,
      clearPendingNavigation: () => {},
    };
    useShortcutStore.getState().setPaletteOpen(false);
  });
  afterEach(() => {
    vi.unstubAllEnvs();
    window.matchMedia = originalMatchMedia;
    resetHostPathForTests();
    useShortcutStore.getState().setPaletteOpen(false);
    useShortcutStore.getState().closeSheet();
  });

  it("on a dataset page it reads Ask Falcon AI, and Cmd+K opens the assistant", async () => {
    await openPage("/dashboard/develop/ds-1", "dataset-page");
    const commandKey = await openSheetRow();
    expect(within(commandKey).getByText("Ask Falcon AI")).toBeInTheDocument();
    expect(within(commandKey).queryByText("Command palette")).toBeNull();
    expect(capsOf(commandKey)).toEqual([COMMAND_K_CAP]);
    await closeSheet();
    fireEvent.keyDown(window, { key: "k", metaKey: true });
    expect(toggleSidebar).toHaveBeenCalledTimes(1);
    expect(useShortcutStore.getState().paletteOpen).toBe(false);
  });

  it("on the Hub it reads Command palette, and Cmd+K opens the palette", async () => {
    await openPage("/dashboard/hub", "hub-page");
    const commandKey = await openSheetRow();
    expect(within(commandKey).getByText("Command palette")).toBeInTheDocument();
    expect(within(commandKey).queryByText("Ask Falcon AI")).toBeNull();
    expect(capsOf(commandKey)).toEqual([COMMAND_K_CAP]);
    await closeSheet();
    fireEvent.keyDown(window, { key: "k", metaKey: true });
    expect(
      await screen.findByRole("dialog", { name: "Command palette" }),
    ).toBeVisible();
    expect(toggleSidebar).not.toHaveBeenCalled();
  });
});
