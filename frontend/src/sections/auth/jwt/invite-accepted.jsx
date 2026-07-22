import React, { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import Link from "@mui/material/Link";
import LoadingButton from "@mui/lab/LoadingButton";
import InputAdornment from "@mui/material/InputAdornment";
import IconButton from "@mui/material/IconButton";
import Button from "@mui/material/Button";
import { Alert, Box, CircularProgress } from "@mui/material";
import { paths } from "src/routes/paths";
import { RouterLink } from "src/routes/components";
import Iconify from "src/components/iconify";
import { zodResolver } from "@hookform/resolvers/zod";
import { ResetPasswordSchema } from "./validation";
import { useMutation } from "@tanstack/react-query";
import axiosInstance, { endpoints } from "src/utils/axios";
import { useSnackbar } from "src/components/snackbar";
import { useNavigate, useLocation } from "react-router-dom";
import { useParams } from "src/routes/hooks";
import { useAuthContext } from "src/auth/hooks";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import RightSectionAuth from "./RightSectionAuth";

export default function InviteAcceptedPage() {
  const [showPassword, setShowPassword] = useState({
    password: false,
    confirmPassword: false,
  });
  const [errorMsg, setErrorMsg] = useState("");
  const [tokenStatus, setTokenStatus] = useState("loading"); // "loading" | "valid" | "invalid" | "wrong_user"
  const { enqueueSnackbar } = useSnackbar();
  const { uuid, token } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const { login, logout } = useAuthContext();

  const orgName = location.state?.orgName || "";

  // Validate invite token on mount
  useEffect(() => {
    let cancelled = false;

    const validateToken = async () => {
      try {
        await axiosInstance.get(
          endpoints.invite.accept_invitation(uuid, token),
        );
        if (!cancelled) {
          setTokenStatus("valid");
        }
      } catch (error) {
        if (!cancelled) {
          // Check if this is a "wrong user" error
          if (error?.code === "authenticated_user_mismatch") {
            setTokenStatus("wrong_user");
            setErrorMsg(
              error?.error ||
                "You are logged in as a different user. Please logout first.",
            );
          } else {
            setTokenStatus("invalid");
          }
        }
      }
    };

    validateToken();

    return () => {
      cancelled = true;
    };
  }, [uuid, token]);

  const defaultValues = {
    newPassword: "",
    repeatPassword: "",
  };

  const handleShowPassword = (name) => {
    setShowPassword((pre) => ({
      ...pre,
      [name]: !pre[name],
    }));
  };

  const { handleSubmit, control, watch, trigger } = useForm({
    mode: "onChange",
    defaultValues,
    resolver: zodResolver(ResetPasswordSchema),
  });

  const { mutate: setNewPassword, isPending } = useMutation({
    mutationFn: (body) =>
      axiosInstance.post(endpoints.invite.accept_invitation(uuid, token), {
        new_password: body.newPassword,
        repeat_password: body.repeatPassword,
      }),
    onSuccess: async (response) => {
      try {
        await login(response);
        if (response?.data?.is_first_login) {
          navigate(paths.auth.jwt.setup_org);
        } else {
          navigate(paths.dashboard.root);
        }
      } catch {
        enqueueSnackbar("Password set successfully. Please log in.", {
          variant: "success",
        });
        navigate(paths.auth.jwt.login);
      }
    },
    meta: { errorHandled: true },
    onError: (error) => {
      setErrorMsg(
        typeof error?.result === "string"
          ? error.result
          : error?.error || "An unexpected error occurred.",
      );
    },
  });

  const onSubmit = handleSubmit(async (data) => {
    setErrorMsg("");
    setNewPassword(data);
  });

  const renderHead = (
    <Stack sx={{ mb: 4 }}>
      <Typography
        fontWeight="fontWeightSemiBold"
        sx={{
          fontSize: "28px",
          color: "text.primary",
          fontFamily: "Inter",
          lineHeight: "36px",
        }}
      >
        Set Your Password
      </Typography>
      {orgName && (
        <Typography
          fontWeight="fontWeightRegular"
          sx={{
            fontSize: "16px",
            color: "text.secondary",
            fontFamily: "Inter",
            lineHeight: "24px",
            mt: 1,
          }}
        >
          Welcome to {orgName}! Set a password to get started.
        </Typography>
      )}
    </Stack>
  );

  const renderForm = (
    <Stack spacing={3} maxWidth="440px">
      <Typography
        variant="m3"
        fontWeight="fontWeightMedium"
        fontFamily="Inter"
        lineHeight="24px"
      >
        Please set your password. The password should have at least 8 characters
        with at least 1 letter and 1 number or special character
      </Typography>
      <FormTextFieldV2
        size="small"
        fullWidth
        control={control}
        placeholder="Enter your password"
        fieldName="newPassword"
        label="Password"
        onChange={() => {
          if (watch("repeatPassword")) trigger("repeatPassword");
        }}
        type={showPassword.password ? "text" : "password"}
        InputProps={{
          endAdornment: (
            <InputAdornment position="end">
              <IconButton
                onClick={() => handleShowPassword("password")}
                edge="end"
              >
                <Iconify
                  icon={
                    showPassword.password
                      ? "solar:eye-bold"
                      : "solar:eye-closed-bold"
                  }
                />
              </IconButton>
            </InputAdornment>
          ),
        }}
      />
      <FormTextFieldV2
        fullWidth
        size="small"
        control={control}
        fieldName="repeatPassword"
        placeholder="Confirm your password"
        label="Confirm Password"
        type={showPassword.confirmPassword ? "text" : "password"}
        InputProps={{
          endAdornment: (
            <InputAdornment position="end">
              <IconButton
                onClick={() => handleShowPassword("confirmPassword")}
                edge="end"
              >
                <Iconify
                  icon={
                    showPassword.confirmPassword
                      ? "solar:eye-bold"
                      : "solar:eye-closed-bold"
                  }
                />
              </IconButton>
            </InputAdornment>
          ),
        }}
      />
      {!!errorMsg && (
        <Alert
          icon={<Iconify icon="fluent:warning-24-regular" color="red.500" />}
          severity="error"
          sx={{
            color: "red.500",
            border: "1px solid",
            borderColor: "red.200",
            backgroundColor: "red.o5",
            width: "100%",
          }}
        >
          {errorMsg}
        </Alert>
      )}
      <LoadingButton
        fullWidth
        size="large"
        type="submit"
        variant="contained"
        loading={isPending}
        color="primary"
        sx={{
          borderRadius: 0.5,
          height: "42px",
        }}
      >
        Set Password & Continue
      </LoadingButton>
    </Stack>
  );

  const renderTokenInvalid = (
    <Stack sx={{ maxWidth: "440px" }} spacing={3}>
      <Typography
        fontWeight="fontWeightSemiBold"
        sx={{
          fontSize: "28px",
          color: "text.primary",
          fontFamily: "Inter",
          lineHeight: "36px",
        }}
      >
        Invite Link Expired
      </Typography>
      <Alert
        icon={<Iconify icon="fluent:warning-24-regular" color="red.500" />}
        severity="error"
        sx={{
          color: "red.500",
          border: "1px solid",
          borderColor: "red.200",
          backgroundColor: "red.o5",
          width: "100%",
        }}
      >
        This invite link has expired or is invalid.
      </Alert>
      <Typography
        variant="body2"
        sx={{
          color: "text.secondary",
          fontFamily: "Inter",
        }}
      >
        Contact your administrator for a new invite.
      </Typography>
      <Link
        component={RouterLink}
        href={paths.auth.jwt["forget-password"]}
        variant="subtitle2"
        color="primary"
        underline="hover"
      >
        Go to Forgot Password
      </Link>
    </Stack>
  );

  const renderLoading = (
    <Stack spacing={2} alignItems="center" sx={{ py: 10 }}>
      <CircularProgress size={32} />
      <Typography
        fontWeight="fontWeightMedium"
        sx={{
          fontSize: "14px",
          color: "text.secondary",
          fontFamily: "Inter",
        }}
      >
        Validating invite link...
      </Typography>
    </Stack>
  );

  const handleLogoutAndRetry = async () => {
    try {
      await logout();
      // After logout, reload the page to retry the invitation
      window.location.reload();
    } catch {
      // If logout fails, just redirect to login
      window.location.href = "/auth/jwt/login";
    }
  };

  const renderWrongUser = (
    <Stack sx={{ maxWidth: "440px" }} spacing={3}>
      <Typography
        fontWeight="fontWeightSemiBold"
        sx={{
          fontSize: "28px",
          color: "text.primary",
          fontFamily: "Inter",
          lineHeight: "36px",
        }}
      >
        Wrong Account
      </Typography>
      <Alert
        icon={<Iconify icon="fluent:warning-24-regular" color="warning.main" />}
        severity="warning"
        sx={{
          color: "warning.dark",
          border: "1px solid",
          borderColor: "warning.light",
          backgroundColor: "warning.lighter",
          width: "100%",
        }}
      >
        {errorMsg}
      </Alert>
      <Typography
        variant="body2"
        sx={{
          color: "text.secondary",
          fontFamily: "Inter",
        }}
      >
        This invitation is for a different account. Please logout and try again
        with the correct account.
      </Typography>
      <Button
        fullWidth
        size="large"
        variant="contained"
        color="primary"
        onClick={handleLogoutAndRetry}
        sx={{
          borderRadius: 0.5,
          height: "42px",
        }}
      >
        Logout & Try Again
      </Button>
    </Stack>
  );

  const renderPageContent = () => {
    if (tokenStatus === "loading") return renderLoading;
    if (tokenStatus === "invalid") return renderTokenInvalid;
    if (tokenStatus === "wrong_user") return renderWrongUser;
    return (
      <form onSubmit={onSubmit}>
        {renderHead}
        {renderForm}
      </form>
    );
  };

  return (
    <Box sx={{ width: "100%", height: "100vh", display: "flex" }}>
      <Box
        sx={{
          width: "50%",
          height: "100vh",
          display: "flex",
          justifyContent: "center",
          bgcolor: "background.paper",
          paddingY: "100px",
          overflowY: "auto",
        }}
      >
        <Box sx={{ width: "640px", px: 10, height: "fit-content" }}>
          {renderPageContent()}
        </Box>
      </Box>

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
