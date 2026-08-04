import React, { useState } from "react";
import { Box, Stack, Typography, TextField, Alert } from "@mui/material";
import { LoadingButton } from "@mui/lab";
import { useMutation } from "@tanstack/react-query";
import { useSnackbar } from "src/components/snackbar";
import axiosInstance, { endpoints } from "src/utils/axios";
import { useAuthContext } from "src/auth/hooks";
import { paths } from "src/routes/paths";
import Iconify from "src/components/iconify";
import RightSectionAuth from "./RightSectionAuth";
import logger from "src/utils/logger";

export default function OrgRemovedPage() {
  const { initialize } = useAuthContext();
  const { enqueueSnackbar } = useSnackbar();
  const [orgName, setOrgName] = useState("");

  const { mutate: createOrg, isPending } = useMutation({
    mutationFn: async () => {
      const payload = {};
      if (orgName.trim()) {
        payload.organization_name = orgName.trim();
      }
      return axiosInstance.post(endpoints.auth.createOrganization, payload);
    },
    onSuccess: () => {
      enqueueSnackbar("Organization created successfully", {
        variant: "success",
      });
      // Re-initialize auth state so user gets fresh user data with the new org
      initialize()
        .then(() => {
          window.location.href = paths.auth.jwt.setup_org;
        })
        .catch((err) => {
          logger.error("Failed to re-initialize after org creation:", err);
          // Force reload to pick up the new org state
          window.location.href = paths.auth.jwt.setup_org;
        });
    },
    onError: (error) => {
      logger.error("Failed to create organization:", error);
      enqueueSnackbar(
        error?.message || error?.detail || "Failed to create organization",
        { variant: "error" },
      );
    },
  });

  const handleSubmit = (e) => {
    e.preventDefault();
    createOrg();
  };

  return (
    <Box sx={{ width: "100%", height: "100vh", display: "flex" }}>
      {/* Left Side - Content */}
      <Box
        sx={{
          width: "50%",
          height: "100vh",
          display: "flex",
          justifyContent: "center",
          bgcolor: "background.paper",
          overflowY: "auto",
        }}
      >
        <Box
          sx={{
            maxWidth: "640px",
            paddingY: "100px",
            width: "100%",
            px: 10,
            height: "fit-content",
          }}
        >
          <Stack spacing={3}>
            <Stack sx={{ mb: 1 }}>
              <Typography
                fontWeight="fontWeightSemiBold"
                sx={{
                  fontSize: "28px",
                  color: "text.primary",
                  fontFamily: "Inter",
                  lineHeight: "36px",
                }}
              >
                You&apos;re not part of
              </Typography>
              <Typography
                fontWeight="fontWeightSemiBold"
                sx={{
                  fontSize: "28px",
                  color: "text.secondary",
                  fontFamily: "Inter",
                  lineHeight: "36px",
                  maxWidth: "440px",
                }}
              >
                any organization
              </Typography>
            </Stack>

            <Alert
              icon={<Iconify icon="fluent:info-24-regular" width={20} />}
              severity="info"
              sx={{ maxWidth: "440px" }}
            >
              Your account is still active. Create a new organization to get
              started, or contact your previous admin to be re-invited.
            </Alert>

            <form onSubmit={handleSubmit}>
              <Stack spacing={2.5} sx={{ maxWidth: "440px" }}>
                <TextField
                  label="Organization Name"
                  placeholder="Enter your new organization name"
                  value={orgName}
                  onChange={(e) => setOrgName(e.target.value)}
                  size="small"
                  fullWidth
                  helperText="Leave blank to auto-generate from your email domain"
                  sx={{
                    "& .MuiOutlinedInput-root": { borderRadius: 0.5 },
                  }}
                />

                <LoadingButton
                  fullWidth
                  type="submit"
                  variant="contained"
                  color="primary"
                  loading={isPending}
                  sx={{ height: "42px", borderRadius: 0.5 }}
                >
                  Create New Organization
                </LoadingButton>
              </Stack>
            </form>
          </Stack>
        </Box>
      </Box>

      {/* Right Side */}
      <Box
        sx={{
          width: "50%",
          height: "100%",
          backgroundColor: "background.neutral",
        }}
      >
        <RightSectionAuth />
      </Box>
    </Box>
  );
}
