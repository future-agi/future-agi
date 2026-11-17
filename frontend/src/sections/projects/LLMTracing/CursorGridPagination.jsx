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
    <Iconify icon="solar:alt-arrow-left-linear" width={18} />
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
    <Iconify icon="solar:alt-arrow-right-linear" width={18} />
  </Box>
);

const isCount = (value) => Number.isSafeInteger(value) && value >= 0;

// Where the visible page sits in the list: "26–50", plus " of 57" only when
// the caller passes an exact total the list API reported. Returns null — and
// nothing is drawn — while the rows on this page are unknown (a page not yet
// published, or a caller that does not pass them) or the page is empty, so the
// footer never shows a guessed range.
const visibleRangeLabel = ({ page, pageSize, rowCount, totalCount }) => {
  if (!isCount(rowCount) || rowCount === 0) return null;
  if (!Number.isSafeInteger(page) || page < 1) return null;
  if (!Number.isSafeInteger(pageSize) || pageSize < 1) return null;
  const first = (page - 1) * pageSize + 1;
  const last = first + Math.min(rowCount, pageSize) - 1;
  const range = `${first.toLocaleString()}–${last.toLocaleString()}`;
  // A total below the last row on screen is stale; print the range alone
  // rather than "26–50 of 40".
  return isCount(totalCount) && totalCount >= last
    ? `${range} of ${totalCount.toLocaleString()}`
    : range;
};

// Cursor-paged grids know a page count but usually not an exact total: a
// windowed, numbered pager ("Go to page N", with an ellipsis past the furthest
// page reached), beside the range of rows on screen ("1–25", "1–25 of 57").
export default function CursorGridPagination({
  disabled = false,
  // "Is the end of the list still unknown?" — distinct from `hasMore`, which
  // answers "can you move forward from here". Consumers holding a pagination
  // frontier pass this; those that do not omit it and keep the old behaviour.
  endUnknown,
  // The deepest page this pagination generation has ever reached (the
  // monotone "frontier"), pre-gated by the caller on `canReachPage` so it is
  // 0 (no boundary) whenever the route back to that page no longer exists.
  // Drawn as a right-hand boundary mirroring page 1's left-hand one.
  furthestPage = 0,
  hasMore = false,
  loading = false,
  onPageChange,
  onPageSizeChange,
  page,
  pageSize,
  provenNext = false,
  // Rows on the page now on screen, once that page has been published. Omit
  // (or pass null) while unknown; the range is then not drawn.
  rowCount = null,
  // The list's exact total, passed only when the list API reported one (not a
  // lower bound). Omit (or pass null) otherwise; the range then has no "of N".
  totalCount = null,
}) {
  const showTrailingEllipsis = endUnknown === undefined ? hasMore : endUnknown;
  const { pages: pageNumbers, boundaryPage } = windowedPageNumbers({
    page,
    provenNext,
    furthestPage,
  });
  const rangeLabel = visibleRangeLabel({
    page,
    pageSize,
    rowCount,
    totalCount,
  });
  return (
    <Stack
      direction="row"
      alignItems="center"
      justifyContent="space-between"
      sx={{
        minHeight: 44,
        py: 1,
        px: 2,
        borderTop: "1px solid",
        borderColor: "divider",
        flexShrink: 0,
      }}
    >
      <Stack gap={1} direction="row" alignItems="center">
        <Typography
          typography="s2"
          color="text.secondary"
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
          sx={{ bgcolor: "background.paper" }}
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

      {rangeLabel ? (
        <Typography
          data-testid="pager-range"
          typography="s2"
          color="text.secondary"
          fontWeight="fontWeightRegular"
          sx={{
            mr: 1.5,
            flexShrink: 0,
            whiteSpace: "nowrap",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {rangeLabel}
        </Typography>
      ) : null}

      {/* Rendered from PaginationItem rather than plain buttons so the control
          keeps the metrics it had when it was a MUI <Pagination>: 32px items,
          3px gutters, 4px radius, and the Back/Next text labels. Only the item
          *set* is ours — a window plus ellipses, capped at five numbers once
          the furthest-visited-page boundary is drawn — because a cursor list
          has no total to enumerate. */}
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

        {pageNumbers.map((pageNumber, index, all) => (
          <Box key={pageNumber} display="contents">
            {index > 0 && pageNumber > all[index - 1] + 1 ? (
              <Box
                component="span"
                data-testid={
                  pageNumber === boundaryPage
                    ? "pager-boundary-ellipsis"
                    : "pager-leading-ellipsis"
                }
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
        ))}

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
  furthestPage: PropTypes.number,
  hasMore: PropTypes.bool,
  loading: PropTypes.bool,
  onPageChange: PropTypes.func.isRequired,
  onPageSizeChange: PropTypes.func.isRequired,
  page: PropTypes.number.isRequired,
  pageSize: PropTypes.number.isRequired,
  provenNext: PropTypes.bool,
  rowCount: PropTypes.number,
  totalCount: PropTypes.number,
};
