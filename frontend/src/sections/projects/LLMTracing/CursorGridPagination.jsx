import PropTypes from "prop-types";
import {
  Box,
  CircularProgress,
  MenuItem,
  PaginationItem,
  Select,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { OBSERVE_LIST_PAGE_SIZE_OPTIONS } from "src/config/runtime_limits";
import { windowedPageNumbers } from "./listPagerState";

// Rendered inside the Back/Next buttons. Defined at module scope, not inline in
// `slots`, so React sees a stable component type and reuses the DOM node. An
// inline arrow here is a new type on every render, which remounts this element
// and — while an ancestor re-renders in a loop — destroys the click target
// between pointerdown and pointerup, so no click event is ever produced.
// `pointerEvents: "none"` keeps the button itself the event target, matching
// how MUI treats the ripple span.
const BackLabel = () => (
  <Box
    display="flex"
    alignItems="center"
    gap={0.5}
    sx={{ pointerEvents: "none" }}
  >
    <Iconify icon="octicon:chevron-left-24" width={18} />
    Back
  </Box>
);

const NextLabel = () => (
  <Box
    display="flex"
    alignItems="center"
    gap={0.5}
    sx={{ pointerEvents: "none" }}
  >
    Next
    <Iconify icon="octicon:chevron-right-24" width={18} />
  </Box>
);

export default function CursorGridPagination({
  disabled = false,
  // "Is the end of the list still unknown?" — distinct from `hasMore`, which
  // answers "can you move forward from here". Consumers holding a pagination
  // frontier pass this; those that do not omit it and keep the old behaviour.
  endUnknown,
  hasMore = false,
  loading = false,
  onPageChange,
  onPageSizeChange,
  page,
  pageSize,
  provenNext = false,
}) {
  const showTrailingEllipsis = endUnknown === undefined ? hasMore : endUnknown;
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

      {/* Rendered from PaginationItem rather than plain buttons so the control
          keeps the metrics it had when it was a MUI <Pagination>: 32px items,
          3px gutters, 4px radius, and the Back/Next text labels. Only the item
          *set* is ours — a window plus ellipses — because a cursor list has no
          total to enumerate. */}
      <Stack direction="row" alignItems="center" sx={{ userSelect: "none" }}>
        <PaginationItem
          type="previous"
          aria-label="Previous page"
          variant="outlined"
          shape="rounded"
          color="primary"
          disabled={disabled || loading || page <= 1}
          onClick={() => onPageChange(page - 1)}
          sx={{ borderRadius: "4px", bgcolor: "background.paper" }}
          slots={{ previous: BackLabel }}
        />

        {windowedPageNumbers({ page, provenNext }).map(
          (pageNumber, index, all) => (
            <Box key={pageNumber} display="contents">
              {index > 0 && pageNumber > all[index - 1] + 1 ? (
                <Box
                  component="span"
                  data-testid="pager-leading-ellipsis"
                  display="contents"
                >
                  <PaginationItem
                    type="start-ellipsis"
                    disabled
                    variant="outlined"
                    shape="rounded"
                    sx={{
                      borderRadius: "4px",
                      bgcolor: "background.paper",
                      userSelect: "none",
                    }}
                  />
                </Box>
              ) : null}
              <PaginationItem
                type="page"
                page={pageNumber}
                aria-label={`Go to page ${pageNumber}`}
                aria-current={pageNumber === page ? "page" : undefined}
                selected={pageNumber === page}
                variant="outlined"
                shape="rounded"
                color="primary"
                disabled={disabled || loading}
                onClick={() => onPageChange(pageNumber)}
                sx={{ borderRadius: "4px", bgcolor: "background.paper" }}
              />
            </Box>
          ),
        )}

        {showTrailingEllipsis ? (
          <Box
            component="span"
            data-testid="pager-trailing-ellipsis"
            display="contents"
          >
            <PaginationItem
              type="end-ellipsis"
              disabled
              variant="outlined"
              shape="rounded"
              sx={{
                borderRadius: "4px",
                bgcolor: "background.paper",
                userSelect: "none",
              }}
            />
          </Box>
        ) : null}

        <PaginationItem
          type="next"
          aria-label="Next page"
          variant="outlined"
          shape="rounded"
          color="primary"
          disabled={disabled || loading || !hasMore}
          onClick={() => onPageChange(page + 1)}
          sx={{ borderRadius: "4px", bgcolor: "background.paper" }}
          slots={{ next: NextLabel }}
        />
      </Stack>
    </Stack>
  );
}

CursorGridPagination.propTypes = {
  disabled: PropTypes.bool,
  endUnknown: PropTypes.bool,
  hasMore: PropTypes.bool,
  loading: PropTypes.bool,
  onPageChange: PropTypes.func.isRequired,
  onPageSizeChange: PropTypes.func.isRequired,
  page: PropTypes.number.isRequired,
  pageSize: PropTypes.number.isRequired,
  provenNext: PropTypes.bool,
};
