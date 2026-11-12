import React, { useId } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Drawer from "@mui/material/Drawer";
import IconButton from "@mui/material/IconButton";
import Typography from "@mui/material/Typography";

import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";

import { GatewayIcon } from "../../shell/gateway-icons";

// The detail panel a gateway table row opens: a right-hand drawer with a
// title, optional subtitle and actions, and a scrolling body. Full width on
// a phone, 560 px on a desktop.
export default function GatewayDetailDrawer({
  open,
  onClose,
  paperRef,
  title,
  subtitle,
  actions,
  width = 560,
  children,
}) {
  const titleId = useId();
  // Port dev drawer coverage once, at the v2 replacement boundary. Keep the
  // covered drawer mounted so closing a nested drawer restores its local state.
  const covered = useDrawerCovered(open);
  return (
    <Drawer
      sx={coveredDrawerSx(covered)}
      anchor="right"
      open={open}
      onClose={onClose}
      PaperProps={{
        ref: paperRef,
        role: "dialog",
        "aria-labelledby": titleId,
        sx: {
          width: { xs: "100%", sm: width },
          maxWidth: "100%",
          display: "flex",
          flexDirection: "column",
          backgroundColor: "background.paper",
        },
      }}
    >
      <Box
        sx={{
          display: "flex",
          flexWrap: "wrap",
          alignItems: "flex-start",
          gap: 1,
          px: 2,
          py: 1.5,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography
            id={titleId}
            component="h2"
            typography="m3"
            fontWeight="fontWeightMedium"
            color="text.primary"
            sx={{ overflowWrap: "anywhere" }}
          >
            {title}
          </Typography>
          {subtitle && (
            <Typography typography="s2" color="text.secondary">
              {subtitle}
            </Typography>
          )}
        </Box>
        {actions && (
          // On a phone the actions take their own row under the title, so a
          // long title is not squeezed beside them; the close button stays
          // next to the title.
          <Box
            sx={(theme) => ({
              display: "flex",
              flexWrap: "wrap",
              alignItems: "center",
              gap: 1,
              order: 2,
              flexBasis: "100%",
              [theme.breakpoints.up("sm")]: { order: 0, flexBasis: "auto" },
            })}
          >
            {actions}
          </Box>
        )}
        <IconButton
          aria-label="Close"
          onClick={onClose}
          size="small"
          sx={(theme) => ({
            order: 1,
            [theme.breakpoints.up("sm")]: { order: 0 },
          })}
        >
          <GatewayIcon name="close" size={16} />
        </IconButton>
      </Box>
      <Box sx={{ flex: 1, minHeight: 0, overflow: "auto", p: 2 }}>
        {children}
      </Box>
    </Drawer>
  );
}

GatewayDetailDrawer.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  paperRef: PropTypes.oneOfType([PropTypes.func, PropTypes.object]),
  title: PropTypes.node.isRequired,
  subtitle: PropTypes.node,
  actions: PropTypes.node,
  width: PropTypes.number,
  children: PropTypes.node,
};
