import React, { useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Button,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
} from "@mui/material";
import Iconify from "src/components/iconify";

const ACTIONS = [
  {
    id: "dataset",
    label: "Move to dataset",
    icon: "solar:move-to-folder-linear",
  },
  {
    id: "prompt-workbench",
    label: "Iterate in prompt workbench",
    icon: "solar:pen-linear",
  },
  // {
  //   id: "agent-playground",
  //   label: "Iterate in agent playground",
  //   icon: "solar:bot-linear",
  // },
  { id: "tags", label: "Add Tags", icon: "solar:tag-linear" },
  { id: "annotate", label: "Annotate", icon: "solar:pen-new-square-linear" },
  {
    id: "annotation-queue",
    label: "Add to annotation queue",
    icon: "solar:clipboard-list-linear",
  },
];

const ActionsDropdown = ({ onAction }) => {
  const [open, setOpen] = useState(false);
  const anchorRef = useRef(null);

  return (
    <>
      <Button
        ref={anchorRef}
        variant="outlined"
        size="small"
        endIcon={<Iconify icon="solar:alt-arrow-down-linear" width={14} />}
        onClick={() => setOpen(true)}
        sx={{
          borderColor: "divider",
          color: "text.primary",
        }}
      >
        Actions
      </Button>

      <Menu
        open={open}
        anchorEl={anchorRef.current}
        onClose={() => setOpen(false)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{ paper: { sx: { minWidth: 240, mt: 0.5 } } }}
      >
        {ACTIONS.map((action) => (
          <MenuItem
            key={action.id}
            onClick={() => {
              setOpen(false);
              onAction?.(action.id);
            }}
            dense
          >
            <ListItemIcon>
              <Iconify icon={action.icon} width={16} />
            </ListItemIcon>
            <ListItemText
              primaryTypographyProps={{ variant: "body2", fontSize: 13 }}
            >
              {action.label}
            </ListItemText>
          </MenuItem>
        ))}
      </Menu>
    </>
  );
};

ActionsDropdown.propTypes = {
  onAction: PropTypes.func,
};

export default React.memo(ActionsDropdown);
