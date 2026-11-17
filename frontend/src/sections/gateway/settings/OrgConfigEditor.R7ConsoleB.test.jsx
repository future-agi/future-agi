// MAIN 917 r2: Cache stays editable and saved while caching is off.
// A2A/Audit remain read-only; MCP settings have moved to the MCP page.
// Existing enforcement notes for other editable sections stay unchanged.
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import OrgConfigEditor from "./OrgConfigEditor";
import CacheStatusView from "../providers/CacheStatusView";

vi.mock("../providers/hooks/useOrgConfig", () => ({
  useOrgConfig: () => ({
    data: { ...STORED, cache: { ...STORED.cache, enabled: false } },
  }),
  useCreateOrgConfig: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("../analytics/hooks/useAnalyticsOverview", () => ({
  useAnalyticsOverview: () => ({ data: undefined }),
}));

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
vi.mock("./RateLimitingConfigTab", () => ({
  // eslint-disable-next-line react/prop-types
  default: ({ rateLimiting, onChange }) => (
    <button
      type="button"
      onClick={() => onChange({ ...rateLimiting, global_rpm: 120 })}
    >
      Rate limiting body
    </button>
  ),
}));
vi.mock("./BudgetsConfigTab", stub("Budgets"));
vi.mock("./SecurityConfigTab", stub("Security"));
vi.mock("./AlertingConfigTab", stub("Alerting"));
vi.mock("./ModelDatabaseConfigTab", stub("Model database"));
vi.mock("./ModelMapConfigTab", stub("Model map"));

const STORED = {
  version: 4,
  rate_limiting: { enabled: true, global_rpm: 60 },
  audit: { enabled: true, sinks: [{ type: "webhook", url: "https://siem" }] },
  a2a: { enabled: true, agents: { helper: { url: "https://agent" } } },
  mcp: { enabled: true, servers: { search: { url: "https://search" } } },
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
  const rendered = render(
    <QueryClientProvider client={client}>
      <OrgConfigEditor
        open
        onClose={onClose}
        initialConfig={STORED}
        {...props}
      />
    </QueryClientProvider>,
  );
  return { ...rendered, onClose };
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

describe("NG-16 / NG-17: tabs", () => {
  it("retains read-only A2A and Audit, no MCP tab; indices 0-9 are unchanged", () => {
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
      "Model Database",
      "Model Map",
      "A2A",
      "Audit",
    ]);
  });

  it.each([
    [2, "Routing body"],
    [3, "Enable Response Cache"],
    [4, "Rate limiting body"],
    [8, "Model database body"],
    [9, "Model map body"],
    ["routing", "Routing body"],
    ["cache", "Enable Response Cache"],
    ["model_map", "Model map body"],
  ])("defaultTab %s opens %s", (defaultTab, text) => {
    renderEditor({ defaultTab });
    expect(screen.getByText(text)).toBeInTheDocument();
  });

  it("defaultTab mcp, which has no tab, opens Providers", () => {
    renderEditor({ defaultTab: "mcp" });
    expect(screen.getByText("Providers body")).toBeInTheDocument();
    expect(screen.queryByText("MCP body")).toBeNull();
  });

  it("a save never sends, nulls or drops stored cache, audit, a2a and mcp sections", async () => {
    const snapshot = JSON.stringify(STORED);
    gatewayConfig.current = { org_sections_enforced: ["cache"] };
    const user = userEvent.setup();
    const { onClose } = renderEditor({ defaultTab: 4 });
    await user.click(
      screen.getByRole("button", { name: "Rate limiting body" }),
    );
    await user.click(screen.getByRole("button", { name: "Save & Activate" }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    const [url, body] = post.mock.calls[0];
    expect(url).toBe("/agentcc/gateways/gw-1/update-config/");
    expect(body).toEqual({ rate_limiting: { global_rpm: 120 } });
    for (const section of ["cache", "audit", "a2a", "mcp"])
      expect(body).not.toHaveProperty(section);
    expect(JSON.stringify(STORED)).toBe(snapshot);
  });

  it("points once to MCP's separate editor without adding another draft section", () => {
    renderEditor();
    expect(
      screen.getAllByText("MCP settings are on the MCP page."),
    ).toHaveLength(1);
  });
});

describe("NG-03 (K2): enforcement notes", () => {
  const TABS = [
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
    [8, "Model database body"],
    [9, "Model map body"],
  ])("tab %i (no K2 section) carries no note", async (defaultTab, text) => {
    gatewayConfig.current = { org_sections_enforced: [] };
    renderEditor({ defaultTab });
    expect(screen.getByText(text)).toBeInTheDocument();
    await waitFor(() => expect(get).toHaveBeenCalled());
    expect(screen.queryByText(NOT_ENFORCED)).toBeNull();
    expect(screen.queryByText(UNKNOWN)).toBeNull();
  });
});

describe("NG-15: the Cache tab", () => {
  it.each(["Enable cache", "Configure cache"])(
    "%s opens the real editor and lets an admin opt into active caching",
    async (action) => {
      gatewayConfig.current = { org_sections_enforced: ["cache"] };
      const user = userEvent.setup();
      const queryClient = new QueryClient({
        defaultOptions: { queries: { retry: false } },
      });
      render(
        <QueryClientProvider client={queryClient}>
          <CacheStatusView config={gatewayConfig.current} gatewayId="gw-1" />
        </QueryClientProvider>,
      );
      await user.click(screen.getByRole("button", { name: action }));
      const enabled = await screen.findByRole("checkbox", {
        name: "Enable Response Cache",
      });
      expect(enabled).toBeEnabled();
      expect(enabled).not.toBeChecked();
      await user.click(enabled);
      await user.click(screen.getByRole("button", { name: "Save & Activate" }));
      await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
      expect(post.mock.calls[0][1]).toEqual({ cache: { enabled: true } });
    },
  );
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

  it.each([
    { org_sections_enforced: ["budgets"] },
    { cache: { enabled: false } },
    {
      org_sections_enforced: ["cache"],
      org_sections_not_enforced: { cache: "not applied" },
    },
  ])(
    "917 r2: edits and saves Cache while off for report %j",
    async (config) => {
      gatewayConfig.current = config;
      const { onClose } = renderEditor({ defaultTab: "cache" });
      await waitFor(() =>
        expect(
          client.getQueryState(["agentcc-gateway-config", "gw-1"])?.status,
        ).toBe("success"),
      );
      expect(screen.getByRole("tab", { name: "Cache" })).toHaveAttribute(
        "aria-selected",
        "true",
      );
      expect(
        screen.getByRole("checkbox", { name: "Enable Response Cache" }),
      ).toBeEnabled();
      expect(screen.getByLabelText(/Default TTL/)).toBeEnabled();
      expect(screen.getByLabelText(/Default TTL/)).toHaveValue("300");
      expect(
        screen.getAllByText(
          "Response caching is off on this gateway. Changes you save apply when response caching is turned on.",
        ),
      ).toHaveLength(1);
      fireEvent.change(screen.getByLabelText(/Default TTL/), {
        target: { value: "1h" },
      });
      fireEvent.click(screen.getByRole("button", { name: "Save & Activate" }));
      await waitFor(() => expect(onClose).toHaveBeenCalled());
      expect(post).toHaveBeenCalledTimes(1);
      expect(post.mock.calls[0][1]).toEqual({ cache: { default_ttl: "1h" } });
    },
  );

  it.each([{}, { org_sections_enforced: ["cache"] }])(
    "keeps both controls editable when enforcement is unknown or enforced: %j",
    async (config) => {
      gatewayConfig.current = config;
      renderEditor({ defaultTab: "cache" });
      await waitFor(() =>
        expect(
          client.getQueryState(["agentcc-gateway-config", "gw-1"])?.status,
        ).toBe("success"),
      );
      expect(
        screen.getByRole("checkbox", { name: "Enable Response Cache" }),
      ).toBeEnabled();
      expect(screen.getByLabelText(/Default TTL/)).toBeEnabled();
      expect(
        screen.queryByText(/Changes you save apply when response caching/),
      ).toBeNull();
      if (!config.org_sections_enforced) {
        expect(screen.getByText(UNKNOWN)).toBeInTheDocument();
      }
    },
  );

  it("turning the cache off sends only enabled; stored backend settings stay", async () => {
    gatewayConfig.current = { org_sections_enforced: ["cache"] };
    const snapshot = JSON.stringify(STORED);
    const user = userEvent.setup();
    const { onClose } = renderEditor({ defaultTab: "cache" });
    await user.click(
      screen.getByRole("checkbox", { name: "Enable Response Cache" }),
    );
    await user.click(screen.getByRole("button", { name: "Save & Activate" }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(post.mock.calls[0][1]).toEqual({ cache: { enabled: false } });
    expect(JSON.stringify(STORED)).toBe(snapshot);
  });

  it("saves an off-state opt-in and TTL, then reads those saved values when the gateway reports caching on", async () => {
    // Frontend persistence/on-state integration. Runtime cache hits/TTL require
    // the separate backend proof; this fixture does not simulate a cache engine.
    gatewayConfig.current = { cache: { enabled: false } };
    const initialConfig = {
      ...STORED,
      cache: { ...STORED.cache, enabled: false },
    };
    const snapshot = JSON.stringify(initialConfig);
    const first = renderEditor({ defaultTab: "cache", initialConfig });
    await screen.findByText(/Changes you save apply when response caching/);
    fireEvent.click(
      screen.getByRole("checkbox", { name: "Enable Response Cache" }),
    );
    fireEvent.change(screen.getByLabelText(/Default TTL/), {
      target: { value: "1h" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save & Activate" }));
    await waitFor(() => expect(first.onClose).toHaveBeenCalled());
    expect(post).toHaveBeenCalledTimes(1);
    const patch = post.mock.calls[0][1];
    expect(patch).toEqual({ cache: { enabled: true, default_ttl: "1h" } });
    expect(JSON.stringify(initialConfig)).toBe(snapshot);
    first.unmount();

    gatewayConfig.current = { org_sections_enforced: ["cache"] };
    const savedConfig = {
      ...initialConfig,
      cache: { ...initialConfig.cache, ...patch.cache },
    };
    const second = renderEditor({
      defaultTab: "cache",
      initialConfig: savedConfig,
    });
    await waitFor(() =>
      expect(
        client.getQueryState(["agentcc-gateway-config", "gw-1"])?.status,
      ).toBe("success"),
    );
    expect(
      screen.getByRole("checkbox", { name: "Enable Response Cache" }),
    ).toBeChecked();
    expect(screen.getByLabelText(/Default TTL/)).toHaveValue("1h");
    expect(screen.getByLabelText(/Default TTL/)).toBeEnabled();
    expect(
      screen.queryByText(/Changes you save apply when response caching/),
    ).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Save & Activate" }));
    await waitFor(() => expect(second.onClose).toHaveBeenCalled());
    expect(post).toHaveBeenCalledTimes(1);
    expect(savedConfig.cache.semantic).toEqual(STORED.cache.semantic);
  });

  it("keeps an unsaved edit and sends it if the gateway report flips off", async () => {
    gatewayConfig.current = { org_sections_enforced: ["cache"] };
    const { onClose } = renderEditor({ defaultTab: "cache" });
    await waitFor(() =>
      expect(
        client.getQueryState(["agentcc-gateway-config", "gw-1"])?.status,
      ).toBe("success"),
    );
    fireEvent.change(screen.getByLabelText(/Default TTL/), {
      target: { value: "1h" },
    });
    act(() =>
      client.setQueryData(["agentcc-gateway-config", "gw-1"], {
        cache: { enabled: false },
      }),
    );
    await screen.findByText(/Changes you save apply when response caching/);
    expect(screen.getByLabelText(/Default TTL/)).toBeEnabled();
    expect(screen.getByLabelText(/Default TTL/)).toHaveValue("1h");
    fireEvent.click(screen.getByRole("button", { name: "Save & Activate" }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(post.mock.calls[0][1]).toEqual({ cache: { default_ttl: "1h" } });
  });

  it("can create Cache settings while off with an empty stored section", async () => {
    const { onClose } = renderEditor({
      defaultTab: "cache",
      initialConfig: {},
    });
    await screen.findByText(/Changes you save apply when response caching/);
    const enabled = screen.getByRole("checkbox", {
      name: "Enable Response Cache",
    });
    expect(enabled).not.toBeChecked();
    expect(enabled).toBeEnabled();
    fireEvent.click(enabled);
    fireEvent.click(screen.getByRole("button", { name: "Save & Activate" }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(post.mock.calls[0][1]).toEqual({ cache: { enabled: true } });
  });
});
