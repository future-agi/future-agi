import React, { useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Collapse, Stack, Typography } from "@mui/material";
import { FilterButton, useComplexFilterBinding } from "src/components/filter";
import { getRandomId } from "src/utils/utils";
import { isCompleteFilter } from "./leaderboardModel";
import { leaderboardFilterDefinition } from "./leaderboardFilterDefinition";
import { VersionSelect } from "./LeaderboardToolbarMenus";

const DEFAULT_FILTER = {
  column_id: "",
  filter_config: { filter_type: "", filter_op: "", filter_value: "" },
};

const withIds = (filters) =>
  filters.length
    ? filters.map((f) => ({ ...f, id: f.id ?? getRandomId() }))
    : [{ ...DEFAULT_FILTER, id: getRandomId() }];

/**
 * Filters under the toolbar use the shared design-system panel, with the
 * existing row contract. Rows are edited locally; complete rows go to the URL.
 * A2-04: the dataset version is a filter, so it is the panel's first field
 * (`Version: All versions ▾`); set, it also shows as a `v2 ×` chip by
 * `Filter`.
 */
const LeaderboardFilters = ({
  open,
  onClose,
  filters,
  onChange,
  runs,
  metricDefs,
  version = "all",
  versions = [],
  onVersion,
}) => {
  const [rows, setRowsState] = useState(() => withIds(filters));
  const rowsRef = useRef(rows);
  const setRows = (value) => {
    rowsRef.current = value;
    setRowsState(value);
  };
  const definition = useMemo(
    () => leaderboardFilterDefinition(runs, metricDefs),
    [runs, metricDefs],
  );

  // External changes (Clear filters, back/forward) reset the editor.
  const external = JSON.stringify(filters.map(({ id, ...f }) => f));
  useEffect(() => {
    const local = JSON.stringify(
      rows.filter(isCompleteFilter).map(({ id, ...f }) => f),
    );
    if (local !== external) setRows(withIds(filters));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [external]);

  const update = (next) => {
    const value = typeof next === "function" ? next(rowsRef.current) : next;
    setRows(value);
    onChange(value.filter(isCompleteFilter));
  };
  const filterBinding = useComplexFilterBinding({
    filters: rows,
    setFilters: update,
    filterDefinition: definition,
    defaultFilter: DEFAULT_FILTER,
  });

  return (
    <Collapse in={open} unmountOnExit>
      <Box
        sx={{
          mx: 1.5,
          mb: 1,
          p: 1.5,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
        }}
      >
        {onVersion && (
          <Stack
            direction="row"
            alignItems="center"
            gap={1}
            sx={{ mb: 1.25 }}
            data-testid="filter-version"
          >
            <VersionSelect
              value={version}
              versions={versions}
              onChange={onVersion}
            />
            <Typography typography="s3" color="text.secondary">
              {version === "all"
                ? "Runs on any version of this dataset"
                : "Only runs on this version"}
            </Typography>
          </Stack>
        )}
        <FilterButton
          label="Add filter"
          fields={filterBinding.fields}
          value={filterBinding.value}
          onChange={filterBinding.onChange}
        />
        <Stack direction="row" justifyContent="flex-end" gap={1} sx={{ mt: 1 }}>
          <Button
            size="small"
            disabled={!filters.length && version === "all"}
            onClick={() => {
              setRows(withIds([]));
              onChange([]);
              if (version !== "all") onVersion?.("all");
            }}
          >
            Clear filters
          </Button>
          <Button size="small" variant="outlined" onClick={onClose}>
            Done
          </Button>
        </Stack>
      </Box>
    </Collapse>
  );
};

LeaderboardFilters.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  filters: PropTypes.array.isRequired,
  onChange: PropTypes.func.isRequired,
  runs: PropTypes.array.isRequired,
  metricDefs: PropTypes.array.isRequired,
  version: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  versions: PropTypes.array,
  onVersion: PropTypes.func,
};

export default LeaderboardFilters;
