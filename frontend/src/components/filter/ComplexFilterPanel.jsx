/**
 * ComplexFilterPanel — drop-in replacement for <ComplexFilter /> that edits
 * the same `filters` state through the shared Filter panel.
 *
 * Callers keep their `filters` / `setFilters` / `filterDefinition` /
 * `defaultFilter`; only the editing surface changes. The panel opens as a
 * popover on `anchorEl` instead of expanding inline.
 */
import PropTypes from "prop-types";
import React, { useCallback, useMemo } from "react";
import FilterPanel from "./FilterPanel";
import {
  buildFilterLeaves,
  fromPanelValue,
  leavesToFields,
  toPanelValue,
} from "./complexFilterAdapter";

export const useComplexFilterBinding = ({
  filters,
  setFilters,
  filterDefinition,
  defaultFilter,
}) => {
  const leaves = useMemo(
    () => buildFilterLeaves(filterDefinition, filters),
    [filterDefinition, filters],
  );
  const fields = useMemo(() => leavesToFields(leaves), [leaves]);
  const value = useMemo(() => toPanelValue(filters, leaves), [filters, leaves]);
  const onChange = useCallback(
    (rows) => {
      // Callers pass either a state setter or an updater-only function.
      const next = fromPanelValue(rows || [], leaves, { defaultFilter });
      setFilters(() => next);
    },
    [setFilters, leaves, defaultFilter],
  );
  return { fields, value, onChange, count: value.length };
};

const ComplexFilterPanel = ({
  anchorEl,
  open,
  onClose,
  filters,
  setFilters,
  filterDefinition,
  defaultFilter,
  ...panelProps
}) => {
  const { fields, value, onChange } = useComplexFilterBinding({
    filters,
    setFilters,
    filterDefinition,
    defaultFilter,
  });
  return (
    <FilterPanel
      anchorEl={anchorEl}
      open={open}
      onClose={onClose}
      fields={fields}
      value={value}
      onChange={onChange}
      {...panelProps}
    />
  );
};

ComplexFilterPanel.propTypes = {
  anchorEl: PropTypes.any,
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  filters: PropTypes.array.isRequired,
  setFilters: PropTypes.func.isRequired,
  filterDefinition: PropTypes.array.isRequired,
  defaultFilter: PropTypes.object.isRequired,
};

export default ComplexFilterPanel;
