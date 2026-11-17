// Settings sidebar, "Your workspaces": a long workspace name ran out of the
// row and was cut mid-letter with no ellipsis. The name sat straight inside a
// flex box, so the row's ellipsis styles never reached the text. jsdom has no
// layout, so these tests pin the styles that decide it on the name's own box,
// and prove the whole name stays reachable: a name that is cut off shows in
// full in a tooltip when keyboard focus reaches its link.
import React from "react";
import { MemoryRouter } from "react-router-dom";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { NavSectionVertical } from "src/components/nav-section";
import { useNavSettingsData } from "src/layouts/dashboard/config-navigation";
import { capabilitiesAnswer } from "src/sections/omega/shell/__tests__/rollout-answer";
import { ShellTestProviders } from "src/sections/omega/shell/__tests__/test-providers";

const LONG_NAME =
  "Customer Success Operations and Enterprise Onboarding (EMEA)";
const SHORT_NAME = "Research";

// The same mocks as withheld-row-layout.test.jsx, with workspaces on.
const observed = vi.hoisted(() => ({ response: null, workspaces: [] }));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    user: {
      id: "workspace-name-owner",
      organization_role: "Owner",
      role: "Owner",
      ws_enabled: true,
    },
  }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceRole: null }),
}));
vi.mock("src/hooks/useCapabilities", async (importOriginal) => ({
  ...(await importOriginal()),
  useCapabilities: () => observed.response,
}));
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({ isCloud: true }),
}));
vi.mock("src/api/workspaces/list", () => ({
  useWorkspacesList: () => ({ data: observed.workspaces }),
  useWorkspaceFromList: () => ({ workspace: null }),
}));
vi.mock("src/sections/settings/BillingV2/useCentralBilling", () => ({
  useCentralBilling: () => ({ visible: false }),
}));
vi.mock("src/sections/omega/shell/useEarlyAccess", async (importOriginal) => ({
  ...(await importOriginal()),
  useEarlyAccess: () => ({ data: undefined }),
}));
vi.mock("src/utils/Mixpanel", () => ({
  trackEvent: vi.fn(),
  Events: new Proxy({}, { get: (_, key) => String(key) }),
  PropertyName: new Proxy({}, { get: (_, key) => String(key) }),
}));

const SETTINGS_PATH = "/dashboard/settings/profile-settings";
const ZERO = /^0(px)?$/;

// Widths jsdom cannot produce: every box is 160px wide and its text takes 7px
// a character, so only text longer than 22 characters overflows.
const CHAR_PX = 7;
const BOX_PX = 160;
const MEASURES = ["scrollWidth", "clientWidth"];

function SettingsNav() {
  return <NavSectionVertical data={useNavSettingsData()} />;
}

function renderSettingsNav() {
  render(
    <ShellTestProviders>
      <MemoryRouter initialEntries={[SETTINGS_PATH]}>
        <SettingsNav />
      </MemoryRouter>
    </ShellTestProviders>,
  );
  return document.querySelector("#nav-section-vertical");
}

async function tabTo(user, getTarget) {
  for (let i = 0; i < 80; i += 1) {
    if (document.activeElement === getTarget()) return;
    await user.tab();
  }
  throw new Error("keyboard focus never reached the link");
}

beforeEach(() => {
  observed.response = {
    data: capabilitiesAnswer({ admitted: [] }),
    isLoading: false,
    isError: false,
  };
  observed.workspaces = [
    { id: "ws-long", name: LONG_NAME },
    { id: "ws-short", name: SHORT_NAME },
  ];
  Object.defineProperty(HTMLElement.prototype, "scrollWidth", {
    configurable: true,
    get() {
      return (this.textContent || "").length * CHAR_PX;
    },
  });
  Object.defineProperty(HTMLElement.prototype, "clientWidth", {
    configurable: true,
    get() {
      return BOX_PX;
    },
  });
});
afterEach(() => {
  cleanup();
  // The getters live on Element.prototype; dropping the overrides restores
  // jsdom's own.
  MEASURES.forEach((prop) => delete HTMLElement.prototype[prop]);
});

describe("Settings sidebar: a long workspace name", () => {
  it("ends in an ellipsis on one line instead of being cut mid-letter", () => {
    const nav = renderSettingsNav();
    const link = within(nav).getByRole("link", { name: LONG_NAME });
    const name = within(link).getByText(LONG_NAME, { exact: true });

    // The name is the one text node in its own box ...
    expect(name.childNodes).toHaveLength(1);
    expect(name.firstChild.nodeType).toBe(Node.TEXT_NODE);

    // ... and that box holds it to one line, hides the rest and draws the
    // ellipsis, and may shrink below the text's width.
    const style = getComputedStyle(name);
    expect(style.whiteSpace).toBe("nowrap");
    expect(style.overflow).toBe("hidden");
    expect(style.textOverflow).toBe("ellipsis");
    expect(style.minWidth).toMatch(ZERO);
    expect(style.display).toBe("block");

    // The row box around it may shrink too, so the ellipsis lands inside
    // the sidebar rather than past its edge.
    const row = name.parentElement;
    expect(getComputedStyle(row).minWidth).toMatch(ZERO);
  });

  it("shows the whole name in a tooltip when keyboard focus reaches it, and only when it is cut off", async () => {
    const nav = renderSettingsNav();
    const user = userEvent.setup();
    const longLink = () => within(nav).getByRole("link", { name: LONG_NAME });
    const shortLink = () => within(nav).getByRole("link", { name: SHORT_NAME });

    await tabTo(user, longLink);
    const tooltip = await screen.findByRole("tooltip");
    expect(tooltip).toHaveTextContent(LONG_NAME);
    expect(longLink()).toHaveFocus();
    // The link is still named by its own text.
    expect(longLink()).toHaveAccessibleName(LONG_NAME);

    // A name that fits gets no tooltip and no hover text.
    await tabTo(user, shortLink);
    await new Promise((resolve) => setTimeout(resolve, 400));
    expect(screen.queryByRole("tooltip")).toBeNull();
    expect(shortLink()).not.toHaveAttribute("title");
    expect(shortLink()).toHaveAccessibleName(SHORT_NAME);
  });
});

describe("Settings sidebar: the pricing row", () => {
  it("reads Plans & pricing, like the page it opens", () => {
    const nav = renderSettingsNav();
    expect(
      within(nav).getByRole("link", { name: "Plans & pricing" }),
    ).toHaveAttribute("href", "/dashboard/settings/pricing");
    expect(within(nav).queryByText("Plans & Pricing")).toBeNull();
  });
});
