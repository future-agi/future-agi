import { Box } from "@mui/material";
import PropTypes from "prop-types";
import React, { useCallback, useMemo, useState } from "react";
import { FilterPanel } from "src/components/filter";
import {
  blankDatasetFilter,
  fromPanelValue,
  propertiesToFields,
  toPanelValue,
} from "./panelAdapter";

/**
 * Model dataset filter. Same props as before; the editing surface is the
 * shared Filter panel, opened below the toolbar.
 */
const DatasetFilter = ({
  filterOpen,
  setFilterOpen,
  filters,
  properties,
  setFilters,
  datasetOptions,
}) => {
  const [anchorEl, setAnchorEl] = useState(null);
  const fields = useMemo(
    () => propertiesToFields(properties, datasetOptions),
    [properties, datasetOptions],
  );
  const value = useMemo(() => toPanelValue(filters, fields), [filters, fields]);
  const handleChange = useCallback(
    (rows) => {
      const next = fromPanelValue(rows, fields);
      setFilters(() => (next.length ? next : [blankDatasetFilter()]));
    },
    [fields, setFilters],
  );

  return (
    <>
      <Box ref={setAnchorEl} sx={{ height: 0 }} />
      <FilterPanel
        anchorEl={anchorEl}
        open={Boolean(filterOpen && anchorEl)}
        onClose={() => setFilterOpen?.(false)}
        fields={fields}
        value={value}
        onChange={handleChange}
      />
    </>
  );
};

DatasetFilter.propTypes = {
  filterOpen: PropTypes.bool,
  setFilterOpen: PropTypes.func,
  properties: PropTypes.array,
  filters: PropTypes.array,
  setFilters: PropTypes.func,
  datasetOptions: PropTypes.object,
  addFilter: PropTypes.func,
  removeFilter: PropTypes.func,
};

export default DatasetFilter;
