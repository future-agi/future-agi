import React from "react";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

// Renders an MCP view the way the console does: FA theme, a fresh query
// client with no retries, a router and Helmet.
export function renderMcp(ui, { mode = "light", route = "/" } = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const tree = (content) => (
    <HelmetProvider>
      <QueryClientProvider client={client}>
        <ThemeProvider
          theme={createTheme({ palette: palette(mode), typography })}
        >
          <MemoryRouter initialEntries={[route]}>{content}</MemoryRouter>
        </ThemeProvider>
      </QueryClientProvider>
    </HelmetProvider>
  );
  const result = render(tree(ui));
  return {
    ...result,
    client,
    rerender: (content) => result.rerender(tree(content)),
  };
}

// The demo org's MCP section, as /agentcc/gateways/<id>/config/ returns it.
export const DEMO_MCP = {
  access: { default: "allow" },
  enabled: true,
  servers: {
    "crm-tools": {
      url: "http://demo-upstream:8080/mcp-crm",
      enabled: true,
      transport: "http",
      timeout_ms: 10000,
      tools_cache_ttl: "5m",
    },
    "support-tools": {
      url: "http://demo-upstream:8080/mcp",
      enabled: true,
      transport: "http",
      timeout_ms: 10000,
      tools_cache_ttl: "5m",
    },
  },
  guardrails: {
    enabled: true,
    blocked_tools: ["support-tools__delete_customer_data"],
    validate_inputs: true,
    tool_rate_limits: { "support-tools__create_ticket": 30 },
    validate_outputs: false,
  },
  virtual_servers: {
    "support-desk": {
      id: "vs_01M5CA9CSQ7WV0A7Z7W8T71E58",
      mode: "direct",
      title: "Support desk",
      naming: "prefixed",
      members: ["support-tools", "crm-tools"],
      approval: {
        tools: ["support-tools__create_ticket"],
        require: "listed",
        validity: "once",
      },
      description: "Tools the support bot may call",
    },
  },
};

export function demoToolCall(overrides = {}) {
  return {
    id: 46,
    tool_call_id: "tc_01M5CATN89F208ECV2SCS5VMTV",
    request_id: "mcp_01M5CATN86KSEYB76RFBYRJYWX",
    started_at: "2026-10-20T09:38:36.937293Z",
    api_key_id: "key_5bedce7c58ba2ea7428d44be",
    principal_kind: "key",
    virtual_server_id: "vs_01M5CA9CSQ7WV0A7Z7W8T71E58",
    server_id: "support-tools",
    tool: "support-tools__delete_customer_data",
    upstream_tool: "delete_customer_data",
    protocol_version: "2025-06-18",
    agent_step: "tool",
    agent_run_id: "",
    outcome: "success",
    is_error: false,
    error_class: "",
    error_origin: "",
    error_code: "",
    error_message: "",
    guardrail: { masked: [], shadow: [], blocked: [], checked: [] },
    latency_ms: 103,
    upstream_latency_ms: 102,
    gateway_overhead_ms: 1,
    bytes_in: 200,
    bytes_out: 81,
    billing_mode: "byok",
    credential_source: "none",
    upstream_cost_micros: 0,
    billed_cost_micros: 0,
    markup_micros: 0,
    content_mode: "full",
    ...overrides,
  };
}
