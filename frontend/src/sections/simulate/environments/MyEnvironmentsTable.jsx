import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { DataTable } from "src/components/data-table";
import DataTablePagination from "src/components/data-table/DataTablePagination";
import {
  EMPTY_MESSAGE,
  ROW_HEIGHT,
  DEFAULT_PAGE_SIZE,
} from "./myEnvironments.constants";
import { buildEnvironmentColumns } from "./components/environmentTableColumns";
import RowActionsMenu from "./components/RowActionsMenu";
import DeleteEnvironmentDialog from "./components/DeleteEnvironmentDialog";

// Same DataTable the platform uses on Evals, Datasets and Agents.
export default function MyEnvironmentsTable({
  rows = [],
  isLoading = false,
  onOpen,
  onRun,
  onDelete,
}) {
  const [menuFor, setMenuFor] = useState(null); /* { row, anchorEl } */
  const [confirmDelete, setConfirmDelete] = useState(null); /* row */

  const columns = useMemo(
    () =>
      buildEnvironmentColumns({
        onRowActions: (row, anchorEl) => setMenuFor({ row, anchorEl }),
      }),
    [],
  );

  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const [page, setPage] = useState(0);
  const pageCount = Math.max(1, Math.ceil(rows.length / pageSize));
  const currentPage = Math.min(page, pageCount - 1);
  const start = currentPage * pageSize;
  const pageRows = rows.slice(start, start + pageSize);

  return (
    <>
      <DataTable
        columns={columns}
        data={pageRows}
        isLoading={isLoading}
        rowCount={rows.length}
        getRowId={(row) => row.id}
        onRowClick={(row) => onOpen?.(row)}
        rowHeight={ROW_HEIGHT}
        emptyMessage={EMPTY_MESSAGE}
      />
      {rows.length > 0 && (
        <DataTablePagination
          page={currentPage}
          pageSize={pageSize}
          total={rows.length}
          onPageChange={setPage}
          onPageSizeChange={(n) => {
            setPageSize(n);
            setPage(0);
          }}
        />
      )}

      <RowActionsMenu
        menuFor={menuFor}
        onClose={() => setMenuFor(null)}
        onOpen={onOpen}
        onRun={onRun}
        onDeleteRequest={setConfirmDelete}
      />

      <DeleteEnvironmentDialog
        env={confirmDelete}
        onCancel={() => setConfirmDelete(null)}
        onConfirm={() => {
          onDelete?.(confirmDelete);
          setConfirmDelete(null);
        }}
      />
    </>
  );
}

MyEnvironmentsTable.propTypes = {
  rows: PropTypes.array,
  isLoading: PropTypes.bool,
  onOpen: PropTypes.func,
  onRun: PropTypes.func,
  onDelete: PropTypes.func,
};
