/* eslint-disable react/prop-types */
import { Box, IconButton, Typography } from "@mui/material";
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
 */
export default function DataTablePagination({
  page = 0,
  pageSize = 25,
  total = 0,
  onPageChange,
  onPageSizeChange,
  pageSizeOptions = [10, 25, 50],
}) {
  const from = total > 0 ? page * pageSize + 1 : 0;
  const to = Math.min((page + 1) * pageSize, total);
  const isFirstPage = page === 0;
  const isLastPage = (page + 1) * pageSize >= total;

  return (
    <Box
      sx={{
        display: "flex",
        justifyContent: "flex-end",
        alignItems: "center",
        gap: 2,
        py: 1,
        px: 2,
        fontSize: 14,
        color: "text.secondary",
        flexShrink: 0,
        borderTop: "1px solid",
        borderColor: "divider",
      }}
    >
      <Box component="span">
        Rows per page:
        <Box
          component="select"
          aria-label="Rows per page"
          value={pageSize}
          onChange={(e) => onPageSizeChange?.(parseInt(e.target.value, 10))}
          sx={{
            ml: 1,
            border: "none",
            background: "transparent",
            color: "text.primary",
            fontSize: 14,
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
        {total > 0 ? `${from}–${to} of ${total}` : "0 of 0"}
      </Typography>

      <IconButton
        size="small"
        aria-label="Previous page"
        disabled={isFirstPage}
        onClick={() => onPageChange?.(page - 1)}
      >
        <Iconify icon="eva:chevron-left-fill" width={20} />
      </IconButton>
      <IconButton
        size="small"
        aria-label="Next page"
        disabled={isLastPage}
        onClick={() => onPageChange?.(page + 1)}
      >
        <Iconify icon="eva:chevron-right-fill" width={20} />
      </IconButton>
    </Box>
  );
}
