import React from "react";
import PropTypes from "prop-types";
import { useEffect, useCallback } from "react";

import { paths } from "src/routes/paths";

import { SplashScreen } from "src/components/loading-screen";

import { useAuthContext } from "../hooks";
import { useSearchParams } from "react-router-dom";
import { useRouter } from "src/routes/hooks";
import { resolveAuthSuccessRoute } from "src/sections/auth/jwt/post-login-navigation";

// ----------------------------------------------------------------------

export default function GuestGuard({ children }) {
  const { loading } = useAuthContext();

  return <>{loading ? <SplashScreen /> : <Container> {children}</Container>}</>;
}

GuestGuard.propTypes = {
  children: PropTypes.node,
};

// ----------------------------------------------------------------------

function Container({ children }) {
  const router = useRouter();

  const [searchParams] = useSearchParams();

  const returnTo = resolveAuthSuccessRoute({
    returnTo: searchParams.get("returnTo"),
    fallbackPath: paths.dashboard.root,
  });

  const { authenticated } = useAuthContext();

  const check = useCallback(() => {
    if (authenticated) {
      const pathname = window.location.pathname;
      const isExemptPath =
        pathname.startsWith("/auth/jwt/invitation/") ||
        pathname.startsWith("/auth/jwt/verify/") ||
        pathname === "/auth/jwt/setup-org" ||
        pathname === "/auth/jwt/org-removed";
      if (!isExemptPath) {
        router.replace(returnTo);
      }
    }
  }, [authenticated, returnTo, router]);

  useEffect(() => {
    check();
  }, [check]);

  return <>{children}</>;
}

Container.propTypes = {
  children: PropTypes.node,
};
