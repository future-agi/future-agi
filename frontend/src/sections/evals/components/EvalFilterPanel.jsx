import PropTypes from "prop-types";
import React, { useCallback, useMemo } from "react";
import { FilterPanel } from "src/components/filter";
import { TYPED_EVAL_TYPES } from "src/sections/common/EvalTypes/typedEvalTypes";
import { typedTabsOf } from "../types/typedViews";
import {
  EVAL_FILTER_FIELDS,
  filtersToRows,
  rowsToFilters,
} from "./evalFilterAdapter";

const EvalFilterPanel = ({
  anchorEl,
  open,
  onClose,
  onApply,
  currentFilters,
  lockedFilters,
}) => {
  const fields = useMemo(() => {
    const locked = Object.keys(lockedFilters || {});
    return EVAL_FILTER_FIELDS.filter((f) => !locked.includes(f.id)).map(
      (field) => {
        if (field.id !== "eval_type") return field;
        // Typed types join with the label the Eval Type column shows for
        // them (EvalTypeBadge reads the same TYPED_EVAL_TYPES names).
        const known = new Set(field.options.map((option) => option.value));
        const typed = typedTabsOf(TYPED_EVAL_TYPES)
          .filter((type) => !known.has(type.value))
          .map(({ value, label }) => ({ value, label }));
        return { ...field, options: [...field.options, ...typed] };
      },
    );
  }, [lockedFilters]);
  const value = useMemo(
    () =>
      filtersToRows(currentFilters).filter((row) =>
        fields.some((f) => f.id === row.field),
      ),
    [currentFilters, fields],
  );
  const handleChange = useCallback(
    (rows) => onApply(rowsToFilters(rows)),
    [onApply],
  );

  return (
    <FilterPanel
      anchorEl={anchorEl}
      open={open}
      onClose={onClose}
      fields={fields}
      value={value}
      onChange={handleChange}
      showAi
    />
  );
};

EvalFilterPanel.propTypes = {
  anchorEl: PropTypes.any,
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  onApply: PropTypes.func.isRequired,
  currentFilters: PropTypes.object,
  lockedFilters: PropTypes.object,
};

export default EvalFilterPanel;
