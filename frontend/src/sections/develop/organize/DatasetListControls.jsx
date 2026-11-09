// The dataset list's Filter and Group by controls and the chips row (W9-
// SCOPE §5 A2, A3): the generic ListFilter with the dataset fields the
// list answers (plus one per metadata key, typed by `metadataTypes`, the
// query's `metadata_types`), facet counts in value menus,
// and the left-out sentence under the chips. The host owns the state
// (URL keys `f` and `group`) and the query.
import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { MenuItem, TextField } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import ListFilterButton from "src/sections/common/ListFilter/ListFilterButton";
import FilterChips from "src/sections/common/ListFilter/FilterChips";
import {
  GROUP_HINT,
  groupOptions,
  labelOfFor,
  leftOutNotes,
  listFields,
} from "./datasetListView";

export function DatasetListControls({
  filters,
  onFilters,
  group,
  onGroup,
  facets,
  metadataTypes,
}) {
  const fields = useMemo(
    () => listFields(facets, metadataTypes),
    [facets, metadataTypes],
  );
  const labelOf = useMemo(() => labelOfFor(facets), [facets]);
  const options = groupOptions(fields);
  return (
    <>
      <ListFilterButton
        title="Filter datasets"
        fields={fields}
        filters={filters}
        onChange={onFilters}
        facets={facets}
        labelOf={labelOf}
      />
      <CustomTooltip show title={GROUP_HINT} size="small">
        <TextField
          select
          size="small"
          label="Group by"
          value={options.some((o) => o.id === group) ? group : ""}
          onChange={(e) => onGroup(e.target.value)}
          sx={{
            minWidth: 140,
            "& .MuiOutlinedInput-root": { height: 34, typography: "s2" },
          }}
        >
          {options.map((o) => (
            <MenuItem
              key={o.id || "none"}
              value={o.id}
              sx={{ typography: "s2" }}
            >
              {o.label}
            </MenuItem>
          ))}
        </TextField>
      </CustomTooltip>
    </>
  );
}

DatasetListControls.propTypes = {
  filters: PropTypes.array.isRequired,
  onFilters: PropTypes.func.isRequired,
  group: PropTypes.string,
  onGroup: PropTypes.func.isRequired,
  facets: PropTypes.object,
  metadataTypes: PropTypes.object,
};

export function DatasetFilterChips({
  filters,
  onFilters,
  facets,
  metadataTypes,
  leftOut,
}) {
  const fields = useMemo(
    () => listFields(facets, metadataTypes),
    [facets, metadataTypes],
  );
  const labelOf = useMemo(() => labelOfFor(facets), [facets]);
  if (!filters?.length) return null;
  return (
    <FilterChips
      filters={filters}
      fields={fields}
      onChange={onFilters}
      labelOf={labelOf}
      notes={leftOutNotes(leftOut, fields)}
    />
  );
}

DatasetFilterChips.propTypes = {
  filters: PropTypes.array,
  onFilters: PropTypes.func.isRequired,
  facets: PropTypes.object,
  metadataTypes: PropTypes.object,
  leftOut: PropTypes.object,
};
