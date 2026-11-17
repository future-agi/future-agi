import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HelmetProvider } from "react-helmet-async";
import { render, screen, waitFor, within } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

import SessionExplorerSection from "../SessionExplorerSection";

vi.mock("ag-grid-react", () => import("../../logs/__tests__/fakeAgGrid"));
vi.mock("../SessionDetailDrawer", () => ({ default: () => null }));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 15 }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ wsLevel: 3 }),
}));

const { get, post, snackbar } = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  snackbar: vi.fn(),
}));
vi.mock("notistack", () => ({ enqueueSnackbar: snackbar }));
// All transport is mocked. The real useCloseSession mutation must preserve
// the rejection until the page's confirmation handler displays it.
vi.mock("src/utils/axios", () => ({
  default: { get, post },
  endpoints: {
    gateway: {
      sessions: {
        list: "/agentcc/sessions/",
        close: (id) => `/agentcc/sessions/${id}/close/`,
      },
    },
  },
}));

const SESSION = {
  id: "row-12",
  session_id: "conv-012",
  name: "conv-012",
  status: "active",
  closed_reason: "",
  idle_timeout_seconds: 1800,
  created_at: "2026-10-20T09:38:16.807Z",
  stats: { request_count: 1, total_tokens: 54, total_cost: 0.001 },
};
const FALLBACK = "Session conv-012 could not be closed";
const DENIAL = "You no longer have write access to this workspace.";

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <HelmetProvider>
      <QueryClientProvider client={client}>
        <SessionExplorerSection />
      </QueryClientProvider>
    </HelmetProvider>,
  );
}

beforeEach(() => {
  get.mockReset();
  post.mockReset();
  snackbar.mockReset();
  get.mockImplementation((url) => {
    if (url === "/agentcc/sessions/") {
      return Promise.resolve({ data: { count: 1, results: [SESSION] } });
    }
    return Promise.reject(new Error(`Unexpected request: ${url}`));
  });
});

describe("Session close errors", () => {
  it.each([
    ["normalized backend result", { result: DENIAL }, DENIAL],
    [
      "axios backend result before a generic transport message",
      {
        response: { data: { result: DENIAL } },
        message: "Request failed with status code 403",
      },
      DENIAL,
    ],
    ["backend detail", { response: { data: { detail: DENIAL } } }, DENIAL],
    [
      "backend error",
      { error: "This session cannot be closed." },
      "This session cannot be closed.",
    ],
    [
      "transport failure",
      new Error("The session API is unavailable."),
      "The session API is unavailable.",
    ],
    [
      "structured error without readable text",
      { result: { code: "denied" } },
      FALLBACK,
    ],
    ["missing rejection", undefined, FALLBACK],
  ])("surfaces the %s after confirmation", async (_label, error, message) => {
    const user = userEvent.setup();
    post.mockRejectedValue(error);
    renderPage();
    await screen.findByText("conv-012");
    await user.click(
      screen.getByRole("button", { name: "Actions for session conv-012" }),
    );
    await user.click(
      await screen.findByRole("menuitem", { name: /Close session/ }),
    );
    const dialog = screen.getByRole("dialog", {
      name: "Close session conv-012?",
    });
    expect(post).not.toHaveBeenCalled();
    await user.click(
      within(dialog).getByRole("button", { name: "Close session" }),
    );

    expect(post).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith("/agentcc/sessions/row-12/close/");
    await waitFor(() =>
      expect(snackbar).toHaveBeenCalledWith(message, { variant: "error" }),
    );
    expect(snackbar).toHaveBeenCalledTimes(1);
    expect(snackbar).not.toHaveBeenCalledWith(expect.anything(), {
      variant: "success",
    });
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    // A rejected close must not present the existing session as closed.
    expect(
      within(screen.getByTestId("ag-grid")).getByText("Active"),
    ).toBeInTheDocument();
  });
});
