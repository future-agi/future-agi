import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { render } from "src/utils/test-utils";
import DataTable from "../DataTable";

const columns = [{ id: "name", accessorKey: "name", header: "Name" }];
const data = [
  { id: "run-1", name: "first" },
  { id: "run-2", name: "second" },
];
const rowEl = (container, id) => container.querySelector(`[data-id="${id}"]`);

describe("DataTable active row", () => {
  const scrollIntoView = vi.fn();
  Element.prototype.scrollIntoView = scrollIntoView;

  afterEach(() => scrollIntoView.mockClear());

  it("highlights the active row and keeps it in view as it moves", () => {
    const { container, rerender } = render(
      <DataTable columns={columns} data={data} activeRowId="run-1" />,
    );
    expect(rowEl(container, "run-1")).toHaveClass("DataTable-activeRow");
    expect(rowEl(container, "run-2")).not.toHaveClass("DataTable-activeRow");

    scrollIntoView.mockClear();
    rerender(<DataTable columns={columns} data={data} activeRowId="run-2" />);
    expect(rowEl(container, "run-2")).toHaveClass("DataTable-activeRow");
    expect(rowEl(container, "run-1")).not.toHaveClass("DataTable-activeRow");
    expect(scrollIntoView).toHaveBeenCalledWith({ block: "nearest" });
  });

  it("highlights nothing without an active row", () => {
    const { container } = render(<DataTable columns={columns} data={data} />);
    expect(rowEl(container, "run-1")).not.toHaveClass("DataTable-activeRow");
    expect(rowEl(container, "run-2")).not.toHaveClass("DataTable-activeRow");
    expect(scrollIntoView).not.toHaveBeenCalled();
  });
});
