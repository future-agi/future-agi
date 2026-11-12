import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import OrgConfigEditor from "./OrgConfigEditor";
import { createFakeBackend } from "../budgets/admission/testing/serverFake";

// An org whose daily admission caps are on in its stored config keeps them on
// while no gateway reports the spend ledger: Org Config > Budgets shows them
// on with the caps-on sentence, and saving any other setting never writes the
// switch.
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
const SENTENCE =
  "Daily caps are on. A key under a cap can make only non-streaming chat requests, and its requests over the day's cap are refused. Streaming and Gemini requests from capped keys are refused, and native message and response requests are refused for every key while daily caps are on.";
const STORED = [
  ["canonical", { admission_caps_enabled: true, policy_revision: 3 }],
  ["alias-only", { admissionCapsEnabled: true, policyRevision: 3 }],
];

let client;
let backend;
const saveButton = () =>
  screen.getByRole("button", { name: "Save & Activate" });
function openEditor(policy) {
  const config = {
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
  backend = createFakeBackend({ config, ledger: "absent" });
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
beforeEach(() => {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  get.mockReset();
  post.mockReset();
  context.canEdit = true;
});
afterEach(() => client.clear());

describe("stored daily admission caps while the switch can't be turned on", () => {
  it.each(STORED)(
    "%s on policy: shown on with the sentence; saving another setting never writes the switch",
    async (_name, policy) => {
      const onClose = openEditor(policy);
      await screen.findByTestId("admission-policy-current");
      expect(
        screen.getByText("Daily caps active in saved policy"),
      ).toBeInTheDocument();
      expect(screen.getAllByText(SENTENCE)).toHaveLength(1);
      expect(screen.getByTestId("admission-deactivate")).toBeEnabled();

      fireEvent.click(
        screen.getByRole("checkbox", { name: "Enable custom cost tracking" }),
      );
      fireEvent.click(saveButton());
      await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));

      expect(post).toHaveBeenCalledTimes(1);
      expect(post).toHaveBeenCalledWith(
        UPDATE_URL,
        { cost_tracking: { enabled: true } },
        expect.anything(),
      );
      // The stored switch is untouched: no admission write, still on.
      expect(backend.state.posts).toHaveLength(0);
      expect(
        backend.state.config.budgets.admission_caps_enabled ??
          backend.state.config.budgets.admissionCapsEnabled,
      ).toBe(true);
      expect(
        screen.getByText("Daily caps active in saved policy"),
      ).toBeInTheDocument();
    },
  );
});
