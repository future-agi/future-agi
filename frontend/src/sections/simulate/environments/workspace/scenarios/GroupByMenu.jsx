import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Button, Divider, Menu, MenuItem } from "@mui/material";
import Iconify from "src/components/iconify";
import { scenarioToolbarButtonSx } from "./scenarios.constants";

// Vel groups by these two as well; the server takes any group_by and the page
// says so when it falls back.
const EXTRA = [
  { value: "source", label: "Source" },
  { value: "added_by", label: "Added by" },
];
export const SUB_GOALS_MODE = "__subgoals";

export const groupOptionsOf = (groupings = []) => {
  const known = new Set(groupings.map((g) => g.value));
  return [...groupings, ...EXTRA.filter((g) => !known.has(g.value))];
};

// Vel's toolbar Group by: the axis the list is grouped on, then the sub-goals
// list as its own mode.
export default function GroupByMenu({
  groupings,
  value,
  onChange,
  mode,
  onModeChange,
}) {
  const [anchor, setAnchor] = useState(null);
  const options = groupOptionsOf(groupings);
  const current =
    mode === SUB_GOALS_MODE
      ? "Sub-goals"
      : options.find((g) => g.value === value)?.label || "None";
  const pick = (fn) => () => {
    setAnchor(null);
    fn();
  };
  return (
    <>
      <Button
        aria-haspopup="menu"
        aria-expanded={!!anchor}
        onClick={(e) => setAnchor(e.currentTarget)}
        startIcon={
          <Iconify icon="solar:layers-minimalistic-linear" width={16} />
        }
        endIcon={<Iconify icon="solar:alt-arrow-down-linear" width={12} />}
        sx={scenarioToolbarButtonSx}
      >
        Group by
        <Box
          component="span"
          sx={{ ml: 0.75, color: "text.primary", fontWeight: 600 }}
        >
          {current}
        </Box>
      </Button>
      <Menu open={!!anchor} anchorEl={anchor} onClose={() => setAnchor(null)}>
        {options.map((g) => (
          <MenuItem
            key={g.value || "none"}
            role="menuitemradio"
            aria-checked={mode !== SUB_GOALS_MODE && g.value === value}
            selected={mode !== SUB_GOALS_MODE && g.value === value}
            onClick={pick(() => {
              onModeChange(null);
              onChange(g.value);
            })}
            sx={{ typography: "s2" }}
          >
            {g.label || "None"}
          </MenuItem>
        ))}
        <Divider />
        <MenuItem
          role="menuitemradio"
          aria-checked={mode === SUB_GOALS_MODE}
          selected={mode === SUB_GOALS_MODE}
          onClick={pick(() => onModeChange(SUB_GOALS_MODE))}
          sx={{ typography: "s2" }}
        >
          Sub-goals list
        </MenuItem>
      </Menu>
    </>
  );
}

GroupByMenu.propTypes = {
  groupings: PropTypes.arrayOf(
    PropTypes.shape({ value: PropTypes.string, label: PropTypes.string }),
  ),
  value: PropTypes.string,
  onChange: PropTypes.func.isRequired,
  mode: PropTypes.string,
  onModeChange: PropTypes.func.isRequired,
};
