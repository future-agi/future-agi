// The provider, routing and reload callers of
// useGatewayConfig's writes on a save the gateway did not apply
// (gateway_synced:false). Each shows exactly one toast, the warning, never
// its success toast and never its generic "Failed to ..." toast.
//
//   providers/AddProviderDialog.jsx          add / edit provider (useUpdateProvider)
//   providers/ProviderConfigView.jsx         remove provider (useRemoveProvider)
//   providers/RoutingConfigView.jsx          save routing (useUpdateConfig)
//   providers/ProviderManagementSection.jsx  Reload Config (useReloadConfig)
//   settings/GatewaySettingsSection.jsx      Reload Config (useReloadConfig)
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { render, screen, waitFor } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

import AddProviderDialog from "../AddProviderDialog";
import ProviderConfigView from "../ProviderConfigView";
import RoutingConfigView from "../RoutingConfigView";
import ProviderManagementSection from "../ProviderManagementSection";
import GatewaySettingsSection from "../../settings/GatewaySettingsSection";

const WARNING =
  "Config saved but gateway sync failed. Changes will apply on next gateway restart.";
const SENTENCE = `Saved, but the gateway did not apply it: ${WARNING}`;

const { post, get, snackbars, route } = vi.hoisted(() => ({
  post: vi.fn(),
  get: vi.fn(),
  snackbars: [],
  route: { tab: undefined },
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: (message, options) => snackbars.push({ message, options }),
}));

// The write controls are Owner or Admin only (useGatewayAdmin); these cases
// act as an Admin. AdminGating.test.jsx covers the other levels.
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));

vi.mock("src/utils/axios", () => {
  const p = (name) => (id) => `/agentcc/gateways/${id}/${name}/`;
  return {
    default: { post, get },
    endpoints: {
      gateway: {
        config: p("config"),
        providers: p("providers"),
        reload: p("reload"),
        healthCheck: p("health_check"),
        updateProvider: p("update-provider"),
        removeProvider: p("remove-provider"),
        updateConfig: p("update-config"),
        providerCredentials: { fetchModels: "/agentcc/fetch-models/" },
        orgConfig: {
          list: "/agentcc/org-configs/",
          active: "/agentcc/org-configs/active/",
        },
      },
    },
  };
});

vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal()),
  useParams: () => ({ tab: route.tab }),
  useNavigate: () => vi.fn(),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("../../context/useGatewayContext", () => ({
  useGatewayContext: () => ({
    gatewayId: "gw-1",
    isLoading: false,
    gateway: { id: "gw-1", name: "Gateway", status: "healthy" },
  }),
}));
vi.mock("./useOrgConfig", () => ({
  useOrgConfig: () => ({ data: null, isLoading: false }),
  useCreateOrgConfig: () => ({ mutate: vi.fn(), isPending: false }),
}));

// Only the header buttons of the two pages are under test.
vi.mock("../ProviderHealthTable", () => ({ default: () => null }));
vi.mock("../CacheStatusView", () => ({ default: () => null }));
vi.mock("../../settings/OrgConfigSection", () => ({ default: () => null }));
vi.mock("../../settings/EmailAlertsCard", () => ({ default: () => null }));
vi.mock("../../settings/hooks/useBatchJobs", () => ({
  useSubmitBatch: () => ({ mutate: vi.fn(), isPending: false }),
  useBatchStatus: () => ({ data: [], isLoading: false }),
  useCancelBatch: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("../../settings/OrgConfigEditor", () => ({ default: () => null }));

const CONFIG = {
  providers: { openai: { models: ["gpt-4o"], api_format: "openai" } },
  routing: { strategy: "round_robin" },
};

function renderWithClient(ui) {
  const client = new QueryClient({
    mutationCache: new MutationCache({ onError: () => {} }),
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
  );
}

const expectOnlyTheWarning = () =>
  expect(snackbars).toEqual([
    { message: SENTENCE, options: { variant: "warning" } },
  ]);

beforeEach(() => {
  post.mockReset();
  get.mockReset();
  snackbars.length = 0;
  route.tab = undefined;
  get.mockImplementation(async (url) =>
    url.endsWith("/config/")
      ? { data: { status: true, result: CONFIG } }
      : { data: { status: true, result: null } },
  );
  post.mockImplementation(async (url) =>
    url === "/agentcc/fetch-models/"
      ? { data: { status: true, result: { models: ["gpt-4o"] } } }
      : {
          data: {
            status: true,
            result: { gateway_synced: false, gateway_warning: WARNING },
          },
        },
  );
});

describe("provider, routing and reload callers on gateway_synced:false", () => {
  it("Edit provider: the warning and the dialog's Alert, no 'updated' toast", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    renderWithClient(
      <AddProviderDialog
        open
        onClose={onClose}
        gatewayId="gw-1"
        provider={{
          name: "openai",
          config: { models: ["gpt-4o"], default_timeout: 30 },
        }}
      />,
    );
    const save = await screen.findByRole("button", { name: "Save Changes" });
    await waitFor(() => expect(save).toBeEnabled());
    await user.click(save);

    await waitFor(() => expect(snackbars).toHaveLength(1));
    expectOnlyTheWarning();
    expect(
      post.mock.calls.some(
        ([u]) => u === "/agentcc/gateways/gw-1/update-provider/",
      ),
    ).toBe(true);
    expect(await screen.findByText(SENTENCE)).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("Remove provider: the warning, no 'removed' and no 'Failed to remove' toast", async () => {
    const user = userEvent.setup();
    renderWithClient(<ProviderConfigView config={CONFIG} gatewayId="gw-1" />);
    await user.click(screen.getByTitle("Remove provider"));
    await user.type(
      await screen.findByLabelText('Type "openai" to confirm'),
      "openai",
    );
    await user.click(screen.getByRole("button", { name: "Remove" }));

    await waitFor(() => expect(snackbars).toHaveLength(1));
    expectOnlyTheWarning();
    expect(post.mock.calls[0][0]).toBe(
      "/agentcc/gateways/gw-1/remove-provider/",
    );
  });

  it("Routing config save: the warning, no 'Routing config updated' toast", async () => {
    const user = userEvent.setup();
    renderWithClient(<RoutingConfigView config={CONFIG} gatewayId="gw-1" />);
    await user.click(screen.getByLabelText("Default Strategy"));
    await user.click(screen.getByRole("option", { name: "Least Latency" }));
    await user.click(
      screen.getByRole("button", { name: "Save Routing Config" }),
    );

    await waitFor(() => expect(snackbars).toHaveLength(1));
    expectOnlyTheWarning();
    expect(post.mock.calls[0][0]).toBe("/agentcc/gateways/gw-1/update-config/");
  });

  it("Providers page Reload Config: the warning, no 'Configuration reloaded' toast", async () => {
    const user = userEvent.setup();
    renderWithClient(<ProviderManagementSection />);
    await user.click(
      await screen.findByRole("button", { name: "Reload Config" }),
    );

    await waitFor(() => expect(snackbars).toHaveLength(1));
    expectOnlyTheWarning();
    expect(post.mock.calls[0][0]).toBe("/agentcc/gateways/gw-1/reload/");
  });

  it("Settings page Reload Config: the warning, no 'Configuration reloaded' toast", async () => {
    route.tab = "settings";
    const user = userEvent.setup();
    renderWithClient(<GatewaySettingsSection />);
    await user.click(
      await screen.findByRole("button", { name: "Reload Config" }),
    );

    await waitFor(() => expect(snackbars).toHaveLength(1));
    expectOnlyTheWarning();
    expect(post.mock.calls[0][0]).toBe("/agentcc/gateways/gw-1/reload/");
  });
});
