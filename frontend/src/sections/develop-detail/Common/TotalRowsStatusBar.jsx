import { Box, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React, { useCallback, useEffect, useState, useRef } from "react";

const TotalRowsStatusBar = ({ api }) => {
  const [totalCount, setTotalCount] = useState(0);
  const [visibleRange, setVisibleRange] = useState({ start: 0, end: 0 });
  const [isLoading, setIsLoading] = useState(true);

  // Use ref to track if component is mounted to avoid state updates on unmounted component
  const isMountedRef = useRef(true);

  // Keep track of last valid range to avoid showing 0 during fast scrolling
  const lastValidRangeRef = useRef({ start: 0, end: 0 });

  /**
   * Get the actual visible row range based on scroll position
   * This shows users exactly which rows they're viewing (e.g., "Viewing: 45-65 of 1000 rows")
   */
  const getVisibleRowRange = useCallback(() => {
    if (!api) return { start: 0, end: 0, hasActualData: false };

    // Use newer API methods (getFirstDisplayedRowIndex was introduced in v31.1)
    // Falls back to getFirstDisplayedRow for older versions
    const firstIndex =
      (api.getFirstDisplayedRowIndex
        ? api.getFirstDisplayedRowIndex()
        : api.getFirstDisplayedRow()) ?? 0;
    const lastIndex =
      (api.getLastDisplayedRowIndex
        ? api.getLastDisplayedRowIndex()
        : api.getLastDisplayedRow()) ?? 0;

    // Count actual loaded (non-stub) rows in the visible range
    let actualFirstLoaded = -1;
    let actualLastLoaded = -1;
    let hasActualData = false;

    // Find first loaded row (skip stubs/loading rows)
    for (let i = firstIndex; i <= lastIndex; i++) {
      const rowNode = api.getDisplayedRowAtIndex(i);
      if (rowNode && rowNode.data && !rowNode.stub) {
        if (actualFirstLoaded === -1) {
          actualFirstLoaded = i;
        }
        actualLastLoaded = i;
        hasActualData = true;
      }
    }

    // If no actual data found, return last valid range instead of zeros
    if (!hasActualData) {
      return {
        start: lastValidRangeRef.current.start,
        end: lastValidRangeRef.current.end,
        hasActualData: false,
      };
    }

    // Update last valid range
    const newRange = {
      start: actualFirstLoaded + 1, // +1 for human-readable (1-based indexing)
      end: actualLastLoaded + 1,
      hasActualData: true,
    };

    lastValidRangeRef.current = { start: newRange.start, end: newRange.end };

    return newRange;
  }, [api]);

  /**
   * Update status bar with current scroll position
   * Throttled via modelUpdated event which fires on scroll/filter/sort
   */
  const updateStatusBar = useCallback(() => {
    if (!api || !isMountedRef.current) return;

    // Get total row count
    const total = api.totalRowCount ?? api.getDisplayedRowCount();
    if (api?.totalRowCount !== undefined) {
      setIsLoading(false);
    }

    // Get visible range based on scroll position
    const range = getVisibleRowRange();

    setTotalCount(total);
    setVisibleRange(range);
  }, [api, getVisibleRowRange]);

  // Set up event listeners
  useEffect(() => {
    if (!api) return;

    // Reset mounted ref to true when effect runs
    isMountedRef.current = true;

    // Initial update
    updateStatusBar();

    // Listen to grid events that affect visible rows
    const events = [
      "modelUpdated", // Fires on data changes, filtering, sorting
      "bodyScroll", // Fires on scroll - gives real-time feedback
      "viewportChanged", // Fires when viewport changes
      "firstDataRendered",
    ];

    events.forEach((event) => {
      api.addEventListener(event, updateStatusBar);
    });

    return () => {
      isMountedRef.current = false;
      if (!api.isDestroyed()) {
        events.forEach((event) => {
          api.removeEventListener(event, updateStatusBar);
        });
      }
    };
  }, [api, updateStatusBar]);

  // Handle edge cases for display
  const getDisplayText = () => {
    const isLowerBound = api?.totalRowCountIsLowerBound === true;
    // Only an exact total may cap the visible range. A proven lower bound can
    // legitimately be smaller than rows already loaded on a later page.
    const end = isLowerBound
      ? visibleRange.end
      : Math.min(visibleRange.end, totalCount);
    const totalLabel = `${totalCount.toLocaleString()}${isLowerBound ? "+" : ""}`;

    // All rows visible (small dataset or no virtualization)
    if (!isLowerBound && visibleRange.start === 1 && end === totalCount) {
      return `Viewing: ${totalCount}/${totalCount} ${totalCount === 1 ? "row" : "rows"}`;
    }

    // Viewing a range of rows (typical case with scrolling)
    return `Viewing: ${end}/${totalLabel} rows`;
  };
  if (isLoading) {
    return;
  }

  return (
    <Box sx={{ padding: 1 }}>
      <Typography typography="s2" color="text.primary">
        {getDisplayText()}
      </Typography>
    </Box>
  );
};

TotalRowsStatusBar.propTypes = {
  api: PropTypes.object,
};

export default TotalRowsStatusBar;
