import React, { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import LoadingButton from "@mui/lab/LoadingButton";
import InputAdornment from "@mui/material/InputAdornment";
import IconButton from "@mui/material/IconButton";
import { Alert, Box } from "@mui/material";
import { trackEvent, Events, PropertyName } from "src/utils/Mixpanel";
import { paths } from "src/routes/paths";
import Iconify from "src/components/iconify";
import { zodResolver } from "@hookform/resolvers/zod";
import { ResetPasswordSchema } from "./validation";
import { useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useSnackbar } from "src/components/snackbar";
import { useNavigate } from "react-router";
import { useParams } from "src/routes/hooks";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import RightSectionAuth from "./RightSectionAuth";

export default function ResetPasswordView() {
  const [showPassword, setShowPassword] = useState({
    password: false,
    confirmPassword: false,
  });
  const [errorMsg, setErrorMsg] = useState("");
  const { enqueueSnackbar } = useSnackbar();
  const { uuid, token } = useParams();
  const navigate = useNavigate();

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

  const { handleSubmit, control, reset, watch, trigger } = useForm({
    mode: "onChange",
    defaultValues,
    resolver: zodResolver(ResetPasswordSchema),
  });

  const newPassword = watch("newPassword");
  const repeatPassword = watch("repeatPassword");

  const handlePasswordChange = () => {
    trackEvent(Events.passwordChangeEvents, {
      [PropertyName.formFields]: { new_password_entered: true },
    });
  };

  const handleRepeatPasswordChange = () => {
    trackEvent(Events.passwordChangeEvents, {
      [PropertyName.formFields]: { re_entered_new_password_entered: true },
    });
  };

  useEffect(() => {
    if (repeatPassword) {
      trigger("repeatPassword");
    }
  }, [newPassword, trigger, repeatPassword]);

  useEffect(() => {
    if (newPassword && repeatPassword && newPassword === repeatPassword) {
      trackEvent(Events.passwordChangeEvents, {
        [PropertyName.formFields]: { password_match: true },
      });
    }
  }, [newPassword, repeatPassword]);

  const { mutate: setNewPassword, isPending } = useMutation({
    mutationFn: (body) =>
      axios.post(endpoints.auth.passwordReset(uuid, token), body),
    onSuccess: () => {
      trackEvent(Events.passwordChangeEvents, {
        [PropertyName.formFields]: {
          new_password_entered: true,
          re_entered_new_password_entered: true,
          password_match: true,
          updateClicked: true,
        },
      });
      enqueueSnackbar("New password set successfully", { variant: "success" });
      reset();
      navigate(paths.auth.jwt.login);
    },
    meta: { errorHandled: true },
    onError: (error) => {
      setErrorMsg(
        typeof error?.result === "string"
          ? error?.result
          : "An unexpected error occurred.",
      );
    },
  });

  const onSubmit = handleSubmit(async (data) => {
    setErrorMsg("");
    setNewPassword({
      new_password: data.newPassword,
      repeat_password: data.repeatPassword,
    });
    trackEvent(Events.passwordChangeEvents, {
      [PropertyName.formFields]: { updateClicked: true },
    });
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
        Reset Password
      </Typography>
    </Stack>
  );

  const renderForm = (
    <Stack spacing={3} maxWidth={"440px"}>
      <Typography
        variant="m3"
        fontWeight={"fontWeightMedium"}
        fontFamily={"Inter"}
        lineHeight={"24px"}
      >
        Please reset your password. The password should have at least 8
        characters with at least 1 letter and 1 number or special character
      </Typography>
      <FormTextFieldV2
        size="small"
        fullWidth
        control={control}
        placeholder="Enter your password"
        fieldName="newPassword"
        label="Password"
        type={showPassword.password ? "text" : "password"}
        onChange={handlePasswordChange}
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
        label="Confirm  Password"
        type={showPassword.confirmPassword ? "text" : "password"}
        onChange={handleRepeatPasswordChange}
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
        Update Password
      </LoadingButton>
      {/* 
      <Divider flexItem>or</Divider>

      <Link
        component={RouterLink}
        href={paths.auth.jwt.login}
        color="primary"
        variant="subtitle2"
        sx={{ alignItems: "center", display: "inline-flex" }}
      >
        <Iconify icon="eva:arrow-ios-back-fill" width={16} />
        Return to sign in
      </Link> */}
    </Stack>
  );

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
          <form onSubmit={onSubmit}>
            {renderHead}
            {renderForm}
          </form>
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
