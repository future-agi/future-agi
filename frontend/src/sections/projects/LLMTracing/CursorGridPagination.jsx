import PropTypes from "prop-types";
import {
  Box,
  CircularProgress,
  MenuItem,
  Pagination,
  PaginationItem,
  Select,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { OBSERVE_LIST_PAGE_SIZE_OPTIONS } from "src/config/runtime_limits";

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
    <Stack
      direction="row"
      alignItems="center"
      justifyContent="space-between"
      sx={{
        minHeight: 56,
        p: 1,
        borderTop: "1px solid var(--border-default)",
        flexShrink: 0,
      }}
    >
      <Stack gap={1} direction="row" alignItems="center">
        <Typography
          typography="s2"
          color="text.primary"
          fontWeight="fontWeightRegular"
        >
          Results per page
        </Typography>
        <Select
          size="small"
          aria-label="Results per page"
          value={pageSize}
          disabled={disabled}
          onChange={(event) => onPageSizeChange(Number(event.target.value))}
          sx={{ height: 36, bgcolor: "background.paper" }}
        >
          {OBSERVE_LIST_PAGE_SIZE_OPTIONS.map((size) => (
            <MenuItem key={size} value={size}>
              {size}
            </MenuItem>
          ))}
        </Select>
      </Stack>

      <Box sx={{ flex: 1, display: "flex", justifyContent: "center" }}>
        {loading ? (
          <Stack
            role="status"
            aria-live="polite"
            direction="row"
            alignItems="center"
            gap={1}
          >
            <CircularProgress size={16} />
            <Typography typography="s2" color="text.secondary">
              Loading page…
            </Typography>
          </Stack>
        ) : null}
      </Box>

      <Pagination
        count={pageCount}
        variant="outlined"
        shape="rounded"
        page={Math.min(page, pageCount)}
        color="primary"
        disabled={disabled || loading}
        onChange={(_event, value) => onPageChange(value)}
        renderItem={(item) => (
          <PaginationItem
            {...item}
            sx={{ borderRadius: "4px", bgcolor: "background.paper" }}
            slots={{
              previous: () => (
                <Box display="flex" alignItems="center" gap={0.5}>
                  <Iconify icon="octicon:chevron-left-24" width={18} />
                  Back
                </Box>
              ),
              next: () => (
                <Box display="flex" alignItems="center" gap={0.5}>
                  Next
                  <Iconify icon="octicon:chevron-right-24" width={18} />
                </Box>
              ),
            }}
          />
        )}
      />
    </Stack>
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
