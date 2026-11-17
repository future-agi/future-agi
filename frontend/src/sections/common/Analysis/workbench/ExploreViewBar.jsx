// Explore's view switch and ⋯ menu (W5 B6, B8): `Pivot & charts` · `Config`
// (URL `wbtab`), and `Print or save as PDF` (URL `print=1`). A view or the
// match door whose route this build does not offer stays in its place,
// turned off, with the shared line.
import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  IconButton,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
} from "@mui/material";
import Iconify from "src/components/iconify";
import NotInReleaseChip from "src/components/NotInRelease/NotInReleaseChip";
import { analysisRouteOffered } from "src/sections/common/Collab/routeOffer";
import { exploreTabs } from "./workbenchModel";
import { hasW5 } from "./w5Api";
import { SEGMENTED_SX } from "src/sections/experiment-detail/ExperimentBarRightSection/ExperimentBarSummaryRightSection";

// A turned-off tab or item keeps its label and chip at full strength; the
// chip and the missing hover mark it as off.
const OFF_SX = { "&.Mui-disabled": { opacity: 1, color: "text.secondary" } };

export default function ExploreViewBar({ tab, onTab, onPrint, onMatch }) {
  const [anchor, setAnchor] = useState(null);
  const matchOn = analysisRouteOffered("resultsMatch");
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
        {exploreTabs(hasW5).map((t) => {
          const off = Boolean(t.api) && !analysisRouteOffered(t.api);
          return (
            <ToggleButton
              key={t.key}
              value={t.key}
              disabled={off}
              sx={off ? OFF_SX : undefined}
            >
              {off ? (
                <Stack
                  component="span"
                  direction="row"
                  alignItems="center"
                  spacing={0.75}
                >
                  <span>{t.label}</span>
                  <NotInReleaseChip short />
                </Stack>
              ) : (
                t.label
              )}
            </ToggleButton>
          );
        })}
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
            disabled={!matchOn}
            sx={matchOn ? undefined : OFF_SX}
            onClick={
              matchOn
                ? () => {
                    setAnchor(null);
                    onMatch();
                  }
                : undefined
            }
          >
            <ListItemIcon>
              <Iconify icon="mdi:key-link" width={18} />
            </ListItemIcon>
            <ListItemText primaryTypographyProps={{ typography: "s1" }}>
              Compare with a run on another dataset…
            </ListItemText>
            {!matchOn && <NotInReleaseChip sx={{ ml: 1 }} />}
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
