// Explore's view switch and ⋯ menu (W5 B6, B8): `Pivot & charts` · `Config`
// (URL `wbtab`), and `Print or save as PDF` (URL `print=1`).
import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  IconButton,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  ToggleButton,
  ToggleButtonGroup,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { exploreTabs } from "./workbenchModel";
import { hasW5 } from "./w5Api";
import { SEGMENTED_SX } from "src/sections/experiment-detail/ExperimentBarRightSection/ExperimentBarSummaryRightSection";

export default function ExploreViewBar({ tab, onTab, onPrint, onMatch }) {
  const [anchor, setAnchor] = useState(null);
  return (
    <>
      <ToggleButtonGroup
        exclusive
        size="small"
        value={tab}
        onChange={(_, v) => v && onTab(v)}
        aria-label="Explore view"
        sx={SEGMENTED_SX}
      >
        {exploreTabs(hasW5).map((t) => (
          <ToggleButton key={t.key} value={t.key}>
            {t.label}
          </ToggleButton>
        ))}
      </ToggleButtonGroup>
      <IconButton
        size="small"
        aria-label="More Explore actions"
        onClick={(e) => setAnchor(e.currentTarget)}
      >
        <Iconify icon="mdi:dots-horizontal" width={18} />
      </IconButton>
      <Menu
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={() => setAnchor(null)}
      >
        <MenuItem
          onClick={() => {
            setAnchor(null);
            onPrint();
          }}
        >
          <ListItemIcon>
            <Iconify icon="mdi:printer-outline" width={18} />
          </ListItemIcon>
          <ListItemText primaryTypographyProps={{ typography: "s1" }}>
            Print or save as PDF
          </ListItemText>
        </MenuItem>
        {onMatch && hasW5("resultsMatch") && (
          <MenuItem
            onClick={() => {
              setAnchor(null);
              onMatch();
            }}
          >
            <ListItemIcon>
              <Iconify icon="mdi:key-link" width={18} />
            </ListItemIcon>
            <ListItemText primaryTypographyProps={{ typography: "s1" }}>
              Compare with a run on another dataset…
            </ListItemText>
          </MenuItem>
        )}
      </Menu>
    </>
  );
}

ExploreViewBar.propTypes = {
  tab: PropTypes.string.isRequired,
  onTab: PropTypes.func.isRequired,
  onPrint: PropTypes.func.isRequired,
  onMatch: PropTypes.func,
};
