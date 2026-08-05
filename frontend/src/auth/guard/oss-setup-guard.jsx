import React from "react";
import PropTypes from "prop-types";
import { Navigate } from "react-router-dom";

import { paths } from "src/routes/paths";
import { SplashScreen } from "src/components/loading-screen";
import { useDeploymentMode } from "src/hooks/useDeploymentMode";
import { isValidationDone } from "src/sections/oss-first-run/ossFlowState";

// ----------------------------------------------------------------------

// Signup is the end of the first-run flow, so anyone arriving before the checks
// gets them first. Not applied to login: completion is per-browser, and bouncing
// an existing user into a wizard would read as a broken install.
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
