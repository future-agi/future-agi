// Org Config shows only what works, and says when this gateway does not
// enforce a section.
//   No Audit tab (org audit sinks are not built); a stored audit section is
//   never deleted.
//   No A2A tab (the gateway applies no per-org A2A section); a stored a2a
//   section is never deleted.
//   Around the Budgets, Security (IP ACL, Tool Policy), Cache, Alerting and
//   Rate Limiting tab content, "Not enforced on this gateway" when the
//   gateway's org_sections_enforced lacks the section, "enforcement unknown"
//   when it does not report the field, and nothing while the config is
//   loading or when it is enforced.
//   The Cache tab keeps Enabled and Default TTL only (what the gateway
//   reads), disables both when the gateway reports caching off, and never
//   drops a stored backend, semantic or edge setting.
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { render, screen, waitFor } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
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

const NOT_ENFORCED = /Not enforced on this gateway/;
const UNKNOWN = /enforcement unknown/i;

beforeEach(() => {
  post.mockReset();
  get.mockReset();
  post.mockResolvedValue({
    data: { status: true, result: { version: 5, gateway_synced: true } },
  });
  gatewayConfig.current = { org_sections_enforced: [] };
  get.mockImplementation(async () => ({
    data: { status: true, result: gatewayConfig.current },
  }));
});

describe("Org Config tabs", () => {
  it("offers no Audit and no A2A tab, and lists the tabs in this order", () => {
    renderEditor();
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
      "Providers",
      "Guardrails",
      "Routing",
      "Cache",
      "Rate Limiting",
      "Budgets",
      "Security",
      "Alerting",
      "MCP",
      "Model Database",
      "Model Map",
    ]);
  });

  it.each([
    [2, "Routing body"],
    [3, "Enable Response Cache"],
    [4, "Rate limiting body"],
    [8, "MCP body"],
    [9, "Model database body"],
    [10, "Model map body"],
  ])("defaultTab %i opens %s", (defaultTab, text) => {
    renderEditor({ defaultTab });
    expect(screen.getByText(text)).toBeInTheDocument();
  });

  it("a save never sends, nulls or drops the stored audit and a2a sections", async () => {
    gatewayConfig.current = { org_sections_enforced: ["cache"] };
    const user = userEvent.setup();
    const { onClose } = renderEditor({ defaultTab: 3 });
    const ttl = await screen.findByLabelText(/Default TTL/);
    await waitFor(() => expect(ttl).toBeEnabled());
    await user.clear(ttl);
    await user.type(ttl, "1h");
    await user.click(screen.getByRole("button", { name: "Save & Activate" }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    const [url, body] = post.mock.calls[0];
    expect(url).toBe("/agentcc/gateways/gw-1/update-config/");
    expect(body).toEqual({ cache: { default_ttl: "1h" } });
    expect(body).not.toHaveProperty("audit");
    expect(body).not.toHaveProperty("a2a");
  });
});

describe("Enforcement notes", () => {
  const TABS = [
    [3, ["Cache"]],
    [4, ["Rate Limiting"]],
    [5, ["Budgets"]],
    [6, ["IP ACL", "Tool Policy"]],
    [7, ["Alerting"]],
  ];

  it.each(TABS)(
    "tab %i says 'Not enforced on this gateway' when the section is missing",
    async (defaultTab, labels) => {
      gatewayConfig.current = { org_sections_enforced: [] };
      renderEditor({ defaultTab });
      const notes = await screen.findAllByText(NOT_ENFORCED);
      expect(notes).toHaveLength(labels.length);
      labels.forEach((label, i) =>
        expect(notes[i].textContent).toContain(label),
      );
      expect(screen.queryByText(UNKNOWN)).toBeNull();
    },
  );

  it.each(TABS)(
    "tab %i says 'enforcement unknown' when the gateway does not report the field",
    async (defaultTab, labels) => {
      gatewayConfig.current = { gateway: { status: "unreachable" } };
      renderEditor({ defaultTab });
      const notes = await screen.findAllByText(UNKNOWN);
      expect(notes).toHaveLength(labels.length);
      expect(screen.queryByText(NOT_ENFORCED)).toBeNull();
    },
  );

  it.each(TABS)(
    "tab %i shows no note when every section is enforced",
    async (defaultTab) => {
      gatewayConfig.current = {
        org_sections_enforced: [
          "alerting",
          "budgets",
          "cache",
          "ip_acl",
          "rate_limiting",
          "tool_policy",
        ],
      };
      renderEditor({ defaultTab });
      await waitFor(() => expect(get).toHaveBeenCalled());
      await waitFor(() =>
        expect(
          client.getQueryState(["agentcc-gateway-config", "gw-1"])?.status,
        ).toBe("success"),
      );
      expect(screen.queryByText(NOT_ENFORCED)).toBeNull();
      expect(screen.queryByText(UNKNOWN)).toBeNull();
    },
  );

  it("Security names only the section that is missing", async () => {
    gatewayConfig.current = { org_sections_enforced: ["ip_acl"] };
    renderEditor({ defaultTab: 6 });
    const notes = await screen.findAllByText(NOT_ENFORCED);
    expect(notes).toHaveLength(1);
    expect(notes[0].textContent).toContain("Tool Policy");
    expect(notes[0].textContent).not.toContain("IP ACL");
  });

  it("a read that failed is 'enforcement unknown', never a claim either way", async () => {
    get.mockRejectedValue({ statusCode: 500, result: "boom" });
    renderEditor({ defaultTab: 5 });
    expect(await screen.findByText(UNKNOWN)).toBeInTheDocument();
    expect(screen.queryByText(NOT_ENFORCED)).toBeNull();
  });

  it("shows nothing while the gateway config is loading", () => {
    get.mockImplementation(() => new Promise(() => {}));
    renderEditor({ defaultTab: 5 });
    expect(screen.queryByText(NOT_ENFORCED)).toBeNull();
    expect(screen.queryByText(UNKNOWN)).toBeNull();
  });

  it.each([
    [0, "Providers body"],
    [1, "Guardrails body"],
    [2, "Routing body"],
    [8, "MCP body"],
    [9, "Model database body"],
  ])(
    "tab %i (a section the gateway does not report on) carries no note",
    async (defaultTab, text) => {
      gatewayConfig.current = { org_sections_enforced: [] };
      renderEditor({ defaultTab });
      expect(screen.getByText(text)).toBeInTheDocument();
      await waitFor(() => expect(get).toHaveBeenCalled());
      expect(screen.queryByText(NOT_ENFORCED)).toBeNull();
      expect(screen.queryByText(UNKNOWN)).toBeNull();
    },
  );
});

describe("The Cache tab", () => {
  it("keeps Enabled and Default TTL and nothing the gateway does not read", async () => {
    gatewayConfig.current = { org_sections_enforced: ["cache"] };
    renderEditor({ defaultTab: 3 });
    expect(
      await screen.findByRole("checkbox", { name: "Enable Response Cache" }),
    ).toBeChecked();
    expect(screen.getByLabelText(/Default TTL/)).toHaveValue("300");
    for (const gone of [
      /Max Entries/,
      /L1 Backend/,
      /Backend Type/,
      /Semantic/,
      /Edge Caching/,
      /Directory/,
      /Path on the gateway server/,
      /Redis/,
    ]) {
      expect(screen.queryByText(gone)).toBeNull();
      expect(screen.queryByLabelText(gone)).toBeNull();
    }
  });

  it("says caching is off on this gateway and disables both controls", async () => {
    gatewayConfig.current = { org_sections_enforced: ["budgets"] };
    renderEditor({ defaultTab: 3 });
    expect(
      await screen.findByText(/response caching is off on this gateway/i),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(
        screen.getByRole("checkbox", { name: "Enable Response Cache" }),
      ).toBeDisabled(),
    );
    expect(screen.getByLabelText(/Default TTL/)).toBeDisabled();
  });

  it("keeps both controls editable when enforcement is unknown", async () => {
    gatewayConfig.current = {};
    renderEditor({ defaultTab: 3 });
    expect(await screen.findByText(UNKNOWN)).toBeInTheDocument();
    expect(
      screen.getByRole("checkbox", { name: "Enable Response Cache" }),
    ).toBeEnabled();
    expect(screen.getByLabelText(/Default TTL/)).toBeEnabled();
  });

  it("turning the cache off sends only enabled; stored backend settings stay", async () => {
    gatewayConfig.current = { org_sections_enforced: ["cache"] };
    const user = userEvent.setup();
    const { onClose } = renderEditor({ defaultTab: 3 });
    await user.click(
      await screen.findByRole("checkbox", { name: "Enable Response Cache" }),
    );
    await user.click(screen.getByRole("button", { name: "Save & Activate" }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(post.mock.calls[0][1]).toEqual({ cache: { enabled: false } });
  });
});
