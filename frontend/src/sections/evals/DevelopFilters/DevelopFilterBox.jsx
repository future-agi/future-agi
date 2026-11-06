import PropTypes from "prop-types";
import { Box } from "@mui/material";
import React, { useCallback, useMemo, useState } from "react";
import { FilterPanel } from "src/components/filter";
import { getRandomId } from "src/utils/utils";
import { DefaultFilter } from "./common";
import { columnsToFields, fromPanelValue, toPanelValue } from "./panelAdapter";

/**
 * Evals logs filter. Same props as before; the editing surface is the shared
 * Filter panel, opened below the toolbar.
 */
const DevelopFilterBox = ({
  setDevelopFilterOpen,
  developFilterOpen,
  filters,
  setFilters,
  allColumns,
}) => {
  const [anchorEl, setAnchorEl] = useState(null);
  const fields = useMemo(() => columnsToFields(allColumns), [allColumns]);
  const value = useMemo(() => toPanelValue(filters, fields), [filters, fields]);

  const handleChange = useCallback(
    (rows) => {
      const next = fromPanelValue(rows);
      setFilters(() =>
        next.length ? next : [{ ...DefaultFilter, id: getRandomId() }],
      );
    },
    [setFilters],
  );

  return (
    <>
      <Box ref={setAnchorEl} sx={{ height: 0 }} />
      <FilterPanel
        anchorEl={anchorEl}
        open={Boolean(developFilterOpen && anchorEl)}
        onClose={() => setDevelopFilterOpen(false)}
        fields={fields}
        value={value}
        onChange={handleChange}
      />
    </>
  );
};

DevelopFilterBox.propTypes = {
  setDevelopFilterOpen: PropTypes.func,
  developFilterOpen: PropTypes.bool,
  filters: PropTypes.array,
  setFilters: PropTypes.func,
  allColumns: PropTypes.array,
};

export default DevelopFilterBox;
