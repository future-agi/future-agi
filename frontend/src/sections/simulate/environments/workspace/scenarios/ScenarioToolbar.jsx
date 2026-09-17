import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Stack, Typography, Button, Tab, TextField } from "@mui/material";

import Iconify from "src/components/iconify";
import { SegmentedTabs } from "src/components/tabs/tabs";
import UseCaseFilterPopover from "./UseCaseFilterPopover";
import { SCENARIOS_COPY } from "./scenarios.constants";
import { USE_CASE_SHAPE } from "./scenarios.shapes";

// Shared toolbar for both scenario views: search, the use-case filter, an
// "N of M" count with a Clear reset, and the Table/List switch. Lifting it to
// the parent lets filters survive a view change — both views read the same
// filtered rows.
export default function ScenarioToolbar({
  query, onQueryChange,
  view, onViewChange,
  allUseCases, countBy, selectedUseCases, onUseCasesChange,
  shownCount, totalCount, onClear,
}) {
  const [filterAnchor, setFilterAnchor] = useState(null);
  const anyFilter = query.length > 0 || selectedUseCases.length > 0;

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
          onClick={(e) => setFilterAnchor(e.currentTarget)}
          startIcon={<Iconify icon="mage:filter" width={14} />}
          endIcon={<Iconify icon="solar:alt-arrow-down-linear" width={12} />}
          sx={{
            typography: "s2", fontWeight: "fontWeightBold", textTransform: "none",
            color: selectedUseCases.length ? "primary.main" : "text.primary",
            borderColor: selectedUseCases.length ? "primary.main" : "divider",
          }}
        >
          {SCENARIOS_COPY.filterLabel}{selectedUseCases.length ? ` (${selectedUseCases.length})` : ""}
        </Button>
        {anyFilter && (
          <>
            <Typography sx={{ typography: "s3", color: "text.subtitle", whiteSpace: "nowrap" }}>
              {`${shownCount} of ${totalCount}`}
            </Typography>
            <Button
              size="small"
              onClick={onClear}
              sx={{ typography: "s3", fontWeight: "fontWeightMedium", color: "text.secondary" }}
            >
              {SCENARIOS_COPY.clearLabel}
            </Button>
          </>
        )}
        <Box sx={{ flex: 1 }} />
        <SegmentedTabs value={view} onChange={(_, v) => onViewChange(v)} sx={{ flexShrink: 0 }}>
          <Tab value="table" label="Table" />
          <Tab value="list" label="List" />
        </SegmentedTabs>
      </Stack>

      <UseCaseFilterPopover
        anchorEl={filterAnchor}
        onClose={() => setFilterAnchor(null)}
        allUseCases={allUseCases}
        countBy={countBy}
        selected={selectedUseCases}
        onChange={onUseCasesChange}
      />
    </>
  );
}

ScenarioToolbar.propTypes = {
  query: PropTypes.string.isRequired,
  onQueryChange: PropTypes.func.isRequired,
  view: PropTypes.string.isRequired,
  onViewChange: PropTypes.func.isRequired,
  allUseCases: PropTypes.arrayOf(USE_CASE_SHAPE),
  countBy: PropTypes.func,
  selectedUseCases: PropTypes.arrayOf(PropTypes.string),
  onUseCasesChange: PropTypes.func,
  shownCount: PropTypes.number,
  totalCount: PropTypes.number,
  onClear: PropTypes.func,
};
