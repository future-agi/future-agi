import React, { useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { paths } from "src/routes/paths";
import OssSetupShell from "./OssSetupShell";
import LaunchModeStep from "./LaunchModeStep";
import ValidationStep from "./ValidationStep";
import HorizontalSpaceship from "./HorizontalSpaceship";
import { SplashScreen } from "src/components/loading-screen";
import { useAuthContext } from "src/auth/hooks";
import {
  useDeploymentMode,
  usePostLoginPath,
} from "src/hooks/useDeploymentMode";
import { DEFAULT_LAUNCH_MODE } from "./constants";
import { isValidationDone, markValidationDone } from "./ossFlowState";

// OSS first-run flow.
//   step 0 — pick a launch mode, which decides which checks are required
//   step 1 — run the infrastructure checks
// then straight into signup to create the admin account.
//
// No "launch mode seen" marker: root routing only sends anyone here while
// validation is unrecorded, so this runs once and the flag would have no reader.
export default function OssSetupView() {
  const navigate = useNavigate();
  const { authenticated } = useAuthContext();
  const postLoginPath = usePostLoginPath();
  const { isOSS, isLoading, isSuccess } = useDeploymentMode();
  const [step, setStep] = useState(0);
  const [mode, setMode] = useState(DEFAULT_LAUNCH_MODE);
  const [validationProgress, setValidationProgress] = useState(0);

  const handleValidationContinue = () => {
    // Read before marking, or firstRun is always false.
    const firstRun = !isValidationDone();
    markValidationDone();

    if (authenticated) {
      navigate(postLoginPath);
      return;
    }
    // Signup only on a genuine first run. Otherwise login, which carries a
    // "Sign up" link — there is no reliable client-side signal for whether an
    // account exists, so login is the safe default.
    navigate(firstRun ? paths.auth.jwt.register : paths.auth.jwt.login);
  };

  // This flow is self-hosted only. On dev this URL 404'd for everyone; keep it
  // unreachable for cloud and EE rather than letting a typed URL drop a paying
  // user into an infra-setup wizard.
  if (isLoading) return <SplashScreen />;
  if (isSuccess && !isOSS) return <Navigate to={postLoginPath} replace />;

  return (
    <OssSetupShell
      step={step}
      totalSteps={2}
      illustration={
        step === 1 ? (
          <HorizontalSpaceship progress={validationProgress} height={46} />
        ) : undefined
      }
    >
      {step === 0 && (
        <LaunchModeStep
          value={mode}
          onChange={setMode}
          onContinue={() => setStep(1)}
        />
      )}

      {step === 1 && (
        <ValidationStep
          mode={mode}
          onBack={() => setStep(0)}
          onContinue={handleValidationContinue}
          onProgress={setValidationProgress}
        />
      )}
    </OssSetupShell>
  );
}
