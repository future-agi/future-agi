import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import OrgConfigEditor from "./OrgConfigEditor";
import {
  ACTIVE_URL,
  CREATE_URL,
  createFakeBackend,
} from "../budgets/admission/testing/serverFake";

// The page's cached Org Config read (useOrgConfig, 60 s
// staleTime) says admission caps are inactive, while the admission panel's
// own read of the server (its initial load or "Reload admission policy")
// says they are active. No newer page read arrives. The guard must follow
// the panel's newer read: the switch is locked and explained, Save sends no
// budgets.enabled=false, other edits are saved in one update-config, and
// deactivation stays available. A later page read outranks the panel's read,
// the editor's own read after a deactivation outranks it too, an inactive
// panel read leaves ordinary on/off alone, and viewers stay read-only.

const { get, post, context, stub } = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  context: { canEdit: true },
  stub: () => ({ default: () => null }),
}));
vi.mock("src/utils/axios", () => ({
  default: { get, post },
  endpoints: {
    gateway: {
      orgConfig: {
        active: "/agentcc/org-configs/active/",
        create: "/agentcc/org-configs/",
      },
      apiKeys: "/agentcc/api-keys/",
      updateConfig: (id) => `/agentcc/gateways/${id}/update-config/`,
    },
  },
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("@iconify/react", () => ({ Icon: () => null }));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: "org-a" }),
}));
vi.mock("../hooks/useGatewayAdmin", () => ({
  useGatewayAdmin: () => ({ canEdit: context.canEdit }),
}));
vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "gw-1" }),
}));
vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useGatewayConfig: () => ({
    data: { org_sections_enforced: ["budgets", "routing", "cost_tracking"] },
  }),
  useReloadConfig: () => ({ mutateAsync: vi.fn(), isPending: false }),
  refreshGatewayConfig: vi.fn(async () => {}),
  asRequestError: (error) => error,
}));
vi.mock("../hooks/useAvailableModels", () => ({
  useAvailableModels: () => [],
}));
vi.mock("./ProviderConfigTab", stub);
vi.mock("./GuardrailConfigTab", stub);
vi.mock("./RoutingConfigTab", stub);
vi.mock("./CacheConfigTab", stub);
vi.mock("./RateLimitingConfigTab", stub);
vi.mock("./SecurityConfigTab", stub);
vi.mock("./AlertingConfigTab", stub);
vi.mock("./ModelDatabaseConfigTab", stub);
vi.mock("./ModelMapConfigTab", stub);

const UPDATE_URL = "/agentcc/gateways/gw-1/update-config/";
// Active spellings in the panel's read: canonical, else the alias.
const ACTIVE = [
  ["canonical", { admission_caps_enabled: true, policy_revision: 4 }],
  ["alias-only", { admissionCapsEnabled: true, policyRevision: 4 }],
];
// Inactive spellings in the panel's read, including an active alias that a
// present canonical value (false or null) shadows.
const INACTIVE = [
  ["inactive canonical", { admission_caps_enabled: false, policy_revision: 3 }],
  ["inactive alias", { admissionCapsEnabled: false, policyRevision: 3 }],
  [
    "canonical false over active alias",
    {
      admission_caps_enabled: false,
      admissionCapsEnabled: true,
      policy_revision: 3,
    },
  ],
  [
    "canonical null over active alias",
    {
      admission_caps_enabled: null,
      admissionCapsEnabled: true,
      policy_revision: 3,
    },
  ],
];
const EXPLANATION = /deactivate daily admission caps.*before turning/i;
const PANEL_ACTIVE = "Daily caps active in saved policy";
const PANEL_INACTIVE = "Daily caps inactive";

let client;
const budgetToggle = () =>
  screen.getByRole("checkbox", { name: "Enable per-org budgets" });
const saveButton = () =>
  screen.getByRole("button", { name: "Save & Activate" });
const synced = () =>
  Promise.resolve({ data: { result: { gateway_synced: true } } });

// Synthetic org configs only.
function readOf(version, policy) {
  return {
    organization: "org-a",
    version,
    budgets: {
      enabled: true,
      ...policy,
      key_ids: {
        "synthetic-key-id": {
          limit: 5,
          period: "daily",
          activated_at: "2026-11-09T00:00:00Z",
        },
      },
    },
    routing: {},
  };
}
// The page's populated, cached read when the editor opened: caps inactive.
const pageRead = () =>
  readOf(4, { admission_caps_enabled: false, policy_revision: 3 });

// Opens the editor on the page's read while the server holds `server`; the
// admission panel reads the server itself. Most cases never call deliver(),
// the page's own newer read. With hold, the panel's first read stays pending
// until release().
function openOnPageRead(server, { hold = false } = {}) {
  const backend = createFakeBackend({ config: server });
  let releaseRead = () => {};
  const held = hold
    ? new Promise((resolve) => {
        releaseRead = resolve;
      })
    : null;
  get.mockImplementation((url, options) =>
    held && url === ACTIVE_URL
      ? held.then(() => backend.get(url, options))
      : backend.get(url, options),
  );
  post.mockImplementation((url, body, options) =>
    url === UPDATE_URL ? synced() : backend.post(url, body, options),
  );
  const onClose = vi.fn();
  const ui = (initialConfig) => (
    <QueryClientProvider client={client}>
      <OrgConfigEditor
        open
        onClose={onClose}
        initialConfig={initialConfig}
        defaultTab="budgets"
      />
    </QueryClientProvider>
  );
  const view = render(ui(pageRead()));
  return {
    backend,
    onClose,
    deliver: (config) => view.rerender(ui(config)),
    release: () =>
      act(async () => {
        releaseRead();
      }),
  };
}
async function ready() {
  await screen.findByTestId("admission-policy-current");
}
async function save(onClose) {
  fireEvent.click(saveButton());
  await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
}
function expectNoEnforcementOffPost() {
  post.mock.calls.forEach(([, body]) =>
    expect(body?.budgets?.enabled).not.toBe(false),
  );
}
// Exactly one update-config, carrying only the unrelated edit.
function expectOnlyTheOtherEditSaved() {
  expectNoEnforcementOffPost();
  expect(post).toHaveBeenCalledTimes(1);
  expect(post).toHaveBeenCalledWith(
    UPDATE_URL,
    { cost_tracking: { enabled: true } },
    expect.anything(),
  );
}
async function expectLockedAndExplained() {
  await waitFor(() => expect(budgetToggle()).toBeDisabled());
  expect(budgetToggle()).toBeChecked();
  expect(screen.getByText(EXPLANATION)).toHaveTextContent(
    /Admission cap activation panel above/i,
  );
}
// The user turns budgets off with the real switch while it is an ordinary
// edit: the editor shows inactive caps at that moment.
function turnOffWhileInactive() {
  expect(budgetToggle()).toBeEnabled();
  expect(budgetToggle()).toBeChecked();
  fireEvent.click(budgetToggle());
  expect(budgetToggle()).not.toBeChecked();
}
function turnOnCostTracking() {
  fireEvent.click(
    screen.getByRole("checkbox", { name: "Enable custom cost tracking" }),
  );
}

beforeEach(() => {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  get.mockReset();
  post.mockReset();
  context.canEdit = true;
});
afterEach(() => client.clear());

describe("the panel's initial read with active caps reaches the guard without a page read", () => {
  it.each(ACTIVE)(
    "%s: the real off toggle plus another edit saves only the other edit in one update-config; deactivation stays available",
    async (_name, policy) => {
      const { onClose } = openOnPageRead(readOf(5, policy));
      await ready();
      expect(screen.getByTestId("admission-policy-current")).toHaveTextContent(
        PANEL_ACTIVE,
      );
      fireEvent.click(budgetToggle());
      turnOnCostTracking();
      expect(budgetToggle()).toBeChecked();
      expect(screen.getByTestId("admission-deactivate")).toBeEnabled();
      await save(onClose);
      expectOnlyTheOtherEditSaved();
    },
  );

  it.each(ACTIVE)(
    "%s: the switch shows on, locked and explained; the ignored off alone sends nothing and closes",
    async (_name, policy) => {
      const { onClose } = openOnPageRead(readOf(5, policy));
      await ready();
      await expectLockedAndExplained();
      fireEvent.click(budgetToggle());
      expect(budgetToggle()).toBeChecked();
      await save(onClose);
      expect(post).not.toHaveBeenCalled();
    },
  );

  it.each(ACTIVE)(
    "%s: an off made while the panel is still loading is left out once its read arrives; other edits are kept",
    async (_name, policy) => {
      const { onClose, release } = openOnPageRead(readOf(5, policy), {
        hold: true,
      });
      expect(screen.getByTestId("admission-loading")).toBeInTheDocument();
      turnOffWhileInactive();
      turnOnCostTracking();
      await release();
      await ready();
      await expectLockedAndExplained();
      expect(screen.getByTestId("admission-deactivate")).toBeEnabled();
      await save(onClose);
      expectOnlyTheOtherEditSaved();
    },
  );
});

describe("'Reload admission policy' reaches the guard through the same read path", () => {
  it.each(ACTIVE)(
    "%s: caps activated elsewhere and reloaded lock the switch; the off is left out and other edits are kept",
    async (_name, policy) => {
      const { backend, onClose } = openOnPageRead(pageRead());
      await ready();
      expect(screen.getByTestId("admission-policy-current")).toHaveTextContent(
        PANEL_INACTIVE,
      );
      turnOffWhileInactive();
      turnOnCostTracking();
      // Caps are activated in another tab; only the panel reads the server.
      backend.state.config = readOf(5, policy);
      fireEvent.click(
        screen.getByRole("button", { name: "Reload admission policy" }),
      );
      await screen.findByText(PANEL_ACTIVE);
      await expectLockedAndExplained();
      expect(screen.getByTestId("admission-deactivate")).toBeEnabled();
      await save(onClose);
      expectOnlyTheOtherEditSaved();
    },
  );
});

describe("the newest read decides", () => {
  it("a page read after the panel's active read outranks it: inactive caps there give ordinary off", async () => {
    const { onClose, deliver } = openOnPageRead(readOf(5, ACTIVE[0][1]));
    await ready();
    await expectLockedAndExplained();
    // Caps are deactivated elsewhere and the page reads again.
    deliver(readOf(6, { admission_caps_enabled: false, policy_revision: 5 }));
    await waitFor(() => expect(budgetToggle()).toBeEnabled());
    expect(screen.queryByText(EXPLANATION)).not.toBeInTheDocument();
    turnOffWhileInactive();
    await save(onClose);
    expect(post).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith(
      UPDATE_URL,
      { budgets: { enabled: false } },
      expect.anything(),
    );
  });

  it("the editor's own deactivation read outranks the panel's earlier active read: ordinary off works afterwards", async () => {
    const { onClose } = openOnPageRead(readOf(5, ACTIVE[0][1]));
    await ready();
    await expectLockedAndExplained();
    fireEvent.click(screen.getByTestId("admission-deactivate"));
    fireEvent.click(screen.getByRole("button", { name: "Deactivate caps" }));
    await screen.findByText(PANEL_INACTIVE);
    expect(post).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith(
      CREATE_URL,
      expect.objectContaining({
        budgets: expect.objectContaining({
          enabled: true,
          admission_caps_enabled: false,
          policy_revision: 4,
        }),
      }),
      expect.anything(),
    );
    await waitFor(() => expect(budgetToggle()).toBeEnabled());
    turnOffWhileInactive();
    await save(onClose);
    expect(post).toHaveBeenCalledTimes(2);
    expect(post).toHaveBeenLastCalledWith(
      UPDATE_URL,
      { budgets: { enabled: false } },
      expect.anything(),
    );
  });

  it.each(INACTIVE)(
    "%s panel read: ordinary on/off and the minimal patch are unchanged",
    async (_name, policy) => {
      const { onClose } = openOnPageRead(readOf(5, policy));
      await ready();
      expect(screen.getByTestId("admission-policy-current")).toHaveTextContent(
        PANEL_INACTIVE,
      );
      expect(screen.queryByText(EXPLANATION)).not.toBeInTheDocument();
      turnOffWhileInactive();
      await save(onClose);
      expect(post).toHaveBeenCalledTimes(1);
      expect(post).toHaveBeenCalledWith(
        UPDATE_URL,
        { budgets: { enabled: false } },
        expect.anything(),
      );
    },
  );
});

describe("viewers stay read-only", () => {
  it.each([
    ["active", ACTIVE[0][1]],
    ["inactive", INACTIVE[0][1]],
  ])("%s panel read: no write path", async (_name, policy) => {
    context.canEdit = false;
    openOnPageRead(readOf(5, policy));
    await ready();
    expect(budgetToggle()).toBeDisabled();
    expect(saveButton()).toBeDisabled();
    fireEvent.click(saveButton());
    expect(post).not.toHaveBeenCalled();
  });
});
