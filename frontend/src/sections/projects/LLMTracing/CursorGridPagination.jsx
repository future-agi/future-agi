import PropTypes from "prop-types";
import {
  Box,
  Button,
  CircularProgress,
  IconButton,
  MenuItem,
  Select,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { OBSERVE_LIST_PAGE_SIZE_OPTIONS } from "src/config/runtime_limits";
import { windowedPageNumbers } from "./listPagerState";

export default function CursorGridPagination({
  disabled = false,
  hasMore = false,
  loading = false,
  onPageChange,
  onPageSizeChange,
  page,
  pageSize,
  provenNext = false,
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

      <Stack direction="row" alignItems="center" gap={0.5}>
        <IconButton
          size="small"
          aria-label="Previous page"
          disabled={disabled || loading || page <= 1}
          onClick={() => onPageChange(page - 1)}
        >
          <Iconify icon="octicon:chevron-left-24" width={18} />
        </IconButton>

        {windowedPageNumbers({ page, provenNext }).map((pageNumber, index, all) => (
          <Stack key={pageNumber} direction="row" alignItems="center" gap={0.5}>
            {index > 0 && pageNumber > all[index - 1] + 1 ? (
              <Typography
                aria-hidden="true"
                typography="s2"
                color="text.disabled"
                data-testid="pager-leading-ellipsis"
              >
                …
              </Typography>
            ) : null}
            <Button
              size="small"
              aria-label={`Go to page ${pageNumber}`}
              aria-current={pageNumber === page ? "page" : undefined}
              variant={pageNumber === page ? "contained" : "outlined"}
              color="primary"
              disabled={disabled || loading}
              onClick={() => onPageChange(pageNumber)}
              sx={{ minWidth: 36, height: 36, borderRadius: "4px" }}
            >
              {pageNumber}
            </Button>
          </Stack>
        ))}

        {hasMore ? (
          <Typography
            aria-hidden="true"
            typography="s2"
            color="text.disabled"
            data-testid="pager-trailing-ellipsis"
          >
            …
          </Typography>
        ) : null}

        <IconButton
          size="small"
          aria-label="Next page"
          disabled={disabled || loading || !hasMore}
          onClick={() => onPageChange(page + 1)}
        >
          <Iconify icon="octicon:chevron-right-24" width={18} />
        </IconButton>
      </Stack>
    </Stack>
  );
}

CursorGridPagination.propTypes = {
  disabled: PropTypes.bool,
  hasMore: PropTypes.bool,
  loading: PropTypes.bool,
  onPageChange: PropTypes.func.isRequired,
  onPageSizeChange: PropTypes.func.isRequired,
  page: PropTypes.number.isRequired,
  pageSize: PropTypes.number.isRequired,
  provenNext: PropTypes.bool,
};
