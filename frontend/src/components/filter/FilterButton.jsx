/**
 * FilterButton — the Filter trigger from the design system: a small secondary
 * button with the filter icon, a count of active filters and a pressed state
 * while the panel is open. Renders the panel too, anchored to itself, unless
 * the caller passes `open` / `onOpenChange` to control it.
 */
import PropTypes from "prop-types";
import React, { useCallback, useState } from "react";
import FilterPanel from "./FilterPanel";
import FilterTrigger from "./FilterTrigger";
import { countActiveFilters } from "./fieldSchema";

const FilterButton = ({
  fields,
  value,
  onChange,
  count,
  label = "Filter",
  open: controlledOpen,
  onOpenChange,
  sx,
  ...panelProps
}) => {
  const [buttonEl, setButtonEl] = useState(null);
  const [innerOpen, setInnerOpen] = useState(false);
  const isControlled = controlledOpen !== undefined;
  const open = isControlled ? controlledOpen : innerOpen;
  const total = count ?? countActiveFilters(value);

  const setOpen = useCallback(
    (next) => {
      if (!isControlled) setInnerOpen(next);
      onOpenChange?.(next);
    },
    [isControlled, onOpenChange],
  );
  const handleClick = useCallback(() => setOpen(!open), [open, setOpen]);
  const handleClose = useCallback(() => setOpen(false), [setOpen]);

  return (
    <>
      <FilterTrigger
        ref={setButtonEl}
        count={total}
        open={open}
        label={label}
        onClick={handleClick}
        sx={sx}
      />
      <FilterPanel
        anchorEl={buttonEl}
        open={open && Boolean(buttonEl)}
        onClose={handleClose}
        fields={fields}
        value={value}
        onChange={onChange}
        {...panelProps}
      />
    </>
  );
};

FilterButton.propTypes = {
  fields: PropTypes.array.isRequired,
  value: PropTypes.array,
  onChange: PropTypes.func.isRequired,
  count: PropTypes.number,
  label: PropTypes.string,
  open: PropTypes.bool,
  onOpenChange: PropTypes.func,
  sx: PropTypes.object,
};

export default FilterButton;
