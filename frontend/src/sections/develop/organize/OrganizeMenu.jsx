// `Organize ▾` in the dataset list's selection bar (W9-SCOPE §5 A1, A6,
// A7): Metadata…, Move to folder…, Agents… on the selected datasets. Shown
// only when the host passes `onPick`; read-only people see it disabled with
// the reason.
import React, { useState } from "react";
import PropTypes from "prop-types";
import { Button, ListItemIcon, Menu, MenuItem } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { offeredOrganizeItems, ORGANIZE_READ_ONLY } from "./organizeItems";

export default function OrganizeMenu({ onPick, canEdit, buttonSx }) {
  const [anchor, setAnchor] = useState(null);
  return (
    <>
      <CustomTooltip show={!canEdit} title={ORGANIZE_READ_ONLY} size="small">
        <span>
          <Button
            size="small"
            color="inherit"
            disabled={!canEdit}
            startIcon={<Iconify icon="mdi:folder-cog-outline" width={16} />}
            endIcon={<Iconify icon="mdi:chevron-down" width={16} />}
            onClick={(e) => setAnchor(e.currentTarget)}
            aria-haspopup="menu"
            aria-expanded={Boolean(anchor)}
            sx={buttonSx}
          >
            Organize
          </Button>
        </span>
      </CustomTooltip>
      <Menu
        open={Boolean(anchor)}
        anchorEl={anchor}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "top", horizontal: "left" }}
        transformOrigin={{ vertical: "bottom", horizontal: "left" }}
      >
        {offeredOrganizeItems().map((item) => (
          <MenuItem
            key={item.kind}
            onClick={() => {
              setAnchor(null);
              onPick(item.kind);
            }}
            sx={{ typography: "s2" }}
          >
            <ListItemIcon>
              <Iconify icon={item.icon} width={16} />
            </ListItemIcon>
            {item.label}
          </MenuItem>
        ))}
      </Menu>
    </>
  );
}

OrganizeMenu.propTypes = {
  onPick: PropTypes.func.isRequired,
  canEdit: PropTypes.bool,
  buttonSx: PropTypes.object,
};
