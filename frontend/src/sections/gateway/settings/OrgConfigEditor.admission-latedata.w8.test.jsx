import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import OrgConfigEditor from "./OrgConfigEditor";
import {
  CREATE_URL,
  createFakeBackend,
} from "../budgets/admission/testing/serverFake";

// Late data: the Cache and Routing pages open Org Config with
// initialConfig=null while their Org Config query is still pending, and the
// editor takes its draft when it opens. Active admission caps (canonical or
// alias) that arrive after that must still keep budgets.enabled out of the
// save without dropping other edits, lock the switch with the same
// explanation, and never strand deactivation. Inactive caps keep ordinary
// on/off, and viewers stay read-only.

const { get, post, context, budgetForm, stub } = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  context: { canEdit: true },
  budgetForm: { props: null },
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
// The real form, with its props captured so a test can queue an off draft
// independently of the switch's own lock.
vi.mock("./BudgetsConfigTab", async (importOriginal) => {
  const { default: BudgetsConfigTab } = await importOriginal();
  return {
    default: (props) => {
      budgetForm.props = props;
      return <BudgetsConfigTab {...props} />;
    },
  };
});

const UPDATE_URL = "/agentcc/gateways/gw-1/update-config/";
// The spellings the backend reads as active: the canonical key when present,
// else its camelCase alias.
const ACTIVE = [
  ["canonical", { admission_caps_enabled: true, policy_revision: 3 }],
  ["alias-only", { admissionCapsEnabled: true, policyRevision: 3 }],
  [
    "canonical active over inactive alias",
    {
      admission_caps_enabled: true,
      admissionCapsEnabled: false,
      policy_revision: 3,
    },
  ],
];
const INACTIVE = [
  ["inactive canonical", { admission_caps_enabled: false }],
  ["inactive alias", { admissionCapsEnabled: false }],
  ["absent activation", {}],
  [
    "canonical false over active alias",
    { admission_caps_enabled: false, admissionCapsEnabled: true },
  ],
  [
    "canonical null over active alias",
    { admission_caps_enabled: null, admissionCapsEnabled: true },
  ],
];
const EXPLANATION = /deactivate daily admission caps.*before turning/i;

let client;
const budgetToggle = () =>
  screen.getByRole("checkbox", { name: "Enable per-org budgets" });
const saveButton = () =>
  screen.getByRole("button", { name: "Save & Activate" });

// The stored org config (synthetic values only).
function storedConfig(policy) {
  return {
    organization: "org-a",
    version: 4,
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

// Opens the editor the way the Cache and Routing pages do before their Org
// Config query resolves: initialConfig=null. The admission panel reads the
// stored config itself. deliver() is the page's query resolving while the
// editor stays open; it hands over the page's own copy of that read.
function openBeforeLoad(config) {
  const backend = createFakeBackend({ config });
  get.mockImplementation(backend.get);
  post.mockImplementation((url, body, options) =>
    url === UPDATE_URL
      ? Promise.resolve({ data: { result: { gateway_synced: true } } })
      : backend.post(url, body, options),
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
  const view = render(ui(null));
  return {
    onClose,
    deliver: () => view.rerender(ui(JSON.parse(JSON.stringify(config)))),
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
function queueOffDraft() {
  act(() => {
    budgetForm.props.onBudgetsChange({
      ...budgetForm.props.budgets,
      enabled: false,
    });
  });
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
  budgetForm.props = null;
});
afterEach(() => client.clear());

describe("Org Config opened before its config loads, admission caps active", () => {
  it.each(ACTIVE)(
    "%s: a pending off draft is left out of the save after the config arrives, other edits are kept",
    async (_name, policy) => {
      const { onClose, deliver } = openBeforeLoad(storedConfig(policy));
      await ready();
      deliver();
      queueOffDraft();
      turnOnCostTracking();
      await save(onClose);
      expectNoEnforcementOffPost();
      expect(post).toHaveBeenCalledTimes(1);
      expect(post).toHaveBeenCalledWith(
        UPDATE_URL,
        { cost_tracking: { enabled: true } },
        expect.anything(),
      );
    },
  );

  it.each(ACTIVE)(
    "%s: once the config arrives the switch shows on, locked and explained; Save sends nothing",
    async (_name, policy) => {
      const { onClose, deliver } = openBeforeLoad(storedConfig(policy));
      await ready();
      deliver();
      await waitFor(() => expect(budgetToggle()).toBeDisabled());
      expect(budgetToggle()).toBeChecked();
      expect(screen.getByText(EXPLANATION)).toHaveTextContent(
        /Admission cap activation panel above/i,
      );
      expect(screen.getByTestId("admission-deactivate")).toBeEnabled();
      fireEvent.click(budgetToggle());
      expect(budgetToggle()).toBeChecked();
      await save(onClose);
      expectNoEnforcementOffPost();
      expect(post).not.toHaveBeenCalled();
    },
  );

  it.each(ACTIVE)(
    "%s: turned on while loading, then active caps arrive: Save sends no enabled=false",
    async (_name, policy) => {
      const { onClose, deliver } = openBeforeLoad(storedConfig(policy));
      await ready();
      expect(budgetToggle()).not.toBeChecked();
      fireEvent.click(budgetToggle());
      expect(budgetToggle()).toBeChecked();
      deliver();
      await waitFor(() => expect(budgetToggle()).toBeDisabled());
      expect(budgetToggle()).toBeChecked();
      fireEvent.click(budgetToggle());
      await save(onClose);
      expectNoEnforcementOffPost();
      // While caps are active the backend fences any budgets.enabled change
      // behind the policy revision (cap_revision.py CAP_PREREQUISITES), so
      // the while-loading on is left out too: there is nothing to send.
      expect(post).not.toHaveBeenCalled();
    },
  );

  it.each(ACTIVE)(
    "%s: a pending off draft cannot strand deactivation after the config arrives; ordinary disabling works afterwards",
    async (_name, policy) => {
      const { onClose, deliver } = openBeforeLoad(storedConfig(policy));
      await ready();
      deliver();
      queueOffDraft();
      const deactivate = screen.getByTestId("admission-deactivate");
      expect(deactivate).toBeEnabled();
      fireEvent.click(deactivate);
      expect(post).not.toHaveBeenCalled();
      fireEvent.click(screen.getByRole("button", { name: "Deactivate caps" }));
      await screen.findByText("Daily caps inactive");
      expect(post).toHaveBeenCalledTimes(1);
      expect(post).toHaveBeenCalledWith(
        CREATE_URL,
        expect.objectContaining({
          budgets: expect.objectContaining({
            enabled: true,
            admission_caps_enabled: false,
            policy_revision: 3,
          }),
        }),
        expect.anything(),
      );
      expectNoEnforcementOffPost();
      // The page's copy still says active; the editor's own read after
      // deactivation is the newer one.
      await waitFor(() => expect(budgetToggle()).toBeEnabled());
      expect(budgetToggle()).toBeChecked();
      fireEvent.click(budgetToggle());
      await save(onClose);
      expect(post).toHaveBeenLastCalledWith(
        UPDATE_URL,
        { budgets: { enabled: false } },
        expect.anything(),
      );
    },
  );
});

describe("Org Config opened before its config loads, admission caps inactive", () => {
  it.each(INACTIVE)(
    "%s: once the config arrives budgets turn on and off as before",
    async (_name, policy) => {
      const { onClose, deliver } = openBeforeLoad(
        storedConfig({ ...policy, policy_revision: 3 }),
      );
      await ready();
      deliver();
      expect(budgetToggle()).toBeEnabled();
      fireEvent.click(budgetToggle());
      expect(budgetToggle()).toBeChecked();
      fireEvent.click(budgetToggle());
      expect(budgetToggle()).not.toBeChecked();
      expect(screen.queryByText(EXPLANATION)).not.toBeInTheDocument();
      await save(onClose);
      expect(post).toHaveBeenCalledTimes(1);
      expect(post).toHaveBeenCalledWith(
        UPDATE_URL,
        { budgets: { enabled: false } },
        expect.anything(),
      );
    },
  );

  it.each([
    ["active", ACTIVE[0][1]],
    ["inactive", { admission_caps_enabled: false, policy_revision: 3 }],
  ])(
    "%s caps: a viewer still has no write path after the config arrives",
    async (_name, policy) => {
      context.canEdit = false;
      const { deliver } = openBeforeLoad(storedConfig(policy));
      await ready();
      deliver();
      expect(budgetToggle()).toBeDisabled();
      expect(saveButton()).toBeDisabled();
      fireEvent.click(saveButton());
      expect(post).not.toHaveBeenCalled();
    },
  );
});
