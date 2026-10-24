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
import SvgColor from "src/components/svg-color";

const RunTestsActionRenderer = (params) => {
  const { data } = params;
  const { viewDetails, onDelete } = params?.colDef?.cellRendererParams ?? {};
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

  const handleNavigate = () => {
    viewDetails?.(data);
    handleClose();
  };

  const handleDelete = () => {
    onDelete?.(data);
    handleClose();
  };

  return (
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
        <MenuItem onClick={handleNavigate} sx={{ px: 1.25, py: 0.75 }}>
          <ListItemIcon sx={{ minWidth: "unset", mr: 0 }}>
            <SvgColor
              src="/assets/icons/ic_hide.svg"
              sx={{
                width: 20,
                height: 20,
              }}
            />
          </ListItemIcon>
          <ListItemText
            primary="View details"
            primaryTypographyProps={{
              typography: "s1",
              fontWeight: "500",
            }}
          />
        </MenuItem>
        <MenuItem
          onClick={handleDelete}
          // eslint-disable-next-line no-restricted-syntax -- ds: destructive menu item
          sx={{ color: "status.danger.fg", px: 1.25, py: 0.75 }}
        >
          <ListItemIcon sx={{ minWidth: "unset", mr: 0 }}>
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
              typography: "s1",
              fontWeight: "500",
            }}
          />
        </MenuItem>
      </Menu>
    </Box>
  );
};

RunTestsActionRenderer.propTypes = {
  test: PropTypes.object,
  viewDetails: PropTypes.func,
  onDelete: PropTypes.func,
};

export default RunTestsActionRenderer;
