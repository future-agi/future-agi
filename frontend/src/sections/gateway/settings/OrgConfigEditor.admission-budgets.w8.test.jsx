import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import OrgConfigEditor from "./OrgConfigEditor";
import {
  CREATE_URL,
  createFakeBackend,
} from "../budgets/admission/testing/serverFake";

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
// Render the real form. Capture its boundary only to exercise a pending off
// draft independently of the switch's UI/handler guard in the parent tests.
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
const ACTIVE_POLICIES = [
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
let client;
let backend;
const budgetToggle = () =>
  screen.getByRole("checkbox", { name: "Enable per-org budgets" });
const saveButton = () =>
  screen.getByRole("button", { name: "Save & Activate" });
function expectNoEnforcementOffPost() {
  expect(post).not.toHaveBeenCalledWith(
    expect.any(String),
    expect.objectContaining({
      budgets: expect.objectContaining({ enabled: false }),
    }),
    expect.anything(),
  );
}
function openEditor(policy, enabled = true) {
  const config = {
    organization: "org-a",
    version: 4,
    budgets: {
      enabled,
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
  backend = createFakeBackend({ config });
  get.mockImplementation(backend.get);
  post.mockImplementation((url, body, options) =>
    url === UPDATE_URL
      ? Promise.resolve({ data: { result: { gateway_synced: true } } })
      : backend.post(url, body, options),
  );
  const onClose = vi.fn();
  render(
    <QueryClientProvider client={client}>
      <OrgConfigEditor
        open
        onClose={onClose}
        initialConfig={config}
        defaultTab="budgets"
      />
    </QueryClientProvider>,
  );
  return onClose;
}
async function ready() {
  await screen.findByTestId("admission-policy-current");
}
async function save(onClose) {
  fireEvent.click(saveButton());
  await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
}
function queueOffDraft() {
  act(() => {
    budgetForm.props.onBudgetsChange({
      ...budgetForm.props.budgets,
      enabled: false,
    });
  });
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

describe("Org Config enforcement-off dependency", () => {
  it.each(ACTIVE_POLICIES)(
    "%s active caps lock the real switch and Save & Activate never sends enabled=false",
    async (_name, policy) => {
      const onClose = openEditor(policy);
      await ready();
      fireEvent.click(budgetToggle());
      await save(onClose);
      expectNoEnforcementOffPost();
      expect(post).not.toHaveBeenCalled();
      expect(budgetToggle()).toBeChecked();
      expect(budgetToggle()).toBeDisabled();
      expect(
        screen.getByText(/deactivate daily admission caps.*before turning/i),
      ).toHaveTextContent(/Admission cap activation panel above/i);
      expect(screen.getByTestId("admission-deactivate")).toBeEnabled();
    },
  );

  it.each(ACTIVE_POLICIES)(
    "%s pending off draft is excluded from the save without dropping other edits",
    async (_name, policy) => {
      const onClose = openEditor(policy);
      await ready();
      queueOffDraft();
      fireEvent.click(
        screen.getByRole("checkbox", { name: "Enable custom cost tracking" }),
      );
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

  it.each(ACTIVE_POLICIES)(
    "%s pending off draft cannot strand admission deactivation; ordinary disabling works afterwards",
    async (_name, policy) => {
      const onClose = openEditor(policy);
      await ready();
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

  it.each([
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
  ])(
    "%s preserves ordinary disabling and the minimal patch",
    async (_name, policy) => {
      const onClose = openEditor({ ...policy, policy_revision: 3 });
      await ready();
      expect(budgetToggle()).toBeEnabled();
      expect(
        screen.queryByText(/deactivate daily admission caps.*before turning/i),
      ).not.toBeInTheDocument();
      fireEvent.click(budgetToggle());
      expect(budgetToggle()).not.toBeChecked();
      await save(onClose);
      expect(post).toHaveBeenCalledTimes(1);
      expect(post).toHaveBeenCalledWith(
        UPDATE_URL,
        { budgets: { enabled: false } },
        expect.anything(),
      );
    },
  );

  it("preserves enabling when admission caps are inactive", async () => {
    const onClose = openEditor({ admission_caps_enabled: false }, false);
    await ready();
    expect(budgetToggle()).not.toBeChecked();
    expect(budgetToggle()).toBeEnabled();
    fireEvent.click(budgetToggle());
    expect(budgetToggle()).toBeChecked();
    await save(onClose);
    expect(post).toHaveBeenCalledWith(
      UPDATE_URL,
      { budgets: { enabled: true } },
      expect.anything(),
    );
  });

  it("does not turn the inactive switch or Save into a viewer write path", async () => {
    context.canEdit = false;
    openEditor({ admission_caps_enabled: false });
    await ready();
    expect(budgetToggle()).toBeDisabled();
    expect(saveButton()).toBeDisabled();
    fireEvent.click(saveButton());
    expect(post).not.toHaveBeenCalled();
  });
});
