/**
 * ObjectFilterPanel — the shared Filter panel for screens that keep their
 * filters as an object of value lists (`{ status: ["failed"], owner_not: [...] }`)
 * and describe their fields as `{ value, label, type, choices, ... }`.
 * Same props and same `onApply` payload as the panel it replaces.
 */
import PropTypes from "prop-types";
import React, { useCallback, useMemo } from "react";
import FilterPanel from "./FilterPanel";
import {
  fromPanelValue,
  objectFieldsToFields,
  toPanelValue,
} from "./objectFilterAdapter";

const ObjectFilterPanel = ({
  anchorEl,
  open,
  onClose,
  filterFields,
  currentFilters,
  onApply,
  aiPlaceholder,
  width,
  basicOnly = false,
  placement = "bottom-start",
}) => {
  const fields = useMemo(
    () => objectFieldsToFields(filterFields),
    [filterFields],
  );
  const value = useMemo(
    () => toPanelValue(currentFilters, filterFields),
    [currentFilters, filterFields],
  );
  const handleChange = useCallback(
    (rows) => onApply(fromPanelValue(rows, filterFields, currentFilters)),
    [onApply, filterFields, currentFilters],
  );

  return (
    <FilterPanel
      anchorEl={anchorEl}
      open={open}
      onClose={onClose}
      fields={fields}
      value={value}
      onChange={handleChange}
      showAi={!basicOnly}
      showQueryTab={!basicOnly}
      aiPlaceholder={aiPlaceholder}
      placement={placement}
      panelWidth={width}
    />
  );
};

ObjectFilterPanel.propTypes = {
  anchorEl: PropTypes.any,
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  filterFields: PropTypes.arrayOf(
    PropTypes.shape({
      value: PropTypes.string.isRequired,
      label: PropTypes.string.isRequired,
      type: PropTypes.oneOf(["string", "enum"]).isRequired,
      choices: PropTypes.array,
      choiceLabels: PropTypes.object,
      single: PropTypes.bool,
      operators: PropTypes.arrayOf(PropTypes.string),
    }),
  ).isRequired,
  currentFilters: PropTypes.oneOfType([PropTypes.object, PropTypes.array]),
  onApply: PropTypes.func.isRequired,
  aiPlaceholder: PropTypes.string,
  width: PropTypes.number,
  basicOnly: PropTypes.bool,
  placement: PropTypes.oneOf(["bottom-start", "bottom-end"]),
};

export default ObjectFilterPanel;
