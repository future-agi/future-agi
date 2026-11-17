import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Divider,
  IconButton,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import KeyCap from "src/sections/common/ux/KeyCap";
import KeyHintTooltip from "src/sections/common/ux/KeyHintTooltip";
import { HoverCard } from "src/sections/common/ux/oneview";
import DivergenceRibbon, { RibbonKey } from "./DivergenceRibbon";

/**
 * The Map (ONE-VIEW-SPEC §7.1 item 3, A4-03): today's divergence ribbon as
 * a collapsible strip. `Map` + `Key` (legend in a hover card) sit left of
 * the marker row, `⋯` right (`Width: equal / by time`, `Hide map  M`).
 * The markers `First difference` and `▲ First wrong step` jump to and open
 * that step. Collapsed, it takes no room: the toolbar shows `Map` instead.
 */
export default function MapStrip({ width, onWidth, onHide, ...ribbon }) {
  const [menu, setMenu] = useState(null);
  return (
    <DivergenceRibbon
      {...ribbon}
      width={width}
      lead={
        <Box sx={{ display: "flex", alignItems: "center", gap: 0.75 }}>
          <Typography typography="s2" fontWeight={700}>
            Map
          </Typography>
          <HoverCard
            title="How to read the map"
            trigger={
              <Typography
                component="span"
                typography="s3"
                sx={{
                  color: "text.secondary",
                  textDecoration: "underline dotted",
                  textUnderlineOffset: 3,
                }}
              >
                Key
              </Typography>
            }
          >
            <RibbonKey />
          </HoverCard>
        </Box>
      }
      trail={
        <>
          <KeyHintTooltip title="Map options">
            <IconButton
              size="small"
              aria-label="Map options"
              aria-haspopup="menu"
              onClick={(e) => setMenu(e.currentTarget)}
              sx={{ p: 0.25, color: "text.secondary" }}
            >
              <Iconify icon="mdi:dots-horizontal" width={16} />
            </IconButton>
          </KeyHintTooltip>
          <Menu
            anchorEl={menu}
            open={Boolean(menu)}
            onClose={() => setMenu(null)}
            anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
            transformOrigin={{ vertical: "top", horizontal: "right" }}
          >
            {[
              ["equal", "Width: equal", "One block per step"],
              ["time", "Width: by time", "Blocks sized by the time spent"],
            ].map(([v, label, sub]) => (
              <MenuItem
                key={v}
                selected={width === v}
                onClick={() => (onWidth(v), setMenu(null))}
                sx={{ typography: "s2", minWidth: 220 }}
              >
                <ListItemIcon>
                  {width === v ? <Iconify icon="mdi:check" width={16} /> : null}
                </ListItemIcon>
                <ListItemText
                  primary={label}
                  secondary={sub}
                  primaryTypographyProps={{ typography: "s2" }}
                  secondaryTypographyProps={{ typography: "s3" }}
                />
              </MenuItem>
            ))}
            <Divider />
            <MenuItem
              onClick={() => (setMenu(null), onHide())}
              sx={{ typography: "s2" }}
            >
              <ListItemIcon>
                <Iconify icon="mdi:eye-off-outline" width={16} />
              </ListItemIcon>
              <ListItemText primaryTypographyProps={{ typography: "s2" }}>
                Hide map
              </ListItemText>
              <KeyCap sx={{ ml: 2 }}>M</KeyCap>
            </MenuItem>
          </Menu>
        </>
      }
    />
  );
}

MapStrip.propTypes = {
  width: PropTypes.string,
  onWidth: PropTypes.func.isRequired,
  onHide: PropTypes.func.isRequired,
};
