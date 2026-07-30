import React from "react";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Typography,
  IconButton,
  Box,
  Divider,
} from "@mui/material";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";
import { ProcessingStatusCell } from "./CellRender";
import { orgLevelToString, wsLevelToString } from "./constant";

const ActionForm = ({
  userData = { email: "", role: "", status: "", name: "" },
  open = false,
  onClose,
  actionButton,
  title,
  showUserData,
  showRole,
  showRoleInfo = false,
  formSection = <></>,
  headSection = <></>,
}) => {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      fullWidth
      PaperProps={{
        sx: {
          width: "520px",
          borderRadius: 2,
          p: 2,
          display: "flex",
          flexDirection: "column",
          gap: 1,
        },
      }}
    >
      <DialogTitle
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          padding: 0,
          margin: 0,
        }}
      >
        <Typography variant="h6" component="div" sx={{ fontWeight: 600 }}>
          {title}
        </Typography>
        <IconButton onClick={onClose}>
          <Iconify
            // @ts-ignore
            icon="mdi:close"
          />
        </IconButton>
      </DialogTitle>
      <Divider orientation="horizontal" />
      <DialogContent
        sx={{
          padding: 0,
          margin: 0,
          display: "flex",
          flexDirection: "column",
          gap: 2,
        }}
      >
        {headSection}
        {/* Role Information Alert — only on invite/edit dialogs */}
        {showRoleInfo && (
          <Box
            sx={{
              p: 2,
              backgroundColor: "orange.o5",
              borderRadius: 1,
            }}
          >
            <Typography variant="body2" sx={{ fontWeight: 600, mb: 0.5 }}>
              Owner: Full control. Can manage billing, members, and transfer
              ownership.
            </Typography>
            <Typography variant="body2" sx={{ mb: 0.5 }}>
              Admin: Full control on manage settings, members, and permissions.
            </Typography>
            <Typography variant="body2" sx={{ mb: 0.5 }}>
              Member: Collaborate within the space, accessing tools and tasks
              shared with them.
            </Typography>
            <Typography variant="body2">
              Viewer: Read-only access to shared resources.
            </Typography>
          </Box>
        )}

        {/* User Information */}
        {showUserData && (
          <Box
            sx={{
              p: 2,
              backgroundColor: "background.default",
              borderRadius: 1,
              display: "flex",
              flexDirection: "column",
              gap: 1,
            }}
          >
            <Box sx={{ display: "flex" }}>
              <Typography
                variant="body1"
                sx={{ minWidth: 120, fontWeight: 500, color: "text.secondary" }}
              >
                User name:
              </Typography>
              <Typography variant="body1" sx={{ color: "text.primary" }}>
                {userData?.name || "N/A"}
              </Typography>
            </Box>
            {showRole && (
              <>
                <Box sx={{ display: "flex" }}>
                  <Typography
                    variant="body1"
                    sx={{
                      minWidth: 120,
                      fontWeight: 500,
                      color: "text.secondary",
                    }}
                  >
                    Org Role:
                  </Typography>
                  <Typography variant="body1" sx={{ color: "text.primary" }}>
                    {userData?.org_role ||
                      orgLevelToString[userData?.org_level] ||
                      userData?.role ||
                      "N/A"}
                  </Typography>
                </Box>
                {userData?.ws_role && (
                  <Box sx={{ display: "flex" }}>
                    <Typography
                      variant="body1"
                      sx={{
                        minWidth: 120,
                        fontWeight: 500,
                        color: "text.secondary",
                      }}
                    >
                      WS Role:
                    </Typography>
                    <Typography variant="body1" sx={{ color: "text.primary" }}>
                      {userData?.ws_role ||
                        wsLevelToString[userData?.ws_level] ||
                        "N/A"}
                    </Typography>
                  </Box>
                )}
              </>
            )}

            <Box sx={{ display: "flex" }}>
              <Typography
                variant="body1"
                sx={{ minWidth: 120, fontWeight: 500, color: "text.secondary" }}
              >
                Email:
              </Typography>
              <Typography variant="body1" sx={{ color: "text.primary" }}>
                {userData?.email || "N/A"}
              </Typography>
            </Box>

            <Box sx={{ display: "flex" }}>
              <Typography
                variant="body1"
                sx={{ minWidth: 120, fontWeight: 500, color: "text.secondary" }}
              >
                Status:
              </Typography>
              <ProcessingStatusCell value={userData?.status} />
            </Box>
          </Box>
        )}
        {formSection}
      </DialogContent>

      <DialogActions sx={{ padding: 0, margin: 0, marginTop: 1 }}>
        {actionButton}
      </DialogActions>
    </Dialog>
  );
};

export default ActionForm;

ActionForm.propTypes = {
  userData: PropTypes.object,
  open: PropTypes.bool,
  onClose: PropTypes.func,
  title: PropTypes.string,
  actionButton: PropTypes.node,
  showUserData: PropTypes.bool,
  showRole: PropTypes.bool,
  showRoleInfo: PropTypes.bool,
  formSection: PropTypes.node,
  headSection: PropTypes.node,
};
