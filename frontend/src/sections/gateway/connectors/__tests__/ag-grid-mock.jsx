import React from "react";
import PropTypes from "prop-types";

// A stand-in for ag-grid-react in jsdom (which has no layout for AG Grid's
// virtual rows): a plain table that renders every visible column the way
// the grid would, through its cellRenderer or valueGetter, and reports row
// clicks and Enter on a row as the grid does.
function cellValue(column, data) {
  if (column.valueGetter) return column.valueGetter({ data });
  return column.field ? data[column.field] : undefined;
}

function Cell({ column, data }) {
  const value = cellValue(column, data);
  if (column.cellRenderer) {
    const Renderer = column.cellRenderer;
    return <Renderer data={data} value={value} />;
  }
  if (column.valueFormatter) return column.valueFormatter({ data, value });
  return value ?? "";
}

Cell.propTypes = { column: PropTypes.object, data: PropTypes.object };

export function AgGridReact({
  rowData,
  columnDefs,
  getRowId,
  onRowClicked,
  onCellKeyDown,
}) {
  const columns = columnDefs.filter((column) => !column.hide);
  return (
    <table data-testid="ag-grid-mock">
      <thead>
        <tr>
          {columns.map((column) => (
            <th key={column.colId}>{column.headerName}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {(rowData || []).map((data) => (
          <tr
            key={getRowId ? getRowId({ data }) : data.id}
            data-testid={`row-${data.id}`}
            onClick={(event) => onRowClicked?.({ data, event })}
            onKeyDown={(event) => onCellKeyDown?.({ data, event })}
          >
            {columns.map((column) => (
              <td key={column.colId} data-col={column.colId}>
                <Cell column={column} data={data} />
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

AgGridReact.propTypes = {
  rowData: PropTypes.array,
  columnDefs: PropTypes.array.isRequired,
  getRowId: PropTypes.func,
  onRowClicked: PropTypes.func,
  onCellKeyDown: PropTypes.func,
};
