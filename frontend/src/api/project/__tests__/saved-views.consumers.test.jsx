import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import axios from "src/utils/axios";
import { useUpdateSavedView, useDeleteSavedView, useUpdateWorkspaceSavedView,
  useDeleteWorkspaceSavedView, useCreateSavedView, useGetWorkspaceSavedViews,
  useReorderSavedViews } from "../saved-views";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  endpoints: { savedViews: {
    list: "/tracer/saved-views/", create: "/tracer/saved-views/",
    detail: (id) => `/tracer/saved-views/${id}/`, update: (id) => `/tracer/saved-views/${id}/`,
    delete: (id) => `/tracer/saved-views/${id}/`, reorder: "/tracer/saved-views/reorder/",
  } },
}));

const record = { id: "v", revision: 7, config: { widgets: [], sub_tab: "traces" } };
function setup(hook) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const wrapper = ({ children }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  return { ...renderHook(hook, { wrapper }), client };
}

beforeEach(() => {
  vi.resetAllMocks();
  axios.get.mockResolvedValue({ data: { result: record } });
  axios.put.mockResolvedValue({ data: { result: { ...record, revision: 8 } } });
  axios.delete.mockResolvedValue({ data: { result: { message: "View deleted." } } });
});

// Exercise the exact shared-hook call shapes used by each consumer. Deliberately
// start cold: drawer/annotation entry points need not mount a saved-view list.
describe("F-14b saved-view consumer contract matrix", () => {
  it.each([
    ["TraceDetailDrawerV2", "project", "update"],
    ["VoiceDetailDrawerV2", "project", "delete"],
    ["ChatDetailDrawerV2", "project", "delete"],
    ["annotations content-panel", "project", "delete"],
    ["UsersView", "users", "update"],
    ["UsersPageTabBar", "users", "delete"],
    ["UserDetailTabBar", "user_detail", "update"],
    ["Sessions-view", "project", "update"],
    ["ViewConfigModal", "project", "update"],
  ])("%s supplies a cold-cache revision through its existing %s %s hook", async (_consumer, bucket, action) => {
    const project = bucket === "project";
    const hook = action === "update"
      ? () => project ? useUpdateSavedView("p") : useUpdateWorkspaceSavedView(bucket)
      : () => project ? useDeleteSavedView("p") : useDeleteWorkspaceSavedView(bucket);
    const { result } = setup(hook);
    await result.current.mutateAsync(action === "delete" ? "v" : { id: "v", name: "Updated" });
    expect(axios.get).toHaveBeenCalledWith("/tracer/saved-views/v/", { params: { ...(project ? { project_id: "p" } : {}), consistency: "primary" } });
    if (action === "update") expect(axios.put).toHaveBeenCalledWith("/tracer/saved-views/v/", { name: "Updated", expected_revision: 7 }, { params: project ? { project_id: "p" } : {} });
    else expect(axios.delete).toHaveBeenCalledWith("/tracer/saved-views/v/", { params: { ...(project ? { project_id: "p" } : {}), expected_revision: 7 } });
  });
  it.each(["TraceDetailDrawerV2", "VoiceDetailDrawerV2", "ChatDetailDrawerV2"])("%s reorders a cold project bucket conditionally", async () => {
    axios.get.mockResolvedValue({ data: { result: { custom_views: [record], tab_order: { revision: 4, order: ["v"] } } } });
    axios.post.mockResolvedValue({ data: { result: { tab_order: { revision: 5, order: ["v"] } } } });
    const { result } = setup(() => useReorderSavedViews("p"));
    await result.current.mutateAsync({ project_id: "p", order: [{ id: "v", position: 0 }] });
    expect(axios.post).toHaveBeenCalledWith("/tracer/saved-views/reorder/", { project_id: "p", order: [{ id: "v", position: 0 }], expected_revision: 4 });
  });
  it.each([["CrossProjectUserDetailPage", "user_detail"], ["UsersList", "users"]])("%s preserves committed revisions on workspace reads", async (_name, type) => {
    axios.get.mockResolvedValue({ data: { result: { custom_views: [record], tab_order: { revision: 4, order: ["v"] } } } });
    const { result } = setup(() => useGetWorkspaceSavedViews(type));
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data.custom_views[0].revision).toBe(7);
    expect(result.current.data.tab_order.revision).toBe(4);
    expect(axios.put).not.toHaveBeenCalled();
  });
  it("ImagineTab creates a new record at revision 1 without a write precondition", async () => {
    axios.post.mockResolvedValue({ data: { result: { ...record, revision: 1 } } });
    const { result, client } = setup(() => useCreateSavedView("p"));
    await result.current.mutateAsync({ project_id: "p", name: "Analysis", tab_type: "imagine", config: { widgets: [] } });
    expect(axios.post.mock.calls[0][1]).not.toHaveProperty("expected_revision");
    expect(client.getQueryData(["saved-views", "p"]).custom_views[0].revision).toBe(1);
  });
});
