/* eslint-disable react/prop-types */
import React from "react";

// A stand-in for AgGridReact in MCP tests: each row is a clickable element
// whose cells are the columns' values, formatted and rendered the way the
// real grid would, so a test can read what a user would see.
function cellValue(col, data) {
  if (col.valueGetter) return col.valueGetter({ data });
  return col.field ? data[col.field] : undefined;
}

export function MockAgGrid({
  rowData = [],
  columnDefs = [],
  onRowClicked,
  context,
}) {
  return (
    <div data-testid="ag-grid">
      {rowData.map((data, index) => (
        <div
          role="row"
          key={data.id ?? index}
          tabIndex={0}
          onClick={() => onRowClicked?.({ data })}
          onKeyDown={() => {}}
        >
          {columnDefs.map((col) => {
            const value = cellValue(col, data);
            const formatted = col.valueFormatter
              ? col.valueFormatter({ value, data })
              : value;
            const Renderer = col.cellRenderer;
            return (
              <div
                role="gridcell"
                key={col.colId || col.field || col.headerName}
                data-col={col.colId || col.field}
              >
                {Renderer ? (
                  <Renderer
                    value={value}
                    data={data}
                    valueFormatted={formatted}
                    context={context}
                  />
                ) : (
                  String(formatted ?? "")
                )}
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}

export function columnHeaders(columnDefs) {
  return columnDefs.map((col) => col.headerName);
}
