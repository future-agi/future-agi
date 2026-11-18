/**
 * FilterTrigger — the Filter button on its own: small secondary button with
 * the filter icon, a count of active filters and a pressed state while the
 * panel is open. Use it where the panel renders elsewhere than the button;
 * otherwise use FilterButton, which renders both.
 */
import { Box, Button } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import Iconify from "src/components/iconify";

const FilterTrigger = React.forwardRef(function FilterTrigger(
  { count = 0, open = false, label = "Filter", onClick, sx, ...rest },
  ref,
) {
  return (
    <Button
      ref={ref}
      variant="outlined"
      size="small"
      color="inherit"
      onClick={onClick}
      aria-haspopup="dialog"
      aria-expanded={open}
      startIcon={<Iconify icon="solar:filter-linear" width={16} />}
      sx={{
        flexShrink: 0,
        borderColor: "divider",
        color: "text.primary",
        bgcolor: open ? "action.selected" : "background.paper",
        "&:hover": { bgcolor: "action.hover", borderColor: "text.disabled" },
        ...sx,
      }}
      {...rest}
    >
      {label}
      {count > 0 && (
        <Box
          component="span"
          sx={{
            ml: 0.5,
            color: "text.secondary",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {count}
        </Box>
      )}
    </Button>
  );
});

FilterTrigger.propTypes = {
  count: PropTypes.number,
  open: PropTypes.bool,
  label: PropTypes.string,
  onClick: PropTypes.func,
  sx: PropTypes.object,
};

export default FilterTrigger;
