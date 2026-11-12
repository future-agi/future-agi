import React, { useState } from "react";
import { Box, Stack, Typography, TextField, Alert, Link } from "@mui/material";
import { LoadingButton } from "@mui/lab";
import { useMutation } from "@tanstack/react-query";
import { useSnackbar } from "src/components/snackbar";
import axiosInstance, { endpoints } from "src/utils/axios";
import { useAuthContext } from "src/auth/hooks";
import { signOutToSignIn } from "src/auth/sign-out";
import { paths } from "src/routes/paths";
import { useRouter } from "src/routes/hooks";
import Iconify from "src/components/iconify";
import RightSectionAuth from "./RightSectionAuth";
import {
  authSplitSx,
  authFormPanelSx,
  authAsideSx,
  authFormGutterSx,
} from "src/layouts/auth/responsive-styles";
import logger from "src/utils/logger";

export default function OrgRemovedPage() {
  const router = useRouter();
  const { initialize, logout, user } = useAuthContext();
  const { enqueueSnackbar } = useSnackbar();
  const [orgName, setOrgName] = useState("");

  // Every route sends a signed-in user with no organization here, and the
  // sign-in page sends a signed-in visitor back, so signing out (wrong
  // account, shared device) has to be possible from this page.
  const handleSignOut = () => signOutToSignIn(logout, router.replace);

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
    <Box sx={authSplitSx}>
      {/* Left Side - Content */}
      <Box
        sx={{
          ...authFormPanelSx,
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
            ...authFormGutterSx,
            height: "fit-content",
          }}
        >
          <Stack spacing={3}>
            <Stack sx={{ mb: 1 }}>
              <Typography
                fontWeight="fontWeightSemiBold"
                sx={{
                  fontSize: "24px",
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
                  fontSize: "24px",
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
              icon={<Iconify icon="solar:info-circle-linear" width={20} />}
              severity="info"
              sx={{ maxWidth: "440px" }}
            >
              Your account is still active. Create a new organization, or ask
              your admin to re-invite you and open the invitation email to
              rejoin.
            </Alert>

            <form onSubmit={handleSubmit}>
              <Stack spacing={2} sx={{ maxWidth: "440px" }}>
                <TextField
                  label="Organization Name"
                  placeholder="Enter your new organization name"
                  value={orgName}
                  onChange={(e) => setOrgName(e.target.value)}
                  size="medium"
                  fullWidth
                  helperText="Leave blank to auto-generate from your email domain"
                  sx={{
                    "& .MuiOutlinedInput-root": { borderRadius: "4px" },
                  }}
                />

                <LoadingButton
                  size="large"
                  fullWidth
                  type="submit"
                  variant="contained"
                  color="primary"
                  loading={isPending}
                >
                  Create New Organization
                </LoadingButton>
              </Stack>
            </form>

            <Typography
              variant="body2"
              color="text.secondary"
              sx={{
                maxWidth: "440px",
                textAlign: "center",
                overflowWrap: "anywhere",
              }}
            >
              {user?.email ? `Signed in as ${user.email}. Not you? ` : null}
              <Link
                component="button"
                type="button"
                variant="subtitle2"
                onClick={handleSignOut}
                sx={{ color: "primary.main", verticalAlign: "baseline" }}
              >
                Sign out
              </Link>
            </Typography>
          </Stack>
        </Box>
      </Box>

      {/* Right Side */}
      <Box sx={authAsideSx}>
        <RightSectionAuth />
      </Box>
    </Box>
  );
}
