import React from "react";
import PropTypes from "prop-types";
import { MemoryRouter, useLocation } from "react-router-dom";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import axios from "src/utils/axios";
import { CAPABILITIES_QUERY_KEY } from "src/hooks/useCapabilities";
import { capabilitiesAnswer } from "src/sections/omega/shell/__tests__/rollout-answer";
import { rolloutQueryClient } from "src/sections/omega/shell/__tests__/rollout-fixtures";
import {
  ShellTestProviders,
  ensureWebStorage,
  installViewport,
} from "src/sections/omega/shell/__tests__/test-providers";
import NavVertical from "../nav-vertical";
import NavMini from "../nav-mini";

const observed = vi.hoisted(() => ({ restricted: false, answer: null }));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    user: {
      id: "owner-fixture",
      organization_role: "Owner",
      restrict_simulate_to_environments: observed.restricted,
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
  useWorkspaceFromList: () => ({ workspace: null }),
}));
vi.mock("src/layouts/dashboard/WorkspaceSwitcher/WorkspaceSwitcher", () => ({
  default: () => <div data-testid="workspace-switcher" />,
}));
vi.mock("src/components/settings", () => ({
  useSettingsContext: () => ({ themeLayout: "vertical", onUpdate: () => {} }),
}));
vi.mock("src/utils/Mixpanel", () => ({
  trackEvent: vi.fn(),
  Events: new Proxy({}, { get: (_, key) => String(key) }),
  PropertyName: new Proxy({}, { get: (_, key) => String(key) }),
}));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    default: {
      ...actual.default,
      get: vi.fn(async (url) => {
        if (url.endsWith("/api/capabilities/"))
          return { data: observed.answer };
        throw new Error(`Unexpected GET ${url}`);
      }),
    },
  };
});

const originalMatchMedia = window.matchMedia;
beforeEach(() => {
  ensureWebStorage();
  installViewport(1280);
  vi.clearAllMocks();
  vi.stubEnv("VITE_OMEGA_CONSOLE", "1");
  const answer = capabilitiesAnswer({ admitted: ["studio_tab", "hub"] });
  observed.answer = {
    ...answer,
    deployment_flavor: "cloud_image",
    display_mode: "cloud",
    features: {
      ...answer.features,
      omega_part_hosted_sandboxes: {
        allowed: false,
        reason_code: "PRODUCT_NOT_ADMITTED",
      },
      omega_part_hosted_deployments: {
        allowed: false,
        reason_code: "PRODUCT_NOT_ADMITTED",
      },
    },
  };
});
afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
  window.matchMedia = originalMatchMedia;
});

function Navigation({ Nav }) {
  const location = useLocation();
  return (
    <>
      <Nav openNav={false} onCloseNav={() => {}} />
      <output data-testid="location">{location.pathname}</output>
    </>
  );
}
Navigation.propTypes = { Nav: PropTypes.elementType.isRequired };

// HC09 / P2, SIMULATE-POSITIVE-CONTROL-FA-r1.json: this is the frontend
// navigation leg only, not proof of provider execution or tenant refusals.
// The real capabilities hook observes an HTTP-shaped cloud/OFF answer; it is
// not mocked, nor are the shell descriptor composition and nav renderer.
describe.each([
  ["full", NavVertical, "#nav-section-vertical"],
  ["mini", NavMini, "#nav-section-mini"],
])(
  "%s sidebar: Simulate positive control with hosted_sandboxes OFF",
  (_name, Nav, selector) => {
    it.each([true, false])(
      "keeps Environment visible and navigable with restrict_simulate_to_environments=%s",
      async (restricted) => {
        observed.restricted = restricted;
        const client = rolloutQueryClient({ admitted: ["studio_tab", "hub"] });
        client.removeQueries({ queryKey: CAPABILITIES_QUERY_KEY });
        render(
          <ShellTestProviders queryClient={client}>
            <MemoryRouter initialEntries={["/dashboard/develop"]}>
              <Navigation Nav={Nav} />
            </MemoryRouter>
          </ShellTestProviders>,
        );
        await waitFor(() => {
          expect(client.getQueryData(CAPABILITIES_QUERY_KEY)?.data).toEqual(
            observed.answer,
          );
        });
        expect(axios.get).toHaveBeenCalledWith(
          expect.stringMatching(/\/api\/capabilities\/$/),
        );
        const nav = document.querySelectorAll(selector)[1];
        expect(nav).toBeInTheDocument();
        // RED witness: both rows were absent at the base. The positive-control
        // link below is deliberately still working, not another withheld product.
        for (const title of ["Sandboxes", "Deployments"]) {
          const titleNode = within(nav).getByText(title, { exact: true });
          const row = titleNode.parentElement;
          expect(titleNode).toBeVisible();
          expect(
            within(row).getByText("Not available in this deployment", {
              exact: true,
            }),
          ).toBeVisible();
          expect(
            row.querySelector(
              "a, [href], button, [tabindex], [role='link'], [role='button']",
            ),
          ).toBeNull();
        }
        const link = nav.querySelector(
          "a[href='/dashboard/simulate/environments']",
        );
        expect(link).toBeVisible();
        expect(
          within(link).queryByText("Not available in this deployment"),
        ).toBeNull();
        await userEvent.click(link);
        expect(screen.getByTestId("location")).toHaveTextContent(
          "/dashboard/simulate/environments",
        );
        expect(
          nav.querySelector("a[href='/dashboard/simulate/scenarios']") !== null,
        ).toBe(!restricted);
      },
    );
  },
);
