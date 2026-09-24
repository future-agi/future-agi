import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Stack, Typography, Button, Tab, TextField, Popover, MenuItem } from "@mui/material";

import Iconify from "src/components/iconify";
import { SegmentedTabs } from "src/components/tabs/tabs";
import { FilterPanel } from "src/components/filter-panel";
import { SCENARIOS_COPY, SCENARIO_GROUPINGS } from "./scenarios.constants";

// Shared toolbar for both scenario views: search, the group-by axis, the
// platform FilterPanel, an "N of M · N groups hidden" count with a Clear/Show
// all reset, and the Table/List switch. Lifting it to the parent lets filters
// and grouping survive a view change — both views read the same filtered,
// grouped rows.
export default function ScenarioToolbar({
  query, onQueryChange,
  view, onViewChange,
  groupBy, onGroupByChange,
  filterFields, filters, onApplyFilters, filterCount,
  shownCount, totalCount, hiddenCount = 0, onClear,
}) {
  const [filterAnchor, setFilterAnchor] = useState(null);
  const [groupByAnchor, setGroupByAnchor] = useState(null);
  const anyFilter = query.length > 0 || filterCount > 0;
  // "Show all" reads truer than "Clear" when the only thing set is a hidden
  // group; a search or filter present means the reset also drops those.
  const onlyGroupsHidden = hiddenCount > 0 && query.length === 0 && filterCount === 0;
  const activeGrouping = SCENARIO_GROUPINGS.find((g) => g.id === groupBy) || SCENARIO_GROUPINGS[0];
  const hiddenSuffix = hiddenCount > 0
    ? ` · ${hiddenCount} group${hiddenCount === 1 ? "" : "s"} hidden`
    : "";

  return (
    <>
      <Stack
        direction="row" alignItems="center" spacing={1}
        sx={{ px: 2.5, py: 1.25, borderBottom: "1px solid", borderColor: "divider" }}
      >
        <TextField
          size="small"
          value={query}
          onChange={(e) => onQueryChange(e.target.value)}
          placeholder={SCENARIOS_COPY.searchPlaceholder}
          InputProps={{
            sx: { typography: "s2" },
            startAdornment: (
              <Box sx={{ pr: 0.75, pl: 0.25, display: "flex", color: "text.subtitle" }}>
                <Iconify icon="solar:magnifer-linear" width={14} />
              </Box>
            ),
          }}
          sx={{ maxWidth: 380, flex: 1 }}
        />
        <Button
          size="small" variant="outlined"
          onClick={(e) => setGroupByAnchor(e.currentTarget)}
          startIcon={<Iconify icon={activeGrouping.icon} width={14} />}
          endIcon={<Iconify icon="solar:alt-arrow-down-linear" width={12} />}
          sx={{
            typography: "s2", fontWeight: "fontWeightBold", textTransform: "none",
            color: "text.primary", borderColor: "divider",
          }}
        >
          {`${SCENARIOS_COPY.groupByLabel} · ${activeGrouping.label}`}
        </Button>
        <Popover
          open={!!groupByAnchor}
          anchorEl={groupByAnchor}
          onClose={() => setGroupByAnchor(null)}
          anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
          transformOrigin={{ vertical: "top", horizontal: "left" }}
          slotProps={{ paper: { sx: { minWidth: 200, p: 0.5, mt: 0.5 } } }}
        >
          {SCENARIO_GROUPINGS.map((g) => {
            const active = g.id === groupBy;
            return (
              <MenuItem
                key={g.id}
                selected={active}
                onClick={() => { onGroupByChange(g.id); setGroupByAnchor(null); }}
                sx={{ gap: 1, px: 1.25, py: 0.875, borderRadius: 0.75 }}
              >
                <Iconify icon={g.icon} width={14} sx={{ color: active ? "primary.main" : "text.subtitle" }} />
                <Typography sx={{ typography: "s2", flex: 1, fontWeight: active ? "fontWeightBold" : "fontWeightMedium" }}>
                  {g.label}
                </Typography>
                {active && <Iconify icon="eva:checkmark-fill" width={14} sx={{ color: "primary.main" }} />}
              </MenuItem>
            );
          })}
        </Popover>
        <Button
          size="small" variant="outlined"
          onClick={(e) => setFilterAnchor(e.currentTarget)}
          startIcon={<Iconify icon="mage:filter" width={14} />}
          endIcon={<Iconify icon="solar:alt-arrow-down-linear" width={12} />}
          sx={{
            typography: "s2", fontWeight: "fontWeightBold", textTransform: "none",
            color: filterCount ? "primary.main" : "text.primary",
            borderColor: filterCount ? "primary.main" : "divider",
          }}
        >
          {SCENARIOS_COPY.filterLabel}{filterCount ? ` (${filterCount})` : ""}
        </Button>
        {(anyFilter || hiddenCount > 0) && (
          <>
            <Typography sx={{ typography: "s3", color: "text.subtitle", whiteSpace: "nowrap" }}>
              {`${shownCount} of ${totalCount}${hiddenSuffix}`}
            </Typography>
            <Button
              size="small"
              onClick={onClear}
              sx={{ typography: "s3", fontWeight: "fontWeightMedium", color: "text.secondary" }}
            >
              {onlyGroupsHidden ? SCENARIOS_COPY.showAll : SCENARIOS_COPY.clearLabel}
            </Button>
          </>
        )}
        <Box sx={{ flex: 1 }} />
        <SegmentedTabs value={view} onChange={(_, v) => onViewChange(v)} sx={{ flexShrink: 0 }}>
          <Tab value="table" label="Table" />
          <Tab value="list" label="List" />
        </SegmentedTabs>
      </Stack>

      <FilterPanel
        anchorEl={filterAnchor}
        open={!!filterAnchor}
        onClose={() => setFilterAnchor(null)}
        filterFields={filterFields}
        currentFilters={filters}
        onApply={onApplyFilters}
        aiPlaceholder="Ask AI — e.g. 'show me scenarios with the impatient persona'"
        placement="bottom-start"
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
  filterFields: PropTypes.array,
  filters: PropTypes.object,
  onApplyFilters: PropTypes.func,
  filterCount: PropTypes.number,
  shownCount: PropTypes.number,
  totalCount: PropTypes.number,
  hiddenCount: PropTypes.number,
  onClear: PropTypes.func,
};
