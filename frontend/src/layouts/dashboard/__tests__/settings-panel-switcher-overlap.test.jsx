import React from "react";
import PropTypes from "prop-types";
import { MemoryRouter } from "react-router-dom";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HelmetProvider } from "react-helmet-async";
import { QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import {
  afterAll,
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

import NavVertical from "../nav-vertical";
import NavMini from "../nav-mini";
import {
  rolloutQueryClient,
  capabilitiesAnswer,
} from "src/sections/omega/shell/__tests__/rollout-fixtures";
import { CAPABILITIES_QUERY_KEY } from "src/hooks/useCapabilities";

// Staging walk, head-72: on Settings pages the workspace switcher at the top
// of the sidebar could not be clicked. The Settings panel (the menu that
// replaces the app navigation on /dashboard/settings) was sized to the whole
// sidebar column, so it lay over the brand row and the switcher. The panel
// must cover only the navigation below the switcher, in the open sidebar, the
// collapsed rail and the narrow-window drawer. jsdom does no hit testing: the
// structural cause is asserted here and the click itself needs a browser walk.
// The panel is shorter than the whole column, so its list must scroll inside
// it: on a short window no Settings row may be cut off.

const WORKSPACE_ID = "11111111-2222-3333-4444-555555555555";
const layout = vi.hoisted(() => ({ themeLayout: "vertical", width: 1280 }));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    user: {
      id: "u1",
      role: "Owner",
      organization_role: "Owner",
      getStartedCompleted: false,
      ws_enabled: true,
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
vi.mock("src/api/workspaces/list", () => ({
  useWorkspacesList: () => ({ data: [] }),
  useWorkspaceFromList: (id) => ({
    workspace: id
      ? {
          id,
          name: "walk-ws",
          display_name: "Walk workspace",
          user_ws_level: 8,
        }
      : null,
  }),
}));
vi.mock("src/utils/Mixpanel", () => {
  const names = new Proxy({}, { get: (_, key) => String(key) });
  return { trackEvent: () => {}, Events: names, PropertyName: names };
});
// The switcher's own menu is covered by its keyboard and scope tests; here it
// stands in as a trigger that opens a menu, so the test can find and use it.
vi.mock(
  "src/layouts/dashboard/WorkspaceSwitcher/WorkspaceSwitcher",
  async () => {
    const { useState } = await import("react");
    const Switcher = ({ collapsed }) => {
      const [open, setOpen] = useState(false);
      return (
        <div data-collapsed={collapsed ? "yes" : "no"}>
          <div
            role="button"
            tabIndex={0}
            aria-label="Switch workspace"
            aria-expanded={open}
            data-testid="workspace-switcher"
            onClick={() => setOpen((v) => !v)}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                setOpen((v) => !v);
              }
            }}
          />
          {open && <div role="dialog" aria-label="Workspace and account" />}
        </div>
      );
    };
    Switcher.propTypes = { collapsed: PropTypes.bool };
    return { default: Switcher };
  },
);
vi.mock("src/components/settings", () => ({
  useSettingsContext: () => ({
    themeLayout: layout.themeLayout,
    onUpdate: () => {},
  }),
}));

const theme = createTheme({ palette: palette("light"), typography });
const originalMatchMedia = window.matchMedia;

function installViewport(width) {
  window.matchMedia = (query) => {
    const min = /min-width:\s*([\d.]+)px/.exec(query);
    const max = /max-width:\s*([\d.]+)px/.exec(query);
    let matches = Boolean(min || max);
    if (min && width < Number(min[1])) matches = false;
    if (max && width > Number(max[1])) matches = false;
    return {
      matches,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    };
  };
}

function renderNav(Nav, path, props = {}) {
  const client = rolloutQueryClient();
  const capabilities = capabilitiesAnswer();
  client.setQueryData(CAPABILITIES_QUERY_KEY, {
    data: {
      ...capabilities,
      features: { ...capabilities.features, falcon_ai: { allowed: true } },
    },
  });
  return render(
    <HelmetProvider>
      <QueryClientProvider client={client}>
        <ThemeProvider theme={theme}>
          <MemoryRouter initialEntries={[path]}>
            <Nav openNav={false} onCloseNav={() => {}} {...props} />
          </MemoryRouter>
        </ThemeProvider>
      </QueryClientProvider>
    </HelmetProvider>,
  );
}

const positionOf = (el) => window.getComputedStyle(el).position;

// The Settings panel: the absolutely positioned box around its back button.
function settingsPanel(backButton) {
  let el = backButton.parentElement;
  while (el && positionOf(el) !== "absolute") el = el.parentElement;
  return el;
}

// The box the panel's top: 0 / height: 100% are measured against.
function containingBlock(el) {
  let block = el.parentElement;
  while (
    block &&
    !["relative", "absolute", "fixed", "sticky"].includes(positionOf(block))
  ) {
    block = block.parentElement;
  }
  return block;
}

function expectSwitcherOutsidePanelArea(backName) {
  const trigger = screen.getByRole("button", { name: "Switch workspace" });
  const panel = settingsPanel(screen.getByRole("button", { name: backName }));
  expect(panel).not.toBeNull();
  expect(panel.contains(trigger)).toBe(false);
  const block = containingBlock(panel);
  expect(block).not.toBeNull();
  // The panel spans its containing block from top to bottom, so that block
  // must not hold the switcher: the switcher sits above it.
  expect(block.contains(trigger)).toBe(false);
  expect(
    trigger.compareDocumentPosition(block) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  return trigger;
}

// The box around the panel's list (the Settings menu, or one workspace's
// settings) is the part of the panel that takes the height left under the
// back button. It must scroll, so every row stays reachable however short the
// window or long the "Your Workspaces" group.
function expectPanelListScrolls(backName) {
  const panel = settingsPanel(screen.getByRole("button", { name: backName }));
  expect(panel).not.toBeNull();
  const list = panel.querySelector("nav");
  expect(list).not.toBeNull();
  const listBox = list.parentElement;
  expect(listBox).not.toBe(panel);
  expect(panel.contains(listBox)).toBe(true);
  expect(["auto", "scroll"]).toContain(
    window.getComputedStyle(listBox).overflowY,
  );
}

const SETTINGS_PATHS = [
  "/dashboard/settings/workspace",
  "/dashboard/settings/user-management",
  "/dashboard/settings/profile-settings",
  "/dashboard/settings/ee-licenses",
  "/dashboard/settings/ai-providers",
];

const PANEL_LISTS = [
  ["/dashboard/settings/workspace", "Back to app"],
  [`/dashboard/settings/workspace/${WORKSPACE_ID}/general`, "Back to Settings"],
];

beforeEach(() => {
  layout.themeLayout = "vertical";
  layout.width = 1280;
});
afterEach(() => {
  cleanup();
  window.matchMedia = originalMatchMedia;
});
afterAll(() => {
  window.matchMedia = originalMatchMedia;
});

describe("workspace switcher on Settings pages", () => {
  it.each(
    SETTINGS_PATHS.flatMap((p) => [
      [p, 1280],
      [p, 1024],
    ]),
  )(
    "open sidebar at %s (%ipx): the Settings panel lies below the switcher",
    (path, width) => {
      installViewport(width);
      renderNav(NavVertical, path);
      expectSwitcherOutsidePanelArea("Back to app");
    },
  );

  it("open sidebar on one workspace's settings: the panel lies below the switcher", () => {
    installViewport(1280);
    renderNav(
      NavVertical,
      `/dashboard/settings/workspace/${WORKSPACE_ID}/general`,
    );
    expect(screen.getByText("Walk workspace")).toBeInTheDocument();
    expectSwitcherOutsidePanelArea("Back to Settings");
  });

  it.each(SETTINGS_PATHS)(
    "collapsed rail at %s: the Settings panel lies below the switcher",
    (path) => {
      layout.themeLayout = "mini";
      installViewport(1280);
      renderNav(NavMini, path);
      expectSwitcherOutsidePanelArea("Back to app");
      // The rail's Expand sidebar toggle sits above the switcher, so it is
      // outside the panel's area too.
      const panel = settingsPanel(
        screen.getByRole("button", { name: "Back to app" }),
      );
      expect(
        containingBlock(panel).contains(
          screen.getByRole("button", { name: "Expand sidebar" }),
        ),
      ).toBe(false);
    },
  );

  it("narrow-window drawer on Settings: the panel lies below the switcher", () => {
    installViewport(800);
    renderNav(NavVertical, "/dashboard/settings/workspace", {
      temporary: true,
      openNav: true,
    });
    expectSwitcherOutsidePanelArea("Back to app");
  });

  it("the Settings menu still replaces the app navigation below the switcher", () => {
    installViewport(1280);
    renderNav(NavVertical, "/dashboard/settings/workspace");
    const panel = settingsPanel(
      screen.getByRole("button", { name: "Back to app" }),
    );
    const block = containingBlock(panel);
    // The app's own navigation (the Main group, Get started) is inside the
    // area the panel covers, so the Settings menu still replaces it.
    expect(
      block.contains(screen.getByRole("navigation", { name: "Settings" })),
    ).toBe(true);
    expect(block.contains(screen.getByText("Get started"))).toBe(true);
  });

  it("opens the switcher by mouse and by keyboard on Settings, Workspaces", async () => {
    installViewport(1024);
    renderNav(NavVertical, "/dashboard/settings/workspace");
    const trigger = expectSwitcherOutsidePanelArea("Back to app");
    await userEvent.click(trigger);
    expect(
      screen.getByRole("dialog", { name: "Workspace and account" }),
    ).toBeInTheDocument();
    await userEvent.click(trigger);
    expect(
      screen.queryByRole("dialog", { name: "Workspace and account" }),
    ).toBeNull();
    trigger.focus();
    await userEvent.keyboard("{Enter}");
    expect(
      within(document.body).getByRole("dialog", {
        name: "Workspace and account",
      }),
    ).toBeInTheDocument();
  });
});

describe("the Settings panel's list scrolls inside the panel", () => {
  it.each(PANEL_LISTS)(
    "open sidebar at %s: the list under %s scrolls",
    (path, backName) => {
      installViewport(1024);
      renderNav(NavVertical, path);
      expectPanelListScrolls(backName);
    },
  );

  it.each(PANEL_LISTS)(
    "collapsed rail at %s: the list under %s scrolls",
    (path, backName) => {
      layout.themeLayout = "mini";
      installViewport(1280);
      renderNav(NavMini, path);
      expectPanelListScrolls(backName);
    },
  );
});
