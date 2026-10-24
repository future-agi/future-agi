/* eslint-disable react/prop-types */
import { Box, CircularProgress, IconButton, Typography } from "@mui/material";
import Iconify from "src/components/iconify";

/**
 * Pagination footer for DataTable.
 *
 * Props:
 *  page        — 0-indexed current page
 *  pageSize    — rows per page
 *  total       — total row count from server
 *  onPageChange     — (newPage) => void
 *  onPageSizeChange — (newSize) => void
 *  pageSizeOptions  — [10, 25, 50]
 *
 * Optional:
 *  pageCount — known page count when the server has no exact total (cursor
 *              paging). Replaces "from–to of total" with "Page N of M".
 *  disabled  — freeze every control (a page is in flight).
 *  loading   — show a "Loading page…" status next to the controls.
 *  leading   — node pinned to the left edge (e.g. a Stop button).
 */
export default function DataTablePagination({
  page = 0,
  pageSize = 25,
  total = 0,
  onPageChange,
  onPageSizeChange,
  pageSizeOptions = [10, 25, 50],
  pageCount,
  disabled = false,
  loading = false,
  leading,
}) {
  const from = total > 0 ? page * pageSize + 1 : 0;
  const to = Math.min((page + 1) * pageSize, total);
  const isFirstPage = page === 0;
  const isLastPage =
    pageCount != null ? page + 1 >= pageCount : (page + 1) * pageSize >= total;
  const summary =
    pageCount != null
      ? `Page ${page + 1} of ${pageCount}`
      : total > 0
        ? `${from}–${to} of ${total}`
        : "0 of 0";

  return (
    <Box
      sx={{
        display: "flex",
        justifyContent: "flex-end",
        alignItems: "center",
        gap: 2,
        minHeight: 44,
        py: 1,
        px: 2,
        fontSize: 13,
        color: "text.secondary",
        flexShrink: 0,
        borderTop: "1px solid",
        borderColor: "divider",
      }}
    >
      {leading}
      {loading ? (
        <Box
          role="status"
          aria-live="polite"
          sx={{ display: "flex", alignItems: "center", gap: 1, mr: "auto" }}
        >
          <CircularProgress size={14} />
          <Typography variant="body2" color="text.secondary">
            Loading page…
          </Typography>
        </Box>
      ) : (
        <Box sx={{ mr: "auto" }} />
      )}
      <Box component="span">
        Rows per page:
        <Box
          component="select"
          value={pageSize}
          disabled={disabled}
          aria-label="Rows per page"
          onChange={(e) => onPageSizeChange?.(parseInt(e.target.value, 10))}
          sx={{
            ml: 1,
            border: "none",
            background: "transparent",
            color: "text.primary",
            fontSize: 13,
            cursor: "pointer",
            outline: "none",
          }}
        >
          {pageSizeOptions.map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </Box>
      </Box>

      <Typography variant="body2" color="text.secondary">
        {summary}
      </Typography>

      <IconButton
        size="small"
        disabled={disabled || isFirstPage}
        onClick={() => onPageChange?.(page - 1)}
        aria-label="Previous page"
      >
        <Iconify icon="solar:alt-arrow-left-linear" width={20} />
      </IconButton>
      <IconButton
        size="small"
        disabled={disabled || isLastPage}
        onClick={() => onPageChange?.(page + 1)}
        aria-label="Next page"
      >
        <Iconify icon="solar:alt-arrow-right-linear" width={20} />
      </IconButton>
    </Box>
  );
}
