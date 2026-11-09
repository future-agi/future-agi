import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import KnowledgeBaseData from "../KnowledgeBaseData";

const transport = vi.hoisted(() => ({ get: vi.fn(), delete: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: transport,
  endpoints: {
    knowledge: { list: "/test/knowledge/list", knowledgeBase: "/test/knowledge" },
  },
}));
vi.mock("src/auth/hooks", () => ({ useAuthContext: () => ({ role: "Owner" }) }));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/sections/projects/UsersView/common", () => ({
  formatNumberWithCommas: (value) => String(value),
}));

/* eslint-disable react/prop-types */
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("../CellRenderer", () => ({ ProcessingStatusCell: () => null }));
/* eslint-enable react/prop-types */

// Trim unused barrel exports, not the table: exercise the real index-to-ID
// adapter, MUI DataGrid checkboxes, pagination and destructive dialog.
vi.mock("src/components/data-table", async () => ({
  DataTable: (await import("src/components/data-table/DataTable")).default,
  DataTablePagination: (await import("src/components/data-table/DataTablePagination")).default,
}));

const atlas = { id: "atlas-id", name: "Atlas", files_uploaded: 1 };
const beacon = { id: "beacon-id", name: "Beacon", files_uploaded: 1 };
const cedar = { id: "cedar-id", name: "Cedar", files_uploaded: 1 };
const listKey = (page = 0, pageSize = 25, search = "") => [
  "knowledge-base", page, pageSize, search,
];
const response = (rows, total = rows.length) => ({
  data: { result: { table_data: rows, total_rows: total } },
});

let client;
let responses;
let remainingIds;

function seed(rows, { page = 0, pageSize = 25, search = "", total = rows.length } = {}) {
  const key = listKey(page, pageSize, search);
  const data = response(rows, total);
  responses.set(JSON.stringify(key), data);
  act(() => client.setQueryData(key, data));
}

function mountList(rows, total = rows.length) {
  seed(rows, { total });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <div style={{ width: 1000, height: 600 }}>
          <KnowledgeBaseData
            setHasData={vi.fn()}
            setShowHelp={vi.fn()}
            setCreateKnowledgeBase={vi.fn()}
          />
        </div>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function gridRows() {
  return Array.from(document.querySelectorAll('.MuiDataGrid-row[data-id]'));
}

function checkbox(id) {
  const row = gridRows().find((node) => node.getAttribute("data-id") === id);
  expect(row).toBeDefined();
  return within(row).getByRole("checkbox", { hidden: true });
}

function checkedIds() {
  return gridRows()
    .filter((row) => within(row).getByRole("checkbox", { hidden: true }).checked)
    .map((row) => row.getAttribute("data-id"))
    .sort();
}

function expectSelection(ids) {
  expect(checkedIds()).toEqual([...ids].sort());
  if (ids.length) {
    expect(screen.getByText(new RegExp(`^${ids.length} selected$`, "i"))).toBeInTheDocument();
  } else {
    expect(screen.queryByText(/^\d+ selected$/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^delete$/i })).not.toBeInTheDocument();
  }
}

async function openConfirmation(count) {
  fireEvent.click(screen.getByRole("button", { name: /^delete$/i }));
  const dialog = await screen.findByRole("dialog");
  const message = count > 1
    ? `Are you sure you want to delete the selected ${count} knowledge base?`
    : "Are you sure you want to delete this knowledge base?";
  expect(within(dialog).getByText(message)).toBeInTheDocument();
  return dialog;
}

async function confirmDeletion(dialog, ids) {
  expect(checkedIds()).toEqual([...ids].sort());
  fireEvent.click(within(dialog).getByRole("button", { name: /^delete$/i }));
  await waitFor(() => expect(transport.delete).toHaveBeenCalledTimes(1));
  expect(transport.delete).toHaveBeenCalledWith("/test/knowledge", {
    data: { kb_ids: ids },
  });
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expectSelection([]);
}

async function refresh(rows, total = rows.length) {
  // Resolve a real active-query refetch asynchronously, as polling would.
  const data = response(rows, total);
  responses.set(JSON.stringify(listKey()), data);
  let finish;
  const pending = new Promise((resolve) => { finish = resolve; });
  transport.get.mockReturnValueOnce(pending);
  let fetching;
  act(() => {
    fetching = client.refetchQueries({ queryKey: listKey(), exact: true });
  });
  await act(async () => {
    finish(data);
    await fetching;
  });
  await waitFor(() => {
    expect(gridRows().map((row) => row.getAttribute("data-id"))).toEqual(rows.map((row) => row.id));
  });
}

beforeEach(() => {
  transport.get.mockReset();
  transport.delete.mockReset();
  responses = new Map();
  remainingIds = new Set([atlas.id, beacon.id, cedar.id]);
  client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity, refetchOnWindowFocus: false } },
  });
  transport.get.mockImplementation((_url, { params }) => {
    const key = listKey(params.page_number, params.page_size, params.search);
    const data = responses.get(JSON.stringify(key));
    if (!data) throw new Error(`Missing list response for ${JSON.stringify(key)}`);
    return Promise.resolve(data);
  });
  transport.delete.mockImplementation((_url, { data }) => {
    data.kb_ids.forEach((id) => remainingIds.delete(id));
    return Promise.resolve({ data: { result: "Deleted" } });
  });
});

afterEach(() => {
  cleanup();
  client.clear();
});

describe("W5-A-001 knowledge-base deletion identity", () => {
  it("W5-A-001-search clears selection before debounce and requires a fresh choice for cached results", async () => {
    seed([atlas], { search: "Atlas" });
    seed([beacon], { search: "Beacon" });
    mountList([atlas]);
    const search = screen.getByPlaceholderText("Search");
    fireEvent.change(search, { target: { value: "Atlas" } });
    await waitFor(() => expect(transport.get).toHaveBeenCalledWith("/test/knowledge/list", {
      params: { search: "Atlas", page_number: 0, page_size: 25 },
    }));
    fireEvent.click(checkbox(atlas.id));
    expectSelection([atlas.id]);

    fireEvent.change(search, { target: { value: "Beacon" } });
    expectSelection([]);
    expect(transport.delete).not.toHaveBeenCalled();
    await screen.findByText("Beacon");
    expectSelection([]);

    fireEvent.click(checkbox(beacon.id));
    expectSelection([beacon.id]);
    await confirmDeletion(await openConfirmation(1), [beacon.id]);
    expect(remainingIds.has(atlas.id)).toBe(true);
    expect(remainingIds.has(beacon.id)).toBe(false);
  });

  it("W5-A-001-page clears select-all across cached pages with different result counts", async () => {
    seed([beacon], { page: 1, total: 26 });
    mountList([atlas, cedar], 26);
    await screen.findByText("Atlas");
    fireEvent.click(screen.getByRole("checkbox", { name: /select all rows/i }));
    expectSelection([atlas.id, cedar.id]);
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    await screen.findByText("Beacon");
    expectSelection([]);
    expect(transport.delete).not.toHaveBeenCalled();

    fireEvent.click(checkbox(beacon.id));
    expectSelection([beacon.id]);
    await confirmDeletion(await openConfirmation(1), [beacon.id]);
    expect(remainingIds.has(atlas.id)).toBe(true);
    expect(remainingIds.has(cedar.id)).toBe(true);
  });

  it("W5-A-001-page-size clears selection even when a new page size contains the same IDs", async () => {
    seed([atlas, beacon], { pageSize: 50 });
    mountList([atlas, beacon]);
    await screen.findByText("Atlas");
    fireEvent.click(checkbox(atlas.id));
    expectSelection([atlas.id]);
    fireEvent.change(screen.getByRole("combobox", { name: "Rows per page" }), {
      target: { value: "50" },
    });
    expectSelection([]);
    await waitFor(() => expect(transport.get).toHaveBeenCalledWith("/test/knowledge/list", {
      params: { search: "", page_number: 0, page_size: 50 },
    }));
    expectSelection([]);
    expect(transport.delete).not.toHaveBeenCalled();
  });

  it("W5-A-001-reorder keeps checked IDs and the open confirmation payload aligned after polling", async () => {
    mountList([atlas, beacon]);
    await screen.findByText("Atlas");
    fireEvent.click(checkbox(atlas.id));
    expectSelection([atlas.id]);
    const dialog = await openConfirmation(1);
    await refresh([beacon, { ...atlas, files_uploaded: 2 }]);
    expectSelection([atlas.id]);
    expect(checkbox(beacon.id)).not.toBeChecked();
    await confirmDeletion(dialog, [atlas.id]);
    expect(remainingIds.has(beacon.id)).toBe(true);
  });

  it("W5-A-001-replace closes confirmation when polling replaces the selected KB at the same index", async () => {
    mountList([atlas]);
    await screen.findByText("Atlas");
    fireEvent.click(checkbox(atlas.id));
    await openConfirmation(1);
    await refresh([beacon]);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expectSelection([]);
    expect(transport.delete).not.toHaveBeenCalled();
    fireEvent.click(checkbox(beacon.id));
    await confirmDeletion(await openConfirmation(1), [beacon.id]);
    expect(remainingIds.has(atlas.id)).toBe(true);
  });

  it("W5-A-001-prune closes an old bulk confirmation and confirms only the remaining visible IDs", async () => {
    mountList([atlas, beacon]);
    await screen.findByText("Atlas");
    fireEvent.click(screen.getByRole("checkbox", { name: /select all rows/i }));
    expectSelection([atlas.id, beacon.id]);
    await openConfirmation(2);
    await refresh([beacon]);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expectSelection([beacon.id]);
    expect(transport.delete).not.toHaveBeenCalled();
    await confirmDeletion(await openConfirmation(1), [beacon.id]);
    expect(remainingIds.has(atlas.id)).toBe(true);
  });

  it("W5-A-001-reappear does not restore a removed selection when its ID returns in a later refresh", async () => {
    mountList([atlas, beacon]);
    await screen.findByText("Atlas");
    fireEvent.click(checkbox(atlas.id));
    await refresh([beacon]);
    expectSelection([]);
    await refresh([atlas, beacon]);
    expectSelection([]);
    expect(transport.delete).not.toHaveBeenCalled();
  });

  it("W5-A-001-cancel clears both checked rows and destructive actions", async () => {
    mountList([atlas, beacon]);
    await screen.findByText("Atlas");
    fireEvent.click(screen.getByRole("checkbox", { name: /select all rows/i }));
    const dialog = await openConfirmation(2);
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expectSelection([]);
    expect(transport.delete).not.toHaveBeenCalled();
  });
});
