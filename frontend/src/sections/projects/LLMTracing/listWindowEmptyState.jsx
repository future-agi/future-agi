import { Button, Stack } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import EmptyContent from "src/components/empty-content";
import DateRangePill from "./DateRangePill";
import {
  describeLastTrace,
  describeListWindow,
  getListWindowBounds,
  isOutsideListWindow,
  showAllTimeDateFilter,
  useProjectLastTrace,
} from "./listWindowState";

// An unfiltered Trace/Spans page that is empty only because of its date
// window says so, names the project's last trace and offers "Show all time".
// A project with nothing traced, or whose activity cannot be read, keeps the
// existing first-run state.

/**
 * Shows `fallback` (the existing empty state) until the read settles, and for
 * good unless the last trace falls outside the window.
 */
export function ListWindowEmptyState({ noun, listWindow, fallback }) {
  const { data: lastTrace } = useProjectLastTrace(listWindow);
  const bounds = getListWindowBounds(listWindow?.dateFilter);
  if (!isOutsideListWindow(lastTrace, bounds)) return fallback;

  const { dateFilter, setDateFilter } = listWindow;
  const { relative, date } = describeLastTrace(lastTrace);
  return (
    <EmptyContent
      icon="solar:calendar-mark-linear"
      title={`No ${noun} ${describeListWindow(bounds, lastTrace)}`}
      description={`This project's last trace was ${relative} (${date}).`}
      action={
        // AG Grid's overlay wrapper is pointer-events: none.
        <Stack
          direction="row"
          spacing={1}
          justifyContent="center"
          alignItems="center"
          style={{ pointerEvents: "auto" }}
        >
          <Button
            size="small"
            variant="contained"
            color="primary"
            onClick={() => setDateFilter((prev) => showAllTimeDateFilter(prev))}
          >
            Show all time
          </Button>
          <DateRangePill
            dateFilter={dateFilter}
            setDateFilter={setDateFilter}
          />
        </Stack>
      }
    />
  );
}

ListWindowEmptyState.propTypes = {
  noun: PropTypes.string.isRequired,
  listWindow: PropTypes.shape({
    projectId: PropTypes.string,
    projectName: PropTypes.string,
    dateFilter: PropTypes.object,
    setDateFilter: PropTypes.func,
  }).isRequired,
  fallback: PropTypes.node,
};
