import React, { useState } from "react";
import { useForm } from "react-hook-form";
import Link from "@mui/material/Link";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import LoadingButton from "@mui/lab/LoadingButton";
import { useNavigate } from "react-router-dom";
import { paths } from "src/routes/paths";
import { RouterLink } from "src/routes/components";
import { Events, trackEvent, PropertyName } from "src/utils/Mixpanel";
import Iconify from "src/components/iconify";
import { zodResolver } from "@hookform/resolvers/zod";
import { ForgotPasswordSchema } from "./validation";
import axios, { endpoints } from "src/utils/axios";
import { useMutation } from "@tanstack/react-query";
import { useSnackbar } from "src/components/snackbar";
import { Alert, Box, Divider } from "@mui/material";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import RightSectionAuth from "./RightSectionAuth";

// ----------------------------------------------------------------------

// Take the path off the link rather than following it: it is built from the
// backend's APP_URL, which need not match the origin serving the app.
const RESET_ROUTE = `${paths.auth.jwt.verify}/`;

const toResetPath = (resetLink) => {
  const start = resetLink?.indexOf(RESET_ROUTE) ?? -1;
  return start === -1 ? null : resetLink.slice(start);
};

export default function ForgotPasswordView() {
  const { enqueueSnackbar } = useSnackbar();
  const navigate = useNavigate();
  const defaultValues = { email: "" };
  const [errorMsg, setErrorMsg] = useState("");

  const { handleSubmit, control, reset, getValues } = useForm({
    resolver: zodResolver(ForgotPasswordSchema),
    defaultValues,
  });

  const { mutate: sendPasswordResetLink, isPending: sendingRequest } =
    useMutation({
      mutationFn: (body) =>
        axios.post(`${endpoints.auth.passwordResetInitiate}`, body),
      onSuccess: (data, variable) => {
        const result = data?.data?.result;
        trackEvent(Events.sendPasswordClicked, {
          [PropertyName.email]: variable?.email,
        });
        reset();

        // Never log or track the link: it sets the password on that account.
        const resetPath = toResetPath(result?.reset_link);
        if (resetPath) {
          navigate(resetPath);
          return;
        }

        enqueueSnackbar(
          result?.message || "Password reset link is sent to your email",
          { variant: "success" },
        );
      },
      meta: { errorHandled: true },
      onError: (error) => {
        setErrorMsg(
          typeof error?.result === "string"
            ? error?.result
            : "An unexpected error occurred.",
        );
        const email = getValues("email");
        trackEvent(Events.resetPasswordRequest, {
          [PropertyName.email]: email,
          [PropertyName.status]: false,
        });
      },
    });

  const onSubmit = handleSubmit(async (data) => {
    trackEvent(Events.resetPasswordRequest, {
      [PropertyName.email]: data.email,
      [PropertyName.status]: false,
    });
    setErrorMsg("");
    sendPasswordResetLink(data);
  });

  const renderHead = (
    <Stack sx={{ mb: 4 }}>
      <Typography
        fontWeight={"fontWeightSemiBold"}
        sx={{
          fontSize: "28px",
          color: "text.primary",
          fontFamily: "Inter",
          lineHeight: "36px",
        }}
      >
        Forgot password?
      </Typography>
      <Typography
        fontWeight={"fontWeightSemiBold"}
        sx={{
          fontSize: "28px",
          lineHeight: "36px",
          color: "text.secondary",
          maxWidth: "440px",
          fontFamily: "Inter",
        }}
      >
        Enter the email address associated with your account
      </Typography>
    </Stack>
  );

  const renderForm = (
    <Stack
      spacing={2.5}
      display={"flex"}
      alignItems="center"
      maxWidth={"440px"}
    >
      <Typography
        variant="m3"
        fontWeight={"fontWeightMedium"}
        alignSelf={"flex-start"}
      >
        We will email you a link to reset your password
      </Typography>
      <FormTextFieldV2
        size="small"
        placeholder="Email"
        label="Email"
        control={control}
        fieldName="email"
        fullWidth
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
        type="submit"
        variant="contained"
        sx={{ height: "42px", borderRadius: 0.5 }}
        loading={sendingRequest}
        color="primary"
      >
        Send Request
      </LoadingButton>

      <Divider flexItem>or</Divider>

      <Link
        component={RouterLink}
        href={paths.auth.jwt.login}
        color="primary"
        variant="subtitle2"
        sx={{ alignItems: "center", display: "inline-flex" }}
      >
        <Iconify icon="eva:arrow-ios-back-fill" width={16} />
        Back to sign in
      </Link>
    </Stack>
  );

  return (
    <Box sx={{ width: "100%", height: "100vh", display: "flex" }}>
      {/* Left Side - Form */}
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
            width: "640px",
            px: 10,
            paddingY: "100px",
            height: "fit-content",
          }}
        >
          <form onSubmit={onSubmit}>
            {renderHead}
            {renderForm}
          </form>
        </Box>
      </Box>

      {/* Right Side - Image with Text Overlay */}
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
