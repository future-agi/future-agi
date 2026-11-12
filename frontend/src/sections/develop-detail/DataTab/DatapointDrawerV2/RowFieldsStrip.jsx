import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Chip,
  IconButton,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Stack,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { KeyCap } from "src/sections/common/RowPanel/ShortcutSheet";

/**
 * The Row tab strip (one view A1-21..A1-24; IT-R2 G5 P6): `Find field…  /`,
 * the section anchors, then ONE `⋯` holding `Diff vs expected` (D; `Diff
 * vs latest` as of an older version; disabled with its reason until a run
 * output exists) and `Collapse all fields` / `Expand all fields` (X). An
 * active diff shows as a removable chip.
 * Raw JSON (and `Copy row as JSON`) is the Raw tab (R) and ⌘K. Sticks to
 * the top of the panel body.
 */
const RowFieldsStrip = ({
  view,
  outline,
  find,
  canDiff,
  diffCount = 0,
  hasExpected = false,
  sections,
}) => {
  const { diffOn, setDiffOn, asOf } = view;
  const [anchor, setAnchor] = useState(null);
  const diffLabel = asOf ? "Diff vs latest" : "Diff vs expected";
  const showDiff = canDiff || asOf || hasExpected;
  const diffDisabled = !asOf && !canDiff;
  const diffHint = asOf
    ? "What changed in each field since this version"
    : canDiff
      ? `Word diff from the expected value to each output${diffCount ? ` (${diffCount})` : ""}`
      : "Nothing to diff: no run output on this row yet";
  const close = () => setAnchor(null);
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={1}
      sx={{
        position: "sticky",
        top: 0,
        zIndex: 2,
        px: 2,
        py: 0.75,
        minWidth: 0,
        "& .MuiToggleButton-root, & .MuiButton-root": { whiteSpace: "nowrap" },
        bgcolor: "background.paper",
        borderBottom: "1px solid",
        borderColor: "divider",
      }}
    >
      {find}
      {/* IT-R2 G5 P6: the section anchors share the line with Find. */}
      <Box sx={{ flex: 1, minWidth: 0 }}>{sections}</Box>
      {diffOn ? (
        <Chip
          size="small"
          label={diffLabel}
          onDelete={() => setDiffOn(false)}
          sx={{ height: 24, typography: "s3", flexShrink: 0 }}
        />
      ) : null}
      {/* IT-R2 G5 P6: Diff and Collapse all live in one ⋯ (keys D / X). */}
      <CustomTooltip show size="small" title="Diff, collapse all">
        <IconButton
          size="small"
          aria-label="Row view options"
          aria-haspopup="menu"
          onClick={(e) => setAnchor(e.currentTarget)}
          sx={{ p: 0.5, color: "text.secondary", flexShrink: 0 }}
        >
          <Iconify icon="mdi:dots-horizontal" width={18} />
        </IconButton>
      </CustomTooltip>
      <Menu
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={close}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{ paper: { sx: { minWidth: 260 } } }}
      >
        {showDiff ? (
          <MenuItem
            dense
            disabled={diffDisabled}
            selected={diffOn}
            onClick={() => {
              setDiffOn(!diffOn);
              close();
            }}
          >
            <ListItemIcon>
              <Iconify icon="mdi:file-compare" width={16} />
            </ListItemIcon>
            <ListItemText
              primary={diffOn ? `Hide ${diffLabel.toLowerCase()}` : diffLabel}
              secondary={diffHint}
              primaryTypographyProps={{ typography: "s2" }}
              secondaryTypographyProps={{ typography: "s3" }}
            />
            <KeyCap sx={{ ml: 1 }}>D</KeyCap>
          </MenuItem>
        ) : null}
        {outline ? (
          <MenuItem
            dense
            onClick={() => {
              if (outline.allOpen) outline.collapseAll();
              else outline.expandAll();
              close();
            }}
          >
            <ListItemIcon>
              <Iconify
                icon={
                  outline.allOpen
                    ? "mdi:unfold-less-horizontal"
                    : "mdi:unfold-more-horizontal"
                }
                width={16}
              />
            </ListItemIcon>
            <ListItemText
              primary={
                outline.allOpen ? "Collapse all fields" : "Expand all fields"
              }
              primaryTypographyProps={{ typography: "s2" }}
            />
            <KeyCap sx={{ ml: 1 }}>X</KeyCap>
          </MenuItem>
        ) : null}
      </Menu>
    </Stack>
  );
};

RowFieldsStrip.propTypes = {
  view: PropTypes.shape({
    diffOn: PropTypes.bool,
    setDiffOn: PropTypes.func,
    asOf: PropTypes.bool,
    onCopyRow: PropTypes.func,
    rowJson: PropTypes.func,
  }).isRequired,
  diffCount: PropTypes.number,
  hasExpected: PropTypes.bool,
  outline: PropTypes.object,
  find: PropTypes.node,
  canDiff: PropTypes.bool,
  sections: PropTypes.node,
};

export default RowFieldsStrip;
