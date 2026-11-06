import React, { useState, useRef } from "react";
import { Popover, MenuItem, Box, Button, MenuList } from "@mui/material";
import SvgColor from "src/components/svg-color";
import Iconify from "src/components/iconify";
import { useNavigate } from "react-router";
import PropTypes from "prop-types";

const ActionsCellRenderer = (props) => {
  const [anchorEl, setAnchorEl] = useState(null);
  const buttonRef = useRef(null);
  const navigate = useNavigate();

  const handleClick = (event) => {
    event.stopPropagation();
    setAnchorEl(event.currentTarget);
  };

  const handleClose = () => {
    setAnchorEl(null);
  };

  const handleMenuClick = (action) => {
    handleClose();
    const userId = props?.data?.user_id;
    if (!userId) return;

    // Route to the user detail page (not the project-level observe page).
    // CrossProjectUserDetailPage reads `userTab` to select the sub-view
    // and renders LLMTracingView / SessionsView in `mode="user"`, which
    // already scopes the grid to this user.
    const userTab = action === "viewTraces" ? "traces" : "sessions";
    navigate(
      `/dashboard/users/${encodeURIComponent(userId)}?userTab=${userTab}`,
    );
  };

  const iconStyles = {
    width: 20,
    height: 20,
    color: "text.disabled",
  };

  return (
    <Box
      display="flex"
      alignItems="center"
      justifyContent="center"
      width="100%"
    >
      <Button
        variant="outlined"
        size="small"
        ref={buttonRef}
        onClick={handleClick}
        sx={{ borderColor: "divider" }}
        startIcon={
          <SvgColor
            src="/assets/icons/action_buttons/ic_configure.svg"
            sx={iconStyles}
          />
        }
      >
        <Iconify icon="solar:alt-arrow-down-linear" color="text.disabled" />
      </Button>

      <Popover
        open={Boolean(anchorEl)}
        anchorEl={anchorEl}
        onClose={handleClose}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
        PaperProps={{
          sx: {
            mt: 1,
            minWidth: 220,
            boxShadow: "none",
            p: 1,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "8px",
            transform: "translateX(-32px) !important",
          },
        }}
      >
        <MenuList>
          <MenuItem
            onClick={() => handleMenuClick("viewTraces")}
            sx={{
              display: "flex",
              alignItems: "center",
              gap: 1,
            }}
          >
            <SvgColor src="/assets/icons/navbar/ic_llm.svg" sx={iconStyles} />
            View Traces
          </MenuItem>
          <MenuItem
            onClick={() => handleMenuClick("viewSessions")}
            sx={{
              display: "flex",
              alignItems: "center",
              gap: 1,
            }}
          >
            <SvgColor
              src="/assets/icons/navbar/ic_sessions.svg"
              sx={iconStyles}
            />
            View Sessions
          </MenuItem>
        </MenuList>
      </Popover>
    </Box>
  );
};

ActionsCellRenderer.propTypes = {
  data: PropTypes.object,
};

export default ActionsCellRenderer;
