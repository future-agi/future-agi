import React, { useEffect, useState } from "react";
import { Helmet } from "react-helmet-async";
import Typography from "@mui/material/Typography";
import { Box, useTheme, Skeleton, CircularProgress } from "@mui/material";
import axios, { endpoints } from "src/utils/axios";
import Tooltip from "@mui/material/Tooltip";
import ProfileInfoModal from "./ProfileInfoModal";
import { trackEvent, Events } from "src/utils/Mixpanel";
import { enqueueSnackbar } from "notistack";
import { useAuthContext } from "src/auth/hooks";
import Iconify from "src/components/iconify";
import logger from "src/utils/logger";
import { useMutation, useQuery } from "@tanstack/react-query";
import { LoadingButton } from "@mui/lab";
import TotpSection from "src/sections/settings/Security/TotpSection";
import PasskeySection from "src/sections/settings/Security/PasskeySection";
import RecoveryCodesSection from "src/sections/settings/Security/RecoveryCodesSection";

const ProfileSettings = () => {
  const theme = useTheme();
  const { updateUserData } = useAuthContext();

  const {
    data: twoFaStatus,
    isLoading: is2faLoading,
    refetch: refetch2fa,
  } = useQuery({
    queryKey: ["2fa-status"],
    queryFn: async () => {
      const res = await axios.get(endpoints.twoFactor.status);
      return res.data;
    },
  });
  const [userName, setUserName] = useState("");
  const [userEmail, setUserEmail] = useState("");

  const [openProfileInfoModal, setOpenProfileInfoModal] = useState(false);
  const [refreshData, setRefreshData] = useState(false);
  const [resetPasswordDisabled, setResetPasswordDisabled] = useState(false);
  const twoFactorEnabled =
    twoFaStatus?.two_factor_enabled ?? twoFaStatus?.twoFactorEnabled;
  const recoveryCodesRemaining =
    twoFaStatus?.recovery_codes_remaining ??
    twoFaStatus?.recoveryCodesRemaining;

  const { mutate: getUserInfo, isPending: isGetUserInfoLoading } = useMutation({
    mutationFn: async () => {
      const response = await axios.get(endpoints.stripe.getUserProfileDetails);
      return response.data;
    },
    onSuccess: (data) => {
      if (updateUserData) {
        updateUserData(data);
      }
      setUserName(data.name);
      setUserEmail(data.email);
    },
    onError: (error) => {
      logger.error("Error fetching user info:", error);
    },
  });

  const { mutate: resetPassword, isPending: isResettingPassword } = useMutation(
    {
      mutationFn: async () => {
        const response = await axios.post(
          endpoints.auth.passwordResetInitiate,
          {
            email: userEmail,
          },
        );
        return response;
      },
      onSuccess: (response) => {
        if (response.status === 200) {
          enqueueSnackbar(
            "A link to change your password has been sent to your email",
            {
              variant: "success",
            },
          );
          // Don't disable reset or show timer on success
          setResetPasswordDisabled(false);
        } else {
          enqueueSnackbar("Failed to send password reset link", {
            variant: "error",
          });
        }
      },
      onError: (error) => {
        if (error.statusCode == 403) {
          enqueueSnackbar(
            "Password reset request limit reached. Please wait 1 hour before trying again.",
            {
              variant: "warning",
            },
          );
          setResetPasswordDisabled(true);
        } else {
          enqueueSnackbar("Failed to send password reset link", {
            variant: "error",
          });
        }
      },
    },
  );

  const handleGetUserInfo = () => {
    getUserInfo();
  };

  const handleResetPassword = () => {
    if (resetPasswordDisabled) {
      return;
    }

    trackEvent(Events.restPassClicked);
    resetPassword();
  };

  useEffect(() => {
    handleGetUserInfo();
  }, [refreshData]);

  return (
    <>
      <Helmet>
        <title>Profile</title>
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
            Profile Details
          </Typography>
          <Typography
            sx={{
              typography: "s1",
              fontWeight: "fontWeightRegular",
              color: "text.primary",
              marginTop: (theme) => theme.spacing(0.5),
            }}
          >
            Manage your profile details
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
              borderBottom: "1px solid",
              borderColor: "divider",
              paddingBottom: theme.spacing(2.5),
            }}
          >
            <Typography
              variant="s1"
              sx={{
                width: "200px",
                color: "text.primary",
                fontWeight: "fontWeightRegular",
              }}
            >
              Full Name
            </Typography>
            <Box
              sx={{
                display: "flex",
                justifyContent: "space-between",
                width: "70%",
              }}
            >
              <Typography
                variant="s1"
                sx={{
                  color: "text.primary",
                  fontWeight: "fontWeightMedium",
                }}
              >
                {isGetUserInfoLoading ? (
                  <Skeleton
                    animation="wave"
                    variant="text"
                    width={150}
                    height={20}
                  />
                ) : (
                  userName
                )}
              </Typography>
              <Tooltip title="Edit Name">
                <Iconify
                  icon="fluent:edit-12-regular"
                  color="text.primary"
                  onClick={() => {
                    trackEvent(Events.editNameClicked);
                    setOpenProfileInfoModal(true);
                  }}
                  sx={{
                    cursor: "pointer",
                    width: "20px",
                    height: "20px",
                  }}
                />
              </Tooltip>
            </Box>
          </Box>
          <Box
            sx={{
              display: "flex",
              justifyContent: "flex-start",
              borderBottom: "1px solid",
              borderColor: "background.neutral",
              marginTop: theme.spacing(2.5),
              paddingBottom: theme.spacing(2.5),
            }}
          >
            <Typography
              variant="s1"
              sx={{
                width: "200px",
                color: "text.primary",
                fontWeight: "fontWeightRegular",
              }}
            >
              Email
            </Typography>
            <Box
              sx={{
                display: "flex",
                justifyContent: "space-between",
                width: "70%",
              }}
            >
              <Typography
                variant="s1"
                sx={{
                  color: "text.primary",
                  fontWeight: "fontWeightMedium",
                }}
              >
                {isGetUserInfoLoading ? (
                  <Skeleton
                    animation="wave"
                    variant="text"
                    width={250}
                    height={20}
                  />
                ) : (
                  userEmail
                )}
              </Typography>
              <Box sx={{ width: "30px" }} />
            </Box>
          </Box>
          <Box
            sx={{
              display: "flex",
              justifyContent: "flex-start",
              marginTop: theme.spacing(2.5),
            }}
          >
            <Typography
              variant="s1"
              sx={{
                width: "200px",
                color: "text.primary",
                fontWeight: "fontWeightRegular",
              }}
            >
              Password
            </Typography>
            <Box
              sx={{
                display: "flex",
                justifyContent: "space-between",
                width: "70%",
              }}
            >
              <Typography
                variant="s1"
                sx={{
                  color: "text.primary",
                  fontWeight: "fontWeightMedium",
                }}
              >
                **********
              </Typography>

              <LoadingButton
                variant="outlined"
                color="primary"
                size="small"
                loading={isResettingPassword}
                onClick={
                  !resetPasswordDisabled ? handleResetPassword : undefined
                }
                sx={{
                  width: "156px",
                  "&:disabled": {
                    color: "common.white",
                    backgroundColor: "action.hover",
                  },
                }}
                disabled={resetPasswordDisabled}
              >
                <Typography variant="s2" fontWeight={"fontWeightMedium"}>
                  Reset Password
                </Typography>
              </LoadingButton>
            </Box>
          </Box>
        </Box>

        {resetPasswordDisabled && (
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              width: "670px",
              marginTop: theme.spacing(2),
              backgroundColor: "background.neutral",
              paddingX: theme.spacing(1.5),
              paddingY: theme.spacing(0.5),
              borderRadius: theme.spacing(1),
            }}
          >
            <Iconify
              icon="bx:error"
              color="red.500"
              sx={{
                width: "18px",
                height: "18px",
              }}
            />
            <Typography
              variant="s1"
              fontWeight="fontWeightRegular"
              color="text.primary"
              sx={{ marginLeft: "8px" }}
            >
              Password reset request limit reached. Please wait 1 hour before
              trying again.
            </Typography>
          </Box>
        )}

        {/* Security Section */}
        <Box sx={{ marginTop: theme.spacing(2) }}>
          <Typography
            sx={{
              typography: "m2",
              fontWeight: "fontWeightSemiBold",
              color: "text.primary",
            }}
          >
            Security
          </Typography>
          <Typography
            sx={{
              typography: "s1",
              fontWeight: "fontWeightRegular",
              color: "text.secondary",
              marginTop: theme.spacing(0.5),
              marginBottom: theme.spacing(2),
            }}
          >
            Manage two-factor authentication and passkeys
          </Typography>

          {is2faLoading ? (
            <Box
              sx={{
                display: "flex",
                justifyContent: "center",
                alignItems: "center",
                height: "100px",
              }}
            >
              <CircularProgress size={24} />
            </Box>
          ) : (
            <Box
              sx={{
                display: "flex",
                flexDirection: "column",
                gap: theme.spacing(2),
              }}
            >
              <TotpSection
                totp={twoFaStatus?.methods?.totp}
                onStatusChange={refetch2fa}
              />
              <PasskeySection
                passkey={twoFaStatus?.methods?.passkey}
                onStatusChange={refetch2fa}
              />
              {twoFactorEnabled && (
                <RecoveryCodesSection
                  remaining={recoveryCodesRemaining}
                  hasTotp={twoFaStatus?.methods?.totp?.enabled}
                  onStatusChange={refetch2fa}
                />
              )}
            </Box>
          )}
        </Box>

        <ProfileInfoModal
          open={openProfileInfoModal}
          onClose={() => setOpenProfileInfoModal(false)}
          fullName={userName}
          setRefreshData={setRefreshData}
        />
      </Box>
    </>
  );
};

export default ProfileSettings;
