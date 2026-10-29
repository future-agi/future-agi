import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Stack, Button, TextField } from "@mui/material";

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
  query, onQueryChange,
  view, onViewChange,
  groupBy, onGroupByChange, groupings = [],
  filterFields, filters, onApplyFilters, filterCount,
  rowHeight = "Short", onRowHeightChange,
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
          role="toolbar" aria-label="Scenarios"
          direction="row" alignItems="center" columnGap={0.5} rowGap={1} flexWrap="wrap"
          sx={{ pb: 1.5 }}
        >
          <TextField
            size="small"
            value={query}
            onChange={(e) => onQueryChange(e.target.value)}
            placeholder={SCENARIOS_COPY.searchPlaceholder}
            InputProps={{
              sx: { typography: "s2", height: 32 },
              startAdornment: (
                <Box sx={{ pr: 0.75, pl: 0.25, display: "flex", color: "text.subtitle" }}>
                  <Iconify icon="solar:magnifer-linear" width={14} />
                </Box>
              ),
            }}
            sx={{
              flex: 1, minWidth: 200, maxWidth: 420, mr: 0.5,
              [SEARCH_OWN_ROW]: {
                flexBasis: "100%", maxWidth: "none", mr: 0,
              },
            }}
          />
          <Button
            onClick={(e) => setFilterAnchor(e.currentTarget)}
            startIcon={<Iconify icon="mdi:filter-outline" width={16} />}
            sx={{
              ...scenarioToolbarButtonSx,
              ...(filterCount > 0 && { color: "primary.main", bgcolor: "action.selected" }),
            }}
          >
            {SCENARIOS_COPY.filterLabel}{filterCount ? ` (${filterCount})` : ""}
          </Button>
          <Button
            aria-haspopup="dialog"
            aria-expanded={!!displayAnchor}
            onClick={(e) => setDisplayAnchor(e.currentTarget)}
            startIcon={<Iconify icon="mdi:tune-vertical" width={16} />}
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
          <Stack direction="row" alignItems="center" spacing={1} sx={{ ml: "auto" }}>
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
  actions: PropTypes.node,
};
