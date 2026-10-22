// When the gateway lists ip_acl in org_sections_not_enforced with a reason,
// the Security tab's IP ACL note gives that reason in the gateway's words and
// never claims the IP rules apply; with no reason it keeps the plugin
// sentence, and with ip_acl enforced it shows no note.
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { render, screen, waitFor } from "src/utils/test-utils";
import OrgConfigEditor from "./OrgConfigEditor";

// Gateway config writes are Owner or Admin only (useGatewayAdmin); these
// cases act as an Admin.
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));

const { post, get, gatewayConfig, stub } = vi.hoisted(() => ({
  post: vi.fn(),
  get: vi.fn(),
  gatewayConfig: { current: {} },
  stub: (label) => () => ({ default: () => `${label} body` }),
}));

vi.mock("src/utils/axios", () => ({
  default: { post, get },
  endpoints: {
    gateway: {
      config: (id) => `/agentcc/gateways/${id}/config/`,
      updateConfig: (id) => `/agentcc/gateways/${id}/update-config/`,
    },
  },
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "gw-1" }),
}));

vi.mock("./ProviderConfigTab", stub("Providers"));
vi.mock("./GuardrailConfigTab", stub("Guardrails"));
vi.mock("./RoutingConfigTab", stub("Routing"));
vi.mock("./RateLimitingConfigTab", stub("Rate limiting"));
vi.mock("./BudgetsConfigTab", stub("Budgets"));
vi.mock("./SecurityConfigTab", stub("Security"));
vi.mock("./AlertingConfigTab", stub("Alerting"));
vi.mock("./MCPConfigTab", stub("MCP"));
vi.mock("./ModelDatabaseConfigTab", stub("Model database"));
vi.mock("./ModelMapConfigTab", stub("Model map"));

const STORED = {
  version: 4,
  rate_limiting: { enabled: true, global_rpm: 60 },
  audit: { enabled: true, sinks: [{ type: "webhook", url: "https://siem" }] },
  a2a: { enabled: true, agents: { helper: { url: "https://agent" } } },
  cache: {
    enabled: true,
    default_ttl: 300,
    backend: "redis",
    max_entries: 5000,
    redis: { address: "redis:6379" },
    semantic: { enabled: true, threshold: 0.9 },
    edge: { enabled: true },
  },
};

let client;
function renderEditor(props = {}) {
  client = new QueryClient({
    mutationCache: new MutationCache({ onError: () => {} }),
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  const onClose = vi.fn();
  render(
    <QueryClientProvider client={client}>
      <OrgConfigEditor
        open
        onClose={onClose}
        initialConfig={STORED}
        {...props}
      />
    </QueryClientProvider>,
  );
  return { onClose };
}

const REASON = "client IP unknown (trusted_proxies not set)";

beforeEach(() => {
  post.mockReset();
  get.mockReset();
  post.mockResolvedValue({
    data: { status: true, result: { version: 5, gateway_synced: true } },
  });
  get.mockImplementation(async () => ({
    data: { status: true, result: gatewayConfig.current },
  }));
});

describe("trusted-proxy rule: the Security tab's IP ACL note", () => {
  it("states the gateway's reason when ip_acl is not enforced for want of a client IP", async () => {
    gatewayConfig.current = {
      org_sections_enforced: ["alerting", "budgets", "tool_policy"],
      org_sections_not_enforced: { ip_acl: REASON },
    };
    renderEditor({ defaultTab: 6 });
    const note = await screen.findByText(
      /IP ACL: Not enforced on this gateway/,
    );
    expect(note.textContent).toBe(
      "IP ACL: Not enforced on this gateway: client IP unknown (trusted_proxies not set). These settings are saved but not applied to requests.",
    );
    expect(screen.queryByText(/Tool Policy: Not enforced/)).toBeNull();
  });

  it("keeps the plugin sentence when the gateway gives no reason", async () => {
    gatewayConfig.current = { org_sections_enforced: ["tool_policy"] };
    renderEditor({ defaultTab: 6 });
    const note = await screen.findByText(
      /IP ACL: Not enforced on this gateway/,
    );
    expect(note.textContent).toContain("does not run the IP ACL plugin");
    expect(note.textContent).not.toContain(REASON);
  });

  it("shows no IP ACL note when the gateway enforces ip_acl", async () => {
    gatewayConfig.current = {
      org_sections_enforced: ["ip_acl", "tool_policy"],
    };
    renderEditor({ defaultTab: 6 });
    await waitFor(() =>
      expect(
        client.getQueryState(["agentcc-gateway-config", "gw-1"])?.status,
      ).toBe("success"),
    );
    expect(screen.queryByText(/IP ACL: Not enforced/)).toBeNull();
  });
});
