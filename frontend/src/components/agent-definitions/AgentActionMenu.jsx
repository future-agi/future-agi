import {
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Box,
  IconButton,
} from "@mui/material";
import React, { useState } from "react";
import PropTypes from "prop-types";
import SvgColor from "../svg-color";
import Iconify from "../iconify";

export const AgentActionMenu = ({ agent, onDelete }) => {
  const [anchorEl, setAnchorEl] = useState(null);
  const open = Boolean(anchorEl);

  const handleClick = (event) => {
    event.stopPropagation();
    setAnchorEl(event.currentTarget);
  };

  const handleClose = () => {
    setAnchorEl(null);
  };

  const handleDelete = () => {
    onDelete?.(agent);
    handleClose();
  };

  return (
    <>
      <Box height={"100%"} display={"flex"} alignItems={"center"}>
        <IconButton size="small" onClick={handleClick}>
          <Iconify icon={"solar:menu-dots-linear"} width={20} height={20} />
        </IconButton>
      </Box>
      <Menu
        anchorEl={anchorEl}
        open={open}
        onClose={handleClose}
        PaperProps={{
          sx: {
            minWidth: 200,
            p: 0.5,
            borderRadius: "8px !important",
          },
        }}
        transformOrigin={{ horizontal: "right", vertical: "top" }}
        anchorOrigin={{ horizontal: "right", vertical: "bottom" }}
      >
        <MenuItem
          onClick={handleDelete}
          // eslint-disable-next-line no-restricted-syntax -- ds: destructive menu item
          sx={{ color: "status.danger.fg", px: 1.25, py: 0.75 }}
        >
          <ListItemIcon sx={{ minWidth: "unset", mr: 1 }}>
            <SvgColor
              src="/assets/icons/ic_delete.svg"
              sx={{
                height: 20,
                width: 20,
              }}
            />
          </ListItemIcon>
          <ListItemText
            primary="Delete"
            primaryTypographyProps={{
              fontSize: 13,
              fontWeight: 400,
            }}
          />
        </MenuItem>
      </Menu>
    </>
  );
};

AgentActionMenu.propTypes = {
  agent: PropTypes.object,
  onDelete: PropTypes.func,
};
