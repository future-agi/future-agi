import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Divider,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { PanelIconButton } from "src/sections/common/RowPanel/RowPanelShell";
import { keyLabel } from "src/utils/keymap";
import { isSurfaceShown } from "src/launch/launchSwitch";

const VIEW_ONLY = "You have view access";

/**
 * The Row view header's ⋯ menu (PRODUCT-SPEC §3.4): Open source trace ·
 * Add to annotation queue… · New experiment on this row… · Duplicate row ·
 * Copy to dataset… · Download row (JSON) · Delete row. As of an older
 * version (integration) the host passes read items only: Open source trace ·
 * Copy row as JSON · Download row (JSON) · Open version history.
 * `items` = {key: {onClick, disabled, hint}}.
 */
const RowMoreMenu = ({ items, canEdit }) => {
  const [anchor, setAnchor] = useState(null);
  const close = () => setAnchor(null);
  const run = (fn, el) => () => {
    close();
    fn?.(el);
  };
  const entry = (key, label, icon, opts = {}) => {
    const item = items[key];
    if (!item) return null;
    const blocked = opts.write && !canEdit;
    // Viewers get no Delete at all (PRODUCT-SPEC §3.4 States).
    if (opts.danger && !canEdit) return null;
    return (
      <MenuItem
        key={key}
        dense
        disabled={blocked || item.disabled}
        onClick={run(item.onClick, anchor)}
        sx={opts.danger ? { color: "error.main" } : undefined}
      >
        <ListItemIcon sx={opts.danger ? { color: "error.main" } : undefined}>
          <Iconify icon={icon} width={18} />
        </ListItemIcon>
        <ListItemText
          primary={label}
          secondary={blocked ? VIEW_ONLY : item.hint}
          primaryTypographyProps={{ typography: "s1" }}
          secondaryTypographyProps={{ typography: "s3" }}
        />
        {opts.key && (
          <ListItemText
            primary={keyLabel(opts.key)}
            sx={{ flex: "0 0 auto", ml: 2, color: "text.disabled" }}
            primaryTypographyProps={{ typography: "s2" }}
          />
        )}
      </MenuItem>
    );
  };
  return (
    <>
      <PanelIconButton
        title="More actions"
        icon="mdi:dots-horizontal"
        onClick={(e) => setAnchor(e.currentTarget)}
        active={Boolean(anchor)}
      />
      <Menu
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={close}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{ paper: { sx: { minWidth: 260 } } }}
      >
        {[
          entry(
            "openTrace",
            "Open source trace",
            "mdi:transit-connection-variant",
          ),
          // integration (DM-07): an expired source opens its kept copy instead.
          entry("openSaved", "Open saved copy", "mdi:history"),
          entry(
            "addToQueue",
            "Add to annotation queue…",
            "mdi:tray-arrow-down",
            {
              key: "addToQueue",
            },
          ),
          entry(
            "newExperiment",
            "New experiment on this row…",
            "material-symbols:science-outline",
            {
              write: true,
            },
          ),
          entry("duplicate", "Duplicate row", "mdi:content-duplicate", {
            write: true,
          }),
          entry("copyTo", "Copy to dataset…", "mdi:database-export-outline", {
            write: true,
          }),
          // integration: the as-of menu's read items (only when given).
          entry("copyJson", "Copy row as JSON", "mdi:content-copy"),
          entry("download", "Download row (JSON)", "mdi:download-outline"),
          isSurfaceShown("dataset.versions") &&
            entry("history", "Open version history", "mdi:history"),
          items.delete ? <Divider key="d" /> : null,
          entry("delete", "Delete row", "solar:trash-bin-trash-linear", {
            write: true,
            danger: true,
          }),
        ]}
      </Menu>
    </>
  );
};

RowMoreMenu.propTypes = {
  items: PropTypes.object.isRequired,
  canEdit: PropTypes.bool,
};

export default RowMoreMenu;
