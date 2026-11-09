import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Button,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
} from "@mui/material";
import Iconify from "src/components/iconify";
import KeyHintTooltip from "./KeyHintTooltip";

/** Row densities (persisted by the caller, e.g. useViewUrlState `density`). */
export const DENSITIES = Object.freeze([
  {
    value: "compact",
    label: "Compact (1 line)",
    lines: 1,
    icon: "mdi:view-headline",
  },
  {
    value: "comfortable",
    label: "Comfortable (3 lines)",
    lines: 3,
    icon: "mdi:view-sequential-outline",
  },
  {
    value: "tall",
    label: "Tall (6 lines)",
    lines: 6,
    icon: "mdi:view-agenda-outline",
  },
  {
    value: "full",
    label: "Full text (up to 20 lines)",
    lines: 20,
    icon: "mdi:text-long",
  },
]);

export const densityOf = (value) =>
  DENSITIES.find((d) => d.value === value) ?? DENSITIES[0];

/** The next density (the `D` key cycles). */
export const nextDensity = (value) => {
  const i = DENSITIES.findIndex((d) => d.value === value);
  return DENSITIES[(i + 1) % DENSITIES.length].value;
};

/**
 * DensityMenu — `Density ▾` with the four densities; the `D` key hint.
 *
 * Props (frozen):
 * @param {"compact"|"comfortable"|"tall"|"full"} props.value
 * @param {(value) => void} props.onChange
 * @param {boolean} [props.iconOnly]
 */
export default function DensityMenu({ value, onChange, iconOnly = false }) {
  const [anchor, setAnchor] = useState(null);
  const current = densityOf(value);
  return (
    <>
      <KeyHintTooltip title={`Row density: ${current.label}`} action="density">
        <Button
          size="small"
          variant="text"
          color="inherit"
          aria-label="Row density"
          aria-haspopup="menu"
          onClick={(e) => setAnchor(e.currentTarget)}
          startIcon={<Iconify icon={current.icon} width={16} />}
          endIcon={
            iconOnly ? null : <Iconify icon="mdi:chevron-down" width={16} />
          }
          sx={{ typography: "s2", minWidth: 0, color: "text.secondary" }}
        >
          {iconOnly ? null : "Density"}
        </Button>
      </KeyHintTooltip>
      <Menu
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={() => setAnchor(null)}
      >
        {DENSITIES.map((d) => (
          <MenuItem
            key={d.value}
            selected={d.value === current.value}
            onClick={() => {
              setAnchor(null);
              onChange?.(d.value);
            }}
          >
            <ListItemIcon>
              <Iconify icon={d.icon} width={18} />
            </ListItemIcon>
            <ListItemText primaryTypographyProps={{ typography: "s1" }}>
              {d.label}
            </ListItemText>
            {d.value === current.value && (
              <Iconify icon="mdi:check" width={16} sx={{ ml: 1 }} />
            )}
          </MenuItem>
        ))}
      </Menu>
    </>
  );
}

DensityMenu.propTypes = {
  value: PropTypes.string,
  onChange: PropTypes.func,
  iconOnly: PropTypes.bool,
};
