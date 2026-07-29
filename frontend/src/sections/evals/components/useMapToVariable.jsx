/* eslint-disable react/prop-types */
import { useState, useCallback } from "react";
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
import CustomTooltip from "src/components/tooltip";
import { enqueueSnackbar } from "src/components/snackbar";

// ---------------------------------------------------------------------------
// Shared "map-from-table" (click-to-map) behaviour for every variable-mapping
// surface. Each Columns/Value row exposes a hover action that maps its path to
// an eval variable without hand-typing it into the mapping field below. With a
// single variable we assign immediately; with several we open a small menu so
// the user picks (and sees) which one, plus a Copy-path fallback.
//
// Usage in a consumer:
//   const { renderRowMapAction, mapMenu, rowHoverSx, hasVariables } =
//     useMapToVariable({ variables, mapping, setMapping });
//   // on each Columns/Value row container: sx={{ ...rowHoverSx, ...existing }}
//   // inside the row, after the value cell: {renderRowMapAction(path)}
//   // once, near the end of the component's JSX: {mapMenu}
// ---------------------------------------------------------------------------

// Spread onto each row container so the action only shows on hover (or while
// that row's menu is open).
export const mapRowHoverSx = {
  "& .row-map-action": {
    opacity: 0,
    transition: "opacity 0.12s ease",
  },
  "&:hover .row-map-action, & .row-map-action.is-open": {
    opacity: 1,
  },
};

export function useMapToVariable({ variables, mapping, setMapping }) {
  const vars = Array.isArray(variables) ? variables : [];
  const map = mapping || {};
  const [mapMenu, setMapMenu] = useState(null); // { anchorEl, path } | null

  const assignPathToVariable = useCallback(
    (variable, path) => {
      setMapping((prev) => ({ ...prev, [variable]: path }));
      enqueueSnackbar(`Mapped to ${variable}`, { variant: "success" });
    },
    [setMapping],
  );

  const handleRowMapClick = useCallback(
    (event, path) => {
      event.stopPropagation();
      if (!vars.length) return;
      if (vars.length === 1) {
        assignPathToVariable(vars[0], path);
        return;
      }
      setMapMenu({ anchorEl: event.currentTarget, path });
    },
    [vars, assignPathToVariable],
  );

  const handleCopyPath = useCallback((path) => {
    if (navigator?.clipboard?.writeText) {
      navigator.clipboard
        .writeText(path)
        .then(() => enqueueSnackbar("Copied path", { variant: "success" }))
        .catch(() =>
          enqueueSnackbar("Couldn't copy path", { variant: "error" }),
        );
    }
  }, []);

  // Per-row hover action button. `sx` lets a consumer nudge alignment for its
  // particular table row (default suits a flex-start row).
  const renderRowMapAction = useCallback(
    (path, sx = {}) => {
      if (!vars.length) return null;
      return (
        <CustomTooltip
          show
          type="black"
          size="small"
          title={vars.length === 1 ? `Map to ${vars[0]}` : "Map to variable"}
          placement="top"
          arrow
        >
          <IconButton
            className={`row-map-action${
              mapMenu?.path === path ? " is-open" : ""
            }`}
            size="small"
            onClick={(e) => handleRowMapClick(e, path)}
            sx={{
              flexShrink: 0,
              ml: 0.5,
              mt: -0.25,
              p: 0.25,
              color: "text.secondary",
              "&:hover": { color: "primary.main" },
              ...sx,
            }}
          >
            <Iconify icon="mdi:arrow-right-bold-box-outline" width={16} />
          </IconButton>
        </CustomTooltip>
      );
    },
    [vars, mapMenu, handleRowMapClick],
  );

  // Render this once, anywhere in the component's tree.
  const mapMenuNode = (
    <Menu
      anchorEl={mapMenu?.anchorEl || null}
      open={Boolean(mapMenu)}
      onClose={() => setMapMenu(null)}
      anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
      transformOrigin={{ vertical: "top", horizontal: "right" }}
      slotProps={{ paper: { sx: { minWidth: 220, maxWidth: 320 } } }}
    >
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ px: 1.5, py: 0.5, display: "block", fontWeight: 600 }}
      >
        Map{" "}
        <Box
          component="span"
          sx={{ fontFamily: "monospace", color: "primary.main" }}
        >
          {mapMenu?.path}
        </Box>{" "}
        to
      </Typography>
      {vars.map((variable) => {
        const current = map[variable];
        const isThis = current === mapMenu?.path;
        return (
          <MenuItem
            key={variable}
            onClick={() => {
              if (mapMenu?.path) assignPathToVariable(variable, mapMenu.path);
              setMapMenu(null);
            }}
            sx={{ py: 0.5 }}
          >
            <ListItemIcon sx={{ minWidth: 0, mr: 0.25 }}>
              <Iconify
                icon={isThis ? "mdi:check-circle" : "mdi:code-braces"}
                width={16}
                sx={{ color: isThis ? "success.main" : "text.secondary" }}
              />
            </ListItemIcon>
            {/* Name and its current mapping share one row — keeps the menu
                compact when an eval has several variables. */}
            <Box
              sx={{
                display: "flex",
                alignItems: "baseline",
                gap: 0.75,
                minWidth: 0,
                flex: 1,
              }}
            >
              <Typography
                sx={{ fontSize: "13px", fontWeight: 600, flexShrink: 0 }}
              >
                {variable}
              </Typography>
              <Typography
                noWrap
                sx={{
                  fontSize: "11px",
                  fontFamily: "monospace",
                  color: current ? "text.secondary" : "text.disabled",
                  minWidth: 0,
                }}
              >
                {current || "unmapped"}
              </Typography>
            </Box>
          </MenuItem>
        );
      })}
      <Divider sx={{ my: 0.5 }} />
      <MenuItem
        onClick={() => {
          if (mapMenu?.path) handleCopyPath(mapMenu.path);
          setMapMenu(null);
        }}
        sx={{ py: 0.5 }}
      >
        <ListItemIcon sx={{ minWidth: 0, mr: 0.25 }}>
          <Iconify icon="mdi:content-copy" width={15} />
        </ListItemIcon>
        <ListItemText
          primary="Copy path"
          primaryTypographyProps={{ fontSize: "13px" }}
        />
      </MenuItem>
    </Menu>
  );

  return {
    renderRowMapAction,
    mapMenu: mapMenuNode,
    rowHoverSx: mapRowHoverSx,
    hasVariables: vars.length > 0,
    mapMenuPath: mapMenu?.path,
  };
}
