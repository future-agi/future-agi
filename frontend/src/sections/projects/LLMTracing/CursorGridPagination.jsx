import PropTypes from "prop-types";
import DataTablePagination from "src/components/data-table/DataTablePagination";
import { OBSERVE_LIST_PAGE_SIZE_OPTIONS } from "src/config/runtime_limits";

// Cursor-paged grids know a page count but not an exact total, so the shared
// footer shows "Page N of M". Pages are 1-indexed here, 0-indexed there.
export default function CursorGridPagination({
  disabled = false,
  loading = false,
  onPageChange,
  onPageSizeChange,
  page,
  pageCount,
  pageSize,
}) {
  return (
    <DataTablePagination
      page={Math.min(page, pageCount) - 1}
      pageCount={pageCount}
      pageSize={pageSize}
      pageSizeOptions={OBSERVE_LIST_PAGE_SIZE_OPTIONS}
      disabled={disabled || loading}
      loading={loading}
      onPageChange={(p) => onPageChange(p + 1)}
      onPageSizeChange={onPageSizeChange}
    />
  );
}

CursorGridPagination.propTypes = {
  disabled: PropTypes.bool,
  loading: PropTypes.bool,
  onPageChange: PropTypes.func.isRequired,
  onPageSizeChange: PropTypes.func.isRequired,
  page: PropTypes.number.isRequired,
  pageCount: PropTypes.number.isRequired,
  pageSize: PropTypes.number.isRequired,
};
