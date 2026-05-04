import React, { Suspense, useMemo } from "react";
import lazyWithRetry from "src/utils/lazyWithRetry";
import { Navigate, useRoutes } from "react-router-dom";

import { PATH_AFTER_LOGIN } from "src/config-global";
import { paths } from "src/routes/paths";

import { mainRoutes } from "./main";
import { authRoutes } from "./auth";
import { dashboardRoutes } from "./dashboard";
import { useAuthContext } from "src/auth/hooks";
import { AuthGuard } from "src/auth/guard";
import { SplashScreen } from "src/components/loading-screen";
import { useWorkspace } from "src/contexts/WorkspaceContext";
import { useDeploymentMode } from "src/hooks/useDeploymentMode";
import SOSLoginPage from "src/pages/SOSLoginPage";

const OAuthConsent = lazyWithRetry(() => import("src/pages/mcp/OAuthConsent"));
const SharedView = lazyWithRetry(() => import("src/pages/shared/SharedView"));

// ----------------------------------------------------------------------

export default function Router() {
  const { user } = useAuthContext();
  const { currentWorkspaceRole } = useWorkspace();
  const { isOSS, isLoading: isDeploymentModeLoading } = useDeploymentMode();

  const dashboardRoutesArray = useMemo(
    () => dashboardRoutes(user, currentWorkspaceRole, { isOSS }),
    [user, currentWorkspaceRole, isOSS],
  );

  const element = useRoutes([
    {
      path: "/",
      element: (
        <Navigate
          to={isOSS ? paths.dashboard.develop : PATH_AFTER_LOGIN}
          replace
        />
      ),
    },
    {
      path: "/sos",
      element: <SOSLoginPage />,
    },

    // MCP OAuth consent (standalone, no dashboard layout, requires auth)
    {
      path: "/mcp/authorize",
      element: (
        <AuthGuard>
          <Suspense fallback={<SplashScreen />}>
            <OAuthConsent />
          </Suspense>
        </AuthGuard>
      ),
    },

    // Auth routes
    ...authRoutes,

    // Dashboard routes
    ...dashboardRoutesArray,

    // Shared resource viewer (public — no dashboard layout, no auth guard)
    {
      path: "/shared/:token",
      element: (
        <Suspense fallback={<SplashScreen />}>
          <SharedView />
        </Suspense>
      ),
    },

    // Main routes
    ...mainRoutes,

    // No match 404
    { path: "*", element: <Navigate to="/404" replace /> },
  ]);

  // Wait for deployment-mode resolution before rendering the route tree.
  // Otherwise the first render uses the hook's default `isOSS=true`, which
  // omits non-OSS routes (billing/pricing/etc.). Stripe Checkout redirects
  // back to /dashboard/settings/pricing?upgrade=success&session_id=... — if
  // that route isn't registered yet, the catch-all sends users to /404 and
  // the session_id is lost before PricingPage can confirm the upgrade.
  if (isDeploymentModeLoading) {
    return <SplashScreen />;
  }

  return element;
}
