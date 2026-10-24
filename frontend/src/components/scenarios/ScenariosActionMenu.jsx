import {
  Box,
  Button,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  useTheme,
} from "@mui/material";
import React, { useState } from "react";
import PropTypes from "prop-types";
import SvgColor from "../svg-color";

const ScenarioActionMenu = ({ scenario, onEdit, onDelete }) => {
  const [anchorEl, setAnchorEl] = useState(null);
  const open = Boolean(anchorEl);
  const theme = useTheme();

  const handleClick = (event) => {
    event.stopPropagation();
    setAnchorEl(event.currentTarget);
  };

  const handleClose = () => {
    setAnchorEl(null);
  };

  const handleEdit = () => {
    onEdit?.(scenario);
    handleClose();
  };

  const handleDelete = () => {
    onDelete?.(scenario);
    handleClose();
  };

  return (
    <>
      <Box height={"100%"} display={"flex"} alignItems={"center"}>
        <Button
          variant="outlined"
          size="small"
          onClick={handleClick}
          sx={{
            display: "flex",
            gap: 1,
            borderColor: `${theme.palette.divider} !important`,
            minWidth: 71,
          }}
        >
          <SvgColor
            sx={{
              bgcolor: "text.disabled",
              height: 16.5,
              width: 16.5,
            }}
            src="/assets/icons/action_buttons/ic_configure.svg"
          />
          <SvgColor
            sx={{
              bgcolor: "text.disabled",
              height: 16.5,
              width: 16.5,
              transform: "rotate(90deg)",
            }}
            src="/assets/icons/custom/lucide--chevron-right.svg"
          />
        </Button>
      </Box>
      <Menu
        anchorEl={anchorEl}
        open={open}
        onClose={handleClose}
        PaperProps={{
          sx: {
            mt: 1,
            minWidth: 200,
            p: 0.5,
          },
        }}
        transformOrigin={{ horizontal: "right", vertical: "top" }}
        anchorOrigin={{ horizontal: "right", vertical: "bottom" }}
      >
        <MenuItem onClick={handleEdit} sx={{ px: 1.25, py: 0.75 }}>
          <ListItemIcon sx={{ minWidth: "unset", mr: 1 }}>
            <SvgColor
              src="/assets/icons/ic_pen.svg"
              sx={{
                width: 16,
                height: 16,
                ml: 0.5,
              }}
            />
          </ListItemIcon>
          <ListItemText
            primary="Edit"
            primaryTypographyProps={{
              fontSize: 13,
              fontWeight: 400,
            }}
          />
        </MenuItem>
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

ScenarioActionMenu.propTypes = {
  scenario: PropTypes.object,
  onEdit: PropTypes.func,
  onDelete: PropTypes.func,
};

export default ScenarioActionMenu;
