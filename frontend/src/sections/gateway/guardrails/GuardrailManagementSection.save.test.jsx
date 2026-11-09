import React from "react";
import PropTypes from "prop-types";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, renderHook } from "@testing-library/react";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";

import { catalogEntry } from "./catalog";
import { useRuleActions } from "./hooks/useRuleActions";

// Saving creates a whole new active org-config version only through POST
// org-configs/, and a section left out of that payload is reset to its
// default. The Guardrails page never saves that way: it switches and edits a
// check with toggle-guardrail and update-guardrail, and removes a check or
// sets the timeout with update-config, which deep-merges only the sections a
// patch sends. So no guardrails save can reset budgets, rate limits, the IP
// ACL, alerting, privacy, tool policy, MCP, A2A, audit or the model sections.

const { post } = vi.hoisted(() => ({ post: vi.fn() }));

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { ...actual.default, post } };
});
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

const SAVED_GUARDRAILS = {
  checks: {
    "pii-detection": {
      enabled: true,
      action: "block",
      confidence_threshold: 0.5,
    },
  },
};

function wrapper({ children }) {
  const client = new QueryClient({
    mutationCache: new MutationCache({ onError: () => {} }),
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
wrapper.propTypes = { children: PropTypes.node };

describe("GuardrailManagementSection Save & Activate", () => {
  beforeEach(() => {
    post.mockReset();
    post.mockResolvedValue({
      data: { result: { version: 8, gateway_synced: true } },
    });
  });

  it("keeps every other org-config section when saving guardrails", async () => {
    const { result } = renderHook(
      () =>
        useRuleActions({ gatewayId: "gateway-1", saved: SAVED_GUARDRAILS }),
      { wrapper },
    );
    const row = {
      id: "pii-detection",
      ruleName: "pii-detection",
      label: "PII Detection",
    };

    await act(() =>
      result.current.saveRule({
        catalogName: "pii-detection",
        meta: catalogEntry("pii-detection"),
        check: SAVED_GUARDRAILS.checks["pii-detection"],
        form: { enabled: true, action: "block", threshold: 0.8, fields: {} },
      }),
    );
    await act(() => result.current.toggleRule(row, false));
    await act(() => result.current.saveTimeout(3000));
    await act(() => result.current.removeRule(row));

    const calls = post.mock.calls.map(([url, body]) => [url, body]);
    expect(calls.map(([url]) => url)).toEqual([
      "/agentcc/gateways/gateway-1/update-guardrail/",
      "/agentcc/gateways/gateway-1/toggle-guardrail/",
      "/agentcc/gateways/gateway-1/update-config/",
      "/agentcc/gateways/gateway-1/update-config/",
    ]);
    // update-guardrail and toggle-guardrail name one check and change it.
    expect(calls[0][1]).toEqual({
      name: "pii-detection",
      config: { confidence_threshold: 0.8 },
    });
    expect(calls[1][1]).toEqual({ name: "pii-detection", enabled: false });
    // update-config gets a guardrails-only patch; every other section stays
    // as stored.
    for (const [, body] of calls.slice(2)) {
      expect(Object.keys(body)).toEqual(["guardrails"]);
    }
    expect(calls[2][1]).toEqual({ guardrails: { timeout_ms: 3000 } });
  });
});
