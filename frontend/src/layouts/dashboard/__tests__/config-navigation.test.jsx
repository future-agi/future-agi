import React from "react";
import PropTypes from "prop-types";
import { renderHook } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { paths } from "src/routes/paths";
import { useDeploymentMode } from "src/hooks/useDeploymentMode";
import { gatewayRailGroups } from "src/sections/gateway/shell/gateway-rail";
import { useNavData } from "../config-navigation";

const authState = { user: null };
const workspaceState = { currentWorkspaceRole: null };
const managed = vi.hoisted(() => ({ status: {} }));
const edition = vi.hoisted(() => ({ mode: {} }));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => authState,
}));

vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => workspaceState,
}));

// The sidebar's Models entry must not depend on whether managed models are
// offered: every answer of the status read is served here, and the entry is
// expected in each.
vi.mock("src/sections/gateway/models/hooks/useManagedModels", () => ({
  useManagedModelsStatus: () => managed.status,
}));

vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: vi.fn(() => edition.mode),
}));

// The fork's nav also lists the Omega products the server admits (a capabilities
// query), so the hook needs a query client as well as a router (round 7 merge).
function Providers({ children }) {
  const [client] = React.useState(
    () => new QueryClient({ defaultOptions: { queries: { retry: false } } }),
  );
  return (
    <QueryClientProvider client={client}>
      <MemoryRouter>{children}</MemoryRouter>
    </QueryClientProvider>
  );
}
Providers.propTypes = { children: PropTypes.node };

function navSections() {
  const { result } = renderHook(() => useNavData(), { wrapper: Providers });
  return result.current;
}

function simulateTitles() {
  const section = navSections().find((s) => s.subheader === "Simulate");
  return section.items.map((item) => item.title);
}

const GATEWAY_MODELS = paths.dashboard.gateway.models;
const modelsEntries = (sections) =>
  sections
    .flatMap((section) => section.items)
    .filter((item) => item.path === GATEWAY_MODELS);

describe("Simulate menu", () => {
  beforeEach(() => {
    authState.user = { organization_role: "Owner" };
  });

  it("shows only Environment to an org restricted to Environments", () => {
    authState.user.restrict_simulate_to_environments = true;
    expect(simulateTitles()).toEqual(["Environment"]);
  });

  it.each([false, undefined])(
    "shows every Simulate page when the flag is %s",
    (flag) => {
      authState.user.restrict_simulate_to_environments = flag;
      expect(simulateTitles()).toEqual([
        "Environment",
        "Scenarios",
        "Run Simulation",
      ]);
    },
  );
});

// The sidebar names the dataset list as the page and Get started do:
// "Datasets", the plural, never "Dataset".
describe("Datasets in the Future AGI sidebar", () => {
  beforeEach(() => {
    authState.user = { organization_role: "Owner" };
  });

  it("reads Datasets on the entry that opens the dataset list", () => {
    const entries = navSections()
      .flatMap((section) => section.items)
      .filter((item) => item.path === paths.dashboard.develop);
    expect(entries).toHaveLength(1);
    expect(entries[0].title).toBe("Datasets");
  });
});

// Models in the Future AGI sidebar opens the AI Gateway model catalog
// (/dashboard/gateway/models), which was reachable only through the product
// switcher before.
describe("Models in the Future AGI sidebar", () => {
  beforeEach(() => {
    authState.user = { organization_role: "Owner" };
    workspaceState.currentWorkspaceRole = null;
    managed.status = { data: undefined, isLoading: true };
    edition.mode = { isOSS: false, isCloud: true, isEE: false };
    vi.mocked(useDeploymentMode).mockClear();
  });

  // (a) One entry named "Models", the word the catalog page and the gateway
  // rail use, at the end of Build (the group of the things a team builds and
  // serves with), with the AI Gateway's mark. Every other Build entry keeps
  // its place.
  it("is the last Build entry, named as the gateway rail names the catalog", () => {
    const sections = navSections();
    const build = sections.find((s) => s.subheader === "BUILD");
    expect(build.items.map((item) => item.title)).toEqual([
      "Datasets",
      "Prompts",
      "Agents",
      "Knowledge base",
      "Evals",
      "Keys",
      "Models",
    ]);
    const [entry] = modelsEntries(sections);
    expect(build.items.at(-1)).toBe(entry);
    expect(entry.title).toBe("Models");
    expect(entry.path).toBe("/dashboard/gateway/models");
    const railModels = gatewayRailGroups()
      .flatMap((group) => group.items)
      .find((item) => item.path === GATEWAY_MODELS);
    expect(entry.title).toBe(railModels.title);
    // The glyph the product switcher draws for the AI Gateway.
    expect(entry.icon.props["data-gateway-icon"]).toBe("gateway");
  });

  // (b) Every role that sees the Future AGI sidebar: members already see the
  // catalog read-only.
  it.each([
    ["Owner", null],
    ["Admin", null],
    ["Member", null],
    ["Viewer", null],
    ["Member", "workspace_admin"],
    ["Member", "workspace_member"],
    ["Viewer", "workspace_viewer"],
  ])("is listed once for an org %s (workspace role %s)", (org, ws) => {
    authState.user = { organization_role: org };
    workspaceState.currentWorkspaceRole = ws;
    const entries = modelsEntries(navSections());
    expect(entries).toHaveLength(1);
    expect(entries[0].title).toBe("Models");
  });

  // (b) Every edition: the product switcher lists the AI Gateway in each, so
  // the entry reads no edition at all.
  it.each([
    ["OSS", { isOSS: true, isCloud: false, isEE: false }],
    ["EE", { isOSS: false, isCloud: false, isEE: true }],
    ["Cloud", { isOSS: false, isCloud: true, isEE: false }],
  ])("is listed in the %s edition without reading the edition", (_, mode) => {
    edition.mode = { ...mode, isLoading: false };
    expect(modelsEntries(navSections())).toHaveLength(1);
    expect(useDeploymentMode).not.toHaveBeenCalled();
  });

  // (c) Shown at launch whatever the managed-model answer: with credits off the
  // catalog lists the org's own-provider models and says so.
  it.each([
    ["while the managed-model status loads", { isLoading: true }],
    ["with no managed models offered", { data: { available: false } }],
    ["with managed models offered", { data: { available: true } }],
    ["after the managed-model status fails", { isError: true }],
  ])("is listed %s", (_, status) => {
    managed.status = status;
    expect(modelsEntries(navSections())).toHaveLength(1);
  });
});
