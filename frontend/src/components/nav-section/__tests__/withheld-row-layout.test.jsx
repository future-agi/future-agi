// Owner review 11-09 (staging): a withheld sidebar row broke at the open
// sidebar's width. "Not available in this deployment" sat beside the title
// in one unwrapping line, took the row's width, and the title stacked one
// letter per line under it. jsdom has no layout, so these tests pin the
// styles that decide it: the title is held to one line, the line sits under
// the title (a column, not a row), and nothing keeps the line from wrapping.
import React from "react";
import PropTypes from "prop-types";
import { MemoryRouter } from "react-router-dom";
import { cleanup, render, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { NavSectionVertical, NavSectionMini } from "src/components/nav-section";
import {
  useNavData,
  useNavSettingsData,
} from "src/layouts/dashboard/config-navigation";
import { NOT_AVAILABLE_IN_DEPLOYMENT } from "src/sections/omega/shell/agent-infrastructure-nav";
import { capabilitiesAnswer } from "src/sections/omega/shell/__tests__/rollout-answer";
import { ShellTestProviders } from "src/sections/omega/shell/__tests__/test-providers";

// The same mocks as settings-nav-hosted-sandboxes.test.jsx: the capabilities
// answer is seeded; the hosted-withheld classifier, descriptor composition
// and nav renderers stay real.
const observed = vi.hoisted(() => ({ response: null }));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    user: {
      id: "withheld-row-owner",
      organization_role: "Owner",
      role: "Owner",
      ws_enabled: false,
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

const APP_PATH = "/dashboard/develop";
const SETTINGS_PATH = "/dashboard/settings/profile-settings";
const INTERACTIVE =
  "a, [href], button, input, select, textarea, [tabindex], [contenteditable='true'], [role='link'], [role='button']";

// Hosted (display_mode=cloud) with Sandboxes and Deployments withheld: the
// product and its hosted part are both explicitly not admitted. Nothing is
// admitted, so the Agent infrastructure group holds only the two withheld
// rows.
function hostedWithheldAnswer() {
  const data = capabilitiesAnswer({ admitted: [] });
  for (const product of ["sandboxes", "deployments"]) {
    data.features[`omega_part_hosted_${product}`] = {
      allowed: false,
      reason_code: "PRODUCT_NOT_ADMITTED",
    };
  }
  return {
    data: { ...data, deployment_flavor: "cloud_image", display_mode: "cloud" },
    isLoading: false,
    isError: false,
  };
}

beforeEach(() => {
  vi.stubEnv("VITE_OMEGA_CONSOLE", "1");
  observed.response = hostedWithheldAnswer();
});
afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
});

function AppNav({ Section }) {
  return <Section data={useNavData()} />;
}
AppNav.propTypes = { Section: PropTypes.elementType.isRequired };

function SettingsNav({ Section }) {
  return <Section data={useNavSettingsData()} />;
}
SettingsNav.propTypes = { Section: PropTypes.elementType.isRequired };

function renderNav(Nav, Section, path) {
  render(
    <ShellTestProviders>
      <MemoryRouter initialEntries={[path]}>
        <Nav Section={Section} />
      </MemoryRouter>
    </ShellTestProviders>,
  );
}

const ROWS = [
  ["Deployments", "Deployments", AppNav, APP_PATH],
  ["Sandboxes", "Sandboxes", AppNav, APP_PATH],
  ["Settings > Sandboxes", "Sandboxes", SettingsNav, SETTINGS_PATH],
];

describe("withheld row in the open sidebar", () => {
  it.each(ROWS)(
    "%s: title on one line, the whole line under it, no link",
    (_row, title, Nav, path) => {
      renderNav(Nav, NavSectionVertical, path);
      const nav = document.querySelector("#nav-section-vertical");
      const titleNode = within(nav).getByText(title, { exact: true });

      // The title is one text node in its own box, held to one line: it may
      // not break between letters.
      expect(titleNode.childNodes).toHaveLength(1);
      expect(titleNode.firstChild.nodeType).toBe(Node.TEXT_NODE);
      expect(titleNode.textContent).toBe(title);
      expect(getComputedStyle(titleNode).whiteSpace).toBe("nowrap");

      // The line is in its own element under the title (their shared box
      // stacks them), not inside the title box nor beside it in the row.
      const textBox = titleNode.parentElement;
      expect(getComputedStyle(textBox).flexDirection).toBe("column");
      const line = within(textBox).getByText(NOT_AVAILABLE_IN_DEPLOYMENT, {
        exact: true,
      });
      expect(line.textContent).toBe(NOT_AVAILABLE_IN_DEPLOYMENT);
      expect(titleNode).not.toContainElement(line);
      expect(line).not.toContainElement(titleNode);

      // Readable in full: from the line up to the title's box, nothing holds
      // it to one line, truncates it or hides its overflow.
      const row = textBox.parentElement;
      for (let node = line; node !== row; node = node.parentElement) {
        const style = getComputedStyle(node);
        expect(style.whiteSpace).not.toBe("nowrap");
        expect(style.textOverflow).not.toBe("ellipsis");
        expect(style.overflow).not.toBe("hidden");
      }
      expect(titleNode).toBeVisible();
      expect(line).toBeVisible();

      // Inert: no link or other control around the row or inside it.
      expect(row.closest(INTERACTIVE)).toBeNull();
      expect(row.querySelector(INTERACTIVE)).toBeNull();
      expect(
        within(nav).queryByRole("link", { name: new RegExp(title) }),
      ).toBeNull();
    },
  );
});

describe("withheld row in the collapsed rail", () => {
  it.each(ROWS)(
    "%s: shows both strings and gives them whole as the row's hover text",
    (_row, title, Nav, path) => {
      renderNav(Nav, NavSectionMini, path);
      const nav = document.querySelector("#nav-section-mini");
      const titleNode = within(nav).getByText(title, { exact: true });
      const line = within(titleNode.parentElement).getByText(
        NOT_AVAILABLE_IN_DEPLOYMENT,
        { exact: true },
      );
      expect(titleNode).toBeVisible();
      expect(line).toBeVisible();

      const row = titleNode.closest("[title]");
      expect(row).not.toBeNull();
      expect(row).toHaveAttribute(
        "title",
        `${title}: ${NOT_AVAILABLE_IN_DEPLOYMENT}`,
      );
      expect(row).toContainElement(line);
      expect(row.closest(INTERACTIVE)).toBeNull();
      expect(row.querySelector(INTERACTIVE)).toBeNull();
    },
  );
});
