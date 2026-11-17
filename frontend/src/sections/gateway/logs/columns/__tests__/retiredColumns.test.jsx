import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import RequestTable from "../../RequestTable";
import {
  BUILTIN_COLUMNS,
  orderColumns,
  resolveColumns,
  resolveGatewayColumns,
  toggleColumn,
  validateConfig,
} from "../columnModel";
import { buildKey, readConfig, writeConfig } from "../columnPrefsStorage";
import { downloadLogExport } from "../../exportLogs";
import { creditsLog, ownKeyLog } from "../../__tests__/priceFixtures";

// The Upstream cost and Markup columns are gone. A layout saved while they
// existed still names them: it loads (no fall back to the default table, no
// error) with the user's other columns, order and hidden state, and no
// empty column where they were.

vi.mock("ag-grid-react", () => import("../../__tests__/fakeAgGrid"));
vi.mock("../../../shell/page-status", async (importOriginal) => ({
  ...(await importOriginal()),
  isGatewayPageHidden: () => false,
}));
vi.mock("../../hooks/useRequestLogs", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => ({
    data: { results: [creditsLog(), ownKeyLog()], count: 2 },
    isLoading: false,
    error: null,
    refetch: () => {},
  }),
}));
const { mockGet } = vi.hoisted(() => ({ mockGet: vi.fn() }));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { ...actual.default, get: mockGet } };
});

const RETIRED = ["gateway:costUpstream", "gateway:costMarkup"];

// Saved before the change: Billing mode shown, both retired columns (one of
// them hidden), Agent hidden, Attempts shown after them.
const SAVED = {
  v: 1,
  columns: [
    ...BUILTIN_COLUMNS.map((col) => ({ id: col.id })),
    { id: "gateway:billingMode" },
    { id: "gateway:costUpstream" },
    { id: "gateway:costMarkup" },
    { id: "gateway:agentId" },
    { id: "gateway:attemptCount" },
  ],
  hidden: ["builtin:sessionId", "gateway:costMarkup", "gateway:agentId"],
};

const KEY = buildKey("user-1", "org-1", "requests");

describe("a saved Logs layout that names the removed columns", () => {
  beforeEach(() => window.localStorage.clear());
  afterEach(() => window.localStorage.clear());

  it("loads with the user's other columns, order and hidden state", () => {
    const config = validateConfig(SAVED);
    expect(config).not.toBeNull();
    expect(config.columns.map((c) => c.id)).toEqual(
      SAVED.columns.map((c) => c.id).filter((id) => !RETIRED.includes(id)),
    );
    expect(config.hidden).toEqual(["builtin:sessionId", "gateway:agentId"]);
  });

  it("reads back from browser storage as that layout, not the default", () => {
    window.localStorage.setItem(KEY, JSON.stringify(SAVED));
    const read = readConfig(KEY);
    expect(read.ok).toBe(true);
    expect(read.config).toEqual(validateConfig(SAVED));
    // The next save writes the layout without the removed ids.
    expect(writeConfig(KEY, read.config).ok).toBe(true);
    expect(window.localStorage.getItem(KEY)).not.toMatch(/costUpstream|costMarkup/);
  });

  it("renders no empty or removed column", () => {
    window.localStorage.setItem(KEY, JSON.stringify(SAVED));
    const { config } = readConfig(KEY);
    const resolved = resolveColumns({
      config,
      declarations: [],
      status: "success",
    });
    const gateway = resolveGatewayColumns(config, { managed: true });
    const columns = orderColumns(config, [
      ...resolved.columns,
      ...gateway.columns,
    ]);
    expect(gateway.entries.map((e) => e.id)).not.toEqual(
      expect.arrayContaining(RETIRED),
    );
    render(
      <RequestTable
        filters={{}}
        setFilter={() => {}}
        setFilters={() => {}}
        onSelectLog={() => {}}
        columns={columns}
      />,
    );
    const [head, ...rows] = within(screen.getByTestId("ag-grid")).getAllByRole(
      "row",
    );
    const headers = within(head)
      .getAllByRole("columnheader")
      .map((th) => th.textContent);
    expect(headers).toEqual([
      "Timestamp",
      "Model",
      "Provider",
      "Application",
      "Service",
      "Status",
      "Latency",
      "Cost",
      "Tokens",
      "Billing mode",
      "Attempts",
    ]);
    expect(headers.every((h) => h.trim().length > 0)).toBe(true);
    // One cell per header in every row: nothing renders in a removed slot.
    rows.forEach((row) =>
      expect(within(row).getAllByRole("cell")).toHaveLength(headers.length),
    );
  });

  it("cannot be given a removed column back", () => {
    const config = validateConfig(SAVED);
    RETIRED.forEach((id) => expect(toggleColumn(config, id)).toBe(config));
  });
});

describe("the Logs export after the columns were removed", () => {
  beforeEach(() => {
    mockGet.mockReset();
    mockGet.mockResolvedValue({ status: 200, data: new Blob(["csv"]) });
    URL.createObjectURL = vi.fn(() => "blob:x");
    URL.revokeObjectURL = vi.fn();
    window.localStorage.clear();
  });
  afterEach(() => {
    delete URL.createObjectURL;
    delete URL.revokeObjectURL;
    window.localStorage.clear();
  });

  // The CSV/JSON file is built by the server from the table's query; the
  // console sends no column choice, so the columns a layout shows cannot
  // change the exported values.
  it("sends the same query whatever the saved layout names", async () => {
    const filters = { status: "error", sort: "-cost", search: "gpt" };
    await downloadLogExport(filters, "csv");
    window.localStorage.setItem(KEY, JSON.stringify(SAVED));
    await downloadLogExport(filters, "csv");
    const [[url, first], [, second]] = mockGet.mock.calls;
    expect(url).toBeTruthy();
    expect(second.params).toEqual(first.params);
    expect(second.responseType).toBe(first.responseType);
    const keys = Object.keys(first.params);
    expect(keys).toContain("export_format");
    expect(keys.filter((k) => /column|field|markup|upstream/i.test(k))).toEqual(
      [],
    );
  });
});
