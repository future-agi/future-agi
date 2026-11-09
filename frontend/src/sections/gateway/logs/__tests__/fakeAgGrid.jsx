/* eslint-disable react/prop-types */
// A stand-in for ag-grid-react in jsdom tests: it renders the column defs as a
// plain table (header names, each row's cellRenderer or formatted value) so a
// test can read cells by role, click a row, and click a sortable header to
// fire onSortChanged with the grid's api shape. Use it with
//
//   vi.mock("ag-grid-react", () => import("./__tests__/fakeAgGrid"));
//
// `grid.props` holds the last props the page passed to the grid;
// `mountedGrids()` lists the latest props of every grid on the page (a page
// can show more than one).
import React, {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
} from "react";

export const grid = { props: null, sortModel: [], mounted: new Set() };

export const mountedGrids = () => [...grid.mounted].map((entry) => entry.props);

function cellContent(colDef, data) {
  const value = colDef.valueGetter
    ? colDef.valueGetter({ data, colDef })
    : colDef.field
      ? data?.[colDef.field]
      : undefined;
  const params = { data, value, colDef };
  if (colDef.cellRenderer) {
    const Renderer = colDef.cellRenderer;
    return <Renderer {...params} />;
  }
  if (colDef.valueFormatter) return colDef.valueFormatter(params);
  return value == null ? "" : String(value);
}

function fakeApi(columnDefs) {
  return {
    getColumnState: () =>
      columnDefs.map((c) => ({
        colId: c.colId || c.field,
        sort: grid.sortModel.find((s) => s.colId === (c.colId || c.field))
          ?.sort,
      })),
    applyColumnState: () => {},
  };
}

export const AgGridReact = forwardRef(function FakeAgGrid(props, ref) {
  grid.props = props;
  const self = useRef({ props });
  self.current.props = props;
  useEffect(() => {
    const entry = self.current;
    grid.mounted.add(entry);
    return () => grid.mounted.delete(entry);
  }, []);
  const columnDefs = (props.columnDefs || []).filter((c) => !c.hide);
  const api = fakeApi(columnDefs);
  useImperativeHandle(ref, () => ({ api }), [api]);

  const sortBy = (colDef) => {
    const colId = colDef.colId || colDef.field;
    const current = colDef.sort;
    const next = current === "desc" ? "asc" : "desc";
    grid.sortModel = [{ colId, sort: next }];
    props.onSortChanged?.({ api, source: "uiColumnSorted", columns: [] });
  };

  return (
    <table data-testid="ag-grid" aria-busy={props.loading ? "true" : "false"}>
      <thead>
        <tr>
          {columnDefs.map((colDef) => {
            const colId = colDef.colId || colDef.field;
            const sortable = colDef.sortable !== false && colDef.sortable;
            return (
              <th
                key={colId}
                data-col-id={colId}
                aria-sort={
                  colDef.sort === "desc"
                    ? "descending"
                    : colDef.sort === "asc"
                      ? "ascending"
                      : undefined
                }
              >
                {colDef.headerComponent ? (
                  <colDef.headerComponent
                    displayName={colDef.headerName}
                    {...(colDef.headerComponentParams || {})}
                  />
                ) : (
                  colDef.headerName
                )}
                {sortable && (
                  <button
                    type="button"
                    aria-label={`Sort by ${colDef.headerName}`}
                    onClick={() => sortBy(colDef)}
                  />
                )}
              </th>
            );
          })}
        </tr>
      </thead>
      <tbody>
        {(props.rowData || []).map((data, rowIndex) => (
          <tr
            key={props.getRowId ? props.getRowId({ data }) : rowIndex}
            data-row-id={props.getRowId ? props.getRowId({ data }) : undefined}
            className={
              props.getRowClass ? props.getRowClass({ data }) : undefined
            }
            onClick={(event) =>
              props.onRowClicked?.({ data, rowIndex, event: event.nativeEvent })
            }
          >
            {columnDefs.map((colDef) => (
              <td key={colDef.colId || colDef.field}>
                {cellContent(colDef, data)}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
});
