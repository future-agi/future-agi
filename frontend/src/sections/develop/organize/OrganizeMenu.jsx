// Selection-bar organize doors. Unavailable actions stay visible with a calm
// explanation; each feature keeps its own availability gate.
import React, { useId, useState } from "react";
import PropTypes from "prop-types";
import {
  Button,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import {
  offeredOrganizeItems,
  organizeItemOff,
  ORGANIZE_READ_ONLY,
} from "./organizeItems";

const OFF_ITEM_SX = {
  typography: "s2",
  alignItems: "flex-start",
  whiteSpace: "normal",
  maxWidth: 300,
  "&.Mui-disabled": { opacity: 1 },
};

export default function OrganizeMenu({ onPick, canEdit, buttonSx, metadataCapability }) {
  const [anchor, setAnchor] = useState(null);
  const offId = useId();
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
        {offeredOrganizeItems().map((item) => {
          const off = organizeItemOff(item, metadataCapability);
          if (off) {
            const lineId = `${offId}-${item.kind}`;
            return (
              <MenuItem
                key={item.kind}
                disabled
                aria-label={item.label}
                aria-describedby={lineId}
                sx={OFF_ITEM_SX}
              >
                <ListItemIcon sx={{ mt: 0.25, opacity: 0.6 }}>
                  <Iconify icon={item.icon} width={16} />
                </ListItemIcon>
                <ListItemText
                  sx={{ my: 0 }}
                  primary={item.label}
                  secondary={off}
                  primaryTypographyProps={{
                    typography: "s2",
                    color: "text.secondary",
                  }}
                  secondaryTypographyProps={{
                    id: lineId,
                    typography: "s3",
                    color: "text.secondary",
                  }}
                />
              </MenuItem>
            );
          }
          return (
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
          );
        })}
      </Menu>
    </>
  );
}

OrganizeMenu.propTypes = {
  onPick: PropTypes.func.isRequired,
  canEdit: PropTypes.bool,
  buttonSx: PropTypes.object,
  metadataCapability: PropTypes.object,
};
