/* eslint-disable react/prop-types */
// Settings > API keys shows each key's masked hint, exactly as the list answer
// sent it, in the API Key and Secret Key columns, with the Keys table's own
// list cell. A key without a hint keeps eight stars, and no full value from
// the answer is ever shown. The grid is replaced by a stand-in that asks the
// page's data source for the first page and renders each column's own cell
// renderer with the row's field, the way the grid hands it to a cell.
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";

import ApiKeysLandingPage from "../ApiKeysLandingPage";

const LIST_URL = "/accounts/key/get_secret_keys/";

const state = vi.hoisted(() => ({
  get: vi.fn(),
  table: [],
  grid: null,
  rowData: null,
}));

vi.mock("ag-grid-react", () => ({
  AgGridReact: React.forwardRef(function AgGridReact(props, _ref) {
    const { columnDefs, serverSideDatasource } = props;
    const [rows, setRows] = React.useState([]);
    state.grid = props;
    React.useEffect(() => {
      let live = true;
      serverSideDatasource.getRows({
        request: { startRow: 0, endRow: 10 },
        success: ({ rowData }) => {
          if (!live) return;
          state.rowData = rowData;
          setRows(rowData);
        },
        fail: () => {},
      });
      return () => {
        live = false;
      };
    }, [serverSideDatasource]);
    return (
      <div role="grid" aria-label="API keys">
        {rows.map((row) => (
          <div role="row" key={row.id} data-id={row.id}>
            {columnDefs.map((colDef) => {
              const Cell = colDef.cellRenderer;
              return (
                <div
                  role="gridcell"
                  key={colDef.headerName}
                  data-field={colDef.field}
                >
                  <Cell
                    value={row[colDef.field]}
                    data={row}
                    colDef={colDef}
                    column={{ colDef }}
                    api={{ refreshServerSide: () => {} }}
                  />
                </div>
              );
            })}
          </div>
        ))}
      </div>
    );
  }),
}));
vi.mock("src/hooks/use-ag-theme", () => ({ useAgThemeWith: () => ({}) }));
vi.mock("src/theme/ag-theme", () => ({
  AG_THEME_OVERRIDES: { withColumnBorder: {} },
}));
vi.mock("src/utils/axios", () => ({
  default: { get: state.get, post: vi.fn(), delete: vi.fn() },
  endpoints: {
    keys: {
      getKeys: "/accounts/key/get_secret_keys/",
      disablekey: "/accounts/key/disable_key/",
      deleteKey: "/accounts/key/delete_secret_key/",
      enableKey: "/accounts/key/enable_key/",
    },
  },
}));
vi.mock("src/auth/hooks/use-auth-context", () => ({
  useSafeAuthContext: () => ({ user: { id: "fixture-owner" } }),
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: "fixture-org" }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceId: "fixture-workspace" }),
}));
vi.mock("../CreateApiKey", () => ({ default: () => null }));
vi.mock("src/sections/common/CellMarkdown", () => ({
  default: ({ text }) => <span>{text}</span>,
}));
vi.mock("src/components/image", () => ({ default: () => null }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));

beforeEach(() => {
  state.table = [];
  state.grid = null;
  state.rowData = null;
  state.get.mockReset().mockImplementation(async () => ({
    data: {
      status: true,
      result: {
        table: state.table,
        metadata: { total_rows: state.table.length },
      },
    },
  }));
});

// Every key value below is a visibly synthetic fixture.
const keyRow = (id, name, api_key, secret_key, extra = {}) => ({
  id,
  type: "user",
  key_name: name,
  api_key,
  secret_key,
  enabled: true,
  created_by: "Fixture Owner",
  created_at: null,
  ...extra,
});

const FULL_VALUES = [
  "fixture-full-api-value-0003",
  "fixture-full-secret-value-0003",
  "fixture-full-api-value-0004",
  "fixture-full-secret-value-0004",
];

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ApiKeysLandingPage />
    </QueryClientProvider>,
  );
}

async function renderKeys(rows) {
  state.table = rows;
  const view = renderPage();
  await screen.findByText(rows[0].key_name);
  return view;
}

const cell = (rowId, field) =>
  document.querySelector(
    `[role="row"][data-id="${rowId}"] [role="gridcell"][data-field="${field}"]`,
  );

const twoKeys = () => [
  keyRow("key-1", "First key", "abcd**********wxyz", "****wxyz"),
  keyRow("key-2", "Second key", "efgh**********stuv", "****stuv"),
];

describe("Settings > API keys: each key's masked hint", () => {
  it("lists each key by name in its own row", async () => {
    await renderKeys(twoKeys());
    expect(cell("key-1", "key_name").textContent).toBe("First key");
    expect(cell("key-2", "key_name").textContent).toBe("Second key");
  });

  it("shows two keys' different hints, each exactly as the list answer sent it", async () => {
    await renderKeys(twoKeys());
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

  it("shows no eight-star cell for a key with a hint, and eight stars for a key without one", async () => {
    await renderKeys([
      ...twoKeys(),
      keyRow("key-none", "Key without a hint", "", "********"),
      keyRow("key-missing", "Key with no hint fields", undefined, undefined),
    ]);
    for (const id of ["key-1", "key-2"]) {
      for (const field of ["api_key", "secret_key"]) {
        expect(cell(id, field).textContent).not.toBe("********");
        expect(cell(id, field).textContent).toContain("*");
      }
    }
    for (const id of ["key-none", "key-missing"]) {
      for (const field of ["api_key", "secret_key"]) {
        expect(cell(id, field).textContent).toBe("********");
      }
    }
  });

  it("renders the hint in the Keys table's list cell, on one line", async () => {
    await renderKeys(twoKeys());
    const oneLine = {
      overflow: "hidden",
      textOverflow: "ellipsis",
      whiteSpace: "nowrap",
    };
    const apiCell = within(cell("key-1", "api_key"));
    const secretCell = within(cell("key-1", "secret_key"));
    expect(apiCell.getByText("abcd**********wxyz")).toHaveStyle(oneLine);
    expect(secretCell.getByText("****wxyz")).toHaveStyle(oneLine);
  });

  it("shows the masked field when the answer also carries a full value in the key field", async () => {
    await renderKeys([
      keyRow("key-1", "First key", FULL_VALUES[0], FULL_VALUES[1], {
        masked_api_key: "abcd**********wxyz",
        masked_secret_key: "****wxyz",
      }),
    ]);
    expect(cell("key-1", "api_key").textContent).toBe("abcd**********wxyz");
    expect(cell("key-1", "secret_key").textContent).toBe("****wxyz");
    const html = document.body.innerHTML;
    expect(html).not.toContain(FULL_VALUES[0]);
    expect(html).not.toContain(FULL_VALUES[1]);
  });
});

describe("Settings > API keys: no full value on the page", () => {
  const answerWithFullValues = () => [
    keyRow("key-1", "First key", "abcd**********wxyz", "****wxyz", {
      full_api_key: FULL_VALUES[0],
      full_secret_key: FULL_VALUES[1],
    }),
    keyRow("key-raw", "Raw key", FULL_VALUES[2], FULL_VALUES[3]),
  ];

  it("shows a full value from the answer nowhere on the page", async () => {
    const { container } = await renderKeys(answerWithFullValues());
    expect(cell("key-raw", "api_key").textContent).toBe("********");
    expect(cell("key-raw", "secret_key").textContent).toBe("********");
    const text = document.body.textContent;
    const html = document.body.innerHTML;
    for (const full of FULL_VALUES) {
      expect(container.textContent).not.toContain(full);
      expect(text).not.toContain(full);
      expect(html).not.toContain(full);
      // Nor a hint the page built from it.
      expect(html).not.toContain(full.slice(0, 4) + "*");
      expect(html).not.toContain("*" + full.slice(-4));
    }
  });

  it("offers no copy or reveal control in a key cell, and gives the grid only starred key values to copy", async () => {
    await renderKeys(answerWithFullValues());
    for (const id of ["key-1", "key-raw"]) {
      for (const field of ["api_key", "secret_key"]) {
        const keyCell = cell(id, field);
        expect(within(keyCell).queryAllByRole("button")).toHaveLength(0);
        expect(keyCell.querySelectorAll("button, a, input")).toHaveLength(0);
      }
    }
    // The grid copies a focused cell's field value: no column's value in any
    // row holds a full value, and the key columns hold starred values.
    expect(state.rowData).toHaveLength(2);
    const fields = state.grid.columnDefs.map((colDef) => colDef.field);
    for (const row of state.rowData) {
      for (const field of fields) {
        for (const full of FULL_VALUES) {
          expect(String(row[field] ?? "")).not.toContain(full);
        }
      }
      expect(row.api_key).toContain("*");
      expect(row.secret_key).toContain("*");
    }
  });
});

describe("Settings > API keys: search, paging and the empty state", () => {
  it("asks the list for the typed search after the pause and keeps the grid when nothing matches", async () => {
    await renderKeys(twoKeys());
    state.table = [];
    fireEvent.change(screen.getByPlaceholderText("Search"), {
      target: { value: "Second" },
    });
    const searched = {
      params: { search: "Second", current_page_index: 0, page_size: 10 },
    };
    const sawSearch = () =>
      expect(state.get).toHaveBeenCalledWith(LIST_URL, searched);
    await waitFor(sawSearch, { timeout: 3000 });
    await waitFor(() => expect(state.rowData).toEqual([]));
    expect(screen.getByRole("grid", { name: "API keys" })).toBeInTheDocument();
    expect(screen.queryByText(/No API keys yet/)).not.toBeInTheDocument();
  });

  it("asks the list for the page and the page size the grid requests", async () => {
    await renderKeys(twoKeys());
    const success = vi.fn();
    const fail = vi.fn();
    await state.grid.serverSideDatasource.getRows({
      request: { startRow: 20, endRow: 30 },
      success,
      fail,
    });
    expect(fail).not.toHaveBeenCalled();
    expect(state.get).toHaveBeenLastCalledWith(LIST_URL, {
      params: { search: null, current_page_index: 2, page_size: 10 },
    });
  });

  it("shows the empty state and no grid when there are no keys", async () => {
    state.table = [];
    renderPage();
    const empty =
      "No API keys yet. Create a personal key to use the SDK, CLI or OTLP.";
    expect(await screen.findByText(empty)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create a key" })).toBeVisible();
    expect(screen.queryByRole("grid")).not.toBeInTheDocument();
  });
});
