import PropTypes from "prop-types";
import { Navigate, useLocation } from "react-router-dom";

import { paths } from "src/routes/paths";

import { SplashScreen } from "src/components/loading-screen";
import { useDeploymentMode } from "src/hooks/useDeploymentMode";

// ----------------------------------------------------------------------

// Redirects OSS-unavailable auth routes to login, opening the CLI-setup modal.
export default function OssRestrictedGuard({ children, redirectHint }) {
  const { isOSS, isLoading, isSuccess } = useDeploymentMode();
  const location = useLocation();

  if (isLoading) return <SplashScreen />;

  if (isSuccess && isOSS) {
    // Carry the incoming query (e.g. returnTo, utm) and add the tab hint.
    const params = new URLSearchParams(location.search);
    if (redirectHint) params.set("ossSetup", redirectHint);
    const search = params.toString();
    return (
      <Navigate
        to={{
          pathname: paths.auth.jwt.login,
          search: search ? `?${search}` : "",
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
