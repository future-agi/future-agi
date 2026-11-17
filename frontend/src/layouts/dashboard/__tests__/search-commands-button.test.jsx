// The command palette's own button. Cmd/Ctrl+K opens Falcon AI, so the
// palette opens from "Search commands" at the top of the side navigation (an
// icon with a tooltip when the navigation is collapsed), or with
// Cmd/Ctrl+Shift+P. The tooltip names that key, except in a browser that
// keeps it for itself (Firefox), where the button still opens the palette.
// Cmd/Ctrl+K pressed with the palette open closes it and opens the assistant.
import React from "react";
import PropTypes from "prop-types";
import { MemoryRouter, Outlet, useLocation, useRoutes } from "react-router-dom";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
  layout: "vertical",
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
// The sidebar opens a socket; this file tests the palette's button.
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
  useSettingsContext: () => ({
    themeLayout: shell.layout,
    onUpdate: () => {},
  }),
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
        {
          path: "develop/experiment/:experimentId/data",
          element: page("experiment-page"),
        },
        { path: "falcon-ai", element: page("falcon-page") },
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

function LocationProbe({ into }) {
  into.pathname = useLocation().pathname;
  return null;
}
LocationProbe.propTypes = { into: PropTypes.object.isRequired };

function renderAt(path) {
  const location = { pathname: null };
  render(
    <ShellTestProviders>
      <MemoryRouter initialEntries={[path]}>
        <App />
        <LocationProbe into={location} />
      </MemoryRouter>
    </ShellTestProviders>,
  );
  return location;
}

const PALETTE_CAP = MOD_KEY_LABEL === "⌘" ? "⌘⇧P" : "Ctrl+Shift+P";
const FIREFOX =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.5; rv:132.0) Gecko/20100101 Firefox/132.0";

const DATASET = ["a dataset page", "/dashboard/develop/ds-1", "dataset-page"];
const EXPERIMENT = [
  "an experiment page",
  "/dashboard/develop/experiment/exp-1/data",
  "experiment-page",
];

const setUserAgent = (userAgent) =>
  Object.defineProperty(window.navigator, "userAgent", {
    value: userAgent,
    configurable: true,
  });

const searchCommands = () =>
  screen.getByRole("button", { name: "Search commands" });
const paletteDialog = () =>
  screen.queryByRole("dialog", { name: "Command palette" });

// The palette loads in its own chunk after the shell paints.
const paletteMounted = () =>
  waitFor(() => expect(activePaletteHost()).not.toBeNull(), {
    timeout: 25000,
  });

async function openPage([, path, testId], layout = "vertical") {
  shell.layout = layout;
  installViewport(1280);
  const location = renderAt(path);
  await screen.findByTestId(testId);
  await paletteMounted();
  return location;
}

describe("Search commands, the command palette's button", () => {
  const originalMatchMedia = window.matchMedia;
  let toggleSidebar;
  beforeEach(() => {
    vi.stubEnv("VITE_OMEGA_CONSOLE", "1");
    ensureWebStorage();
    toggleSidebar = vi.fn();
    shell.layout = "vertical";
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
    delete window.navigator.userAgent;
  });

  it.each([
    [DATASET[0], "expanded", "vertical", DATASET],
    [EXPERIMENT[0], "expanded", "vertical", EXPERIMENT],
    [DATASET[0], "collapsed", "mini", DATASET],
    [EXPERIMENT[0], "collapsed", "mini", EXPERIMENT],
  ])(
    "on %s with the navigation %s (%s) it opens the palette and names its key",
    async (_name, _state, layout, target) => {
      await openPage(target, layout);
      const button = searchCommands();
      if (layout === "vertical") {
        // At the top of the side navigation, with its words.
        expect(
          screen.getByRole("navigation", { name: "Main navigation" }),
        ).toContainElement(button);
        expect(button).toHaveTextContent("Search commands");
      } else {
        // The collapsed rail shows it as an icon.
        expect(button.textContent).toBe("");
      }
      expect(button).toHaveAttribute(
        "aria-keyshortcuts",
        "Meta+Shift+P Control+Shift+P",
      );
      const user = userEvent.setup();
      await user.hover(button);
      expect((await screen.findByRole("tooltip")).textContent).toBe(
        `Search commands (${PALETTE_CAP})`,
      );
      await user.click(button);
      const dialog = await screen.findByRole("dialog", {
        name: "Command palette",
      });
      expect(dialog).toBeVisible();
      expect(toggleSidebar).not.toHaveBeenCalled();
      fireEvent.keyDown(within(dialog).getByRole("combobox"), {
        key: "Escape",
      });
      await waitFor(() => expect(paletteDialog()).toBeNull());
      expect(button).toHaveFocus();
    },
  );

  it.each([
    ["Cmd+Shift+P", ...DATASET, { metaKey: true, key: "P" }],
    ["Ctrl+Shift+P", ...DATASET, { ctrlKey: true, key: "P" }],
    ["Cmd+Shift+P", ...EXPERIMENT, { metaKey: true, key: "p" }],
    ["Ctrl+Shift+P", ...EXPERIMENT, { ctrlKey: true, key: "p" }],
  ])(
    "%s on %s opens the palette and closes it again",
    async (_key, name, path, testId, init) => {
      await openPage([name, path, testId]);
      fireEvent.keyDown(window, { shiftKey: true, ...init });
      expect(
        await screen.findByRole("dialog", { name: "Command palette" }),
      ).toBeVisible();
      fireEvent.keyDown(window, { shiftKey: true, ...init });
      await waitFor(() =>
        expect(useShortcutStore.getState().paletteOpen).toBe(false),
      );
      expect(toggleSidebar).not.toHaveBeenCalled();
    },
  );

  it("in Firefox, which keeps Cmd/Ctrl+Shift+P, the tooltip names no key and the button still opens the palette", async () => {
    setUserAgent(FIREFOX);
    await openPage(DATASET);
    const button = searchCommands();
    expect(button).not.toHaveAttribute("aria-keyshortcuts");
    const user = userEvent.setup();
    await user.hover(button);
    expect((await screen.findByRole("tooltip")).textContent).toBe(
      "Search commands",
    );
    await user.click(button);
    expect(
      await screen.findByRole("dialog", { name: "Command palette" }),
    ).toBeVisible();
  });

  it("Cmd+K with the palette open closes the palette and opens the assistant", async () => {
    await openPage(EXPERIMENT);
    const user = userEvent.setup();
    await user.click(searchCommands());
    expect(
      await screen.findByRole("dialog", { name: "Command palette" }),
    ).toBeVisible();
    fireEvent.keyDown(window, { key: "k", metaKey: true });
    await waitFor(() => expect(paletteDialog()).toBeNull());
    expect(useShortcutStore.getState().paletteOpen).toBe(false);
    expect(toggleSidebar).toHaveBeenCalledTimes(1);
  });
});
