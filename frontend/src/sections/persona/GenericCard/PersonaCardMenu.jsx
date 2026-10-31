import React from "react";
import PropTypes from "prop-types";
import {
  Menu,
  MenuItem,
  ListItemIcon,
  ListItemText,
  CircularProgress,
  Box,
} from "@mui/material";
import SvgColor from "src/components/svg-color";
import { ShowComponent } from "src/components/show";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";

const PersonaCardMenu = ({
  anchorEl,
  open,
  onClose,
  isPrebuilt,
  onEditClick,
  onDuplicateClick,
  onViewClick,
  onDeleteClick,
  isDeleting,
}) => {
  const { role } = useAuthContext();
  const canWrite = RolePermission.SIMULATION_AGENT[PERMISSIONS.UPDATE][role];
  return (
    <Menu
      anchorEl={anchorEl}
      open={open}
      onClose={() => {
        if (!isDeleting) onClose();
      }}
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
      <ShowComponent condition={!isPrebuilt && canWrite}>
        <MenuItem
          onClick={(e) => {
            e.stopPropagation();
            onEditClick();
            onClose();
          }}
          sx={{ px: 1.25, py: 0.75 }}
        >
          <ListItemIcon sx={{ minWidth: "unset", mr: 1 }}>
            <SvgColor
              src="/assets/icons/persona/edit.svg"
              sx={{ width: 16, height: 16, ml: 0.5 }}
            />
          </ListItemIcon>
          <ListItemText
            primary="Edit"
            primaryTypographyProps={{ fontSize: 13, fontWeight: 400 }}
          />
        </MenuItem>
      </ShowComponent>

      <ShowComponent condition={canWrite}>
        <MenuItem onClick={onDuplicateClick} sx={{ px: 1.25, py: 0.75 }}>
          <ListItemIcon sx={{ minWidth: "unset", mr: 1 }}>
            <SvgColor
              src="/assets/icons/persona/duplicate.svg"
              sx={{ height: 20, width: 20 }}
            />
          </ListItemIcon>
          <ListItemText
            primary="Duplicate"
            primaryTypographyProps={{ fontSize: 13, fontWeight: 400 }}
          />
        </MenuItem>
      </ShowComponent>

      <MenuItem
        onClick={() => {
          onClose();
          onViewClick();
        }}
        sx={{ px: 1.25, py: 0.75 }}
      >
        <ListItemIcon sx={{ minWidth: "unset", mr: 1 }}>
          <SvgColor
            src="/assets/icons/persona/view.svg"
            sx={{ height: 20, width: 20 }}
          />
        </ListItemIcon>
        <ListItemText
          primary="View"
          primaryTypographyProps={{ fontSize: 13, fontWeight: 400 }}
        />
      </MenuItem>

      <ShowComponent condition={!isPrebuilt && canWrite}>
        <MenuItem
          // eslint-disable-next-line no-restricted-syntax -- ds: destructive menu item
          sx={{ color: "status.danger.fg", px: 1.25, py: 0.75 }}
          onClick={onDeleteClick}
        >
          <ListItemIcon sx={{ minWidth: "unset", mr: 1 }}>
            <SvgColor
              src="/assets/icons/ic_delete.svg"
              sx={{ height: 20, width: 20, color: "inherit" }}
            />
          </ListItemIcon>
          <ListItemText
            primary="Delete"
            primaryTypographyProps={{ fontSize: 13, fontWeight: 400 }}
          />
          {isDeleting && (
            <Box sx={{ ml: "auto" }}>
              <CircularProgress size={16} color="inherit" />
            </Box>
          )}
        </MenuItem>
      </ShowComponent>
    </Menu>
  );
};

PersonaCardMenu.propTypes = {
  anchorEl: PropTypes.any,
  open: PropTypes.bool,
  onClose: PropTypes.func,
  isPrebuilt: PropTypes.bool,
  onEditClick: PropTypes.func,
  onDuplicateClick: PropTypes.func,
  onViewClick: PropTypes.func,
  onDeleteClick: PropTypes.func,
  isDeleting: PropTypes.bool,
};

export default PersonaCardMenu;
