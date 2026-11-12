// The Settings menu names its pages and groups in sentence case, like the
// rest of the app: only the first word and product names or acronyms (AI,
// MCP, Falcon) keep their capitals. The menu had mixed "Usage Summary" and
// "MCP Server" with "Plans & pricing" and "Early access" in one list.
import React from "react";
import PropTypes from "prop-types";
import { MemoryRouter } from "react-router-dom";
import { cleanup, render, renderHook, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { NavSectionVertical } from "src/components/nav-section";
import { capabilitiesAnswer } from "src/sections/omega/shell/__tests__/rollout-answer";
import { ShellTestProviders } from "src/sections/omega/shell/__tests__/test-providers";
import {
  useNavSettingsData,
  useWorkspaceSettingsNav,
} from "../config-navigation";

const WORKSPACE = { id: "ws-1", name: "Research", user_ws_level: 8 };

// An organization Owner on cloud, with workspaces on, something to bill and
// Falcon AI allowed: every page these labels name is in the menu.
const observed = vi.hoisted(() => ({ response: null }));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    user: {
      id: "settings-case-owner",
      organization_role: "Owner",
      role: "Owner",
      ws_enabled: true,
    },
  }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceRole: null }),
}));
vi.mock("src/hooks/useCapabilities", async (importOriginal) => {
  const actual = /** @type {typeof import("src/hooks/useCapabilities")} */ (
    await importOriginal()
  );
  return {
    ...actual,
    useCapabilities: () => observed.response,
    useFeatureAllowed: (feature) => ({
      allowed: feature === actual.CAPABILITY.FALCON_AI,
      reasonCode: null,
      isLoading: false,
      isError: false,
    }),
  };
});
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({ isCloud: true }),
}));
vi.mock("src/api/workspaces/list", () => ({
  useWorkspacesList: () => ({ data: [WORKSPACE] }),
  useWorkspaceFromList: () => ({ workspace: WORKSPACE }),
}));
vi.mock("src/sections/settings/BillingV2/useCentralBilling", () => ({
  useCentralBilling: () => ({ visible: true }),
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

// Each page the menu names, as it reads now, and as it read before.
const RENAMED = [
  ["AI providers", "AI Providers"],
  ["Falcon AI connectors", "Falcon AI Connectors"],
  ["Usage summary", "Usage Summary"],
  ["MCP server", "MCP Server"],
  ["Org settings", "Org Settings"],
];

function Providers({ children }) {
  return (
    <ShellTestProviders>
      <MemoryRouter initialEntries={[SETTINGS_PATH]}>{children}</MemoryRouter>
    </ShellTestProviders>
  );
}
Providers.propTypes = { children: PropTypes.node };

function SettingsNav() {
  return <NavSectionVertical data={useNavSettingsData()} />;
}

beforeEach(() => {
  observed.response = {
    data: capabilitiesAnswer({ admitted: [] }),
    isLoading: false,
    isError: false,
  };
});
afterEach(() => {
  cleanup();
});

describe("Settings menu: sentence-case labels", () => {
  it("names its groups Workspace settings, Organization and Your workspaces", () => {
    const { result } = renderHook(() => useNavSettingsData(), {
      wrapper: Providers,
    });
    expect(
      result.current.map((section) => section.subheader).filter(Boolean),
    ).toEqual(["Workspace settings", "Organization", "Your workspaces"]);
  });

  it.each(RENAMED)("lists %s, not %s", (label, before) => {
    const { result } = renderHook(() => useNavSettingsData(), {
      wrapper: Providers,
    });
    const titles = result.current.flatMap((section) =>
      section.items.map((item) => item.title),
    );
    expect(titles).toContain(label);
    expect(titles).not.toContain(before);
  });

  it("shows each renamed page as a link by its sentence-case name", () => {
    render(<SettingsNav />, { wrapper: Providers });
    const nav = document.querySelector("#nav-section-vertical");
    expect(nav).not.toBeNull();
    for (const [label, before] of RENAMED) {
      expect(within(nav).getByRole("link", { name: label })).toBeVisible();
      expect(within(nav).queryByRole("link", { name: before })).toBeNull();
    }
  });

  it("names a workspace's AI providers page the same way", () => {
    const { result } = renderHook(() => useWorkspaceSettingsNav(WORKSPACE.id), {
      wrapper: Providers,
    });
    const titles = result.current.data[0].items.map((item) => item.title);
    expect(titles).toContain("AI providers");
    expect(titles).not.toContain("AI Providers");
  });
});
