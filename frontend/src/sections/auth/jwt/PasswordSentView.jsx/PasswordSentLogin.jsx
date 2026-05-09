import { LoadingButton } from "@mui/lab";
import {
  Box,
  Button,
  CircularProgress,
  IconButton,
  InputAdornment,
  Stack,
  Typography,
} from "@mui/material";
import { useMutation } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import PropTypes from "prop-types";
import React from "react";
import { RHFTextField } from "src/components/hook-form";
import Iconify from "src/components/iconify";
import axios, { endpoints } from "src/utils/axios";

const PasswordSentLogin = ({
  email,
  setRegisterSuccess,
  password,
  errorMsg,
  isSubmitting,
}) => {
  const { mutate: handleResendPassword, isPending: isResending } = useMutation({
    mutationFn: (email) =>
      axios.post(endpoints.auth.passwordResetInitiate, {
        email,
        resend_email: true,
      }),
    meta: {
      errorHandled: true,
    },
    onSuccess: () => {
      enqueueSnackbar({
        variant: "success",
        message:
          "Your password has been sent to your registered email address.",
        autoHideDuration: 3000,
      });
    },
    onError: (err) => {
      enqueueSnackbar({
        variant: "error",
        message: `"Error resending password:"${err}`,
        autoHideDuration: 3000,
      });
    },
  });

  return (
    <Stack textAlign={"left"} width={"640px"} paddingX={10} gap={4}>
      <Box sx={{ display: "flex", flexDirection: "column" }}>
        <Typography
          fontWeight={"fontWeightSemiBold"}
          sx={{
            fontSize: "28px",
            color: "text.primary",
            fontFamily: "Inter",
            lineHeight: "36px",
          }}
        >
          We sent you a password
        </Typography>
        <Typography
          fontWeight={"fontWeightSemiBold"}
          sx={{
            fontSize: "28px",
            color: "text.secondary",
            maxWidth: "440px",
            fontFamily: "Inter",
            lineHeight: "36px",
          }}
        >
          Check your email
        </Typography>
      </Box>
      <Stack spacing={2.5}>
        <Box
          sx={{
            display: "flex",
            border: "1px solid",
            borderColor: "divider",
            flexDirection: "column",
            padding: 2.5,
            backgroundColor: "background.neutral",
            alignItems: "flex-start",
            justifyContent: "center",
            width: "440px",
          }}
        >
          <Typography variant="s2" fontWeight={"Medium"}>
            Business Email address
          </Typography>
          <Typography variant="m2" fontWeight={"Medium"}>
            {email}
          </Typography>
        </Box>
        <Box width={"440px"} display={"flex"} flexDirection={"column"} gap={2}>
          <RHFTextField
            name="password"
            label="Password"
            placeholder="Enter password"
            type={password.value ? "text" : "password"}
            autoComplete="current-password"
            sx={{ "& .MuiOutlinedInput-root": { borderRadius: 0.5 } }}
            {...(errorMsg && {
              error: true,
              helperText: "Please recheck the password",
            })}
            InputProps={{
              endAdornment: (
                <InputAdornment position="end">
                  <IconButton onClick={password.onToggle} edge="end">
                    <Iconify
                      icon={
                        password.value
                          ? "solar:eye-bold"
                          : "solar:eye-closed-bold"
                      }
                    />
                  </IconButton>
                </InputAdornment>
              ),
            }}
            size="small"
          />
          <Typography variant="m3" fontWeight={"fontWeightMedium"}>
            Didn&apos;t receive your password?{" "}
            <Typography
              component={"span"}
              onClick={() => !isResending && handleResendPassword(email)}
              fontWeight={"fontWeightMedium"}
              sx={{
                color: isResending ? "text.disabled" : "primary.main",
                cursor: isResending ? "default" : "pointer",
                display: "inline-flex",
                alignItems: "center",
                gap: 0.5,
                pointerEvents: isResending ? "none" : "auto",
              }}
            >
              {isResending && <CircularProgress size={12} color="inherit" />}
              Resend
            </Typography>
          </Typography>
          <Box sx={{ width: "100%" }}>
            <LoadingButton
              fullWidth
              color="primary"
              size="large"
              variant="contained"
              type="submit"
              sx={{ height: "42px" }}
              loading={isSubmitting}
            >
              Continue
            </LoadingButton>
          </Box>
        </Box>
      </Stack>

      <Button
        sx={{ color: "primary.main" }}
        onClick={() => setRegisterSuccess(false)}
        startIcon={
          <Iconify icon="line-md:chevron-left" width={16} height={16} />
        }
      >
        <Typography
          variant="s1.2"
          fontWeight={"Medium"}
          sx={{ paddingRight: 4.5 }}
        >
          Back to sign up
        </Typography>
      </Button>
    </Stack>
  );
};

export default PasswordSentLogin;

PasswordSentLogin.propTypes = {
  email: PropTypes.string.isRequired,
  password: PropTypes.object,
  setRegisterSuccess: PropTypes.func,
  errorMsg: PropTypes.string,
  isSubmitting: PropTypes.bool,
};
