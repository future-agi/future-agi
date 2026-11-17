/* eslint-disable react/prop-types */
// Keys page: the pre-filled key name stays free for a person creating keys
// back to back. The API refuses a name the person already uses
// (KEY_NAME_EXISTS), so a second dialog opened in the same second as the
// first, after that first key is saved, must not offer the same name.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import DevKeysView from "src/sections/keys/view/dev-keys-view";

const state = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  table: [],
}));
vi.mock("src/utils/axios", () => ({
  default: { get: state.get, post: state.post, delete: vi.fn() },
  endpoints: {
    keys: {
      getKeys: "/accounts/key/get_secret_keys/",
      generateSecretKey: "/accounts/key/generate_secret_key/",
      disablekey: "/synthetic/disable/",
      deleteKey: "/synthetic/delete/",
      enableKey: "/synthetic/enable/",
    },
  },
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: { id: "fixture-person" } }),
}));
vi.mock("src/auth/hooks/use-auth-context", () => ({
  useSafeAuthContext: () => ({ user: { id: "fixture-person" } }),
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: "fixture-org" }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({
    currentWorkspaceId: "fixture-workspace",
    currentWorkspaceName: "Fixture workspace",
  }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/components/page-header", () => ({
  default: ({ title }) => <h1>{title}</h1>,
}));
vi.mock("src/components/FormSearchField/FormSearchField", () => ({
  default: ({ placeholder, searchQuery, onChange }) => (
    <input aria-label={placeholder} value={searchQuery} onChange={onChange} />
  ),
}));
// The list the page has loaded, as names only: the grid itself is not under test.
vi.mock("src/components/data-table", () => ({
  DataTable: ({ data }) => (
    <ul aria-label="Loaded keys">
      {data.map((row) => (
        <li key={row.id}>{row.key_name}</li>
      ))}
    </ul>
  ),
  DataTablePagination: () => null,
}));

const listReply = () => ({
  data: {
    status: true,
    result: {
      table: state.table,
      metadata: { totalRows: state.table.length },
    },
  },
});

beforeEach(() => {
  state.table = [
    { id: "older-key", type: "user", key_name: "Older key", enabled: true },
  ];
  state.get.mockReset().mockImplementation(async () => listReply());
  state.post.mockReset().mockImplementation(async (_url, body) => {
    // The saved key joins the list the next time the page loads it.
    state.table = [
      {
        id: `saved-${state.table.length}`,
        type: "user",
        key_name: body.key_name,
        enabled: true,
      },
      ...state.table,
    ];
    return {
      data: {
        status: true,
        result: {
          api_key: "example-api-value",
          secret_key: "example-secret-value",
        },
      },
    };
  });
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <DevKeysView />
    </QueryClientProvider>,
  );
}

describe("Keys page default key name", () => {
  it("gives two dialogs opened in the same second, with one saved key between them, different names", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    const now = new Date(2026, 10, 10, 14, 15, 30, 123);
    vi.setSystemTime(now);
    renderPage();
    await screen.findByText("Older key");

    fireEvent.click(screen.getByRole("button", { name: "Add API Key" }));
    const first = screen.getByLabelText(/Key name/).value;
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Create a key",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "I saved it" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(state.post).toHaveBeenCalledTimes(1);
    expect(state.post.mock.calls[0][1].key_name).toBe(first);
    // The page has reloaded its list and now holds the saved key.
    await screen.findByText(first);

    fireEvent.click(screen.getByRole("button", { name: "Add API Key" }));
    const second = screen.getByLabelText(/Key name/).value;
    // Still the same second: the clock did not move between the two dialogs.
    expect(Date.now()).toBe(now.getTime());
    expect(second).not.toBe(first);
    expect(second).toBe(`${first} (2)`);
    expect(second.startsWith("Personal key, ")).toBe(true);
  });
});
