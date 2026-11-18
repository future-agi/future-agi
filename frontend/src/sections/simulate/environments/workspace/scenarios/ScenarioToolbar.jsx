import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Stack,
  Button,
  MenuItem,
  TextField,
  Typography,
} from "@mui/material";

import Iconify from "src/components/iconify";
import { FilterPanel } from "src/components/filter-panel";
import { SCENARIOS_COPY, scenarioToolbarButtonSx } from "./scenarios.constants";
import DisplayPanel from "./DisplayPanel";

// One row holds everything until the search would shrink below ~200px; under
// that the search takes the whole first row and the controls sit beneath it.
// Set where the selection bar also needs two rows, so selecting never moves
// the table.
const SEARCH_OWN_ROW = "@container (max-width: 599px)";

// Shared toolbar for both scenario views: search, the platform FilterPanel and a
// Display panel holding the view, the group-by axis and the row height. Lifting
// it to the parent lets filters and grouping survive a view change — both views
// read the same filtered, grouped rows. The "N of M · Clear" readout sits with
// the results, in the list's footer, not here.
export default function ScenarioToolbar({
  query,
  onQueryChange,
  view,
  onViewChange,
  groupBy,
  onGroupByChange,
  groupings = [],
  filterFields,
  filters,
  onApplyFilters,
  filterCount,
  rowHeight = "Short",
  onRowHeightChange,
  proofCounts = null,
  lifecycleFilter = "",
  onLifecycleFilterChange,
  versionPair = "",
  versionPairs = [],
  onVersionPairChange,
  actions,
}) {
  const [filterAnchor, setFilterAnchor] = useState(null);
  const [displayAnchor, setDisplayAnchor] = useState(null);

  return (
    <>
      {/* Sized by its own width, not the window's: the builder panel beside
          it changes the room it has. Narrow, the search takes the first row
          and the controls wrap beneath it, the actions staying right-aligned. */}
      <Box sx={{ containerType: "inline-size" }}>
        <Stack
          role="toolbar"
          aria-label="Scenarios"
          direction="row"
          alignItems="center"
          columnGap={0.5}
          rowGap={1}
          flexWrap="wrap"
          sx={{ pb: 1.5 }}
        >
          <TextField
            size="small"
            value={query}
            onChange={(e) => onQueryChange(e.target.value)}
            placeholder={SCENARIOS_COPY.searchPlaceholder}
            InputProps={{
              startAdornment: (
                <Box
                  sx={{
                    pr: 0.75,
                    pl: 0.25,
                    display: "flex",
                    color: "text.subtitle",
                  }}
                >
                  <Iconify icon="solar:magnifer-linear" width={14} />
                </Box>
              ),
            }}
            sx={{
              flex: 1,
              minWidth: 200,
              maxWidth: 420,
              mr: 0.5,
              [SEARCH_OWN_ROW]: {
                flexBasis: "100%",
                maxWidth: "none",
                mr: 0,
              },
            }}
          />
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            onClick={(e) => setFilterAnchor(e.currentTarget)}
            startIcon={<Iconify icon="solar:filter-linear" width={16} />}
            sx={scenarioToolbarButtonSx}
          >
            {SCENARIOS_COPY.filterLabel}
            {filterCount ? ` (${filterCount})` : ""}
          </Button>
          {onVersionPairChange && versionPairs.length > 0 ? (
            <TextField
              select
              size="small"
              value={versionPair}
              onChange={(e) => onVersionPairChange(e.target.value)}
              inputProps={{ "data-testid": "versions-select" }}
              SelectProps={{
                displayEmpty: true,
                renderValue: (value) =>
                  `${SCENARIOS_COPY.versionsLabel}: ${
                    versionPairs.find((one) => one.value === value)?.label ??
                    SCENARIOS_COPY.versionsDefault
                  }`,
                SelectDisplayProps: {
                  "aria-label": SCENARIOS_COPY.versionsLabel,
                },
              }}
              sx={{
                minWidth: 190,
                "& .MuiSelect-select": { py: 0.5, typography: "s2" },
              }}
            >
              <MenuItem value="">{SCENARIOS_COPY.versionsDefault}</MenuItem>
              {versionPairs.map((one) => (
                <MenuItem key={one.value} value={one.value}>
                  {one.label}
                </MenuItem>
              ))}
            </TextField>
          ) : null}
          {onLifecycleFilterChange ? (
            <TextField
              select
              size="small"
              value={lifecycleFilter}
              onChange={(e) => onLifecycleFilterChange(e.target.value)}
              inputProps={{ "data-testid": "lifecycle-filter" }}
              // No floating label: the value reads "Lifecycle: All" in the toolbar row itself.
              SelectProps={{
                displayEmpty: true,
                renderValue: (value) =>
                  `${SCENARIOS_COPY.lifecycleFilterLabel}: ${
                    SCENARIOS_COPY.lifecycleFilters.find(
                      (one) => one.value === value,
                    )?.label ?? ""
                  }`,
                SelectDisplayProps: {
                  "aria-label": SCENARIOS_COPY.lifecycleFilterLabel,
                },
              }}
              sx={{
                minWidth: 170,
                "& .MuiSelect-select": { py: 0.5, typography: "s2" },
              }}
            >
              {SCENARIOS_COPY.lifecycleFilters.map((one) => (
                <MenuItem key={one.value || "all"} value={one.value}>
                  {one.label}
                </MenuItem>
              ))}
            </TextField>
          ) : null}
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            aria-haspopup="dialog"
            aria-expanded={!!displayAnchor}
            onClick={(e) => setDisplayAnchor(e.currentTarget)}
            startIcon={<Iconify icon="solar:tuning-2-linear" width={16} />}
            sx={scenarioToolbarButtonSx}
          >
            {SCENARIOS_COPY.displayLabel}
          </Button>
          <DisplayPanel
            anchorEl={displayAnchor}
            onClose={() => setDisplayAnchor(null)}
            view={view}
            onViewChange={onViewChange}
            groupBy={groupBy}
            onGroupByChange={onGroupByChange}
            groupings={groupings}
            rowHeight={rowHeight}
            onRowHeightChange={onRowHeightChange}
          />
          {proofCounts ? (
            <Typography
              variant="caption"
              color="text.secondary"
              data-testid="scenario-proof-counts"
              sx={{ ml: 1, whiteSpace: "nowrap" }}
            >
              {SCENARIOS_COPY.proofCounts(proofCounts)}
            </Typography>
          ) : null}
          <Stack
            direction="row"
            alignItems="center"
            spacing={1}
            sx={{ ml: "auto" }}
          >
            {actions}
          </Stack>
        </Stack>
      </Box>

      <FilterPanel
        anchorEl={filterAnchor}
        open={!!filterAnchor}
        onClose={() => setFilterAnchor(null)}
        filterFields={filterFields}
        currentFilters={filters}
        onApply={onApplyFilters}
        placement="bottom-start"
        // The grounded AI filter has no `scenarios` source in model-hub yet
        // (README §8), so hide the AI box here — Basic + Query still work.
        showAiFilter={false}
      />
    </>
  );
}

ScenarioToolbar.propTypes = {
  query: PropTypes.string.isRequired,
  onQueryChange: PropTypes.func.isRequired,
  view: PropTypes.string.isRequired,
  onViewChange: PropTypes.func.isRequired,
  groupBy: PropTypes.string,
  onGroupByChange: PropTypes.func,
  groupings: PropTypes.arrayOf(
    PropTypes.shape({ value: PropTypes.string, label: PropTypes.string }),
  ),
  filterFields: PropTypes.array,
  filters: PropTypes.object,
  onApplyFilters: PropTypes.func,
  filterCount: PropTypes.number,
  rowHeight: PropTypes.string,
  onRowHeightChange: PropTypes.func,
  proofCounts: PropTypes.shape({
    proved: PropTypes.number,
    kept_as_written: PropTypes.number,
    not_proved: PropTypes.number,
  }),
  lifecycleFilter: PropTypes.string,
  onLifecycleFilterChange: PropTypes.func,
  versionPair: PropTypes.string,
  versionPairs: PropTypes.arrayOf(
    PropTypes.shape({ value: PropTypes.string, label: PropTypes.string }),
  ),
  onVersionPairChange: PropTypes.func,
  actions: PropTypes.node,
};
