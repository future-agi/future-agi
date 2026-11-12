import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Divider,
  IconButton,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Stack,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import {
  MODE_HINT,
  MODE_LABEL,
  VIEW_MODES,
  copyFormatOfView,
} from "./viewModes";

const COPY_ITEMS = [
  ["value", "Copy value"],
  ["json", "Copy as JSON"],
  ["yaml", "Copy as YAML"],
  ["path", "Copy path"],
  ["curl", "Copy as cURL-safe string"],
];

const COPY_LABEL = { value: "value", json: "JSON", yaml: "YAML" };

const itemSx = { typography: "s2", minHeight: 30, py: 0.5 };

/**
 * ValueToolbar — the value's `View as ▾` (current mode name) and `Copy ▾`
 * menus (R2-SPEC P6 §3.2). Modes that do not apply stay listed, disabled,
 * with the reason. `Wrap lines` and `Full screen` sit in the View menu.
 * `float` places it top-right over the value (8 px inset, paper at 0.9).
 */
const ValueToolbar = ({
  view,
  availability,
  onView,
  wrap,
  onWrap,
  onFullScreen,
  onCopy,
  copyPath,
  float = false,
  visible = true,
}) => {
  const [viewEl, setViewEl] = useState(null);
  const [copyEl, setCopyEl] = useState(null);
  const open = Boolean(viewEl || copyEl);
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={0.25}
      className="value-toolbar"
      onClick={(e) => e.stopPropagation()}
      sx={{
        flexShrink: 0,
        ...(float && {
          position: "absolute",
          top: 8,
          right: 8,
          zIndex: 2,
          px: 0.25,
          borderRadius: 0.75,
          border: "1px solid",
          borderColor: "divider",
          bgcolor: (t) => alpha(t.palette.background.paper, 0.9),
          opacity: visible || open ? 1 : 0,
          pointerEvents: visible || open ? "auto" : "none",
          transition: "opacity 120ms",
          "&:focus-within": { opacity: 1, pointerEvents: "auto" },
        }),
      }}
    >
      <CustomTooltip show size="small" placement="top" arrow title="View as">
        <Button
          size="small"
          variant="text"
          color="inherit"
          aria-label={`View as ${MODE_LABEL[view]}`}
          aria-haspopup="menu"
          onClick={(e) => setViewEl(e.currentTarget)}
          endIcon={<Iconify icon="eva:chevron-down-fill" width={16} />}
          sx={{
            minWidth: 0,
            px: 0.75,
            height: 26,
            typography: "s3",
            fontWeight: 500,
            color: "text.secondary",
            "& .MuiButton-endIcon": { ml: 0.25 },
          }}
        >
          {MODE_LABEL[view]}
        </Button>
      </CustomTooltip>
      {/* dt-10: the icon copies what is shown (YAML while the
          field shows YAML); `Copy as ▾` keeps the other formats. */}
      <CustomTooltip
        show
        size="small"
        placement="top"
        arrow
        title={`Copy ${copyPath ? `${copyPath} ` : "this value "}as shown (${COPY_LABEL[copyFormatOfView(view)]})`}
      >
        <IconButton
          size="small"
          aria-label={`Copy as shown (${COPY_LABEL[copyFormatOfView(view)]})`}
          onClick={() => onCopy?.(copyFormatOfView(view))}
          sx={{ p: 0.5, color: "text.secondary" }}
        >
          <Iconify icon="tabler:copy" width={16} />
        </IconButton>
      </CustomTooltip>
      <CustomTooltip show size="small" placement="top" arrow title="Copy as…">
        <IconButton
          size="small"
          aria-label="Copy as"
          aria-haspopup="menu"
          onClick={(e) => setCopyEl(e.currentTarget)}
          sx={{ p: 0.25, ml: -0.5, color: "text.secondary" }}
        >
          <Iconify icon="eva:chevron-down-fill" width={14} />
        </IconButton>
      </CustomTooltip>
      <Menu
        anchorEl={viewEl}
        open={Boolean(viewEl)}
        onClose={() => setViewEl(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{ paper: { sx: { minWidth: 220 } } }}
      >
        {VIEW_MODES.filter(
          // A mode the value's availability does not name is not offered
          // (Form without a declared schema), never a disabled placeholder.
          (m) => !availability || m in availability,
        ).map((m) => {
          const reason = availability?.[m];
          return (
            <MenuItem
              key={m}
              disabled={Boolean(reason)}
              selected={m === view}
              onClick={() => {
                setViewEl(null);
                onView?.(m);
              }}
              sx={{ ...itemSx, "&.Mui-disabled": { opacity: 1 } }}
            >
              <ListItemIcon sx={{ minWidth: "24px !important" }}>
                {m === view && <Iconify icon="mdi:check" width={16} />}
              </ListItemIcon>
              <ListItemText
                primary={MODE_LABEL[m]}
                secondary={reason || MODE_HINT[m] || null}
                primaryTypographyProps={{
                  typography: "s2",
                  color: reason ? "text.disabled" : "text.primary",
                }}
                secondaryTypographyProps={{
                  typography: "s3",
                  color: "text.disabled",
                }}
              />
            </MenuItem>
          );
        })}
        <Divider />
        <MenuItem
          onClick={() => {
            onWrap?.(!wrap);
          }}
          sx={itemSx}
        >
          <ListItemIcon sx={{ minWidth: "24px !important" }}>
            {wrap && <Iconify icon="mdi:check" width={16} />}
          </ListItemIcon>
          <ListItemText
            primary="Wrap lines"
            primaryTypographyProps={{ typography: "s2" }}
          />
        </MenuItem>
        {onFullScreen && (
          <MenuItem
            onClick={() => {
              setViewEl(null);
              onFullScreen();
            }}
            sx={itemSx}
          >
            <ListItemIcon sx={{ minWidth: "24px !important" }}>
              <Iconify icon="mdi:fullscreen" width={16} />
            </ListItemIcon>
            <ListItemText
              primary="Full screen"
              primaryTypographyProps={{ typography: "s2" }}
            />
          </MenuItem>
        )}
      </Menu>
      <Menu
        anchorEl={copyEl}
        open={Boolean(copyEl)}
        onClose={() => setCopyEl(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
      >
        {COPY_ITEMS.filter(([f]) => f !== "path" || copyPath).map(
          ([f, label]) => (
            <MenuItem
              key={f}
              onClick={() => {
                setCopyEl(null);
                onCopy?.(f);
              }}
              sx={itemSx}
            >
              <ListItemText
                primary={label}
                secondary={f === "path" ? copyPath : null}
                primaryTypographyProps={{ typography: "s2" }}
                secondaryTypographyProps={{
                  typography: "s3",
                  sx: { fontFamily: "monospace" },
                }}
              />
            </MenuItem>
          ),
        )}
      </Menu>
      <Box sx={{ display: "none" }} />
    </Stack>
  );
};

ValueToolbar.propTypes = {
  view: PropTypes.string.isRequired,
  availability: PropTypes.object,
  onView: PropTypes.func,
  wrap: PropTypes.bool,
  onWrap: PropTypes.func,
  onFullScreen: PropTypes.func,
  onCopy: PropTypes.func,
  copyPath: PropTypes.string,
  float: PropTypes.bool,
  visible: PropTypes.bool,
};

export default ValueToolbar;
