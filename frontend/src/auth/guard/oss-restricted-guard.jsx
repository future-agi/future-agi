import PropTypes from "prop-types";
import { Navigate, useLocation } from "react-router-dom";

import { paths } from "src/routes/paths";

import { SplashScreen } from "src/components/loading-screen";
import { useDeploymentMode } from "src/hooks/useDeploymentMode";

// ----------------------------------------------------------------------

export default function OssRestrictedGuard({ children }) {
  const { isOSS, isLoading, isSuccess } = useDeploymentMode();
  const location = useLocation();

  if (isLoading) return <SplashScreen />;

  if (isSuccess && isOSS) {
    const search = new URLSearchParams(location.search).toString();
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
};
