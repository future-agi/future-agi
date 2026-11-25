/**
 * FilterPanel — the one filter panel used by every list, table and chart.
 *
 * It is the Observe tracing panel (Ask AI, Basic tab, Query tab) driven by a
 * field schema instead of the tracing property catalog:
 *
 *   <FilterPanel
 *     anchorEl={anchorEl}
 *     open={open}
 *     onClose={() => setAnchorEl(null)}
 *     fields={[{ id: "status", label: "Status", type: "enum", options: [...] }]}
 *     value={rows}            // [{ field, operator, value, ... }]
 *     onChange={setRows}      // called with the new rows, or null when empty
 *   />
 *
 * Filters apply as they change; there is no Apply button. Ask AI is off unless
 * `showAi` is set, because the screen needs a backend that understands it.
 */
import PropTypes from "prop-types";
import React, { useMemo } from "react";
import TraceFilterPanel from "src/sections/projects/LLMTracing/TraceFilterPanel";
import {
  fieldsToCategories,
  fieldsToProperties,
  SHARED_FILTER_SOURCE,
} from "./fieldSchema";

const FilterPanel = ({
  anchorEl,
  open,
  onClose,
  fields,
  value,
  onChange,
  showAi = false,
  showQueryTab = true,
  categoryLabels,
  source = SHARED_FILTER_SOURCE,
  panelWidth,
  ...panelProps
}) => {
  const properties = useMemo(() => fieldsToProperties(fields), [fields]);
  const categories = useMemo(
    () => fieldsToCategories(fields, categoryLabels),
    [fields, categoryLabels],
  );

  return (
    <TraceFilterPanel
      anchorEl={anchorEl}
      open={open}
      onClose={onClose}
      currentFilters={value}
      onApply={onChange}
      properties={properties}
      categories={categories}
      showAi={showAi}
      showQueryTab={showQueryTab}
      source={source}
      panelWidth={panelWidth}
      {...panelProps}
    />
  );
};

FilterPanel.propTypes = {
  anchorEl: PropTypes.any,
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  fields: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.string.isRequired,
      label: PropTypes.string.isRequired,
      type: PropTypes.oneOf([
        "text",
        "string",
        "number",
        "enum",
        "date",
        "boolean",
        "array",
      ]),
      options: PropTypes.array,
      operators: PropTypes.arrayOf(PropTypes.string),
      category: PropTypes.string,
    }),
  ).isRequired,
  value: PropTypes.array,
  onChange: PropTypes.func.isRequired,
  showAi: PropTypes.bool,
  showQueryTab: PropTypes.bool,
  categoryLabels: PropTypes.object,
  source: PropTypes.string,
  panelWidth: PropTypes.number,
};

export default FilterPanel;
