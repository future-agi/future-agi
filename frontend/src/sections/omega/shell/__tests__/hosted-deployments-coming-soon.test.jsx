// Adapted from /Volumes/nikm4/omega-launch/worktrees/fidep-hub-deploy-gate-1101/frontend/src/sections/omega/shell/__tests__/hosted-deployments-coming-soon.test.jsx
// Source SHA-256: 34f8864b9e499a129942caa6f1c14ef0499bba4d517e2bb33b94ced26c759118
// RL 1808: sidebar information only, for BOTH hosted-withheld products. Unlike
// the source's admitted case, admitted products retain their working entry.
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

import Label from "src/components/label";
import VerticalNavList from "src/components/nav-section/vertical/nav-list";
import MiniNavList from "src/components/nav-section/mini/nav-list";
import HorizontalNavList from "src/components/nav-section/horizontal/nav-list";
import {
  NavSectionVertical,
  NavSectionMini,
  NavSectionHorizontal,
} from "src/components/nav-section";
import { useNavData } from "src/layouts/dashboard/config-navigation";
import { useDeploymentMode } from "src/hooks/useDeploymentMode";
import {
  omegaDashboardRoutes,
  omegaSettingsRoutes,
} from "src/routes/sections/omega";
import { mainRoutes } from "src/routes/sections/main";

import { withAgentInfrastructure } from "../agent-infrastructure-nav";
import { useOmegaProducts } from "../useOmegaProducts";
import { capabilitiesAnswer } from "./rollout-answer";
import { ShellTestProviders } from "./test-providers";

const observed = vi.hoisted(() => ({ response: null }));
vi.mock("src/hooks/useCapabilities", async (importOriginal) => ({
  ...(await importOriginal()),
  useCapabilities: vi.fn(() => observed.response),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    user: { id: "owner-fixture", role: "Owner", organization_role: "Owner" },
  }),
}));
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: vi.fn(() => {
    throw new Error(
      "Reuse the existing capabilities observation, not another mode query",
    );
  }),
}));
vi.mock("src/utils/Mixpanel", () => ({
  trackEvent: vi.fn(),
  Events: new Proxy({}, { get: (_, key) => String(key) }),
  PropertyName: new Proxy({}, { get: (_, key) => String(key) }),
}));

const PRODUCTS = [
  ["deployments", "Deployments"],
  ["sandboxes", "Sandboxes"],
];
const HOST = [
  {
    subheader: "BUILD",
    items: [{ title: "Dataset", path: "/dashboard/develop" }],
  },
];
const allItems = (sections) =>
  sections.flatMap((section) => section.items || []);
const productRows = (sections, title) =>
  allItems(sections).filter((item) => item.title === title);
const denied = () => ({ allowed: false, reason_code: "PRODUCT_NOT_ADMITTED" });
const query = (mode = "cloud", options = {}) => {
  const data = capabilitiesAnswer({ admitted: ["studio_tab", "hub"] });
  for (const [product] of PRODUCTS) {
    data.features[`omega_part_hosted_${product}`] = denied();
  }
  return {
    data: {
      ...data,
      deployment_flavor:
        mode === "cloud" ? "cloud_image" : "self_hosted_ee_image",
      display_mode: mode,
    },
    isLoading: false,
    isError: false,
    ...options,
  };
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubEnv("VITE_OMEGA_CONSOLE", "1");
  observed.response = query();
});
afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
});

function Providers({ children }) {
  return (
    <ShellTestProviders>
      <MemoryRouter initialEntries={["/dashboard/develop"]}>
        {children}
      </MemoryRouter>
    </ShellTestProviders>
  );
}
Providers.propTypes = { children: PropTypes.node };

function Location() {
  return <output data-testid="location">{useLocation().pathname}</output>;
}

function Sidebar({ Section }) {
  const data = useNavData();
  return <Section data={data} />;
}
Sidebar.propTypes = { Section: PropTypes.elementType.isRequired };

function assertInert(row) {
  expect(
    row.querySelector(
      "a, [href], button, input, select, textarea, [role='button'], [role='link']",
    ),
  ).toBeNull();
  expect(row.querySelector("[tabindex], [contenteditable='true']")).toBeNull();
}

// Both hosted regions use display_mode=cloud; there is no region field in
// /api/capabilities/. These cases deliberately use the same server wire shape.
describe.each(["cloud", "EU"])("%s hosted-withheld sidebar (RL 1808)", () => {
  it("adds exactly one pathless informational descriptor per withheld product", () => {
    const { result } = renderHook(() => useNavData(), { wrapper: Providers });
    for (const [, title] of PRODUCTS) {
      const rows = productRows(result.current, title);
      // RED at the base: these rows are absent, not an import/fixture failure.
      expect(rows).toHaveLength(1);
      expect(rows[0].inert).toBe(true);
      for (const key of [
        "path",
        "href",
        "activePath",
        "children",
        "hasChildren",
        "eventTrigger",
        "onClick",
        "onKeyDown",
        "tabIndex",
      ]) {
        expect(rows[0]).not.toHaveProperty(key);
      }
    }
    expect(useDeploymentMode).not.toHaveBeenCalled();
  });

  it.each([
    ["full", NavSectionVertical],
    ["mini", NavSectionMini],
    ["horizontal", NavSectionHorizontal],
  ])(
    "shows both titles and distinct labels in the real %s navigation",
    (_name, Section) => {
      render(<Sidebar Section={Section} />, { wrapper: Providers });
      for (const [, title] of PRODUCTS) {
        const titleNode = screen.getByText(title, { exact: true });
        const row = titleNode.parentElement;
        const label = within(row).getByText(
          "Not available in this deployment",
          { exact: true },
        );
        expect(titleNode).toBeVisible();
        expect(label).toBeVisible();
        expect(label).toHaveStyle({ textTransform: "none" });
        expect(titleNode).not.toContainElement(label);
        expect(label).not.toContainElement(titleNode);
        assertInert(row);
        expect(screen.getAllByText(title, { exact: true })).toHaveLength(1);
      }
    },
  );
});

describe("hosted information is not admission", () => {
  it.each(PRODUCTS)(
    "keeps admitted %s working once, while the other product stays withheld",
    (product, title) => {
      observed.response.data.features[`omega_${product}`] = {
        allowed: true,
        reason_code: null,
      };
      observed.response.data.features[`omega_part_hosted_${product}`] = {
        allowed: true,
        reason_code: null,
      };
      const { result } = renderHook(() => useNavData(), { wrapper: Providers });
      const rows = productRows(result.current, title);
      expect(rows).toHaveLength(1);
      expect(rows[0].path).toBe(`/dashboard/${product}`);
      expect(rows[0]).not.toHaveProperty("inert");
      expect(rows[0]).not.toHaveProperty("info");
      const other = PRODUCTS.find(([key]) => key !== product);
      expect(productRows(result.current, other[1])[0]).toMatchObject({
        inert: true,
      });
    },
  );

  it.each([
    ["full", VerticalNavList],
    ["mini", MiniNavList],
    ["horizontal", HorizontalNavList],
  ])(
    "keeps admitted entries as normal links in %s navigation",
    async (_name, NavList) => {
      for (const [product] of PRODUCTS) {
        observed.response.data.features[`omega_${product}`] = {
          allowed: true,
          reason_code: null,
        };
        observed.response.data.features[`omega_part_hosted_${product}`] = {
          allowed: true,
          reason_code: null,
        };
      }
      const { result } = renderHook(() => useNavData(), { wrapper: Providers });
      render(
        <>
          {PRODUCTS.map(([, title]) => (
            <NavList
              key={title}
              data={productRows(result.current, title)[0]}
              depth={1}
            />
          ))}
          <Location />
        </>,
        { wrapper: Providers },
      );
      expect(screen.queryByText("Not available in this deployment")).toBeNull();
      for (const [product] of PRODUCTS) {
        const links = document.querySelectorAll(
          `a[href='/dashboard/${product}']`,
        );
        expect(links).toHaveLength(1);
        await userEvent.click(links[0]);
        expect(screen.getByTestId("location")).toHaveTextContent(
          `/dashboard/${product}`,
        );
      }
    },
  );

  it("leaves self-hosted/preview descriptors and master-switch OFF unchanged", () => {
    const baseline = withAgentInfrastructure(HOST, true, () => true);
    expect(
      withAgentInfrastructure(HOST, true, () => true, {
        hostedWithheld: () => false,
      }),
    ).toEqual(baseline);
    for (const [product, title] of PRODUCTS) {
      expect(productRows(baseline, title)[0].path).toBe(
        `/dashboard/${product}`,
      );
      expect(productRows(baseline, title)[0]).not.toHaveProperty("inert");
    }
    expect(
      withAgentInfrastructure(HOST, false, () => false, {
        hostedWithheld: () => true,
      }),
    ).toBe(HOST);
    expect(withAgentInfrastructure(HOST, true, () => false)).toBe(HOST);
    vi.stubEnv("VITE_OMEGA_CONSOLE", "0");
    const { result } = renderHook(() => useNavData(), { wrapper: Providers });
    for (const [, title] of PRODUCTS)
      expect(productRows(result.current, title)).toEqual([]);
    expect(omegaDashboardRoutes()).toEqual([]);
    expect(HOST).toHaveLength(1);
  });

  it.each(["oss", "enterprise", "preview"])(
    "keeps admitted %s navigation unchanged",
    (mode) => {
      observed.response = query(mode);
      for (const [product] of PRODUCTS) {
        observed.response.data.features[`omega_${product}`] = {
          allowed: true,
          reason_code: null,
        };
      }
      const { result } = renderHook(() => useNavData(), { wrapper: Providers });
      for (const [product, title] of PRODUCTS) {
        const rows = productRows(result.current, title);
        expect(rows).toHaveLength(1);
        expect(rows[0].path).toBe(`/dashboard/${product}`);
        expect(rows[0]).not.toHaveProperty("inert");
        expect(rows[0]).not.toHaveProperty("info");
      }
    },
  );

  it.each(PRODUCTS)(
    "preserves the existing paused entry for %s",
    (product, title) => {
      observed.response.data.features[`omega_${product}`] = {
        allowed: false,
        reason_code: "PRODUCT_PAUSED",
      };
      const { result } = renderHook(() => useNavData(), { wrapper: Providers });
      const rows = productRows(result.current, title);
      expect(rows).toHaveLength(1);
      expect(rows[0].path).toBe(`/dashboard/${product}`);
      expect(rows[0]).not.toHaveProperty("inert");
      expect(rows[0]).not.toHaveProperty("info");
    },
  );

  it.each(["oss", "oss_locked", "enterprise", "preview", undefined, "unknown"])(
    "does not classify %s as hosted or change its denied navigation",
    (mode) => {
      observed.response = query();
      observed.response.data.display_mode = mode;
      const { result } = renderHook(
        () => ({ products: useOmegaProducts(), sections: useNavData() }),
        { wrapper: Providers },
      );
      expect(result.current.products.isHosted).toBe(false);
      for (const [product, title] of PRODUCTS) {
        expect(result.current.products.listed(product)).toBe(false);
        expect(productRows(result.current.sections, title)).toEqual([]);
      }
    },
  );

  it.each([
    { isError: true },
    { isLoading: true },
    { data: undefined, isLoading: true },
    { data: undefined },
  ])(
    "invents no hosted rows on an unreadable/loading/missing answer (%j)",
    (state) => {
      observed.response = query("cloud", state);
      const { result } = renderHook(
        () => ({ products: useOmegaProducts(), sections: useNavData() }),
        { wrapper: Providers },
      );
      expect(result.current.products.isHosted).toBe(false);
      for (const [product, title] of PRODUCTS) {
        expect(result.current.products.listed(product)).toBe(false);
        expect(productRows(result.current.sections, title)).toEqual([]);
      }
      expect(useDeploymentMode).not.toHaveBeenCalled();
    },
  );

  it.each(PRODUCTS)(
    "requires an explicit withheld part and product answer for %s",
    (product, title) => {
      const { result, rerender } = renderHook(() => useNavData(), {
        wrapper: Providers,
      });
      expect(productRows(result.current, title)).toHaveLength(1);
      for (const featureId of [
        `omega_${product}`,
        `omega_part_hosted_${product}`,
      ]) {
        for (const feature of [
          undefined,
          { allowed: false, reason_code: "RESOLVER_UNAVAILABLE" },
          { allowed: false },
        ]) {
          observed.response = query();
          observed.response.data.features[featureId] = feature;
          rerender();
          expect(productRows(result.current, title)).toEqual([]);
        }
      }
      observed.response = query();
      observed.response.data.features[`omega_part_hosted_${product}`] = {
        allowed: true,
        reason_code: null,
      };
      rerender();
      expect(productRows(result.current, title)).toEqual([]);
    },
  );

  it("uses the successful capabilities observation without granting admission", () => {
    const { result } = renderHook(() => useOmegaProducts(), {
      wrapper: Providers,
    });
    expect(result.current.isHosted).toBe(true);
    for (const [product] of PRODUCTS)
      expect(result.current.listed(product)).toBe(false);
    expect(useDeploymentMode).not.toHaveBeenCalled();
  });
});

describe.each([
  ["full", VerticalNavList],
  ["mini", MiniNavList],
  ["horizontal", HorizontalNavList],
])("%s inert nav rendering", (_name, NavList) => {
  it.each(PRODUCTS)(
    "renders %s without any link, button, tabstop or action",
    async (_product, title) => {
      const requestOrNavigation = vi.fn();
      const user = userEvent.setup();
      // Unexpected interactive fields must not become fallback actions; real
      // hosted descriptors omit all of them. RL 1808 supersedes disabled links.
      const data = {
        title,
        inert: true,
        info: (
          <Label sx={{ textTransform: "none" }}>
            Not available in this deployment
          </Label>
        ),
        path: "/dashboard/deployments",
        href: "/dashboard/deployments",
        tabIndex: 0,
        onClick: requestOrNavigation,
        onKeyDown: requestOrNavigation,
        eventTrigger: requestOrNavigation,
        children: [
          { title: "Never mount this route", path: "/dashboard/deployments" },
        ],
      };
      render(
        <>
          <button type="button">Before</button>
          <section data-testid="row">
            <NavList data={data} depth={1} />
          </section>
          <button type="button">After</button>
          <Location />
        </>,
        { wrapper: Providers },
      );
      const row = screen.getByTestId("row");
      const titleNode = screen.getByText(title, { exact: true });
      const label = screen.getByText("Not available in this deployment", {
        exact: true,
      });
      expect(titleNode).toBeVisible();
      expect(label).toBeVisible();
      expect(titleNode).not.toContainElement(label);
      expect(label).not.toContainElement(titleNode);
      assertInert(row);
      fireEvent.click(titleNode);
      fireEvent.mouseEnter(titleNode);
      fireEvent.keyDown(titleNode, { key: "Enter" });
      fireEvent.keyDown(titleNode, { key: " " });
      expect(screen.queryByText("Never mount this route")).toBeNull();
      expect(requestOrNavigation).not.toHaveBeenCalled();
      expect(screen.getByTestId("location")).toHaveTextContent(
        "/dashboard/develop",
      );
      await user.tab();
      expect(screen.getByRole("button", { name: "Before" })).toHaveFocus();
      await user.tab();
      expect(screen.getByRole("button", { name: "After" })).toHaveFocus();
    },
  );
});

function ProductRoutes() {
  return useRoutes([
    { path: "/dashboard", children: omegaDashboardRoutes() },
    { path: "/dashboard/settings", children: omegaSettingsRoutes() },
    ...mainRoutes,
  ]);
}

describe("RL 1808 preserves the real product route gates and standard not-found page", () => {
  it.each([
    "/dashboard/deployments",
    "/dashboard/deployments/new",
    "/dashboard/deployments/secrets",
    "/dashboard/deployments/example/settings",
    "/dashboard/sandboxes",
    "/dashboard/sandboxes/example/files",
    "/dashboard/settings/sandboxes",
  ])(
    "%s still renders the standard 404, not a coming-soon page",
    async (path) => {
      render(
        <ShellTestProviders>
          <MemoryRouter initialEntries={[path]}>
            <Suspense fallback={null}>
              <ProductRoutes />
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
      expect(screen.queryByTestId("omega-tab-deployments")).toBeNull();
      expect(screen.queryByTestId("omega-tab-sandboxes")).toBeNull();
    },
  );
});
