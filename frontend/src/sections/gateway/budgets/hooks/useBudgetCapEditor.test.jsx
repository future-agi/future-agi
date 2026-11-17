import React from "react";
import PropTypes from "prop-types";
import { describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import axios from "src/utils/axios";
import { useBudgetCapEditor } from "./useBudgetCapEditor";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn() },
  endpoints: {
    gateway: {
      config: (id) => `/agentcc/gateways/${id}/config/`,
      updateConfig: (id) => `/agentcc/gateways/${id}/update-config/`,
    },
  },
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

function wrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  function Provider({ children }) {
    return (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
  }
  Provider.propTypes = { children: PropTypes.node };
  return Provider;
}

const link = { level: "org", id: "current", mode: "config" };

describe("budget editor requests", () => {
  it("keys reads by organization even when gateway ID is shared", async () => {
    axios.get.mockImplementation(async (_url, options) => ({
      data: { result: { org: options.headers["X-Organization-Id"] } },
    }));
    const { result, rerender } = renderHook(
      ({ orgId }) => useBudgetCapEditor({ link, orgId, gatewayId: "default" }),
      { wrapper: wrapper(), initialProps: { orgId: "org-a" } },
    );
    await waitFor(() => expect(result.current.query.data?.org).toBe("org-a"));
    rerender({ orgId: "org-b" });
    await waitFor(() => expect(result.current.query.data?.org).toBe("org-b"));
  });
  it("does not turn a saved-but-unsynced response into success", async () => {
    axios.get.mockResolvedValue({ data: { result: { budgets: {} } } });
    axios.post.mockResolvedValue({
      data: {
        result: { gateway_synced: false, gateway_warning: "not applied" },
      },
    });
    const { result } = renderHook(
      () => useBudgetCapEditor({ link, orgId: "org-a", gatewayId: "default" }),
      { wrapper: wrapper() },
    );
    const success = vi.fn();
    act(() =>
      result.current.mutation.mutate({ budgets: {} }, { onSuccess: success }),
    );
    await waitFor(() => expect(result.current.mutation.isError).toBe(true));
    expect(success).not.toHaveBeenCalled();
    expect(result.current.mutation.error.savedButNotApplied).toBe(true);
  });
  it("puts the scoped-key policy at its existing workspace endpoint with explicit org", async () => {
    const id = "11111111-1111-4111-8111-111111111111";
    const policy = {
      workspace: id,
      budget_usd: "50.00",
      budget_period: "monthly",
    };
    axios.get.mockResolvedValue({ data: { result: policy } });
    axios.put.mockResolvedValue({ data: { result: policy } });
    const { result } = renderHook(
      () =>
        useBudgetCapEditor({
          link: { level: "agent_key", id, mode: "workspace" },
          orgId: "org-a",
          gatewayId: null,
        }),
      { wrapper: wrapper() },
    );
    const body = { budget_usd: "60.00", budget_period: "monthly" };
    act(() => result.current.mutation.mutate(body));
    await waitFor(() => expect(result.current.mutation.isSuccess).toBe(true));
    expect(axios.put).toHaveBeenCalledWith(
      `/agentcc/mcp/service/workspaces/${id}/agent-key-policy/`,
      body,
      expect.objectContaining({ headers: { "X-Organization-Id": "org-a" } }),
    );
  });
});
