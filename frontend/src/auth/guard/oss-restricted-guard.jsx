import PropTypes from "prop-types";
import { Navigate } from "react-router-dom";

import { paths } from "src/routes/paths";

import { SplashScreen } from "src/components/loading-screen";
import { useDeploymentMode } from "src/hooks/useDeploymentMode";

// ----------------------------------------------------------------------

// Redirects OSS-unavailable auth routes to login, opening the CLI-setup modal.
export default function OssRestrictedGuard({ children, redirectHint }) {
  const { isOSS, isLoading } = useDeploymentMode();

  if (isLoading) return <SplashScreen />;

  if (isOSS) {
    return (
      <Navigate
        to={{
          pathname: paths.auth.jwt.login,
          search: redirectHint ? `?ossSetup=${redirectHint}` : "",
        }}
        replace
      />
    );
  }

  return children;
}

OssRestrictedGuard.propTypes = {
  children: PropTypes.node,
  redirectHint: PropTypes.oneOf(["create", "reset"]),
};
