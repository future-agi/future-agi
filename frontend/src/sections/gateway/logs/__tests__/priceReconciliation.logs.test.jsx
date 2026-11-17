import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import RequestTable from "../RequestTable";
import { BUILTIN_COLUMNS } from "../columns/columnModel";
import { GATEWAY_COLUMNS } from "../columns/gatewayColumns";
import { CREDITS, OWN_KEY, creditsLog, ownKeyLog } from "./priceFixtures";

// Price reconciliation, Logs table: a request's Cost cell is the one number
// the org pays on its route (credits: the charged amount; own key: the
// provider's price). No column splits a credits charge into provider cost
// and margin.

vi.mock("ag-grid-react", () => import("./fakeAgGrid"));
vi.mock("../../shell/page-status", async (importOriginal) => ({
  ...(await importOriginal()),
  isGatewayPageHidden: () => false,
}));

const logs = vi.hoisted(() => ({ rows: [] }));
vi.mock("../hooks/useRequestLogs", () => ({
  default: () => ({
    data: { results: logs.rows, count: logs.rows.length },
    isLoading: false,
    error: null,
    refetch: () => {},
  }),
}));

// Every column the Columns picker can offer, Agent & billing included.
const EVERY_COLUMN = [...BUILTIN_COLUMNS, ...GATEWAY_COLUMNS];

function renderTable(rows) {
  logs.rows = rows;
  render(
    <RequestTable
      filters={{}}
      setFilter={() => {}}
      setFilters={() => {}}
      onSelectLog={() => {}}
      columns={EVERY_COLUMN}
    />,
  );
  const [head, ...body] = within(screen.getByTestId("ag-grid")).getAllByRole(
    "row",
  );
  const headers = within(head)
    .getAllByRole("columnheader")
    .map((th) => th.textContent);
  const cellsOf = (row) =>
    within(row)
      .getAllByRole("cell")
      .map((td) => td.textContent);
  return { headers, rows: body.map(cellsOf) };
}

describe("Logs table price reconciliation", () => {
  it("shows a credits request's charged amount, never its provider cost or margin", () => {
    const { headers, rows } = renderTable([creditsLog()]);
    const cost = headers.indexOf("Cost");
    expect(cost).toBeGreaterThan(-1);
    expect(rows[0][cost]).toBe(CREDITS.charged);
    expect(headers).not.toContain("Upstream cost");
    expect(headers).not.toContain("Markup");
    expect(rows[0]).not.toContain(CREDITS.upstream);
    expect(rows[0]).not.toContain(CREDITS.margin);
  });

  it("shows an own-key request's provider price", () => {
    const { headers, rows } = renderTable([ownKeyLog()]);
    expect(rows[0][headers.indexOf("Cost")]).toBe(OWN_KEY.charged);
    // One number per request: the price is not repeated in a second column.
    expect(rows[0].filter((text) => text === OWN_KEY.charged)).toHaveLength(1);
  });

  it("lists both routes with one cost each", () => {
    const { headers, rows } = renderTable([creditsLog(), ownKeyLog()]);
    const cost = headers.indexOf("Cost");
    expect(rows.map((cells) => cells[cost])).toEqual([
      CREDITS.charged,
      OWN_KEY.charged,
    ]);
    expect(headers.filter((h) => /cost|markup/i.test(h))).toEqual(["Cost"]);
  });
});
