// A right-aligned (numeric) column's header reads `⋮ Rows`: its column menu
// comes before the label, so the label ends where the column's values end.
// Every other header keeps `Name ⋮`.
import React from "react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { cleanup, render, screen, within } from "src/utils/test-utils";
import useColumnLayout from "src/sections/common/ux/oneview/useColumnLayout";
import { useLayoutStore } from "src/sections/common/ux/oneview/layoutPrefs";
import DataTable from "../DataTable";

const CATALOG = [
  { id: "name", label: "Name", locked: true },
  { id: "rows", label: "Rows" },
];
const COLUMNS = [
  { id: "name", accessorKey: "name", header: "Name", meta: { flex: 2 } },
  {
    id: "rows",
    accessorKey: "rows",
    header: "Rows",
    size: 112,
    headerAlign: "right",
    align: "right",
  },
];
const DATA = [{ id: "d-hello", name: "hello", rows: 5 }];

function Table() {
  const layout = useColumnLayout({
    surface: "dataTableHeaderAlignTest",
    columns: CATALOG,
    commands: false,
  });
  return (
    <DataTable
      ariaLabel="Datasets"
      columns={COLUMNS}
      data={DATA}
      rowCount={1}
      layout={layout}
      headerMenu
    />
  );
}

/** The column menu button and the label of a column's header. */
const headerParts = (label) => {
  const button = screen.getByRole("button", { name: `Column menu: ${label}` });
  const header = button.closest('[role="columnheader"]');
  return { button, text: within(header).getByText(label) };
};
const comesBefore = (a, b) =>
  Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);

beforeEach(() => {
  window.localStorage.clear();
  useLayoutStore.setState({ byKey: {} });
});
afterEach(cleanup);

describe("DataTable header menu placement", () => {
  it("puts a numeric header's menu before its label and others after", () => {
    render(<Table />);
    const rows = headerParts("Rows");
    expect(comesBefore(rows.button, rows.text)).toBe(true);
    const name = headerParts("Name");
    expect(comesBefore(name.text, name.button)).toBe(true);
  });
});
