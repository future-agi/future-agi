import React, { useContext } from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, waitFor, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { AuthProvider } from "../auth-provider";
import { AuthContext } from "../auth-context";
import { usePromptStore } from "src/sections/workbench-v2/store/usePromptStore";
import { useTraceDrawerStore } from "src/sections/projects/TracesDrawer/useTraceDrawerStore";

const { post, get } = vi.hoisted(() => ({ post: vi.fn(), get: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: { post, get, defaults: { headers: { common: {} } } },
  endpoints: {
    auth: {
      me: "/accounts/user-info/",
      logout: "/accounts/logout/",
      register: "/accounts/register/",
    },
  },
}));

function renderProvider() {
  const queryClient = new QueryClient();
  let auth;
  const Capture = () => {
    auth = useContext(AuthContext);
    return null;
  };
  render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <Capture />
      </AuthProvider>
    </QueryClientProvider>,
  );
  return { queryClient, getAuth: () => auth };
}

function seedPreviousUser(queryClient) {
  queryClient.setQueryData(
    ["projects"],
    [{ id: "p-a", name: "user A project" }],
  );
  usePromptStore.setState({ searchQuery: "user A search" });
  useTraceDrawerStore.setState({ __previousUserSentinel: "user A trace" });
}

function expectPreviousUserGone(queryClient) {
  expect(queryClient.getQueryData(["projects"])).toBeUndefined();
  expect(usePromptStore.getState().searchQuery).toBe("");
  expect(useTraceDrawerStore.getState().__previousUserSentinel).toBeUndefined();
}

describe("AuthProvider logout", () => {
  beforeEach(() => {
    post.mockReset();
    get.mockReset();
    localStorage.clear();
    sessionStorage.clear();
  });

  it("clears the previous user's cached queries and stores", async () => {
    post.mockResolvedValue({ status: 200, data: {} });
    const { queryClient, getAuth } = renderProvider();
    await waitFor(() => expect(getAuth().loading).toBe(false));
    seedPreviousUser(queryClient);

    await act(() => getAuth().logout());

    expect(post).toHaveBeenCalledWith(
      "/accounts/logout/",
      {},
      expect.anything(),
    );
    expectPreviousUserGone(queryClient);
  });

  it("clears them even when the logout request fails", async () => {
    // An expired access token or a network error makes the server-side logout
    // fail; the tab still signs out and the next account can sign in on it.
    post.mockRejectedValue(new Error("401 Unauthorized"));
    const { queryClient, getAuth } = renderProvider();
    await waitFor(() => expect(getAuth().loading).toBe(false));
    seedPreviousUser(queryClient);

    await act(async () => {
      await getAuth()
        .logout()
        .catch(() => {});
    });

    expect(getAuth().authenticated).toBe(false);
    expectPreviousUserGone(queryClient);
  });
});
