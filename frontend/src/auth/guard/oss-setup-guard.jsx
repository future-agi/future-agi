import React from "react";
import PropTypes from "prop-types";
import { Navigate } from "react-router-dom";

import { paths } from "src/routes/paths";
import { SplashScreen } from "src/components/loading-screen";
import { useDeploymentMode } from "src/hooks/useDeploymentMode";
import { isValidationDone } from "src/sections/oss-first-run/ossFlowState";

// ----------------------------------------------------------------------

// Signup is the END of the OSS first-run flow, not a way into it: it is reached
// by finishing the infrastructure checks, which mark completion before
// navigating here. Anyone arriving directly beforehand gets the checks first.
//
// Deliberately not applied to login. Completion lives in localStorage, so it is
// per-browser: an existing user signing in from a second machine, an incognito
// window, or after clearing site data has no flag, and bouncing them into an
// infrastructure wizard would read as a broken install.
export default function OssSetupGuard({ children }) {
  const { isOSS, isLoading, isSuccess } = useDeploymentMode();

  if (isLoading) return <SplashScreen />;

  if (isSuccess && isOSS && !isValidationDone()) {
    return <Navigate to={paths.ossSetup} replace />;
  }

  return children;
}

OssSetupGuard.propTypes = {
  children: PropTypes.node,
};
