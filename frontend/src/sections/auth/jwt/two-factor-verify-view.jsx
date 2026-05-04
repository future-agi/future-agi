import React, { useState, useEffect, useMemo } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import Link from "@mui/material/Link";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import LoadingButton from "@mui/lab/LoadingButton";
import { Box } from "@mui/material";

import { paths } from "src/routes/paths";
import { useRouter } from "src/routes/hooks";

import { useAuthContext } from "src/auth/hooks";
import { setSession, setRefreshToken } from "src/auth/context/jwt/utils";
import { PATH_AFTER_LOGIN } from "src/config-global";
import { useDeploymentMode } from "src/hooks/useDeploymentMode";

import axiosInstance, { endpoints } from "src/utils/axios";
import { useSnackbar } from "src/components/snackbar";

import { startAuthentication } from "@simplewebauthn/browser";

import RightSectionAuth from "./RightSectionAuth";

// ----------------------------------------------------------------------

export default function TwoFactorVerifyView() {
  const { login } = useAuthContext();
  const { isOSS } = useDeploymentMode();
  const router = useRouter();
  const navigate = useNavigate();
  const location = useLocation();
  const { enqueueSnackbar } = useSnackbar();

  const TWO_FA_SESSION_KEY = "2fa_challenge";
  const postLoginPath = isOSS ? paths.dashboard.develop : PATH_AFTER_LOGIN;

  // Retrieve challenge data from navigation state or sessionStorage
  const getInitialChallengeData = () => {
    if (location.state?.challengeToken) {
      // Fresh navigation from login — persist to sessionStorage
      const data = {
        challengeToken: location.state.challengeToken,
        methods: location.state.methods,
        email: location.state.email,
      };
      sessionStorage.setItem(TWO_FA_SESSION_KEY, JSON.stringify(data));
      return data;
    }
    // Page refresh — try sessionStorage
    try {
      const stored = sessionStorage.getItem(TWO_FA_SESSION_KEY);
      if (stored) {
        const data = JSON.parse(stored);
        if (
          typeof data.challengeToken !== "string" ||
          data.challengeToken.length < 10
        ) {
          sessionStorage.removeItem(TWO_FA_SESSION_KEY);
          return {};
        }
        return data;
      }
    } catch {
      sessionStorage.removeItem(TWO_FA_SESSION_KEY);
    }
    return {};
  };

  const [challengeData] = useState(getInitialChallengeData);

  const { challengeToken, methods: availableMethods } = challengeData;

  // Default mode is DERIVED from availableMethods (not stored via useState
  // initializer), so the view stays correct even when HMR preserves the
  // component or when availableMethods changes after mount. A user's
  // explicit pick (via the cross-links) overrides the derived default.
  //
  // Priority: passkey → totp → recovery. Passkey-first — a passkey tap
  // is faster than typing a 6-digit code and is phishing-resistant.
  const [userPickedMode, setUserPickedMode] = useState(null);
  const defaultMode = useMemo(() => {
    const enrolled = availableMethods || [];
    if (enrolled.includes("passkey")) return "passkey";
    if (enrolled.includes("totp")) return "totp";
    return "recovery";
  }, [availableMethods]);
  const mode = userPickedMode ?? defaultMode;
  const [code, setCode] = useState("");
  const [errorMsg, setErrorMsg] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Redirect to login if no challenge token is present
  useEffect(() => {
    if (!challengeToken) {
      navigate(paths.auth.jwt.login);
    }
  }, [challengeToken, navigate]);

  // --------------- Shared helpers ---------------

  const handleExpiredOrInvalidError = (error) => {
    if (
      error?.error?.includes("expired") ||
      error?.error?.includes("Invalid")
    ) {
      sessionStorage.removeItem(TWO_FA_SESSION_KEY);
      enqueueSnackbar("Verification session expired. Please log in again.", {
        variant: "error",
      });
      navigate(paths.auth.jwt.login);
      return true;
    }
    return false;
  };

  const handleLoginSuccess = async (data) => {
    sessionStorage.removeItem(TWO_FA_SESSION_KEY);

    setSession(data.access, null);
    if (data.refresh) setRefreshToken(data.refresh);

    // login() expects a response-shaped object with { status, data }
    await login({ status: 200, data });

    router.push(postLoginPath);
  };

  // --------------- TOTP verification ---------------

  const handleTotpSubmit = async (e) => {
    e.preventDefault();
    setErrorMsg("");
    setIsSubmitting(true);

    try {
      const response = await axiosInstance.post(
        endpoints.twoFactor.verify.totp,
        {
          challenge_token: challengeToken,
          code,
        },
      );

      await handleLoginSuccess(response.data);
    } catch (error) {
      if (!handleExpiredOrInvalidError(error)) {
        setErrorMsg("Invalid code. Please try again.");
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  // --------------- Recovery code verification ---------------

  const handleRecoverySubmit = async (e) => {
    e.preventDefault();
    setErrorMsg("");
    setIsSubmitting(true);

    try {
      const response = await axiosInstance.post(
        endpoints.twoFactor.verify.recovery,
        {
          challenge_token: challengeToken,
          code,
        },
      );

      const data = response.data;

      if (data.recovery_codes_warning) {
        enqueueSnackbar(data.recovery_codes_warning, { variant: "warning" });
      }

      await handleLoginSuccess(data);
    } catch (error) {
      if (!handleExpiredOrInvalidError(error)) {
        setErrorMsg("Invalid recovery code. Please try again.");
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  // --------------- Passkey verification ---------------

  const handlePasskeySubmit = async () => {
    setErrorMsg("");
    setIsSubmitting(true);

    try {
      // Step 1: Get WebAuthn authentication options from the server
      const optionsResponse = await axiosInstance.post(
        endpoints.twoFactor.verify.passkeyOptions,
        {
          challenge_token: challengeToken,
        },
      );

      const options = optionsResponse.data;

      // Step 2: Trigger the browser WebAuthn ceremony
      const assertionResponse = await startAuthentication({
        optionsJSON: options,
      });

      // Step 3: Send the credential back for verification
      const verifyResponse = await axiosInstance.post(
        endpoints.twoFactor.verify.passkey,
        {
          challenge_token: challengeToken,
          credential: JSON.stringify(assertionResponse),
          session_id: options.session_id,
        },
      );

      await handleLoginSuccess(verifyResponse.data);
    } catch (error) {
      if (!handleExpiredOrInvalidError(error)) {
        // WebAuthn can throw DOMException if the user cancels
        if (error?.name === "NotAllowedError") {
          setErrorMsg("Passkey verification was cancelled.");
        } else {
          setErrorMsg("Passkey verification failed. Please try again.");
        }
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  // --------------- Mode switching ---------------

  const switchMode = (newMode) => {
    setUserPickedMode(newMode);
    setCode("");
    setErrorMsg("");
  };

  // --------------- Render helpers ---------------

  const renderHead = (title, subtitle) => (
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
        {title}
      </Typography>
      <Typography
        sx={{
          fontSize: "14px",
          color: "text.secondary",
          fontFamily: "Inter",
          mt: 1,
        }}
      >
        {subtitle}
      </Typography>
    </Stack>
  );

  const renderErrorAlert = () =>
    errorMsg ? (
      <Typography
        variant="body2"
        sx={{
          color: "error.main",
          bgcolor: "error.lighter",
          borderRadius: 0.5,
          px: 2,
          py: 1,
          border: "1px solid",
          borderColor: "error.light",
        }}
      >
        {errorMsg}
      </Typography>
    ) : null;

  const renderBackToLogin = () => (
    <Link
      component="button"
      type="button"
      variant="body2"
      color="text.secondary"
      underline="hover"
      onClick={() => {
        sessionStorage.removeItem("2fa_challenge");
        navigate(paths.auth.jwt.login);
      }}
      sx={{ cursor: "pointer" }}
    >
      Back to login
    </Link>
  );

  // --------------- TOTP mode ---------------

  const renderTotpForm = () => (
    <form onSubmit={handleTotpSubmit}>
      <Stack spacing={2.5} sx={{ maxWidth: "440px" }}>
        {renderHead(
          "Two-factor authentication",
          "Enter the 6-digit code from your authenticator app.",
        )}

        <TextField
          name="totp-code"
          label="Authentication code"
          placeholder="Enter 6-digit code"
          value={code}
          onChange={(e) => setCode(e.target.value)}
          autoComplete="one-time-code"
          inputProps={{ maxLength: 6 }}
          sx={{ "& .MuiOutlinedInput-root": { borderRadius: 0.5 } }}
          size="small"
          autoFocus
        />

        {renderErrorAlert()}

        <LoadingButton
          fullWidth
          color="primary"
          type="submit"
          variant="contained"
          loading={isSubmitting}
          disabled={code.length !== 6}
          sx={{ height: "42px", borderRadius: 0.5 }}
        >
          Verify
        </LoadingButton>

        <Stack spacing={1} alignItems="center">
          {availableMethods?.includes("passkey") && (
            <Link
              component="button"
              type="button"
              variant="body2"
              color="primary"
              underline="hover"
              onClick={() => switchMode("passkey")}
              sx={{ cursor: "pointer" }}
            >
              Use a passkey instead
            </Link>
          )}
          {availableMethods?.includes("recovery") && (
            <Link
              component="button"
              type="button"
              variant="body2"
              color="primary"
              underline="hover"
              onClick={() => switchMode("recovery")}
              sx={{ cursor: "pointer" }}
            >
              Use a recovery code
            </Link>
          )}
          {renderBackToLogin()}
        </Stack>
      </Stack>
    </form>
  );

  // --------------- Recovery mode ---------------

  const renderRecoveryForm = () => (
    <form onSubmit={handleRecoverySubmit}>
      <Stack spacing={2.5} sx={{ maxWidth: "440px" }}>
        {renderHead(
          "Use a recovery code",
          "Enter one of your recovery codes. Each code can only be used once.",
        )}

        <TextField
          name="recovery-code"
          label="Recovery code"
          placeholder="Enter recovery code"
          value={code}
          onChange={(e) => setCode(e.target.value)}
          autoComplete="off"
          sx={{ "& .MuiOutlinedInput-root": { borderRadius: 0.5 } }}
          size="small"
          autoFocus
        />

        {renderErrorAlert()}

        <LoadingButton
          fullWidth
          color="primary"
          type="submit"
          variant="contained"
          loading={isSubmitting}
          disabled={!code.trim()}
          sx={{ height: "42px", borderRadius: 0.5 }}
        >
          Verify
        </LoadingButton>

        <Stack spacing={1} alignItems="center">
          {availableMethods?.includes("totp") && (
            <Link
              component="button"
              type="button"
              variant="body2"
              color="primary"
              underline="hover"
              onClick={() => switchMode("totp")}
              sx={{ cursor: "pointer" }}
            >
              Use authenticator app instead
            </Link>
          )}
          {availableMethods?.includes("passkey") && (
            <Link
              component="button"
              type="button"
              variant="body2"
              color="primary"
              underline="hover"
              onClick={() => switchMode("passkey")}
              sx={{ cursor: "pointer" }}
            >
              Use a passkey instead
            </Link>
          )}
          {renderBackToLogin()}
        </Stack>
      </Stack>
    </form>
  );

  // --------------- Passkey mode ---------------

  const renderPasskeyForm = () => (
    <Stack spacing={2.5} sx={{ maxWidth: "440px" }}>
      {renderHead(
        "Verify with passkey",
        "Use your passkey to complete sign in.",
      )}

      {renderErrorAlert()}

      <LoadingButton
        fullWidth
        color="primary"
        variant="contained"
        loading={isSubmitting}
        onClick={handlePasskeySubmit}
        sx={{ height: "42px", borderRadius: 0.5 }}
      >
        Verify with passkey
      </LoadingButton>

      <Stack spacing={1} alignItems="center">
        {availableMethods?.includes("totp") && (
          <Link
            component="button"
            type="button"
            variant="body2"
            color="primary"
            underline="hover"
            onClick={() => switchMode("totp")}
            sx={{ cursor: "pointer" }}
          >
            Use authenticator app instead
          </Link>
        )}
        {availableMethods?.includes("recovery") && (
          <Link
            component="button"
            type="button"
            variant="body2"
            color="primary"
            underline="hover"
            onClick={() => switchMode("recovery")}
            sx={{ cursor: "pointer" }}
          >
            Use a recovery code
          </Link>
        )}
        {renderBackToLogin()}
      </Stack>
    </Stack>
  );

  // --------------- Main render ---------------

  const renderContent = () => {
    switch (mode) {
      case "recovery":
        return renderRecoveryForm();
      case "passkey":
        return renderPasskeyForm();
      case "totp":
      default:
        return renderTotpForm();
    }
  };

  if (!challengeToken) {
    return null;
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
          {renderContent()}
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
