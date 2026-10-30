import React, { useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Badge, Box, Button, Stack } from "@mui/material";
import Iconify from "src/components/iconify";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import DateRangePill from "./LLMTracing/DateRangePill";
import FilterChips from "./LLMTracing/FilterChips";
import TraceFilterPanel from "./LLMTracing/TraceFilterPanel";
import { pillSx } from "./LLMTracing/toolbarStyles";
import {
  apiFiltersToPanelRows,
  fieldName,
  filterConfigFor,
  panelRowsToApiFilters,
} from "./observeFilterConfig";
import {
  CUSTOM,
  customRangeLabel,
  makeScope,
  throughLastDay,
  withDate,
  withFilters,
} from "./observeScope";

const NO_FILTERS = [];

/**
 * The date and filters of an Observe view, for a page that asks about its
 * rows: the views' own date pill, filter panel and chips, configured for the
 * unit, and for users the search text. Every change hands back the whole
 * scope, relabelled.
 */
export default function ScopeFilterBar({
  projectId,
  unit,
  scope,
  onChange,
  disabled = false,
  dateLabel,
}) {
  const config = filterConfigFor(unit);
  const filterButtonRef = useRef(null);
  // The panel opens at the Filter button, a chip or the chips' "+"; it is
  // mounted only while open, so its catalog is read only when it is used.
  const [panelAnchor, setPanelAnchor] = useState(null);
  const filters = scope?.filters || NO_FILTERS;
  const panelRows = useMemo(() => apiFiltersToPanelRows(filters), [filters]);
  const chips = useMemo(
    () =>
      filters.map((filter) => ({
        ...filter,
        display_name:
          filter.display_name || fieldName(unit, filter.column_id) || undefined,
      })),
    [filters, unit],
  );
  // The pill's custom-range picker takes Dates.
  const dateFilter = useMemo(
    () => ({
      dateFilter: (scope?.window || []).map((value) => new Date(value)),
      dateOption: scope?.dateOption,
    }),
    [scope?.window, scope?.dateOption],
  );

  const setDateFilter = (update) => {
    const next = typeof update === "function" ? update(dateFilter) : update;
    if (next?.dateFilter?.length !== 2) return;
    const window =
      next.dateOption === CUSTOM
        ? throughLastDay(next.dateFilter)
        : next.dateFilter;
    onChange(withDate(scope, next.dateOption, window));
  };
  const setFilters = (next) => onChange(withFilters(scope, next));

  // The box keeps what is typed, spaces included; the scope keeps it
  // trimmed, and a search set elsewhere (a hand-over, a unit switch) shows.
  const [searchText, setSearchText] = useState(scope?.search || "");
  useEffect(() => {
    setSearchText((typed) =>
      typed.trim() === (scope?.search || "") ? typed : scope?.search || "",
    );
  }, [scope?.search]);
  const setSearch = (text) => {
    setSearchText(text);
    if (text.trim() !== (scope?.search || ""))
      onChange(makeScope({ ...scope, search: text }));
  };

  return (
    <Box
      aria-disabled={disabled || undefined}
      sx={disabled ? { pointerEvents: "none", opacity: 0.6 } : { minWidth: 0 }}
    >
      <Stack direction="row" spacing={1} alignItems="center">
        <DateRangePill
          dateFilter={dateFilter}
          setDateFilter={setDateFilter}
          label={
            dateLabel ||
            (scope?.dateOption === CUSTOM
              ? customRangeLabel(scope?.window)
              : undefined)
          }
          sx={{ ...pillSx, height: 28 }}
        />
        <Button
          ref={filterButtonRef}
          variant="outlined"
          size="small"
          disabled={disabled}
          startIcon={
            filters.length ? (
              <Badge variant="dot" color="error" overlap="circular">
                <Iconify icon="mdi:filter-outline" width={16} />
              </Badge>
            ) : (
              <Iconify icon="mdi:filter-outline" width={16} />
            )
          }
          onClick={() => setPanelAnchor(filterButtonRef.current)}
          sx={{
            ...pillSx,
            height: 28,
            bgcolor: panelAnchor ? "action.hover" : "background.paper",
          }}
        >
          Filter
        </Button>
        {unit === "users" && (
          <FormSearchField
            searchQuery={searchText}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search users"
            disabled={disabled}
            inputProps={{ "aria-label": "Search users", maxLength: 200 }}
            sx={{
              width: 200,
              "& .MuiInputBase-root": { height: 28, fontSize: 13 },
            }}
          />
        )}
      </Stack>
      {chips.length > 0 && (
        <Box
          sx={{
            mt: 1,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
            overflow: "hidden",
            "& > div": { borderBottom: 0, px: 1 },
          }}
        >
          <FilterChips
            extraFilters={chips}
            onRemoveFilter={(index) => {
              // Chips are keyed by index, so a removal invalidates a
              // chip-anchored panel.
              setPanelAnchor(null);
              setFilters(filters.filter((_, i) => i !== index));
            }}
            onClearAll={() => {
              setPanelAnchor(null);
              setFilters([]);
            }}
            onAddFilter={(anchorEl) =>
              setPanelAnchor(anchorEl || filterButtonRef.current)
            }
            onChipClick={(_index, anchorEl) =>
              setPanelAnchor(anchorEl || filterButtonRef.current)
            }
          />
        </Box>
      )}
      {panelAnchor && (
        <TraceFilterPanel
          anchorEl={panelAnchor}
          open
          onClose={() => setPanelAnchor(null)}
          currentFilters={panelRows}
          onApply={(rows) =>
            setFilters(rows?.length ? panelRowsToApiFilters(rows) : [])
          }
          filterFields={config.filterFields}
          tab={config.tab}
          isSpansView={config.isSpansView}
          source={config.source}
          propertyNamespace={config.propertyNamespace}
          attributeSource={config.attributeSource}
          projectId={projectId}
        />
      )}
    </Box>
  );
}

ScopeFilterBar.propTypes = {
  projectId: PropTypes.string,
  unit: PropTypes.oneOf(["traces", "spans", "sessions", "users"]).isRequired,
  scope: PropTypes.shape({
    filters: PropTypes.array,
    dateOption: PropTypes.string,
    window: PropTypes.array,
    label: PropTypes.string,
    search: PropTypes.string,
  }).isRequired,
  onChange: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
  // Names a date the presets don't (a legacy report's "Past 24 hours").
  dateLabel: PropTypes.string,
};
