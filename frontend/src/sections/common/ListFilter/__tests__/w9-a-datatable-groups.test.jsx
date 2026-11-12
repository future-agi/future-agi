// @vitest-environment jsdom
/* eslint-disable react/prop-types */
// W9 A3 (W9-SCOPE §5): DataTable group rows: full-width header rows in the
// same grid with role="row" and the label, not selectable; a row in two
// groups shows in both and selecting one copy selects the row; collapsing
// hides a group's rows; callers without `groups` are unchanged.
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "src/utils/test-utils";

afterEach(cleanup);
import DataTable from "src/components/data-table/DataTable";
import {
  flattenGroups,
  gridSelection,
  groupHeaderText,
  rowIdsFromGrid,
} from "../groupRows";

const COLS = [
  { id: "name", header: "Name", cell: ({ row }) => row.original.name },
  { id: "rows", header: "Rows", cell: ({ row }) => String(row.original.rows) },
];
const A = { id: "a", name: "Golden", rows: 248, tags: ["golden", "prod"] };
const B = { id: "b", name: "Refunds", rows: 40, tags: ["golden"] };
const C = { id: "c", name: "Scratch", rows: 3, tags: [] };
const GROUPS = [
  { key: "golden", label: "golden", count: 2, below_baseline: 1, rows: [A, B] },
  { key: "prod", label: "prod", count: 1, rows: [A] },
  { key: "", label: "No tag", count: 1, rows: [C] },
];

describe("groupRows (pure)", () => {
  it("flattens with header rows and per-group grid ids", () => {
    const flat = flattenGroups(GROUPS, (r) => r.id, ["prod"]);
    expect(flat.map((r) => r.__group?.label ?? r.__gridId)).toEqual([
      "golden",
      "golden::a",
      "golden::b",
      "prod",
      "No tag",
      "::c",
    ]);
    expect(flat[3].__group.collapsed).toBe(true);
  });

  it("maps selection between row ids and grid copies", () => {
    const flat = flattenGroups(GROUPS, (r) => r.id);
    expect(gridSelection(flat, ["a"])).toEqual(["golden::a", "prod::a"]);
    // Ticking one copy of a selects a.
    expect(rowIdsFromGrid(flat, ["prod::a"], [])).toEqual(["a"]);
    // Unticking one copy of a (b stays) drops a.
    expect(
      rowIdsFromGrid(flat, ["prod::a", "golden::b"], ["a", "b"]).sort(),
    ).toEqual(["b"]);
    // A selected row hidden in a collapsed group stays selected.
    const closed = flattenGroups(GROUPS, (r) => r.id, ["", "golden"]);
    expect(rowIdsFromGrid(closed, [], ["c"])).toEqual(["c"]);
  });

  it("words the header", () => {
    expect(groupHeaderText(GROUPS[0])).toBe(
      "golden · 2 datasets · 1 below baseline",
    );
    expect(groupHeaderText(GROUPS[1])).toBe("prod · 1 dataset");
    expect(groupHeaderText({ label: "Asha Rao", count: 4 }, "run")).toBe(
      "Asha Rao · 4 runs",
    );
  });
});

function Grouped({ onSel, onToggle, initialClosed = [] }) {
  const [closed, setClosed] = useState(initialClosed);
  const [sel, setSel] = useState({});
  return (
    <DataTable
      columns={COLS}
      data={[A, B, C]}
      rowCount={3}
      enableSelection
      rowSelection={sel}
      onRowSelectionChange={(s) => {
        setSel(s);
        onSel?.(s);
      }}
      groups={GROUPS}
      collapsedGroups={closed}
      onToggleGroup={(key) => {
        onToggle?.(key);
        setClosed((c) =>
          c.includes(key) ? c.filter((k) => k !== key) : [...c, key],
        );
      }}
      ariaLabel="Datasets"
    />
  );
}

describe("DataTable with groups", () => {
  it("renders group headers as rows with their label, not selectable", () => {
    render(<Grouped />);
    const headers = screen.getAllByTestId("group-header");
    expect(headers.map((h) => h.textContent)).toEqual([
      "golden · 2 datasets · 1 below baseline",
      "prod · 1 dataset",
      "No tag · 1 dataset",
    ]);
    headers.forEach((h) => {
      const row = h.closest('[role="row"]');
      expect(row).toBeTruthy();
      expect(row.className).toContain("fa-group-row");
      const box = within(row).queryByRole("checkbox");
      if (box) expect(box.disabled).toBe(true);
    });
    // Golden shows in both of its groups.
    expect(screen.getAllByText("Golden")).toHaveLength(2);
  });

  it("ticking one copy of a row selects it in every group", () => {
    const onSel = vi.fn();
    render(<Grouped onSel={onSel} />);
    const golden = screen.getAllByText("Golden")[1].closest('[role="row"]');
    fireEvent.click(within(golden).getByRole("checkbox"));
    expect(onSel).toHaveBeenLastCalledWith({ [A.id]: true });
    screen
      .getAllByText("Golden")
      .forEach((el) =>
        expect(
          within(el.closest('[role="row"]')).getByRole("checkbox").checked,
        ).toBe(true),
      );
  });

  it("closing a group hides its rows and reports the key", () => {
    const onToggle = vi.fn();
    render(<Grouped onToggle={onToggle} />);
    fireEvent.click(screen.getByRole("button", { name: "Close group golden" }));
    expect(onToggle).toHaveBeenCalledWith("golden");
    expect(screen.queryByText("Refunds")).toBeNull();
    expect(screen.getAllByText("Golden")).toHaveLength(1);
    expect(
      screen
        .getByRole("button", { name: "Open group golden" })
        .getAttribute("aria-expanded"),
    ).toBe("false");
  });

  it("without groups the grid is unchanged", () => {
    render(
      <DataTable
        columns={COLS}
        data={[A, B, C]}
        rowCount={3}
        ariaLabel="Plain"
      />,
    );
    expect(screen.queryByTestId("group-header")).toBeNull();
    expect(screen.getAllByText(/Golden|Refunds|Scratch/)).toHaveLength(3);
  });
});

/* eslint-enable react/prop-types */
