import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "src/utils/test-utils";
import DataTable from "../DataTable";

const columns = [{ id: "name", accessorKey: "name", header: "Name" }];
const A = { id: "record-a", name: "A" };
const B = { id: "record-b", name: "B" };
const C = { id: "record-c", name: "C" };
const checkbox = (id) => within(document.querySelector(`[data-id="${id}"]`))
  .getByRole("checkbox", { hidden: true });

/* eslint-disable react/prop-types */
function Controlled({ data, scope = "page-1", onChange = () => {}, getRowId, sorting, groups, collapsedGroups }) {
  const [selection, setSelection] = useState({});
  return <DataTable columns={columns} data={data} enableSelection
    rowSelection={selection} selectionScope={scope} getRowId={getRowId} sorting={sorting}
    groups={groups} collapsedGroups={collapsedGroups}
    onRowSelectionChange={(ids, records) => { setSelection(ids); onChange(ids, records); }} />;
}
/* eslint-enable react/prop-types */
afterEach(cleanup);

describe("DataTable stable selection contract", () => {
  it("DT-ID emits record IDs and present records, never positions", () => {
    const onChange = vi.fn();
    render(<Controlled data={[A, B]} onChange={onChange} />);
    fireEvent.click(checkbox(A.id));
    expect(onChange).toHaveBeenLastCalledWith({ [A.id]: true }, [A]);
  });

  it("DT-REORDER retains identity after polling and another checkbox click", () => {
    const onChange = vi.fn();
    const { rerender } = render(<Controlled data={[A, B, C]} onChange={onChange} />);
    fireEvent.click(checkbox(A.id));
    rerender(<Controlled data={[B, A, C]} onChange={onChange} />);
    expect(checkbox(A.id)).toBeChecked();
    expect(checkbox(B.id)).not.toBeChecked();
    fireEvent.click(checkbox(C.id));
    expect(onChange).toHaveBeenLastCalledWith({ [A.id]: true, [C.id]: true }, [A, C]);
  });

  it.each(["page", "search", "filter", "page-size", "scope"])(
    "DT-SCOPE clears on %s changes even with identical cached rows", (scope) => {
      const onChange = vi.fn();
      const { rerender } = render(<Controlled data={[A, B]} onChange={onChange} />);
      fireEvent.click(checkbox(A.id));
      rerender(<Controlled data={[A, B]} scope={scope} onChange={onChange} />);
      expect(checkbox(A.id)).not.toBeChecked();
      expect(onChange).toHaveBeenLastCalledWith({}, []);
    },
  );

  it("DT-SORT clears when the controlled sort changes", () => {
    const { rerender } = render(<Controlled data={[A, B]} />);
    fireEvent.click(checkbox(A.id));
    rerender(<Controlled data={[A, B]} sorting={[{ id: "name", desc: true }]} />);
    expect(checkbox(A.id)).not.toBeChecked();
  });

  it("DT-PRUNE drops absent IDs permanently, not just their checkmarks", () => {
    const onChange = vi.fn();
    const { rerender } = render(<Controlled data={[A, B]} onChange={onChange} />);
    fireEvent.click(checkbox(A.id));
    fireEvent.click(checkbox(B.id));
    rerender(<Controlled data={[B, C]} onChange={onChange} />);
    expect(onChange).toHaveBeenLastCalledWith({ [B.id]: true }, [B]);
    rerender(<Controlled data={[A, B, C]} onChange={onChange} />);
    expect(checkbox(A.id)).not.toBeChecked();
    expect(checkbox(B.id)).toBeChecked();
  });

  it("DT-REFRESH does not clear on equivalent new row objects", () => {
    const { rerender } = render(<Controlled data={[A, B]} />);
    fireEvent.click(checkbox(A.id));
    rerender(<Controlled data={[{ ...A, name: "updated" }, { ...B }]} />);
    expect(checkbox(A.id)).toBeChecked();
    expect(checkbox(B.id)).not.toBeChecked();
  });

  it("DT-CUSTOM supports getRowId and numeric zero", () => {
    const row = { key: 0, name: "zero" };
    const onChange = vi.fn();
    render(<Controlled data={[row]} getRowId={(r) => r.key} onChange={onChange} />);
    fireEvent.click(checkbox(0));
    expect(onChange).toHaveBeenLastCalledWith({ 0: true }, [row]);
  });

  it("DT-ALL selects only explicit current-page IDs", () => {
    const onChange = vi.fn();
    render(<Controlled data={[A, B]} onChange={onChange} />);
    fireEvent.click(screen.getByRole("checkbox", { name: /select all rows/i }));
    expect(onChange).toHaveBeenLastCalledWith({ [A.id]: true, [B.id]: true }, [A, B]);
  });

  it("DT-GROUPS emits record IDs, keeps duplicate copies aligned and never selects a reordered slot", () => {
    const onChange = vi.fn();
    const groups = [
      { key: "first", label: "First", rows: [A, B], count: 2 },
      { key: "second", label: "Second", rows: [A, C], count: 2 },
    ];
    const { rerender } = render(<Controlled data={[A, B, C]} groups={groups} onChange={onChange} />);
    fireEvent.click(checkbox(`first::${A.id}`));
    expect(onChange).toHaveBeenLastCalledWith({ [A.id]: true }, [A]);
    expect(checkbox(`second::${A.id}`)).toBeChecked();
    const reordered = [{ ...groups[0], rows: [B, A] }, groups[1]];
    rerender(<Controlled data={[B, A, C]} groups={reordered} onChange={onChange} />);
    expect(checkbox(`first::${B.id}`)).not.toBeChecked();
    fireEvent.click(checkbox(`second::${C.id}`));
    expect(onChange).toHaveBeenLastCalledWith({ [A.id]: true, [C.id]: true }, [A, C]);
    rerender(<Controlled data={[B, A, C]} groups={reordered} collapsedGroups={["first"]} onChange={onChange} />);
    expect(checkbox(`second::${A.id}`)).toBeChecked();
    fireEvent.click(checkbox(`second::${A.id}`));
    expect(onChange).toHaveBeenLastCalledWith({ [C.id]: true }, [C]);
  });

  it("DT-UNMOUNT clears callback-owned snapshots", () => {
    const onChange = vi.fn();
    const { unmount } = render(<Controlled data={[A]} onChange={onChange} />);
    fireEvent.click(checkbox(A.id));
    unmount();
    expect(onChange).toHaveBeenLastCalledWith({}, []);
  });
});
