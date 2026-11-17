import React, { useState, useEffect, useRef } from "react";
import PageHeader from "src/components/page-header";
import { Helmet } from "react-helmet-async";
import {
  Box,
  Typography,
  useTheme,
  TextField,
  CircularProgress,
} from "@mui/material";
import { LoadingButton } from "@mui/lab";
import { useMutation } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import axios, { endpoints } from "src/utils/axios";
import Tooltip from "@mui/material/Tooltip";
import Iconify from "src/components/iconify";
import { useAuthContext } from "src/auth/hooks";
import { useOrganization } from "src/contexts/OrganizationContext";
import OrgTwoFactorPolicySection from "src/sections/settings/Security/OrgTwoFactorPolicySection";
import logger from "src/utils/logger";
import {
  SS_KEY_ORG_DISPLAY_NAME,
  SS_KEY_ORG_NAME,
} from "src/utils/sessionKeys";

// A rename reloads the page so every part of the app reads the new name. The
// confirmation waits in this tab's session storage and shows once after the
// reload, where the user can read it. It carries who renamed which
// organization: it shows only to that person in that organization, and is
// discarded unread for anyone or anywhere else.
const RENAMED_NOTICE_KEY = "orgSettings.renamedNotice";
const RENAMED_MESSAGE = "Organization name updated successfully.";

// True when a notice is waiting for this person in this organization. Any
// notice found is removed, whether it matched or not.
function takeRenamedNotice(userId, organizationId) {
  try {
    const stored = sessionStorage.getItem(RENAMED_NOTICE_KEY);
    if (stored === null) return false;
    sessionStorage.removeItem(RENAMED_NOTICE_KEY);
    const notice = JSON.parse(stored);
    return (
      notice?.userId === String(userId) &&
      notice?.organizationId === String(organizationId)
    );
  } catch {
    return false;
  }
}

function keepRenamedNotice(userId, organizationId) {
  if (userId == null || organizationId == null) return;
  try {
    sessionStorage.setItem(
      RENAMED_NOTICE_KEY,
      JSON.stringify({
        userId: String(userId),
        organizationId: String(organizationId),
      }),
    );
  } catch {
    // Without session storage the page still reloads with the new name.
  }
}

const OrgSettings = () => {
  const theme = useTheme();
  const { user } = useAuthContext();
  const {
    currentOrganizationId,
    currentOrganizationName,
    currentOrganizationDisplayName,
  } = useOrganization();
  const userId = user?.id;

  const [isEditing, setIsEditing] = useState(false);
  const [editName, setEditName] = useState("");

  const orgDisplayName =
    currentOrganizationDisplayName || currentOrganizationName || "";

  useEffect(() => {
    setEditName(orgDisplayName);
  }, [orgDisplayName]);

  // Read once the signed-in person and organization are known; until then the
  // notice is left in place.
  const noticeRead = useRef(false);
  useEffect(() => {
    if (noticeRead.current || userId == null || currentOrganizationId == null)
      return;
    noticeRead.current = true;
    if (takeRenamedNotice(userId, currentOrganizationId)) {
      enqueueSnackbar(RENAMED_MESSAGE, { variant: "success" });
    }
  }, [userId, currentOrganizationId]);

  const { mutate: updateOrgName, isPending: isUpdating } = useMutation({
    mutationFn: async (name) => {
      const response = await axios.patch(endpoints.organizations.update, {
        name,
        display_name: name,
      });
      return response.data;
    },
    onSuccess: () => {
      setIsEditing(false);
      // Update sessionStorage so the context picks up the new name
      sessionStorage.setItem(SS_KEY_ORG_NAME, editName.trim());
      sessionStorage.setItem(SS_KEY_ORG_DISPLAY_NAME, editName.trim());
      // Reload to refresh all contexts with the new org name; the
      // confirmation shows after the reload.
      keepRenamedNotice(userId, currentOrganizationId);
      window.location.reload();
    },
    onError: (error) => {
      logger.error("Error updating organization name:", error);
      const message =
        error?.response?.data?.result ||
        error?.response?.data?.error ||
        "Failed to update organization name.";
      enqueueSnackbar(message, { variant: "error" });
    },
  });

  const handleSave = () => {
    const trimmed = editName.trim();
    if (!trimmed) {
      enqueueSnackbar("Organization name cannot be empty.", {
        variant: "warning",
      });
      return;
    }
    if (trimmed === orgDisplayName) {
      setIsEditing(false);
      return;
    }
    updateOrgName(trimmed);
  };

  const handleCancel = () => {
    setEditName(orgDisplayName);
    setIsEditing(false);
  };

  return (
    <>
      <Helmet>
        <title>Organization settings</title>
      </Helmet>
      <Box
        sx={{ display: "flex", flexDirection: "column", gap: theme.spacing(2) }}
      >
        <PageHeader
          title="Organization settings"
          description="Manage organization-level settings"
          sx={{ mb: 0 }}
        />

        {/* Below md (the compact Settings shell) the card fills the screen
            and the label sits above its value. */}
        <Box
          sx={{
            width: "100%",
            maxWidth: "670px",
            border: "1px solid",
            borderColor: "divider",
            borderRadius: theme.spacing(1),
            padding: theme.spacing(2),
            backgroundColor: "background.paper",
          }}
        >
          <Box
            sx={{
              display: "flex",
              flexWrap: { xs: "wrap", md: "nowrap" },
              rowGap: 1,
              justifyContent: "flex-start",
              alignItems: "center",
            }}
          >
            <Typography
              variant="s1"
              sx={{
                width: { xs: "100%", md: "200px" },
                color: "text.secondary",
                fontWeight: "fontWeightRegular",
              }}
            >
              Organization name
            </Typography>
            <Box
              sx={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                width: { xs: "100%", md: "70%" },
                minWidth: 0,
                overflowWrap: "anywhere",
              }}
            >
              {isEditing ? (
                <Box
                  sx={{
                    display: "flex",
                    alignItems: "center",
                    gap: theme.spacing(1),
                    width: "100%",
                  }}
                >
                  <TextField
                    size="small"
                    value={editName}
                    onChange={(e) => setEditName(e.target.value)}
                    autoFocus
                    sx={{ flex: 1 }}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") handleSave();
                      if (e.key === "Escape") handleCancel();
                    }}
                  />
                  <LoadingButton
                    variant="contained"
                    color="primary"
                    size="small"
                    loading={isUpdating}
                    onClick={handleSave}
                  >
                    Save
                  </LoadingButton>
                  <LoadingButton
                    variant="outlined"
                    size="small"
                    onClick={handleCancel}
                    disabled={isUpdating}
                  >
                    Cancel
                  </LoadingButton>
                </Box>
              ) : (
                <>
                  <Typography
                    variant="s1"
                    sx={{
                      color: "text.primary",
                      fontWeight: "fontWeightMedium",
                    }}
                  >
                    {orgDisplayName || (
                      <CircularProgress size={14} sx={{ ml: 1 }} />
                    )}
                  </Typography>
                  <Tooltip title="Edit organization name">
                    <Iconify
                      icon="solar:pen-linear"
                      color="text.primary"
                      onClick={() => setIsEditing(true)}
                      sx={{
                        cursor: "pointer",
                        width: "20px",
                        height: "20px",
                      }}
                    />
                  </Tooltip>
                </>
              )}
            </Box>
          </Box>
        </Box>

        <OrgTwoFactorPolicySection onStatusChange={() => {}} />
      </Box>
    </>
  );
};

export default OrgSettings;
