import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { DataTable } from "src/components/data-table";
import DataTablePagination from "src/components/data-table/DataTablePagination";
import { EMPTY_MESSAGE, ROW_HEIGHT } from "./myEnvironments.constants";
import { buildEnvironmentColumns } from "./components/environmentTableColumns";
import RowActionsMenu from "./components/RowActionsMenu";
import DeleteEnvironmentDialog from "./components/DeleteEnvironmentDialog";

// Same DataTable the platform uses on Evals, Datasets and Agents. `rows` is a
// single server page; the pager is driven by the server `total`, not a
// client-side slice, so it can walk past the first page of results.
export default function MyEnvironmentsTable({
  rows = [],
  total = 0,
  page = 0,
  pageSize = 25,
  onPageChange,
  onPageSizeChange,
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

  return (
    <>
      <DataTable
        columns={columns}
        data={rows}
        isLoading={isLoading}
        rowCount={total}
        getRowId={(row) => row.id}
        onRowClick={(row) => onOpen?.(row)}
        rowHeight={ROW_HEIGHT}
        emptyMessage={EMPTY_MESSAGE}
      />
      {total > 0 && (
        <DataTablePagination
          page={page}
          pageSize={pageSize}
          total={total}
          onPageChange={onPageChange}
          onPageSizeChange={onPageSizeChange}
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
  total: PropTypes.number,
  page: PropTypes.number,
  pageSize: PropTypes.number,
  onPageChange: PropTypes.func,
  onPageSizeChange: PropTypes.func,
  isLoading: PropTypes.bool,
  onOpen: PropTypes.func,
  onRun: PropTypes.func,
  onDelete: PropTypes.func,
};
