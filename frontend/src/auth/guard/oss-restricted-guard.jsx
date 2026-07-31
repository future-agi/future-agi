import PropTypes from "prop-types";
import { Navigate, useLocation } from "react-router-dom";

import { paths } from "src/routes/paths";

import { SplashScreen } from "src/components/loading-screen";
import { useDeploymentMode } from "src/hooks/useDeploymentMode";

// ----------------------------------------------------------------------

// Redirects OSS-unavailable auth routes to login, opening the CLI-setup modal.
// `requiresEmail` routes are only diverted when mail can't reach the user.
export default function OssRestrictedGuard({
  children,
  redirectHint,
  requiresEmail = false,
}) {
  const { isOSS, isLoading, isSuccess, canDeliverEmail } = useDeploymentMode();
  const location = useLocation();

  if (isLoading) return <SplashScreen />;

  const blocked = isSuccess && isOSS && !(requiresEmail && canDeliverEmail);

  if (blocked) {
    // Carry the incoming query (e.g. returnTo, utm) and add the modal hint.
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
  requiresEmail: PropTypes.bool,
};
