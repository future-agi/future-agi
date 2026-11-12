/* eslint-disable react/prop-types */
import React, { forwardRef } from "react";

// A stand-in for AG Grid in jsdom: renders the rows as a plain table through
// the page's own column definitions (cellRenderer, valueFormatter,
// valueGetter, field), so a test reads what a user would see in each cell.
function cellValue(col, data) {
  if (col.valueGetter) return col.valueGetter({ data, colDef: col });
  return col.field ? data?.[col.field] : undefined;
}

// The last props a page passed to the grid, for column checks.
// eslint-disable-next-line react-refresh/only-export-components
export const lastGrid = { props: null };

export const AgGridReact = forwardRef(function MockGrid(props, _ref) {
  lastGrid.props = props;
  const { rowData, columnDefs = [], loading, onRowClicked, context } = props;
  if (loading) return <div data-testid="ag-grid-loading">Loading</div>;
  const cols = columnDefs.filter((c) => !c.hide);
  return (
    <table data-testid="ag-grid">
      <thead>
        <tr>
          {cols.map((c) => (
            <th
              key={c.colId || c.field || c.headerName}
              data-col={c.colId || c.field}
              data-pinned={c.pinned || ""}
            >
              {c.headerName}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {(rowData || []).map((data, i) => (
          <tr
            key={data.id ?? i}
            data-testid="ag-row"
            onClick={() => onRowClicked?.({ data })}
          >
            {cols.map((c) => {
              const value = cellValue(c, data);
              let content;
              if (c.cellRenderer) {
                const Renderer = c.cellRenderer;
                content = (
                  <Renderer data={data} value={value} context={context} />
                );
              } else if (c.valueFormatter) {
                content = c.valueFormatter({ value, data });
              } else {
                content = value == null ? "" : String(value);
              }
              return (
                <td key={c.colId || c.field || c.headerName}>{content}</td>
              );
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );
});
