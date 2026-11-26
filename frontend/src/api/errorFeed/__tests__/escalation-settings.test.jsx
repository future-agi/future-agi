import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import axios from "src/utils/axios";
import {
  useErrorFeedEscalationSettings,
  useUpdateErrorFeedEscalationSettings,
} from "../error-feed";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), patch: vi.fn() },
  endpoints: {
    errorFeed: { escalationSettings: "/tracer/feed/escalation-settings/" },
  },
}));
const response = (workspace, minimum) => ({
  data: {
    status: true,
    result: {
      workspace_id: workspace,
      revision: 1,
      values: { min_findings: minimum },
    },
  },
});
const scope = { organizationId: "org-a", workspaceId: "workspace-a" };

function setup() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const wrapper = ({ children }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { client, wrapper };
}

beforeEach(() => vi.clearAllMocks());

describe("workspace escalation settings API", () => {
  it("pins organization/workspace headers, consumes cancellation and reads the real envelope", async () => {
    axios.get.mockResolvedValue(response("workspace-a", 25));
    const { wrapper } = setup();
    const { result } = renderHook(() => useErrorFeedEscalationSettings(scope), {
      wrapper,
    });
    await waitFor(() =>
      expect(result.current.data?.values.min_findings).toBe(25),
    );
    expect(axios.get).toHaveBeenCalledWith(
      "/tracer/feed/escalation-settings/",
      {
        headers: {
          "X-Organization-Id": "org-a",
          "X-Workspace-Id": "workspace-a",
        },
        signal: expect.any(AbortSignal),
      },
    );
  });

  it("does not fetch before both scope IDs are available", () => {
    const { wrapper } = setup();
    renderHook(
      () => useErrorFeedEscalationSettings({ organizationId: "org-a" }),
      { wrapper },
    );
    expect(axios.get).not.toHaveBeenCalled();
  });

  it("never shows a delayed workspace A result under workspace B", async () => {
    let resolveA;
    axios.get.mockImplementation((_path, config) =>
      config.headers["X-Workspace-Id"] === "workspace-a"
        ? new Promise((resolve) => {
            resolveA = resolve;
          })
        : Promise.resolve(response("workspace-b", 40)),
    );
    const { wrapper } = setup();
    const { result, rerender } = renderHook(
      (props) => useErrorFeedEscalationSettings(props),
      { wrapper, initialProps: scope },
    );
    rerender({ ...scope, workspaceId: "workspace-b" });
    await waitFor(() =>
      expect(result.current.data?.values.min_findings).toBe(40),
    );
    await act(async () => resolveA(response("workspace-a", 25)));
    expect(result.current.data.workspace_id).toBe("workspace-b");
    expect(result.current.data.values.min_findings).toBe(40);
  });

  it("saves captured scope and revision without adding a project or workspace body field", async () => {
    axios.patch.mockResolvedValue(response("workspace-a", 25));
    const { wrapper } = setup();
    const { result } = renderHook(
      () => useUpdateErrorFeedEscalationSettings(),
      { wrapper },
    );
    await act(async () =>
      result.current.mutateAsync({
        ...scope,
        revision: 0,
        values: { min_findings: 25 },
      }),
    );
    expect(axios.patch).toHaveBeenCalledWith(
      "/tracer/feed/escalation-settings/",
      { revision: 0, min_findings: 25 },
      {
        headers: {
          "X-Organization-Id": "org-a",
          "X-Workspace-Id": "workspace-a",
        },
      },
    );
  });
});
