/* eslint-disable react/prop-types */
// Keys page rough edges a first-time user met in a browser walk: the empty
// workspace box in Create a personal key (K1), a disabled key's name cut off
// by its tag (K2), and the re-enable action named three ways (K3).
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
import CreateApiKey from "src/sections/keys/view/CreateApiKey";
import ActionMenu from "src/sections/keys/view/ActionMenu";
import DevKeysView from "src/sections/keys/view/dev-keys-view";

const state = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  remove: vi.fn(),
  snack: vi.fn(),
  columns: null,
  table: [],
}));
vi.mock("src/utils/axios", () => ({
  default: { get: state.get, post: state.post, delete: state.remove },
  endpoints: {
    keys: {
      getKeys: "/accounts/key/get_secret_keys/",
      generateSecretKey: "/accounts/key/generate_secret_key/",
      disablekey: "/accounts/key/disable_key/",
      deleteKey: "/accounts/key/delete_secret_key/",
      enableKey: "/accounts/key/enable_key/",
    },
  },
}));
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: (...args) => state.snack(...args),
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
// The grid has no layout in jsdom: render each column's own cell and keep the
// column definitions the page hands it.
vi.mock("src/components/data-table", () => ({
  DataTable: ({ columns, data }) => {
    state.columns = columns;
    return (
      <div role="table" aria-label="Keys">
        {data.map((row) => (
          <div role="row" key={row.id}>
            {columns.map((col) => (
              <div role="cell" key={col.id}>
                {col.cell
                  ? col.cell({
                      getValue: () => row[col.accessorKey],
                      row: { original: row },
                    })
                  : row[col.accessorKey]}
              </div>
            ))}
          </div>
        ))}
      </div>
    );
  },
  DataTablePagination: () => null,
}));

const ok = { data: { status: true, result: {} } };

beforeEach(() => {
  state.columns = null;
  state.table = [];
  state.get.mockReset().mockImplementation(async () => ({
    data: {
      status: true,
      result: {
        table: state.table,
        metadata: { totalRows: state.table.length },
      },
    },
  }));
  state.post.mockReset().mockResolvedValue({
    data: {
      status: true,
      result: {
        api_key: "example-api-value",
        secret_key: "example-secret-value",
      },
    },
  });
  state.remove.mockReset().mockResolvedValue(ok);
  state.snack.mockReset();
});
afterEach(() => {
  cleanup();
});

function withClient(node) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{node}</QueryClientProvider>;
}

describe("K1: Create a personal key, Workspace (optional)", () => {
  it("reads 'No workspace restriction' when nothing is chosen, with the label above it", () => {
    render(<CreateApiKey open onClose={vi.fn()} />);
    const box = screen.getByRole("combobox", { name: /Workspace/ });
    expect(box).toHaveTextContent("No workspace restriction");
    // The label must not sit on top of the words in the box.
    const label = screen.getByText("Workspace (optional)", {
      selector: "label",
    });
    expect(label).toHaveAttribute("data-shrink", "true");
  });

  it("reads 'No workspace restriction' again when chosen after a workspace, and creates an unrestricted key", async () => {
    render(<CreateApiKey open onClose={vi.fn()} />);
    const box = () => screen.getByRole("combobox", { name: /Workspace/ });
    fireEvent.mouseDown(box());
    fireEvent.click(screen.getByRole("option", { name: "Fixture workspace" }));
    expect(box()).toHaveTextContent("Fixture workspace");
    fireEvent.mouseDown(box());
    fireEvent.click(
      screen.getByRole("option", { name: "No workspace restriction" }),
    );
    expect(box()).toHaveTextContent("No workspace restriction");
    const name = screen.getByLabelText(/Key name/).value;
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Create a key",
      }),
    );
    await screen.findByRole("button", { name: "I saved it" });
    expect(state.post).toHaveBeenCalledTimes(1);
    expect(state.post.mock.calls[0][1]).toEqual({
      key_name: name,
      workspace_id: null,
    });
    expect(state.post.mock.calls[0][2].headers["X-Workspace-Id"]).toBeNull();
  });
});

describe("K2: a disabled key's name in the Key Name column", () => {
  // The name a first-time user's key gets by default, numbered once.
  const longName = "Personal key, Nov 10, 2:15:30 PM (2)";

  async function renderList() {
    state.table = [
      { id: "disabled-key", type: "user", key_name: longName, enabled: false },
    ];
    render(withClient(<DevKeysView />));
    await screen.findByText("Disabled");
    return screen.getByText(longName);
  }

  // jsdom has no layout: say how wide the text is against its box.
  const textWidth = (el, scrollWidth, clientWidth) => {
    Object.defineProperty(el, "scrollWidth", {
      configurable: true,
      value: scrollWidth,
    });
    Object.defineProperty(el, "clientWidth", {
      configurable: true,
      value: clientWidth,
    });
  };

  it("shows the full name on hover only when the column cuts it off", async () => {
    const name = await renderList();
    textWidth(name, 400, 120);
    fireEvent.mouseEnter(name);
    expect(await screen.findByRole("tooltip")).toHaveTextContent(longName);
    // Screen readers read the whole name, not the cut-off text.
    expect(name.textContent).toBe(longName);
    expect(name).toHaveStyle({
      overflow: "hidden",
      textOverflow: "ellipsis",
      whiteSpace: "nowrap",
    });
    fireEvent.mouseLeave(name);
    await waitFor(() =>
      expect(screen.queryByRole("tooltip")).not.toBeInTheDocument(),
    );
    // A name that fits gets no hover text.
    textWidth(name, 120, 120);
    fireEvent.mouseEnter(name);
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("gives the name more room than the masked key columns, which fit the server's hint on one line", async () => {
    // R-LIT: synthetic hints in the server's shapes (first four + ten stars +
    // last four; stars + last four).
    state.table = [
      {
        id: "disabled-key",
        type: "user",
        key_name: longName,
        api_key: "abcd**********wxyz",
        secret_key: "****wxyz",
        enabled: false,
      },
    ];
    render(withClient(<DevKeysView />));
    await screen.findByText("Disabled");
    const byId = Object.fromEntries(state.columns.map((c) => [c.id, c]));
    const keyName = byId.key_name;
    expect(keyName.meta?.flex).toBeGreaterThanOrEqual(2);
    // DataTable reads a top-level minSize next to meta.flex.
    expect(keyName.minSize).toBeGreaterThanOrEqual(240);
    for (const id of ["api_key", "secret_key"]) {
      expect(byId[id].meta?.flex).toBeUndefined();
      // The longest hint is about 121 px in 12 px Inter; a grid cell adds
      // 10 px of padding on each side.
      expect(byId[id].size).toBeGreaterThanOrEqual(145);
      expect(byId[id].size).toBeLessThan(keyName.minSize);
    }
    // At 1024 px the grid is about 775 px wide (200 px sidebar, 16 px page
    // padding a side, a 17 px scrollbar). The columns' least widths fit in it,
    // so the row does not scroll sideways and the actions stay on screen. A
    // flex column without minSize gets at least 50 px.
    const least = state.columns.reduce(
      (sum, c) => sum + (c.meta?.flex ? c.minSize ?? 50 : c.size),
      0,
    );
    expect(least).toBeLessThanOrEqual(775);
    for (const hint of ["abcd**********wxyz", "****wxyz"]) {
      expect(screen.getByText(hint)).toHaveStyle({
        overflow: "hidden",
        textOverflow: "ellipsis",
        whiteSpace: "nowrap",
      });
    }
  });
});

describe("K3: one name for each key action, in sentence case", () => {
  const row = { id: "fixture-key-id", type: "user", key_name: "Fixture key" };

  async function openAction(enabled, menuText) {
    render(withClient(<ActionMenu data={{ ...row, enabled }} />));
    fireEvent.click(
      screen.getByRole("button", { name: "Actions for Fixture key" }),
    );
    const menu = await screen.findByRole("menu");
    fireEvent.click(within(menu).getByText(menuText));
    return screen.findByRole("dialog");
  }

  it("names re-enable the same way in the menu, the dialog title and its button", async () => {
    const dialog = await openAction(false, "Re-enable key");
    expect(within(dialog).getByRole("heading")).toHaveTextContent(
      /^Re-enable key\?$/,
    );
    expect(
      within(dialog).getByRole("button", { name: "Re-enable key" }),
    ).toBeInTheDocument();
  });

  it("confirms a re-enable with the same verb", async () => {
    const dialog = await openAction(false, "Re-enable key");
    fireEvent.click(within(dialog).getByRole("button", { name: /^Re-enable/ }));
    await waitFor(() => expect(state.snack).toHaveBeenCalled());
    expect(state.post).toHaveBeenCalledWith("/accounts/key/enable_key/", {
      key_id: "fixture-key-id",
    });
    expect(state.snack.mock.calls[0][0]).toBe(
      "Fixture key has been re-enabled",
    );
  });

  it("puts 'Re-enable key' and 'Delete key' in one case in a disabled key's menu", async () => {
    render(withClient(<ActionMenu data={{ ...row, enabled: false }} />));
    fireEvent.click(
      screen.getByRole("button", { name: "Actions for Fixture key" }),
    );
    const menu = await screen.findByRole("menu");
    const items = within(menu)
      .getAllByRole("menuitem")
      .map((item) => item.textContent);
    expect(items).toEqual(["Re-enable key", "Delete key"]);
  });

  it.each([
    {
      enabled: true,
      menuText: "Disable key",
      title: "Disable key?",
      button: "Disable key",
      message: "Fixture key has been disabled",
    },
    {
      enabled: true,
      menuText: "Delete key",
      title: "Delete key?",
      button: "Delete key",
      message: "Fixture key has been deleted",
    },
  ])(
    "names $menuText the same way in the menu, dialog title, button and message",
    async ({ enabled, menuText, title, button, message }) => {
      render(withClient(<ActionMenu data={{ ...row, enabled }} />));
      fireEvent.click(
        screen.getByRole("button", { name: "Actions for Fixture key" }),
      );
      const menu = await screen.findByRole("menu");
      fireEvent.click(within(menu).getByText(menuText));
      const dialog = await screen.findByRole("dialog");
      expect(within(dialog).getByRole("heading")).toHaveTextContent(
        new RegExp(`^${title.replace("?", "\\?")}$`),
      );
      fireEvent.click(within(dialog).getByRole("button", { name: button }));
      await waitFor(() => expect(state.snack).toHaveBeenCalled());
      expect(state.snack.mock.calls[0][0]).toBe(message);
    },
  );
});
