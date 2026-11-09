// `Steps · Graph · Timeline` (R4 lane T): the trajectory view switch, same
// place on every screen. The Graph option carries the recommendation dot +
// reason; keys `G` / `L` are bound by the host (RunTrajectory, Task compare).
import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  ToggleButton,
  ToggleButtonGroup,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { KeyHintTitle } from "src/sections/common/ux/KeyHintTooltip";

export const VIEW_OPTIONS = Object.freeze({
  steps: {
    label: "Steps",
    icon: "mdi:file-tree-outline",
    tip: "Steps — every span as a tree",
  },
  graph: {
    label: "Graph",
    icon: "mdi:graph-outline",
    tip: "Graph — how the steps connect",
    action: "graphView",
  },
  timeline: {
    label: "Timeline",
    icon: "mdi:chart-gantt",
    tip: "Timeline — spans on one time axis",
    action: "timelineView",
  },
  call: {
    label: "Call",
    icon: "mdi:phone-outline",
    tip: "Call — the conversation, turn by turn",
  },
});

export default function ViewSwitch({
  value,
  onChange,
  options = ["steps", "graph", "timeline"],
  recommended,
  ariaLabel = "Trajectory view",
  size = "small",
  labels,
  variant = "tabs",
}) {
  const [anchor, setAnchor] = useState(null);
  if (variant === "menu") {
    // IT-R5 G4 S2: inside a drawer that already has tabs, one `Steps ▾`
    // button (never a third nested tab bar).
    const cur = labels?.[value] ?? VIEW_OPTIONS[value] ?? VIEW_OPTIONS.steps;
    return (
      <>
        <Button
          size="small"
          color="inherit"
          variant="outlined"
          aria-label={`${ariaLabel}: ${cur.label}`}
          aria-haspopup="menu"
          data-view-menu={value}
          onClick={(e) => setAnchor(e.currentTarget)}
          startIcon={<Iconify icon={cur.icon} width={15} />}
          endIcon={<Iconify icon="mdi:chevron-down" width={14} />}
          sx={{
            flexShrink: 0,
            typography: "s2",
            height: 26,
            borderColor: "divider",
          }}
        >
          {cur.label}
          {recommended && value !== "graph" ? (
            <Box
              component="span"
              sx={{
                width: 6,
                height: 6,
                ml: 0.5,
                borderRadius: "50%",
                bgcolor: "primary.main",
              }}
            />
          ) : null}
        </Button>
        <Menu
          anchorEl={anchor}
          open={Boolean(anchor)}
          onClose={() => setAnchor(null)}
          MenuListProps={{ dense: true }}
        >
          {options.map((key) => {
            const opt = labels?.[key] ?? VIEW_OPTIONS[key];
            return (
              <MenuItem
                key={key}
                selected={key === value}
                data-view={key}
                onClick={() => {
                  setAnchor(null);
                  onChange?.(key);
                }}
              >
                <ListItemIcon>
                  <Iconify icon={opt.icon} width={16} />
                </ListItemIcon>
                <ListItemText
                  primaryTypographyProps={{ typography: "s2" }}
                  secondary={
                    key === "graph" && recommended ? recommended : opt.tip
                  }
                  secondaryTypographyProps={{
                    typography: "s3",
                    sx: { whiteSpace: "normal", maxWidth: 260 },
                  }}
                >
                  {opt.label}
                </ListItemText>
              </MenuItem>
            );
          })}
        </Menu>
      </>
    );
  }
  return (
    <ToggleButtonGroup
      exclusive
      size={size}
      value={value}
      aria-label={ariaLabel}
      onChange={(_, v) => v && onChange?.(v)}
      sx={{
        flexShrink: 0,
        "& .MuiToggleButton-root": {
          typography: "s2",
          px: 1,
          py: 0.25,
          gap: 0.5,
          textTransform: "none",
          lineHeight: "20px",
        },
      }}
    >
      {options.map((key) => {
        const opt = labels?.[key] ?? VIEW_OPTIONS[key];
        const dot = key === "graph" && recommended;
        // IT-R2 G4 P5: the recommendation is a line under the tabs
        // (RecommendHint), never a tooltip over the graph's legend.
        const title = <KeyHintTitle title={opt.tip} action={opt.action} />;
        return (
          <ToggleButton
            key={key}
            value={key}
            aria-label={opt.label}
            data-view={key}
          >
            <CustomTooltip
              show
              title={title}
              placement="bottom"
              size="small"
              type="black"
            >
              <Box
                component="span"
                sx={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 0.5,
                  position: "relative",
                }}
              >
                <Iconify icon={opt.icon} width={15} />
                {opt.label}
                {dot ? (
                  <Box
                    component="span"
                    data-recommended="graph"
                    aria-label={recommended}
                    sx={{
                      width: 6,
                      height: 6,
                      borderRadius: "50%",
                      bgcolor: "primary.main",
                      position: "absolute",
                      top: -2,
                      right: -7,
                    }}
                  />
                ) : null}
              </Box>
            </CustomTooltip>
          </ToggleButton>
        );
      })}
    </ToggleButtonGroup>
  );
}

ViewSwitch.propTypes = {
  value: PropTypes.string,
  onChange: PropTypes.func,
  options: PropTypes.arrayOf(PropTypes.string),
  recommended: PropTypes.string,
  ariaLabel: PropTypes.string,
  size: PropTypes.string,
  // additive: option metadata for other switches (`Graph · Sankey · Table`)
  labels: PropTypes.object,
  // IT-R5 G4 S2 (additive): `menu` = one `Steps ▾` button (in a drawer)
  variant: PropTypes.oneOf(["tabs", "menu"]),
};
