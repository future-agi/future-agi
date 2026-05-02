import React from "react";
import PropTypes from "prop-types";
import { useState, useEffect, useCallback } from "react";

import { paths } from "src/routes/paths";
import { useRouter } from "src/routes/hooks";

import { SplashScreen } from "src/components/loading-screen";

import { useAuthContext } from "../hooks";
import { useOrganization } from "src/contexts/OrganizationContext";
import { trackSignupConversion } from "src/utils/googleAds";
import { trackRedditSignup } from "src/utils/redditAds";
import { ROLES } from "src/utils/rolePermissionMapping";

// ----------------------------------------------------------------------

const loginPaths = {
  jwt: paths.auth.jwt.login,
};

// ----------------------------------------------------------------------

export default function AuthGuard({ children }) {
  const { loading } = useAuthContext();

  return <>{loading ? <SplashScreen /> : <Container> {children}</Container>}</>;
}

AuthGuard.propTypes = {
  children: PropTypes.node,
};

// ----------------------------------------------------------------------

function Container({ children }) {
  const router = useRouter();

  const { authenticated, method, user } = useAuthContext();

  const [checked, setChecked] = useState(false);

  const {
    currentOrganizationName,
    currentOrganizationDisplayName,
    isReady: isOrgReady,
  } = useOrganization();

  const check = useCallback(() => {
    const queryParams = new URLSearchParams(location.search);
    const sso_token =
      queryParams.get("sso_token") || queryParams.get("access_token");
    const refreshToken = queryParams.get("refresh_token");
    const returnTo = localStorage.getItem("redirectUrl");

    if (!authenticated) {
      if (sso_token) {
        if (refreshToken) {
          localStorage.setItem("refreshToken", refreshToken);
        }
        localStorage.setItem("accessToken", sso_token);
        // if (newOrg) {
        //   // redirect to onboarding page
        //   window.location.href = "/auth/jwt/setup-org";
        // } else {
        const isRelativePath =
          returnTo && returnTo.startsWith("/") && !returnTo.startsWith("//");
        if (isRelativePath) {
          localStorage.removeItem("redirectUrl");
          window.location.href = returnTo;
        } else {
          localStorage.removeItem("redirectUrl");
          window.location.href = window.location.pathname;
        }
        // }
        return;
      }
      const parameters = window.location.search;
      const searchParams = new URLSearchParams({
        returnTo: window.location.pathname + parameters,
      }).toString();

      const loginPath = loginPaths[method];

      const href = `${loginPath}?${searchParams}`;

      router.replace(href);
    } else {
      setChecked(true);
    }
  }, [authenticated, method, router]);

  useEffect(() => {
    check();
  }, [check]);

  // Handle initial-render redirect (get-started vs develop) — runs once
  // when user data first becomes available. Also fires the Google Ads
  // signup conversion for new OAuth/SSO users (email signups are fired
  // from jwt-register-view.jsx at API-success time).
  useEffect(() => {
    // Skip redirect logic for standalone pages (e.g. MCP OAuth consent)
    if (window.location.pathname.startsWith("/mcp/")) return;
    if (!user) return;

    // New user to platform (requires_org_setup) — redirect to org-setup
    // flow and, for OAuth/SSO paths, fire ad-platform signup conversions.
    if (user?.requires_org_setup) {
      // Default to "oauth" so SSO / deep-link / unknown paths are still
      // counted. "email" is excluded because the register view fires it
      // directly at API-success time.
      const provider = localStorage.getItem("signupProvider") || "oauth";
      if (user?.email && user?.id && provider !== "email") {
        if (typeof window.gtag === "function") {
          trackSignupConversion({
            email: user.email,
            method: provider,
            userId: String(user.id),
          });
        }
        if (typeof window.rdt === "function") {
          trackRedditSignup({
            email: user.email,
            userId: String(user.id),
          });
        }
      }

      if (window.location.pathname !== paths.auth.jwt.org_removed) {
        router.replace(paths.auth.jwt.org_removed);
      }
      return;
    }

    // Onboarding not finished — let the setup-org effect below handle routing.
    if (!user?.onboarding_completed) return;

    const initial = localStorage.getItem("initial-render");

    if (initial !== "done") {
      // If user has an organization_role, they're already part of an org (invited or owner)
      // Skip onboarding and go straight to dashboard
      if (user?.organization_role) {
        router.replace("/dashboard/falcon-ai");
      } else {
        router.replace("/dashboard/get-started");
      }
      localStorage.setItem("initial-render", "done");
    }
  }, [user, router]);

  // Gate "no org → setup-org" redirect on isOrgReady to avoid false
  // redirects while org context is still hydrating.
  useEffect(() => {
    // Skip redirect logic for standalone pages (e.g. MCP OAuth consent)
    if (window.location.pathname.startsWith("/mcp/")) return;
    if (!user || !isOrgReady) return;
    if (user?.requires_org_setup) return;
    // Once onboarding is complete, users with an organization_role are fully
    // set up — leave them where they are.
    if (user?.organization_role && user?.onboarding_completed) return;
    if( user?.default_workspace_role===ROLES.WORKSPACE_VIEWER) return;
    let orgName = currentOrganizationName;
    if (!orgName || orgName.trim() === "") {
      orgName = currentOrganizationDisplayName;
    }

    if (!orgName || orgName.trim() === "" || !user?.onboarding_completed) {
      // if (window.location.pathname !== "/auth/jwt/setup-org") {
      //   router.replace("/auth/jwt/setup-org");
      // }
    }
  }, [
    user,
    isOrgReady,
    currentOrganizationName,
    currentOrganizationDisplayName,
    router,
  ]);

  if (!checked) {
    return null;
  }

  return <>{children}</>;
}

Container.propTypes = {
  children: PropTypes.node,
};
