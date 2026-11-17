import React from "react";
import PropTypes from "prop-types";
import { Drawer } from "@mui/material";
import {
  COVERED_DRAWER_SX,
  useDrawerCovered,
} from "src/hooks/use-drawer-stack";
import { dsSize } from "src/theme/ds-tokens";

// Generic extraction of Vel 2c38e27fc2 (refined by 148ee8a615), without
// importing Simulation. Retains nested-drawer state and MUI focus restoration.
export default function SideDrawer({
  open,
  onClose,
  size = "sm",
  width,
  keepMounted = false,
  persistent = false,
  children,
  sx,
  PaperProps = {},
  ModalProps = {},
  ...other
}) {
  const covered = useDrawerCovered(open);
  return (
    <Drawer
      {...other}
      anchor="right"
      open={open}
      onClose={onClose}
      variant={persistent ? "persistent" : "temporary"}
      PaperProps={{
        role: persistent ? "region" : "dialog",
        "aria-modal": persistent ? undefined : true,
        ...PaperProps,
      }}
      sx={[
        (theme) => ({
          zIndex: theme.zIndex.drawer,
          "&& .MuiDrawer-paper": {
            width: width ?? dsSize.drawer[size],
            maxWidth: "100vw",
            height: "100dvh",
            borderRadius: 0,
            backgroundColor: theme.palette.background.overlay,
            backgroundImage: "none",
            borderLeft: `1px solid ${theme.palette.border.default}`,
            boxShadow: theme.customShadows.drawer,
          },
          ...(covered && COVERED_DRAWER_SX),
        }),
        ...(Array.isArray(sx) ? sx : [sx]),
      ]}
      ModalProps={{
        ...ModalProps,
        keepMounted,
        disableEnforceFocus: covered || ModalProps.disableEnforceFocus,
        BackdropProps: {
          ...ModalProps.BackdropProps,
          sx: { bgcolor: "transparent", ...ModalProps.BackdropProps?.sx },
        },
      }}
    >
      {children}
    </Drawer>
  );
}
SideDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  size: PropTypes.oneOf(["sm", "md", "lg"]),
  width: PropTypes.oneOfType([
    PropTypes.number,
    PropTypes.string,
    PropTypes.object,
  ]),
  keepMounted: PropTypes.bool,
  persistent: PropTypes.bool,
  children: PropTypes.node,
  sx: PropTypes.oneOfType([PropTypes.object, PropTypes.array, PropTypes.func]),
  PaperProps: PropTypes.object,
  ModalProps: PropTypes.object,
};
