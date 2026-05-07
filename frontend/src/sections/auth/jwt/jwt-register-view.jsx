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
import { Box, Button } from "@mui/material";
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
import { useGoogleReCaptcha } from "react-google-recaptcha-v3";
import PasswordSentView from "./password-sent-view";
import { useBoolean } from "src/hooks/use-boolean";
import logger from "src/utils/logger";
import SvgColor from "src/components/svg-color";
import { RouterLink } from "src/routes/components";
import RegionSelect from "src/components/RegionSelect";
import { GOOGLE_SITE_KEY } from "src/config-global";
import RightSectionAuth from "./RightSectionAuth";
import { isValidUtm } from "src/utils/utmUtils";

export default function JwtRegisterView() {
  const { register, login, awsRegister } = useAuthContext();
  const { executeRecaptcha } = useGoogleReCaptcha();
  const [errorMsg, setErrorMsg] = useState("");
  const [editEmail, setEditEmail] = useState(false);
  const [oldEmail, setOldEmail] = useState("");
  const [registerSuccess, setRegisterSuccess] = useState(false);
  const password = useBoolean();
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
    password: Yup.string().when(["$registerSuccess", "$editEmail"], {
      is: (registerSuccess, editEmail) =>
        registerSuccess === true && editEmail === false,
      then: (schema) => schema.required("Password is required"),
      otherwise: (schema) => schema.notRequired(),
    }),
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

  const handleSsoLogin = () => {
    // Navigate to SSO login page
    navigate(paths.auth.jwt.sso);
  };
  const defaultValues = {
    fullName: "",
    email: "",
    password: "",
  };

  const methods = useForm({
    resolver: yupResolver(RegisterSchema),
    defaultValues,
    context: { registerSuccess, editEmail },
  });

  const { handleSubmit, watch } = methods;
  const email = watch("email");

  const handleSignup = async (data) => {
    const token = GOOGLE_SITE_KEY ? await executeRecaptcha("signup") : "";
    setErrorMsg("");
    try {
      setLoading(true);
      const payload = {
        email: data?.email,
        full_name: data?.fullName,
        company_name: "",
        "recaptcha-response": token,
        ...(!!oldEmail && { old_email: oldEmail, update_true: true }),
        allow_email: true,
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
        enqueueSnackbar({
          variant: "success",
          message:
            response?.result?.message ||
            "Thanks for registering! Please check your email.",
          autoHideDuration: 3000,
        });
        setOldEmail(data?.email);
        setEditEmail(false);
        setRegisterSuccess(true);
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

        // Always navigate to login after registration
        // navigate(paths.auth.jwt.login);
      }
      setLoading(false);
    } catch (error) {
      setLoading(false);
      logger.error("Registration Error:", error);
      setErrorMsg(
        typeof error === "string"
          ? error
          : error.error || error.detail || error.result,
      );
    } finally {
      setLoading(false);
    }
  };

  const handleLogin = async (data) => {
    const token = GOOGLE_SITE_KEY ? await executeRecaptcha("login") : "";

    trackEvent(Events.loginClicked, {
      [PropertyName.status]: true,
    });
    try {
      setLoading(true);
      const response = await axios.post(endpoints.auth.login, {
        email: data.email,
        password: data.password,
        "recaptcha-response": token,
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
      logger.error("Login failed", error);
    } finally {
      setLoading(false);
    }
  };

  const onSubmit = handleSubmit(async (data) => {
    if (GOOGLE_SITE_KEY && !executeRecaptcha) {
      enqueueSnackbar({
        message: "reCAPTCHA not ready. Please try again",
        variant: "error",
      });
      return;
    }
    (await (registerSuccess && !editEmail))
      ? handleLogin(data)
      : handleSignup(data);
  });

  const handleServiceProvider = async (provider) => {
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
        label="Business Email ID"
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
          href="https://futureagi.com/terms-and-conditions"
          target="_blank"
          sx={{ cursor: "pointer" }}
        >
          {" "}
          Terms of Service
        </Link>{" "}
        and
        <Link
          href="https://futureagi.com/privacy-policy"
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
      <Divider>
        <Typography variant="body2" sx={{ color: "text.disabled" }}>
          or
        </Typography>
      </Divider>
      <Stack spacing={1.5}>
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
                editEmail={editEmail}
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
