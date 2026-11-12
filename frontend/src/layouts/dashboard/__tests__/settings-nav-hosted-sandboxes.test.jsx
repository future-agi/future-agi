import React, { Suspense } from "react";
import PropTypes from "prop-types";
import { MemoryRouter, useLocation, useRoutes } from "react-router-dom";
import {
  cleanup,
  fireEvent,
  render,
  renderHook,
  screen,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  NavSectionVertical,
  NavSectionMini,
  NavSectionHorizontal,
} from "src/components/nav-section";
import { omegaSettingsRoutes } from "src/routes/sections/omega";
import { mainRoutes } from "src/routes/sections/main";
import { capabilitiesAnswer } from "src/sections/omega/shell/__tests__/rollout-answer";
import { ShellTestProviders } from "src/sections/omega/shell/__tests__/test-providers";
import { useNavSettingsData } from "../config-navigation";

const observed = vi.hoisted(() => ({
  response: null,
  orgRole: "Owner",
  workspaceRole: null,
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    user: {
      id: "settings-owner-fixture",
      organization_role: observed.orgRole,
      role: observed.workspaceRole || observed.orgRole,
      ws_enabled: false,
    },
  }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceRole: observed.workspaceRole }),
}));
vi.mock("src/hooks/useCapabilities", async (importOriginal) => ({
  ...(await importOriginal()),
  // The admission/hosted-withheld classifier and nav renderers remain real.
  useCapabilities: () => observed.response,
}));
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({
    isCloud: observed.response?.data?.display_mode === "cloud",
  }),
}));
vi.mock("src/api/workspaces/list", () => ({
  useWorkspacesList: () => ({ data: [] }),
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
const SANDBOX_PATH = "/dashboard/settings/sandboxes";
const ADMIN_ROLES = [
  ["Owner", null],
  ["Admin", null],
  ["Member", "workspace_admin"],
];
const entries = (sections) =>
  sections
    .flatMap((section) => section.items)
    .filter((item) => item.title === "Sandboxes");

function answer({ mode = "cloud", admitted = [], paused = [] } = {}) {
  const data = capabilitiesAnswer({ admitted, paused });
  // The hosted part is not in OMEGA_PART, so capabilitiesAnswer() never emits it; the real
  // hostedWithheld() classifier requires its explicit denial (as the sidebar tests seed it).
  data.features.omega_part_hosted_sandboxes = {
    allowed: false,
    reason_code: "PRODUCT_NOT_ADMITTED",
  };
  return {
    data: {
      ...data,
      deployment_flavor:
        mode === "cloud" ? "cloud_image" : "self_hosted_ee_image",
      display_mode: mode,
    },
    isLoading: false,
    isError: false,
  };
}

beforeEach(() => {
  vi.stubEnv("VITE_OMEGA_CONSOLE", "1");
  observed.orgRole = "Owner";
  observed.workspaceRole = null;
  observed.response = answer();
});
afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
});

function Providers({ children }) {
  return (
    <ShellTestProviders>
      <MemoryRouter initialEntries={[SETTINGS_PATH]}>{children}</MemoryRouter>
    </ShellTestProviders>
  );
}
Providers.propTypes = { children: PropTypes.node };

function Location() {
  return <output data-testid="location">{useLocation().pathname}</output>;
}

function SettingsNav({ Section = NavSectionVertical }) {
  const data = useNavSettingsData();
  return (
    <>
      <button type="button">Before</button>
      <Section data={data} />
      <button type="button">After</button>
      <Location />
    </>
  );
}
SettingsNav.propTypes = { Section: PropTypes.elementType };

// RED witnesses at the assigned r1 FIX: the Workspace Settings entry is absent,
// despite the readable cloud product + hosted-part PRODUCT_NOT_ADMITTED answer.
describe.each(ADMIN_ROLES)(
  "hosted Settings Sandboxes for %s / %s",
  (orgRole, workspaceRole) => {
    beforeEach(() => {
      observed.orgRole = orgRole;
      observed.workspaceRole = workspaceRole;
    });

    it("adds exactly one display-only descriptor in Workspace Settings", () => {
      const { result } = renderHook(() => useNavSettingsData(), {
        wrapper: Providers,
      });
      expect(entries(result.current)).toHaveLength(1);
      const workspace = result.current.find(
        (section) => section.subheader === "Workspace settings",
      );
      const [entry] = entries([workspace]);
      expect(entry.inert).toBe(true);
      // Includes absence of routes, path/href, handlers, roles and tabIndex.
      expect(Object.keys(entry).sort()).toEqual([
        "icon",
        "inert",
        "info",
        "title",
      ]);
    });

    it.each([
      ["full", NavSectionVertical],
      ["mini", NavSectionMini],
      ["horizontal", NavSectionHorizontal],
    ])(
      "shows inert title and label in %s Settings navigation",
      async (_layout, Section) => {
        render(<SettingsNav Section={Section} />, { wrapper: Providers });
        const title = screen.getByText("Sandboxes", { exact: true });
        const row = title.parentElement;
        const label = within(row).getByText(
          "Not available in this deployment",
          { exact: true },
        );
        expect(title).toBeVisible();
        expect(label).toBeVisible();
        expect(label).toHaveStyle({ textTransform: "none" });
        expect(screen.getAllByText("Sandboxes", { exact: true })).toHaveLength(
          1,
        );
        const interactive =
          "a, [href], button, input, select, textarea, [tabindex], [contenteditable='true'], [role='link'], [role='button']";
        expect(row.matches(interactive)).toBe(false);
        expect(row.querySelector(interactive)).toBeNull();
        fireEvent.click(title);
        fireEvent.click(label);
        fireEvent.keyDown(title, { key: "Enter" });
        fireEvent.keyDown(title, { key: " " });
        expect(screen.getByTestId("location")).toHaveTextContent(SETTINGS_PATH);

        const user = userEvent.setup();
        const after = screen.getByRole("button", {
          name: "After",
          exact: true,
        });
        screen.getByRole("button", { name: "Before", exact: true }).focus();
        for (
          let step = 0;
          step < 40 && document.activeElement !== after;
          step += 1
        ) {
          await user.tab();
          expect(row.contains(document.activeElement)).toBe(false);
        }
        expect(after).toHaveFocus();
      },
    );
  },
);

it.each([
  ["Member", null],
  ["Member", "workspace_member"],
  ["Member", "workspace_viewer"],
  ["Viewer", null],
  ["Viewer", "workspace_viewer"],
  ["workspace_member", "workspace_member"],
  ["workspace_viewer", "workspace_viewer"],
  [undefined, "workspace_admin"],
  ["unknown", "workspace_admin"],
])("does not add Settings Sandboxes for %s / %s", (orgRole, workspaceRole) => {
  observed.orgRole = orgRole;
  observed.workspaceRole = workspaceRole;
  const { result } = renderHook(() => useNavSettingsData(), {
    wrapper: Providers,
  });
  expect(entries(result.current)).toEqual([]);
  render(<SettingsNav />, { wrapper: Providers });
  expect(screen.queryByText("Sandboxes", { exact: true })).toBeNull();
});

it.each([
  ["cloud", []],
  ["cloud", ["sandboxes"]],
  ["enterprise", []],
  ["oss", []],
  ["preview", []],
])(
  "keeps the normal %s admitted/paused=%j Settings link exactly once",
  async (mode, paused) => {
    observed.response = answer({ mode, admitted: ["sandboxes"], paused });
    const { result } = renderHook(() => useNavSettingsData(), {
      wrapper: Providers,
    });
    const rows = entries(result.current);
    expect(rows).toHaveLength(1);
    expect(rows[0].path).toBe(SANDBOX_PATH);
    expect(rows[0]).not.toHaveProperty("inert");
    expect(rows[0]).not.toHaveProperty("info");
    render(<SettingsNav />, { wrapper: Providers });
    const link = screen.getByRole("link", { name: "Sandboxes", exact: true });
    expect(link).toHaveAttribute("href", SANDBOX_PATH);
    expect(screen.getAllByText("Sandboxes", { exact: true })).toHaveLength(1);
    expect(screen.queryByText("Not available in this deployment")).toBeNull();
    await userEvent.click(link);
    expect(screen.getByTestId("location")).toHaveTextContent(SANDBOX_PATH);
  },
);

it.each(["enterprise", "oss", "oss_locked", "preview", "unknown", null])(
  "adds nothing for a denied non-hosted or unknown mode %s",
  (mode) => {
    observed.response = answer({ mode });
    const { result } = renderHook(() => useNavSettingsData(), {
      wrapper: Providers,
    });
    expect(entries(result.current)).toEqual([]);
  },
);

it.each([[], ["sandboxes"]])(
  "keeps master-OFF unchanged with admission %j",
  (admitted) => {
    vi.stubEnv("VITE_OMEGA_CONSOLE", "0");
    observed.response = answer({ admitted });
    const { result } = renderHook(() => useNavSettingsData(), {
      wrapper: Providers,
    });
    expect(entries(result.current)).toEqual([]);
    expect(omegaSettingsRoutes()).toEqual([]);
  },
);

it.each([
  { isLoading: true },
  { isError: true },
  { data: undefined, isLoading: true },
  { data: undefined },
])("invents nothing from a loading/missing/failed answer %j", (state) => {
  observed.response = { ...answer(), ...state };
  const { result } = renderHook(() => useNavSettingsData(), {
    wrapper: Providers,
  });
  expect(entries(result.current)).toEqual([]);
});

it.each(["omega_sandboxes", "omega_part_hosted_sandboxes"])(
  "requires explicit withheld metadata for %s",
  (feature) => {
    const { result, rerender } = renderHook(() => useNavSettingsData(), {
      wrapper: Providers,
    });
    expect(entries(result.current)).toHaveLength(1);
    for (const value of [
      undefined,
      { allowed: false },
      { allowed: false, reason_code: "RESOLVER_UNAVAILABLE" },
    ]) {
      observed.response = answer();
      observed.response.data.features[feature] = value;
      rerender();
      expect(entries(result.current)).toEqual([]);
    }
  },
);

function SettingsRoutes() {
  return useRoutes([
    { path: "/dashboard/settings", children: omegaSettingsRoutes() },
    ...mainRoutes,
  ]);
}

it.each(ADMIN_ROLES)(
  "still renders the real not-found page for the direct URL as %s / %s",
  async (orgRole, workspaceRole) => {
    observed.orgRole = orgRole;
    observed.workspaceRole = workspaceRole;
    render(
      <ShellTestProviders>
        <MemoryRouter initialEntries={[SANDBOX_PATH]}>
          <Suspense fallback={null}>
            <SettingsRoutes />
          </Suspense>
          <Location />
        </MemoryRouter>
      </ShellTestProviders>,
    );
    expect(
      await screen.findByRole("heading", { name: /page not found/i }),
    ).toBeVisible();
    expect(screen.getByTestId("location")).toHaveTextContent("/404");
    expect(screen.queryByText("Not available in this deployment")).toBeNull();
  },
);
