import React, { useEffect, useRef } from "react";
import PropTypes from "prop-types";
import { Box, Popover, Portal, Typography } from "@mui/material";
import SvgColor from "src/components/svg-color";
import { useDeploymentMode } from "src/hooks/useDeploymentMode";
import { actionMenusByStatus } from "./constant";

const ShowActionMenus = ({
  id,
  actionRef,
  open,
  onClose,
  data,
  setOpenActionForm,
  canEdit = true,
  canResend = true,
  menusByStatus,
}) => {
  const popperRef = useRef(null);
  const { isOSS, isSuccess: modeConfirmed } = useDeploymentMode();
  const hidesInviteEmail = modeConfirmed && isOSS;

  useEffect(() => {
    function handleClickOutside(event) {
      if (popperRef?.current && !popperRef?.current?.contains(event.target)) {
        onClose();
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, [popperRef, open, onClose]);

  const editActions = [
    "edit-role",
    "edit-ws-role",
    "remove-member",
    "remove-ws-member",
    "cancel-invite",
  ];
  const resendActions = ["resend-invite", "reactivate-member"];

  const activeMenus = menusByStatus || actionMenusByStatus;
  const menuItems = (activeMenus[data.status] || []).filter((item) => {
    // Self-hosted sends no mail, so resending delivers nothing.
    if (item.action === "resend-invite" && hidesInviteEmail) return false;
    if (editActions.includes(item.action)) return canEdit;
    if (resendActions.includes(item.action)) return canResend;
    return true;
  });

  return (
    <Portal>
      <Popover
        id={id}
        anchorEl={actionRef.current}
        ref={popperRef}
        open={open}
        onClose={onClose}
        anchorOrigin={{
          vertical: "bottom",
          horizontal: "right",
        }}
        transformOrigin={{
          vertical: "top",
          horizontal: "right",
        }}
        disableRestoreFocus
        disableEnforceFocus
        disableAutoFocus
        sx={{
          zIndex: 9999,
          "& .MuiPaper-root": {
            bgcolor: "background.paper",
            border: "1px solid",
            borderColor: "action.hover",
            p: "12px",
            boxShadow: "0px 4px 8px rgba(0, 0, 0, 0.1)",
            maxHeight: `280px`,
            overflow: "hidden",
            display: "flex",
            flexDirection: "column",
          },
        }}
      >
        <Box display="flex" flexDirection={"column"} gap={1}>
          {menuItems.map((item, ind) => {
            return (
              <Box
                key={ind}
                display="flex"
                gap={1}
                alignItems={"center"}
                onClick={() => {
                  onClose();
                  setOpenActionForm(item);
                }}
                sx={{
                  cursor: "pointer",
                  borderRadius: 0.75,
                  padding: 0.5,
                  "&:hover": {
                    backgroundColor: "action.hover",
                  },
                }}
              >
                <SvgColor
                  // @ts-ignore
                  src={item.image}
                  sx={{ color: item.color, width: "16px", height: "16px" }}
                />
                <Typography
                  typography={"s3"}
                  fontWeight="fontWeightRegular"
                  color={item.color}
                >
                  {item.title}
                </Typography>
              </Box>
            );
          })}
        </Box>
      </Popover>
    </Portal>
  );
};

export default ShowActionMenus;

ShowActionMenus.propTypes = {
  id: PropTypes.string,
  actionRef: PropTypes.any,
  open: PropTypes.bool,
  onClose: PropTypes.func,
  data: PropTypes.object,
  setOpenActionForm: PropTypes.func,
  canEdit: PropTypes.bool,
  canResend: PropTypes.bool,
  menusByStatus: PropTypes.object,
};
