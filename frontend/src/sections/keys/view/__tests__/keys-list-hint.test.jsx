/* eslint-disable react/prop-types */
// The Keys table shows each key's masked hint as the server sent it, so a
// person can tell their keys apart (as production does). The list never
// builds a hint itself, never shows a value without a star, and never offers
// a copy action: the full secret is copied only in the one-time reveal.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import DevKeysView from "src/sections/keys/view/dev-keys-view";
import SecretKeyRenderer from "src/sections/keys/view/SecretKeyRenderer";

const state = vi.hoisted(() => ({
  get: vi.fn(),
  table: [],
}));
vi.mock("src/utils/axios", () => ({
  default: { get: state.get, post: vi.fn(), delete: vi.fn() },
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
// The grid has no layout in jsdom: render each column's own cell, named by
// its column, the way DataTable hands getValue() the row's field.
vi.mock("src/components/data-table", () => ({
  DataTable: ({ columns, data }) => (
    <div role="table" aria-label="Keys">
      {data.map((row) => (
        <div role="row" key={row.id} data-id={row.id}>
          {columns.map((col) => (
            <div role="cell" key={col.id} data-field={col.id}>
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
  ),
  DataTablePagination: () => null,
}));

beforeEach(() => {
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

// R-LIT: every key value below is a visibly synthetic fixture.
const keyRow = (id, name, api_key, secret_key) => ({
  id,
  type: "user",
  key_name: name,
  api_key,
  secret_key,
  enabled: true,
  created_by: "Fixture Person",
  created_at: "2026-11-10T12:00:00Z",
});

async function renderKeys(rows) {
  state.table = rows;
  render(withClient(<DevKeysView />));
  await screen.findByText(rows[0].key_name);
}

const cell = (rowId, field) =>
  document.querySelector(
    `[role="row"][data-id="${rowId}"] [role="cell"][data-field="${field}"]`,
  );

describe("Keys table: the server's masked hint in each key's row", () => {
  it("shows the API key hint exactly as the server sent it", async () => {
    await renderKeys([
      keyRow("key-1", "Fixture key", "abcd**********wxyz", "****wxyz"),
    ]);
    expect(cell("key-1", "api_key").textContent).toBe("abcd**********wxyz");
  });

  it("shows the secret key hint exactly as the server sent it", async () => {
    await renderKeys([
      keyRow("key-1", "Fixture key", "abcd**********wxyz", "****wxyz"),
    ]);
    expect(cell("key-1", "secret_key").textContent).toBe("****wxyz");
  });

  it("renders two keys with different hints differently", async () => {
    await renderKeys([
      keyRow("key-1", "First key", "abcd**********wxyz", "****wxyz"),
      keyRow("key-2", "Second key", "efgh**********stuv", "****stuv"),
    ]);
    expect(cell("key-1", "api_key").textContent).toBe("abcd**********wxyz");
    expect(cell("key-2", "api_key").textContent).toBe("efgh**********stuv");
    expect(cell("key-1", "secret_key").textContent).toBe("****wxyz");
    expect(cell("key-2", "secret_key").textContent).toBe("****stuv");
    expect(cell("key-1", "api_key").textContent).not.toBe(
      cell("key-2", "api_key").textContent,
    );
    expect(cell("key-1", "secret_key").textContent).not.toBe(
      cell("key-2", "secret_key").textContent,
    );
  });

  it("shows eight stars for a list value with no star, and the raw value nowhere", async () => {
    await renderKeys([
      keyRow(
        "key-raw",
        "Raw fixture key",
        "fixture-raw-api-value-0001",
        "fixture-raw-secret-value-0001",
      ),
    ]);
    expect(cell("key-raw", "api_key").textContent).toBe("********");
    expect(cell("key-raw", "secret_key").textContent).toBe("********");
    const html = document.body.innerHTML;
    for (const raw of [
      "fixture-raw-api-value-0001",
      "fixture-raw-secret-value-0001",
    ]) {
      expect(html).not.toContain(raw);
      // Nor a hint the page built from it.
      expect(html).not.toContain(raw.slice(0, 4) + "*");
      expect(html).not.toContain("*" + raw.slice(-4));
    }
  });

  it("shows eight stars for an empty list value", async () => {
    await renderKeys([keyRow("key-empty", "Empty fixture key", "", undefined)]);
    expect(cell("key-empty", "api_key").textContent).toBe("********");
    expect(cell("key-empty", "secret_key").textContent).toBe("********");
  });

  it("offers no copy button inside a key cell", async () => {
    await renderKeys([
      keyRow("key-1", "Fixture key", "abcd**********wxyz", "****wxyz"),
      keyRow("key-raw", "Raw fixture key", "fixture-raw-api-value-0002", ""),
    ]);
    for (const id of ["key-1", "key-raw"]) {
      for (const field of ["api_key", "secret_key"]) {
        expect(within(cell(id, field)).queryAllByRole("button")).toHaveLength(
          0,
        );
      }
    }
  });
});

describe("SecretKeyRenderer: the list cell for a key", () => {
  it.each(["abcd**********wxyz", "****wxyz", "abcd****"])(
    "shows the starred value %s exactly, with no copy button",
    (value) => {
      const { container } = render(<SecretKeyRenderer value={value} />);
      expect(container.textContent).toBe(value);
      expect(screen.queryByRole("button")).not.toBeInTheDocument();
    },
  );

  it.each([
    { tag: "raw", value: "fixture1234567890abcdef1234wxyz" },
    { tag: "short raw", value: "fixture" },
    { tag: "empty", value: "" },
    { tag: "missing", value: undefined },
    { tag: "dash", value: "—" },
  ])(
    "shows eight stars for a $tag value, never a hint of its own, and no copy button",
    ({ value }) => {
      const { container } = render(<SecretKeyRenderer value={value} />);
      expect(container.textContent).toBe("********");
      expect(screen.queryByRole("button")).not.toBeInTheDocument();
      if (value) expect(container.innerHTML).not.toContain(value);
    },
  );

  it("keeps a hint on one line in its column", () => {
    render(<SecretKeyRenderer value="abcd**********wxyz" />);
    expect(screen.getByText("abcd**********wxyz")).toHaveStyle({
      overflow: "hidden",
      textOverflow: "ellipsis",
      whiteSpace: "nowrap",
    });
  });
});
