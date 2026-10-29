import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Button, Menu, MenuItem } from "@mui/material";
import Iconify from "src/components/iconify";

/*
  How the scenario table groups its rows — the toolbar's one display control.
  The table is the only layout, and "by sub-goal" is a grouping, so there is
  nothing else to choose.
*/
export default function GroupByMenu({
  value,
  onChange,
  options,
  compact = false,
}) {
  const [anchor, setAnchor] = useState(null);
  const current = options.find((o) => o.id === value) || options[0];
  return (
    <>
      <Button
        size="small"
        variant="outlined"
        onClick={(e) => setAnchor(e.currentTarget)}
        startIcon={<Iconify icon={current.icon} width={14} />}
        endIcon={<Iconify icon="solar:alt-arrow-down-linear" width={12} />}
        sx={{
          flexShrink: 0,
          whiteSpace: "nowrap",
          color: "text.primary",
          fontWeight: 400,
          borderColor: anchor ? "text.primary" : "divider",
          bgcolor: anchor ? "action.selected" : "transparent",
        }}
      >
        {/* Tight toolbar: the current grouping alone — its icon says it's a grouping. */}
        {!compact && (
          <Box
            component="span"
            sx={{ color: "text.secondary", fontWeight: 400, mr: 0.5 }}
          >
            Group by
          </Box>
        )}
        {current.label}
      </Button>
      <Menu
        anchorEl={anchor}
        open={!!anchor}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
        slotProps={{ paper: { sx: { minWidth: 200, mt: 0.5 } } }}
      >
        {options.map((o) => (
          <MenuItem
            key={o.id}
            selected={o.id === current.id}
            onClick={() => {
              setAnchor(null);
              onChange(o.id);
            }}
            sx={{ typography: "s2", gap: 1 }}
          >
            <Iconify
              icon={o.icon}
              width={14}
              sx={{ color: "text.tertiary", flexShrink: 0 }}
            />
            <Box sx={{ flex: 1 }}>{o.label}</Box>
            {o.id === current.id && (
              <Iconify
                icon="solar:check-linear"
                width={16}
                sx={{ color: "text.secondary" }}
              />
            )}
          </MenuItem>
        ))}
      </Menu>
    </>
  );
}
GroupByMenu.propTypes = {
  compact: PropTypes.bool,
  value: PropTypes.string,
  onChange: PropTypes.func.isRequired,
  options: PropTypes.array.isRequired,
};
