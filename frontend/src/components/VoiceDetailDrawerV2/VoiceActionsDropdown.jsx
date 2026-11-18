import React, { useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  ButtonBase,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
} from "@mui/material";
import Iconify from "src/components/iconify";

export const VOICE_ACTIONS = [
  { id: "annotate", label: "Annotate", icon: "solar:chat-round-line-linear" },
  {
    id: "queue",
    label: "Add to annotation queue",
    icon: "solar:list-plus-linear",
  },
  { id: "dataset", label: "Move to dataset", icon: "solar:database-linear" },
  { id: "tags", label: "Add tags", icon: "solar:tag-linear" },
  {
    id: "download",
    label: "Download raw data",
    icon: "solar:download-minimalistic-linear",
  },
];

const VoiceActionsDropdown = ({ onAction, actions = VOICE_ACTIONS }) => {
  const [open, setOpen] = useState(false);
  const anchorRef = useRef(null);

  return (
    <>
      <ButtonBase
        ref={anchorRef}
        data-voice-actions-button
        onClick={() => setOpen((prev) => !prev)}
        sx={{
          display: "inline-flex",
          alignItems: "center",
          gap: 0.5,
          height: 24,
          px: 1,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "4px",
          bgcolor: "background.paper",
          fontSize: 12,
          fontWeight: 500,
          fontFamily: "'Inter', sans-serif",
          color: "text.primary",
          whiteSpace: "nowrap",
          flexShrink: 0,
          "&:hover": {
            bgcolor: "background.neutral",
            borderColor: "text.disabled",
          },
        }}
      >
        <Iconify icon="solar:bolt-linear" width={14} />
        <span>Actions</span>
        <Iconify icon="solar:alt-arrow-down-linear" width={12} />
      </ButtonBase>

      <Menu
        open={open}
        anchorEl={anchorRef.current}
        onClose={() => setOpen(false)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{
          paper: {
            sx: {
              minWidth: 240,
              mt: 0.5,
              borderRadius: "8px",
              "& .MuiMenuItem-root": { fontSize: 12, py: 0.75, px: 1.25 },
            },
          },
        }}
      >
        {actions.map((a) => (
          <MenuItem
            key={a.id}
            onClick={() => {
              setOpen(false);
              onAction?.(a.id);
            }}
            dense
          >
            <ListItemIcon sx={{ minWidth: 0, mr: 1 }}>
              <Iconify icon={a.icon} width={16} />
            </ListItemIcon>
            <ListItemText
              primaryTypographyProps={{ fontSize: 12, fontWeight: 500 }}
            >
              {a.label}
            </ListItemText>
          </MenuItem>
        ))}
      </Menu>
    </>
  );
};

VoiceActionsDropdown.propTypes = {
  onAction: PropTypes.func,
  actions: PropTypes.array,
};

export default VoiceActionsDropdown;
