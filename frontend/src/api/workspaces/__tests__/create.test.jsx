import React from "react";
import { describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const { post } = vi.hoisted(() => ({ post: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: { post },
  endpoints: { workspaces: { create: "/accounts/workspaces/" } },
}));

import { useCreateWorkspace } from "../create";
import { workspacesListKey } from "../list";

describe("useCreateWorkspace", () => {
  it("refreshes the workspace list and then runs the caller's onSuccess", async () => {
    const response = { data: { result: { id: "ws-2" } } };
    post.mockResolvedValueOnce(response);
    const client = new QueryClient();
    const listKey = [...workspacesListKey, "org-1"];
    client.setQueryData(listKey, { pages: [], pageParams: [] });
    const onSuccess = vi.fn();

    const { result } = renderHook(() => useCreateWorkspace({ onSuccess }), {
      wrapper: ({ children }) => (
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
      ),
    });
    await act(() => result.current.mutateAsync({ name: "Research" }));

    expect(post).toHaveBeenCalledWith("/accounts/workspaces/", {
      name: "Research",
    });
    expect(client.getQueryState(listKey).isInvalidated).toBe(true);
    expect(onSuccess.mock.calls[0][0]).toBe(response);
  });
});
