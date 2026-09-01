import React, { useState, useEffect } from "react";
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
import { useOrganization } from "src/contexts/OrganizationContext";
import OrgTwoFactorPolicySection from "src/sections/settings/Security/OrgTwoFactorPolicySection";
import logger from "src/utils/logger";
import {
  SS_KEY_ORG_DISPLAY_NAME,
  SS_KEY_ORG_NAME,
} from "src/utils/sessionKeys";

const OrgSettings = () => {
  const theme = useTheme();
  const { currentOrganizationName, currentOrganizationDisplayName } =
    useOrganization();

  const [isEditing, setIsEditing] = useState(false);
  const [editName, setEditName] = useState("");

  const orgDisplayName =
    currentOrganizationDisplayName || currentOrganizationName || "";

  useEffect(() => {
    setEditName(orgDisplayName);
  }, [orgDisplayName]);

  const { mutate: updateOrgName, isPending: isUpdating } = useMutation({
    mutationFn: async (name) => {
      const response = await axios.patch(endpoints.organizations.update, {
        name,
        display_name: name,
      });
      return response.data;
    },
    onSuccess: () => {
      enqueueSnackbar("Organization name updated successfully.", {
        variant: "success",
      });
      setIsEditing(false);
      // Update sessionStorage so the context picks up the new name
      sessionStorage.setItem(SS_KEY_ORG_NAME, editName.trim());
      sessionStorage.setItem(SS_KEY_ORG_DISPLAY_NAME, editName.trim());
      // Reload to refresh all contexts with the new org name
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
        <title>Organization Settings</title>
      </Helmet>
      <Box
        sx={{ display: "flex", flexDirection: "column", gap: theme.spacing(2) }}
      >
        <Box>
          <Typography
            sx={{
              typography: "m2",
              fontWeight: "fontWeightSemiBold",
              color: "text.primary",
            }}
          >
            Organization Settings
          </Typography>
          <Typography
            sx={{
              typography: "s1",
              fontWeight: "fontWeightRegular",
              color: "text.secondary",
              marginTop: theme.spacing(0.5),
            }}
          >
            Manage organization-level settings
          </Typography>
        </Box>

        <Box
          sx={{
            width: "670px",
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
              justifyContent: "flex-start",
              alignItems: "center",
            }}
          >
            <Typography
              variant="s1"
              sx={{
                width: "200px",
                color: "text.secondary",
                fontWeight: "fontWeightRegular",
              }}
            >
              Organization Name
            </Typography>
            <Box
              sx={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                width: "70%",
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
                  <Tooltip title="Edit Organization Name">
                    <Iconify
                      icon="fluent:edit-12-regular"
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
