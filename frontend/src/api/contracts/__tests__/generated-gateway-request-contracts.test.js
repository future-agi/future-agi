import { describe, expect, it } from "vitest";

import { endpoints } from "src/utils/axios";

import { validateContractedRequestConfig } from "../openapi-contract";

const requests = [
  [
    "Set Budget",
    endpoints.gateway.setBudget("default"),
    {
      level: "org_limit",
      config: { limit: 250, alert_threshold: 80, on_exceed: "warn" },
    },
  ],
  [
    "Add Server",
    endpoints.gateway.updateMcpServer("default"),
    {
      server_id: "search",
      config: {
        url: "https://mcp.example.test/mcp",
        transport: "http",
        auth: { type: "none" },
        tools_cache_ttl: "5m",
      },
    },
  ],
  [
    "MCP guardrails",
    endpoints.gateway.updateMcpGuardrails("default"),
    {
      config: {
        enabled: true,
        blocked_tools: ["delete"],
        allowed_servers: ["search"],
        validate_inputs: false,
        tool_rate_limits: { search: 10 },
      },
    },
  ],
  [
    "MCP tool arguments",
    endpoints.gateway.testMcpTool("default"),
    {
      name: "search",
      arguments: { query: "hello", limit: 5, exact: false, tags: ["docs"] },
    },
  ],
  [
    "batch requests",
    endpoints.gateway.batch.submit("default"),
    {
      requests: [
        {
          model: "gpt-4o-mini",
          messages: [{ role: "user", content: "Hello" }],
          temperature: 0.5,
          stream: false,
        },
      ],
      max_concurrency: 5,
    },
  ],
];

// Section values from the gateway config editors, not object-only maps.
const patches = {
  guardrails: { fail_open: false, rules: [] },
  routing: { strategy: "weighted", weights: { primary: 100 } },
  cache: { enabled: true, ttl: "5m" },
  rate_limiting: { enabled: true },
  budgets: { enabled: true },
  cost_tracking: { enabled: true },
  ip_acl: { enabled: true, allow: ["192.0.2.0/24"] },
  alerting: { channels: [{ type: "webhook", url: "https://example.test" }] },
  privacy: { enabled: true },
  tool_policy: { enabled: true, allow: ["search"] },
  mcp: { enabled: true },
  a2a: { enabled: true },
  audit: { enabled: true, categories: ["request"] },
  model_map: { "gpt-4o-mini": "primary" },
  model_database: {
    overrides: { "gpt-4o-mini": { max_input_tokens: 128000 } },
  },
};

for (const [section, value] of Object.entries(patches)) {
  requests.push([
    `${section} config patch`,
    endpoints.gateway.updateConfig("default"),
    { [section]: value },
  ]);
}

describe("generated gateway request contracts", () => {
  it.each(requests)(
    "accepts %s without blocking the request",
    (_name, url, data) => {
      const result = validateContractedRequestConfig({
        url,
        method: "post",
        data,
      });

      // Include the original config.limit/config.url failure messages in red runs.
      expect(result.ok, result.error?.message).toBe(true);
      expect(result.skipped).not.toBe(true);
    },
  );

  it.each([
    [
      "budget config",
      endpoints.gateway.setBudget("default"),
      { level: "org_limit", config: 250 },
    ],
    [
      "server config",
      endpoints.gateway.updateMcpServer("default"),
      { server_id: "search", config: "https://example.test" },
    ],
    [
      "guardrails config",
      endpoints.gateway.updateMcpGuardrails("default"),
      { config: true },
    ],
    [
      "tool arguments",
      endpoints.gateway.testMcpTool("default"),
      { name: "search", arguments: ["hello"] },
    ],
    [
      "batch items",
      endpoints.gateway.batch.submit("default"),
      { requests: ["hello"] },
    ],
    [
      "config sections",
      endpoints.gateway.updateConfig("default"),
      { routing: "weighted" },
    ],
  ])("still requires objects for %s", (_name, url, data) => {
    expect(
      validateContractedRequestConfig({ url, method: "post", data }).ok,
    ).toBe(false);
  });

  it("keeps optional arguments and patch sections optional", () => {
    for (const [url, data] of [
      [endpoints.gateway.testMcpTool("default"), { name: "search" }],
      [endpoints.gateway.updateConfig("default"), {}],
    ]) {
      expect(
        validateContractedRequestConfig({ url, method: "post", data }).ok,
      ).toBe(true);
    }
  });
});
