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
import { markValidationDone } from "./ossFlowState";

export default function OssSetupView() {
  const navigate = useNavigate();
  const { authenticated } = useAuthContext();
  const postLoginPath = usePostLoginPath();
  const { isOSS, isLoading, isSuccess } = useDeploymentMode();
  const [step, setStep] = useState(0);
  const [mode, setMode] = useState(DEFAULT_LAUNCH_MODE);
  const [validationProgress, setValidationProgress] = useState(0);

  const handleValidationContinue = ({ accountExists = false } = {}) => {
    markValidationDone();

    if (authenticated) {
      navigate(postLoginPath);
      return;
    }
    // Sign-in once an account exists (./bin/install makes the owner); until
    // then sign-up, since login with no account yet is a dead end.
    navigate(accountExists ? paths.auth.jwt.login : paths.auth.jwt.register);
  };

  // Self-hosted only — a typed URL must not drop a cloud user into a wizard.
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
          onSwitchMode={setMode}
          onContinue={handleValidationContinue}
          onProgress={setValidationProgress}
          authenticated={Boolean(authenticated)}
        />
      )}
    </OssSetupShell>
  );
}
