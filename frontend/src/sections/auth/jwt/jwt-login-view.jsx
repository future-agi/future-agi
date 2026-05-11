import React, { useCallback, useEffect } from "react";
import * as Yup from "yup";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { yupResolver } from "@hookform/resolvers/yup";

import Link from "@mui/material/Link";
import Alert from "@mui/material/Alert";
import Stack from "@mui/material/Stack";
import IconButton from "@mui/material/IconButton";
import Typography from "@mui/material/Typography";
import LoadingButton from "@mui/lab/LoadingButton";
import InputAdornment from "@mui/material/InputAdornment";
import Divider from "@mui/material/Divider";

import { paths } from "src/routes/paths";
import { RouterLink } from "src/routes/components";
import { useRouter } from "src/routes/hooks";

import { useBoolean } from "src/hooks/use-boolean";
import { useAuthContext } from "src/auth/hooks";
import { setSession, setRefreshToken } from "src/auth/context/jwt/utils";
import { GOOGLE_SITE_KEY } from "src/config-global";

import Iconify from "src/components/iconify";
import FormProvider, { RHFTextField } from "src/components/hook-form";
import { useSearchParams, useNavigate, useLocation } from "react-router-dom";
import { Events, trackEvent, PropertyName } from "src/utils/Mixpanel";
import { Box, Button, CircularProgress } from "@mui/material";
import axiosInstance, { endpoints } from "src/utils/axios";
import { useSnackbar } from "src/components/snackbar";
import { useMutation } from "@tanstack/react-query";
import { useParams } from "src/routes/hooks";
import { useGoogleReCaptcha } from "react-google-recaptcha-v3";
import logger from "src/utils/logger";
import { FormCheckboxField } from "src/components/FormCheckboxField";
import SvgColor from "src/components/svg-color";
import RegionSelect from "src/components/RegionSelect";
import {
  browserSupportsWebAuthn,
  startAuthentication,
} from "@simplewebauthn/browser";
import RightSectionAuth from "./RightSectionAuth";
import { isValidUtm } from "src/utils/utmUtils";
import { usePostLoginPath } from "src/hooks/useDeploymentMode";

// ----------------------------------------------------------------------

export default function JwtLoginView() {
  const { login } = useAuthContext();
  const postLoginPath = usePostLoginPath();
  const { executeRecaptcha } = useGoogleReCaptcha();
  const router = useRouter();
  const [errorMsg, setErrorMsg] = useState("");
  const [searchParams] = useSearchParams();
  const returnTo = searchParams.get("returnTo");
  const password = useBoolean();
  const navigate = useNavigate();
  const { search } = useLocation();
  const { enqueueSnackbar } = useSnackbar();
  const { uuid, token } = useParams();

  const [inviteFailed, setInviteFailed] = useState(false);

  const { mutate: acceptInvitation } = useMutation({
    mutationFn: () =>
      axiosInstance.get(
        `${endpoints.invite.accept_invitation}${uuid}/${token}/`,
      ),
    onSuccess: (response) => {
      navigate(`/auth/jwt/invitation/set-password/${uuid}/${token}`, {
        state: {
          email: response.data?.email,
          orgName: response.data?.org_name,
        },
      });
    },
    onError: (error) => {
      setInviteFailed(true);
      // If user is logged in as different user, redirect to the invite page
      // which has proper UI for handling this case
      if (error?.code === "authenticated_user_mismatch") {
        navigate(`/auth/jwt/invitation/set-password/${uuid}/${token}`);
        return;
      }
      enqueueSnackbar(
        error?.error ||
          error?.message ||
          error?.detail ||
          "Failed to accept invitation",
        { variant: "error" },
      );
    },
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

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const isDenied = params.get("denied");
    const encodedReason = params.get("reason");

    if (isDenied === "true") {
      if (encodedReason) {
        try {
          const decoded = atob(encodedReason);
          if (decoded) {
            setErrorMsg(decoded);
          } else {
            setErrorMsg("Access Denied");
          }
        } catch {
          setErrorMsg("Access Denied");
        }
      } else {
        setErrorMsg("Access Denied");
      }
    }

    // Show deactivation error passed from axios interceptor
    const authError = sessionStorage.getItem("auth_error");
    if (authError) {
      setErrorMsg(authError);
      sessionStorage.removeItem("auth_error");
    }

    if (token) {
      acceptInvitation();
    }
  }, []);

  const LoginSchema = Yup.object().shape({
    email: Yup.string()
      .transform((value) => (typeof value === "string" ? value.trim() : value))
      .required("Email is required")
      .email("Email must be a valid email address"),
    password: Yup.string().required("Password is required"),
    rememberMe: Yup.boolean(),
  });

  const defaultValues = {
    email: "",
    password: "",
    rememberMe: true,
  };

  const methods = useForm({
    resolver: yupResolver(LoginSchema),
    defaultValues,
  });

  const {
    handleSubmit,
    formState: { isSubmitting },
  } = methods;

  const onSubmit = handleSubmit(async (data) => {
    if (GOOGLE_SITE_KEY && !executeRecaptcha) {
      enqueueSnackbar({
        message: "reCAPTCHA not ready. Please try again",
        variant: "error",
      });
      return;
    }
    const token = GOOGLE_SITE_KEY ? await executeRecaptcha("login") : "";

    trackEvent(Events.loginClicked, {
      [PropertyName.status]: true,
    });
    // trackEvent(Events.loginStarted);
    try {
      const response = await axiosInstance.post(endpoints.auth.login, {
        email: data.email,
        password: data.password,
        remember_me: data.rememberMe,
        "recaptcha-response": token,
      });
      // trackEvent(Events.loginCompleted);

      if (response.status === 200) {
        // User was removed from their org — redirect to org-removed page
        if (response.data.requires_org_setup) {
          setSession(response.data.access, null);
          if (response.data.refresh) setRefreshToken(response.data.refresh);
          navigate(paths.auth.jwt.org_removed);
          return;
        }
        // User has 2FA enabled — redirect to 2FA verification
        if (response.data.requires_two_factor) {
          navigate(paths.auth.jwt.twoFactor, {
            state: {
              challengeToken: response.data.challenge_token,
              methods: response.data.methods,
              email: data.email,
            },
          });
          return;
        }
        await login(response);
        sessionStorage.removeItem("2fa_challenge");
        if (response.data.new_org) {
          localStorage.setItem("signupProvider", "email");
          navigate(paths.auth.jwt.setup_org);
        } else {
          router.push(returnTo || postLoginPath);
        }
      }
    } catch (error) {
      const raw = error;
      const message =
        typeof raw === "object" && raw?.[0] === "I"
          ? Object.keys(raw)
              .filter((key) => !isNaN(key)) // only numeric keys
              .sort((a, b) => a - b) // sort keys in order
              .map((key) => raw[key])
              .join("")
          : error?.detail || error?.result?.error || "Login failed";
      if (message.includes("IP address temporarily blocked")) {
        enqueueSnackbar(message, { variant: "error" });
        return;
      }
      if (error?.detail === "User not found") {
        setErrorMsg(
          "No account found with this email. Please sign up to create one",
        );
      } else if (error?.result?.error === "Account deactivated") {
        setErrorMsg(
          error?.result?.message ||
            "Your account has been deactivated. Please contact your organization admin.",
        );
      } else if (error?.result?.error === "Invalid credentials") {
        setErrorMsg("Enter a valid Email and password combination");
      } else {
        setErrorMsg(
          typeof error === "string"
            ? error
            : error?.detail ||
                error?.result?.error ||
                "An unexpected error occurred",
        );
      }
      logger.error("Login attempt failed", error);
    }
  });

  const [passkeyLoading, setPasskeyLoading] = useState(false);

  const handlePasskeyLogin = async () => {
    try {
      setPasskeyLoading(true);
      // Get authentication options from server
      const optionsRes = await axiosInstance.post(
        endpoints.passkey.authenticateOptions,
      );
      const options = optionsRes.data;

      // Trigger browser WebAuthn ceremony
      const assertionResponse = await startAuthentication({
        optionsJSON: options,
      });

      // Verify with server
      const verifyRes = await axiosInstance.post(
        endpoints.passkey.authenticateVerify,
        {
          credential: JSON.stringify(assertionResponse),
          session_id: options.session_id,
        },
      );

      if (verifyRes.status === 200) {
        await login(verifyRes);
        router.push(returnTo || postLoginPath);
      }
    } catch (error) {
      if (error?.name === "NotAllowedError") {
        enqueueSnackbar("Passkey sign-in was cancelled.", {
          variant: "info",
        });
      } else {
        enqueueSnackbar(
          error?.detail || error?.error || "Passkey sign-in failed.",
          { variant: "error" },
        );
      }
      logger.error("Passkey login failed", error);
    } finally {
      setPasskeyLoading(false);
    }
  };

  const handleSsoLogin = () => {
    // Navigate to SSO login page
    navigate(paths.auth.jwt.sso);
  };

  const handleServiceProvider = async (provider) => {
    trackEvent(Events.ssoLoginClicked, {
      [PropertyName.mode]: provider,
    });
    try {
      const response = await axiosInstance.get(
        endpoints.auth.service(provider),
      );
      logger.debug("Service provider response:", {
        provider,
        response: response.data,
        url: response.data?.result?.url,
      });

      if (response.data?.result?.url) {
        if (
          returnTo &&
          returnTo.startsWith("/") &&
          !returnTo.startsWith("//")
        ) {
          localStorage.setItem("redirectUrl", returnTo);
        }
        logger.debug(response.data?.result?.url);
        window.location.href = response.data.result.url;
      } else {
        logger.error("Missing URL in response:", response.data);
        enqueueSnackbar("Invalid response from authentication service", {
          variant: "error",
        });
      }
    } catch (error) {
      logger.error("Error during social login:", error);
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
        Welcome to FutureAGI
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
        Your AI Agent Engineering and Optimization Platform
      </Typography>
    </Stack>
  );

  const renderForm = (
    <Stack spacing={2.5} sx={{ maxWidth: "440px" }}>
      <RegionSelect />
      <RHFTextField
        name="email"
        label="Email address"
        placeholder="Enter email ID"
        autoComplete="email"
        sx={{ "& .MuiOutlinedInput-root": { borderRadius: 0.5 } }}
        size="small"
      />

      <RHFTextField
        name="password"
        label="Password"
        placeholder="Enter password"
        type={password.value ? "text" : "password"}
        autoComplete="current-password"
        sx={{ "& .MuiOutlinedInput-root": { borderRadius: 0.5 } }}
        InputProps={{
          endAdornment: (
            <InputAdornment position="end">
              <IconButton onClick={password.onToggle} edge="end">
                <Iconify
                  icon={
                    password.value ? "solar:eye-bold" : "solar:eye-closed-bold"
                  }
                />
              </IconButton>
            </InputAdornment>
          ),
        }}
        size="small"
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
          }}
        >
          {errorMsg}
        </Alert>
      )}
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        sx={{ marginTop: -1 }}
      >
        <FormCheckboxField
          control={methods.control}
          label={"Remember me"}
          fieldName={"rememberMe"}
          helperText={undefined}
          defaultValue={true}
          labelProps={{
            gap: 1,
          }}
          checkboxSx={{
            padding: 0,
            "&.Mui-checked": {
              color: "primary.light",
            },
          }}
          labelPlacement="end"
          size={"medium"}
        />
        {/* <Stack direction="row" alignItems="center" spacing={1}>
          <Checkbox defaultChecked size="small" sx={{ mr: "-11px" }} />
          <Typography variant="body2">Remember me</Typography>
        </Stack> */}

        <Link
          variant="body2"
          color="primary"
          underline="always"
          href={paths.auth.jwt["forget-password"]}
          onClick={() => {
            trackEvent(Events.forgotPasswordClicked, {
              [PropertyName.click]: true,
            });
          }}
        >
          Forgot Password
        </Link>
      </Stack>

      <LoadingButton
        fullWidth
        color="primary"
        type="submit"
        variant="contained"
        loading={isSubmitting}
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
      <Divider>
        <Typography variant="body2" sx={{ color: "text.disabled" }}>
          or
        </Typography>
      </Divider>
      <Stack spacing={1.5}>
        {browserSupportsWebAuthn() && (
          <LoadingButton
            sx={{
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 0.5,
              height: 44,
              color: "text.primary",
            }}
            loading={passkeyLoading}
            onClick={handlePasskeyLogin}
            startIcon={<Iconify icon="solar:key-bold" width={20} />}
          >
            <Typography
              fontWeight={"fontWeightMedium"}
              sx={{ fontSize: "15px" }}
            >
              Sign in with Passkey
            </Typography>
          </LoadingButton>
        )}
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
          <Typography fontWeight={"fontWeightMedium"} sx={{ fontSize: "15px" }}>
            Continue with Google
          </Typography>
        </Button>

        {/* <Button
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

        {/* 🔹 New SAML/SSO Login Button */}

        {/* ✅ Added Create Account Link */}
        <Typography
          fontSize={"15px"}
          fontWeight={"fontWeightMedium"}
          color="text.secondary"
          sx={{ textAlign: "center" }}
        >
          Don’t have an account?
          <Link
            variant="subtitle2"
            component={RouterLink}
            to={paths.auth.jwt.register + search}
            sx={{ color: "primary.main" }}
          >
            {" "}
            Sign up
          </Link>
        </Typography>
      </Stack>
    </Stack>
  );

  // Show loading screen while accepting an invitation (token present but not yet failed)
  if (token && !inviteFailed) {
    return (
      <Box sx={{ width: "100%", height: "100vh", display: "flex" }}>
        <Box
          sx={{
            width: "50%",
            height: "100vh",
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
            bgcolor: "background.paper",
          }}
        >
          <Stack spacing={2} alignItems="center">
            <CircularProgress size={32} />
            <Typography
              fontWeight="fontWeightMedium"
              sx={{ fontSize: "16px", color: "text.secondary" }}
            >
              Accepting your invitation...
            </Typography>
          </Stack>
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
            maxWidth: "640px",
            paddingY: "100px",
            width: "100%",
            px: 10,
            height: "fit-content",
          }}
        >
          <FormProvider methods={methods} onSubmit={onSubmit}>
            {renderHead}
            {renderForm}
          </FormProvider>
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
