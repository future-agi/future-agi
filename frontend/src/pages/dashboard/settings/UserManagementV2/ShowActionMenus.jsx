import React, { useEffect, useId, useRef } from "react";
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
  canRemove = canEdit,
  canResend = true,
  canCancel = canEdit,
  disabledReasons,
  menusByStatus,
}) => {
  const popperRef = useRef(null);
  const reasonIdPrefix = useId();
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
    "cancel-invite",
  ];
  const resendActions = ["resend-invite", "reactivate-member"];

  const activeMenus = menusByStatus || actionMenusByStatus;
  const menuItems = (activeMenus[data.status] || []).filter((item) => {
    // Shown disabled, with its reason, rather than left out.
    if (disabledReasons?.[item.action]) return true;
    if (item.action === "remove-ws-member") return canRemove;
    if (item.action === "cancel-invite") return canCancel;
    if (editActions.includes(item.action)) return canEdit;
    if (resendActions.includes(item.action)) return canResend;
    return true;
  });
  const reasons = [
    ...new Set(
      menuItems.map((item) => disabledReasons?.[item.action]).filter(Boolean),
    ),
  ];
  const reasonId = (reason) =>
    `${reasonIdPrefix}-reason-${reasons.indexOf(reason)}`;

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
            maxHeight: `280px`,
            overflow: "hidden",
            display: "flex",
            flexDirection: "column",
          },
        }}
      >
        <Box display="flex" flexDirection={"column"} gap={1}>
          {menuItems.map((item, ind) => {
            const disabledReason = disabledReasons?.[item.action];
            return (
              <Box
                key={ind}
                display="flex"
                gap={1}
                alignItems={"center"}
                role={disabledReason ? "button" : undefined}
                tabIndex={disabledReason ? 0 : undefined}
                aria-disabled={disabledReason ? "true" : undefined}
                aria-describedby={
                  disabledReason ? reasonId(disabledReason) : undefined
                }
                onClick={() => {
                  if (disabledReason) return;
                  onClose();
                  setOpenActionForm(item);
                }}
                sx={{
                  cursor: disabledReason ? "not-allowed" : "pointer",
                  borderRadius: "4px",
                  padding: 0.5,
                  "&:hover": {
                    backgroundColor: disabledReason
                      ? "transparent"
                      : "action.hover",
                  },
                }}
              >
                <SvgColor
                  // @ts-ignore
                  src={item.image}
                  sx={{
                    color: disabledReason ? "text.disabled" : item.color,
                    width: "16px",
                    height: "16px",
                  }}
                />
                <Typography
                  typography={"s3"}
                  fontWeight="fontWeightRegular"
                  color={disabledReason ? "text.disabled" : item.color}
                >
                  {item.action === "resend-invite" && hidesInviteEmail ? "Renew invitation" : item.title}
                </Typography>
              </Box>
            );
          })}
          {reasons.map((reason, ind) => (
            <Typography
              key={reason}
              id={`${reasonIdPrefix}-reason-${ind}`}
              typography={"s3"}
              fontWeight="fontWeightRegular"
              color="text.secondary"
              sx={{ maxWidth: "240px", px: 0.5 }}
            >
              {reason}
            </Typography>
          ))}
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
  canRemove: PropTypes.bool,
  canResend: PropTypes.bool,
  canCancel: PropTypes.bool,
  disabledReasons: PropTypes.object,
  menusByStatus: PropTypes.object,
};
