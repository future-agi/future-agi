import React, { useState, useEffect, useCallback } from "react";
import * as Yup from "yup";
import { useForm } from "react-hook-form";
import { yupResolver } from "@hookform/resolvers/yup";

import Link from "@mui/material/Link";
import Alert from "@mui/material/Alert";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import LoadingButton from "@mui/lab/LoadingButton";
import Divider from "@mui/material/Divider";
import { Box, Button, IconButton, InputAdornment } from "@mui/material";
import { useNavigate, useLocation } from "react-router-dom";
import { paths } from "src/routes/paths";
import { useAuthContext } from "src/auth/hooks";
import { enqueueSnackbar } from "src/components/snackbar";
import axios, { endpoints } from "src/utils/axios";
import { Events, trackEvent, PropertyName } from "src/utils/Mixpanel";
import { trackSignupConversion } from "src/utils/googleAds";
import { trackRedditSignup } from "src/utils/redditAds";
import { trackTwitterSignup } from "src/utils/twitterAds";
import Iconify from "src/components/iconify";
import FormProvider, { RHFTextField } from "src/components/hook-form";
import "./register.css";
import { getRecaptchaToken } from "src/utils/recaptchaService";
import PasswordSentView from "./password-sent-view";
import { useBoolean } from "src/hooks/use-boolean";
import logger from "src/utils/logger";
import SvgColor from "src/components/svg-color";
import { RouterLink } from "src/routes/components";
import RegionSelect from "src/components/RegionSelect";
import RightSectionAuth from "./RightSectionAuth";
import { isValidUtm } from "src/utils/utmUtils";
import { getSignupFieldErrors } from "src/utils/errorUtils";
import {
  useDeploymentMode,
  usePostLoginPath,
} from "src/hooks/useDeploymentMode";

export default function JwtRegisterView() {
  const { register, login, awsRegister } = useAuthContext();
  const [errorMsg, setErrorMsg] = useState("");
  const [registerSuccess, setRegisterSuccess] = useState(false);
  // Confirmed read only: the hook falls back to "oss" when deployment-info
  // errors, and a cloud user must never be shown the password fields.
  const { isOSS: ossMode, isSuccess: modeConfirmed } = useDeploymentMode();
  const isOSS = modeConfirmed && ossMode;
  const postLoginPath = usePostLoginPath();
  const password = useBoolean();
  const confirmPassword = useBoolean();
  const navigate = useNavigate();
  const { search } = useLocation();
  const [loading, setLoading] = useState(false);
  const queryParams = new URLSearchParams(location.search);
  const onboarding_token = queryParams.get("onboarding_token");

  const RegisterSchema = Yup.object().shape({
    fullName: Yup.string().required("Full name required"),
    email: Yup.string()
      .transform((value) => (typeof value === "string" ? value.trim() : value))
      .required("Email is required")
      .email("Email must be a valid email address"),
    // OSS sets the password here at sign-up (name → email → password →
    // confirm, one screen). Cloud still sets it via an emailed link.
    password: isOSS
      ? Yup.string()
          .required("Password is required")
          .min(8, "Password must be at least 8 characters")
      : Yup.string().when("$registerSuccess", {
          is: true,
          then: (schema) => schema.required("Password is required"),
          otherwise: (schema) => schema.notRequired(),
        }),
    confirmPassword: isOSS
      ? Yup.string()
          .required("Please confirm your password")
          .oneOf([Yup.ref("password")], "Passwords do not match")
      : Yup.string().notRequired(),
  });

  const locallyExtractUtmParams = useCallback(() => {
    const params = new URLSearchParams(search);
    const utmParams = new URLSearchParams();
    const utmKeys = ["utm_source", "utm_medium", "utm_campaign"];

    utmKeys.forEach((key) => {
      const val = params.get(key);
      if (isValidUtm(val)) utmParams.set(key, val);
    });

    const returnTo = params.get("returnTo");
    if (returnTo) {
      const decodedReturnTo = decodeURIComponent(returnTo);
      const innerUrl = new URL(decodedReturnTo, window.location.origin);
      const innerParams = new URLSearchParams(innerUrl.search);

      utmKeys.forEach((key) => {
        const val = innerParams.get(key);
        if (isValidUtm(val)) utmParams.set(key, val);
      });
    }

    const utmString = utmParams.toString();
    if (utmString) {
      localStorage.setItem("utm_params", utmString);
    }
  }, [search]);

  useEffect(() => {
    locallyExtractUtmParams();
  }, [locallyExtractUtmParams]);

  // Persist returnTo on an auth action so it survives flows that drop the
  // URL param (OAuth / SSO round-trip, register → setup-org).
  const persistReturnTo = () => {
    const returnTo = new URLSearchParams(search).get("returnTo");
    if (returnTo && returnTo.startsWith("/") && !returnTo.startsWith("//")) {
      localStorage.setItem("redirectUrl", returnTo);
    }
  };

  const handleSsoLogin = () => {
    persistReturnTo();
    // Navigate to SSO login page
    navigate(paths.auth.jwt.sso);
  };
  const defaultValues = {
    fullName: "",
    email: "",
    password: "",
    confirmPassword: "",
  };

  const methods = useForm({
    resolver: yupResolver(RegisterSchema),
    defaultValues,
    context: { registerSuccess },
  });

  const { handleSubmit, watch, setError } = methods;
  const email = watch("email");

  const handleSignup = async (data) => {
    persistReturnTo();
    // No site key on self-hosted, and the backend skips verification there.
    let token = "";
    if (!isOSS) {
      try {
        token = await getRecaptchaToken("signup");
      } catch (err) {
        enqueueSnackbar({
          message: "reCAPTCHA not ready. Please try again",
          variant: "error",
        });
        return;
      }
    }
    setErrorMsg("");
    try {
      setLoading(true);
      const payload = {
        email: data?.email,
        full_name: data?.fullName,
        company_name: "",
        recaptcha_response: token,
        allow_email: true,
        // OSS: password chosen on this screen, no emailed set-password link.
        ...(isOSS ? { password: data?.password } : {}),
      };
      let response;
      if (onboarding_token) {
        response = await awsRegister({
          ...payload,
          onboarding_token: onboarding_token,
        });
      } else {
        response = await register(payload);
      }
      if (response?.result) {
        if (!isOSS) {
          // Cloud: the password is set via an emailed link.
          enqueueSnackbar({
            variant: "success",
            message:
              response?.result?.message ||
              "Thanks for registering! Please check your email.",
            autoHideDuration: 3000,
          });
          setRegisterSuccess(true);
        }
        trackEvent(Events.newUserSignUp, {
          [PropertyName.method]: "email",
          [PropertyName.email]: data.email,
          [PropertyName.name]: data.fullName,
        });
        if (typeof window.gtag === "function") {
          trackSignupConversion({
            email: data.email,
            method: "email",
            userId: response?.result?.user_id,
          });
        }
        if (typeof window.rdt === "function") {
          trackRedditSignup({
            email: data.email,
            userId: response?.result?.user_id,
          });
        }
        trackTwitterSignup({
          email: data.email,
          method: "email",
          userId: response?.result?.user_id,
        });

        if (isOSS) {
          try {
            if (!response.result?.access) {
              throw new Error("signup response carried no access token");
            }
            await login({ status: 200, data: response.result });
            // Stops the auth guard re-firing the conversions sent above.
            localStorage.setItem("signupProvider", "email");

            if (response.result.new_org) {
              navigate(paths.auth.jwt.setup_org + search);
            } else {
              const returnTo = new URLSearchParams(search).get("returnTo");
              if (returnTo) localStorage.setItem("initial-render", "done");
              navigate(returnTo || postLoginPath);
              localStorage.removeItem("redirectUrl");
            }
          } catch (loginErr) {
            logger.info("OSS auto-login after signup failed", loginErr);
            enqueueSnackbar({
              variant: "info",
              message: "Account created. Please sign in to continue.",
              autoHideDuration: 4000,
            });
            navigate(paths.auth.jwt.login + search);
          }
          setLoading(false);
          return;
        }
      }
      setLoading(false);
    } catch (error) {
      setLoading(false);
      if (
        (error?.statusCode >= 400 && error?.statusCode < 500) ||
        error?.name === "NotAllowedError"
      ) {
        logger.info("Registration Error (expected)", error);
      } else {
        logger.error("Registration Error:", error);
      }
      const signupErrors = getSignupFieldErrors(error);
      if (signupErrors) {
        signupErrors.fields.forEach(({ name, message }) =>
          setError(name, { type: "server", message }, { shouldFocus: true }),
        );
        setErrorMsg(signupErrors.message);
        return;
      }

      setErrorMsg(
        typeof error === "string"
          ? error
          : error.error ||
              error.detail ||
              // An object `result` would throw inside the Alert.
              (typeof error.result === "string"
                ? error.result
                : error.result?.error),
      );
    } finally {
      setLoading(false);
    }
  };

  const handleLogin = async (data) => {
    persistReturnTo();
    let token;
    try {
      token = await getRecaptchaToken("login");
    } catch (err) {
      enqueueSnackbar({
        message: "reCAPTCHA not ready. Please try again",
        variant: "error",
      });
      return;
    }

    trackEvent(Events.loginClicked, {
      [PropertyName.status]: true,
    });
    try {
      setLoading(true);
      const response = await axios.post(endpoints.auth.login, {
        email: data.email,
        password: data.password,
        recaptcha_response: token,
      });
      if (response.status === 200) {
        await login(response);
        localStorage.setItem("signupProvider", "email");
        navigate(paths.auth.jwt.setup_org + search);
        trackEvent(Events.loginClicked, {
          [PropertyName.status]: true,
        });
      }
      setLoading(false);
    } catch (error) {
      setLoading(false);
      if (error?.detail === "User not found") {
        // setError("email", {message: "No account found with this email. Please sign up to create one.", type: "focus"}, { shouldFocus: true })
        setErrorMsg(
          "No account found with this email. Please sign up to create one",
        );
      } else if (error?.result?.error === "Invalid credentials") {
        // setError("password", {message: "Check your password and try again", type: "focus"}, { shouldFocus: true })
        setErrorMsg("Enter a valid Email and password combination");
      } else {
        setErrorMsg(
          typeof error === "string"
            ? error
            : error?.detail || error?.result?.error,
        );
      }
      if (
        (error?.statusCode >= 400 && error?.statusCode < 500) ||
        error?.name === "NotAllowedError"
      ) {
        logger.info("Login failed (expected)", error);
      } else {
        logger.error("Login failed", error);
      }
    } finally {
      setLoading(false);
    }
  };

  const onSubmit = handleSubmit(async (data) => {
    (await registerSuccess) ? handleLogin(data) : handleSignup(data);
  });

  const handleServiceProvider = async (provider) => {
    persistReturnTo();
    try {
      const response = await axios.get(endpoints.auth.service(provider));
      logger.debug("Service provider response:", {
        provider,
        response: response.data,
        url: response.data?.result?.url,
      });
      localStorage.setItem("signupProvider", provider);

      if (response.data?.result?.url) {
        window.location.href = response.data.result.url;
      } else {
        logger.error("Missing URL in response:", response.data);
        enqueueSnackbar("Invalid response from authentication service", {
          variant: "error",
        });
      }
    } catch (error) {
      if (
        (error?.statusCode >= 400 && error?.statusCode < 500) ||
        error?.name === "NotAllowedError"
      ) {
        logger.info("Error during social login (expected)", error);
      } else {
        logger.error("Error during social login:", error);
      }
      if (error.response?.status === 302 && error.response?.headers?.reason) {
        enqueueSnackbar(error.response.headers.reason, { variant: "error" });
      } else {
        enqueueSnackbar(error?.response?.data?.message || "Failed to login", {
          variant: "error",
        });
      }
    }
  };

  const renderHead = (
    <Stack>
      <Typography
        fontWeight={"fontWeightSemiBold"}
        sx={{
          fontSize: "28px",
          color: "text.primary",
          fontFamily: "Inter",
          lineHeight: "36px",
        }}
      >
        Create an account
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
        Start your journey with us
      </Typography>
    </Stack>
  );

  const renderForm = (
    <Stack spacing={2.5} maxWidth={"440px"}>
      <RegionSelect />
      <RHFTextField
        placeholder="Enter fullname"
        size="small"
        name="fullName"
        label="Full Name"
      />
      <RHFTextField
        placeholder="Enter Email address"
        size="small"
        name="email"
        label={isOSS ? "Email ID" : "Business Email ID"}
      />
      {isOSS && (
        <>
          <RHFTextField
            placeholder="Enter password"
            size="small"
            name="password"
            label="Set Password"
            type={password.value ? "text" : "password"}
            autoComplete="new-password"
            sx={{ "& .MuiOutlinedInput-root": { borderRadius: 0.5 } }}
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
          />
          <RHFTextField
            placeholder="Re-enter password"
            size="small"
            name="confirmPassword"
            label="Confirm Password"
            type={confirmPassword.value ? "text" : "password"}
            autoComplete="new-password"
            sx={{ "& .MuiOutlinedInput-root": { borderRadius: 0.5 } }}
            InputProps={{
              endAdornment: (
                <InputAdornment position="end">
                  <IconButton onClick={confirmPassword.onToggle} edge="end">
                    <Iconify
                      icon={
                        confirmPassword.value
                          ? "solar:eye-bold"
                          : "solar:eye-closed-bold"
                      }
                    />
                  </IconButton>
                </InputAdornment>
              ),
            }}
          />
        </>
      )}
      {!!errorMsg && (
        <Alert
          icon={<Iconify icon="fluent:warning-24-regular" color="red.500" />}
          severity="error"
          sx={{
            color: "red.500",
            border: "1px solid",
            borderColor: "red.200",
            backgroundColor: "red.o5",
          }}
        >
          {errorMsg}
        </Alert>
      )}

      <LoadingButton
        disabled={registerSuccess}
        fullWidth
        color="primary"
        type="submit"
        variant="contained"
        loading={loading}
        sx={{ height: "42px", borderRadius: 0.5 }}
      >
        Continue
      </LoadingButton>
      <Typography
        fontWeight={"fontWeightRegular"}
        sx={{
          fontSize: "12px",
          paddingX: "10px",
          lineHeight: "24px",
          textAlign: "center",
          marginTop: -2,
          color: "text.secondary",
        }}
      >
        By clicking continue, you agree to our
        <Link
          href="https://futureagi.com/terms"
          target="_blank"
          sx={{ cursor: "pointer" }}
        >
          {" "}
          Terms of Service
        </Link>{" "}
        and
        <Link
          href="https://futureagi.com/privacy"
          target="_blank"
          sx={{ cursor: "pointer" }}
        >
          {" "}
          Privacy Policy
        </Link>
        .
      </Typography>
      {/* {registerSuccess && (
        <Alert severity="success" sx={{ textAlign: "center", display: "flex", justifyContent: "center" }} icon={false}>
          Thanks for registering! <br />
          Your login credentials have been sent to your email.
        </Alert>
      )} */}
      {/* Self-hosted has no OAuth apps and no IdP, so social and SSO sign-up
          are hidden entirely — only local email/password auth works. */}
      {!isOSS && (
        <Divider>
          <Typography variant="body2" sx={{ color: "text.disabled" }}>
            or
          </Typography>
        </Divider>
      )}
      <Stack spacing={1.5}>
        {!isOSS && (
          <>
            <Button
              sx={{
                border: "1px solid",
                borderColor: "divider",
                borderRadius: 0.5,

                color: "text.primary",
                height: 44,
              }}
              onClick={() => handleServiceProvider("google")}
              startIcon={<Iconify icon="logos:google-icon" width={20} />}
            >
              <Typography
                fontWeight={"fontWeightMedium"}
                sx={{ fontSize: "15px" }}
              >
                Continue with Google
              </Typography>
            </Button>
            {/*
      <Button
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius:0.5,
          display: "flex",
          height: 44,

          color: "text.primary",
          "& .MuiButton-startIcon": {
            marginRight: 0,
            width: 24,
            paddingLeft: 1,
          },
        }}
        startIcon={<Iconify icon="logos:microsoft-icon" width={20} />}
        // onClick={() => handleServiceProvider("github")}
      >
        <Typography
          sx={{
            fontWeight: 500,
            paddingLeft: "10px",
            fontSize: "15px",
            marginRight: -1.5,
          }}
        >
          Continue with Microsoft
        </Typography>
      </Button> */}
            <Button
              sx={{
                border: "1px solid",
                borderColor: "divider",
                borderRadius: 0.5,

                height: 44,
              }}
              onClick={() => handleServiceProvider("github")}
              startIcon={
                <Iconify
                  icon="bi:github"
                  width={24}
                  sx={{ color: "text.primary" }}
                />
              }
            >
              <Typography
                fontWeight={"fontWeightMedium"}
                sx={{ fontSize: "15px", color: "text.primary" }}
              >
                Continue with Github
              </Typography>
            </Button>
            <Button
              sx={{
                border: "1px solid",
                borderColor: "divider",
                borderRadius: 0.5,

                height: 44,
                color: "text.primary",
              }}
              onClick={handleSsoLogin}
              startIcon={
                <SvgColor
                  sx={{ marginLeft: 2 }}
                  src="/assets/icons/ic_sso_saml.svg"
                />
              }
            >
              <Typography
                fontWeight={"fontWeightMedium"}
                sx={{ fontSize: "15px", marginRight: -1.5 }}
              >
                Continue with SSO/SAML
              </Typography>
            </Button>
          </>
        )}

        <Typography
          fontSize={"15px"}
          fontWeight={"fontWeightMedium"}
          color="text.secondary"
          sx={{ textAlign: "center" }}
        >
          Have an account?
          <Link
            variant="subtitle2"
            component={RouterLink}
            to={paths.auth.jwt.login + search}
            sx={{ color: "primary.main" }}
          >
            {" "}
            Sign In
          </Link>
        </Typography>
      </Stack>
    </Stack>
  );

  return (
    <Box sx={{ display: "flex", width: "100%", height: "100vh" }}>
      <Box
        sx={{
          width: "50%",
          height: "100%",
          display: "flex",
          justifyContent: "center",

          bgcolor: "background.paper",
          overflowY: "auto",
        }}
      >
        <Box
          sx={{
            maxWidth: "640px",
            width: "100%",
            px: 10,
            paddingY: "100px",
            height: "fit-content",
          }}
        >
          <FormProvider methods={methods} onSubmit={onSubmit}>
            {registerSuccess ? (
              <PasswordSentView
                email={email}
                isSubmitting={loading}
                errorMsg={errorMsg}
                password={password}
                setRegisterSuccess={setRegisterSuccess}
              />
            ) : (
              <Stack spacing={4}>
                {renderHead}
                {renderForm}
              </Stack>
            )}
          </FormProvider>
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
