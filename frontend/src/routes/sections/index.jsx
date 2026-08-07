import React, { Suspense, useMemo } from "react";
import lazyWithRetry from "src/utils/lazyWithRetry";
import { Navigate, useRoutes } from "react-router-dom";

import { mainRoutes } from "./main";
import { authRoutes } from "./auth";
import { dashboardRoutes } from "./dashboard";
import { useAuthContext } from "src/auth/hooks";
import { AuthGuard } from "src/auth/guard";
import { SplashScreen } from "src/components/loading-screen";
import { useWorkspace } from "src/contexts/WorkspaceContext";
import {
  useDeploymentMode,
  usePostLoginPath,
} from "src/hooks/useDeploymentMode";
import SOSLoginPage from "src/pages/SOSLoginPage";
import { paths } from "src/routes/paths";
import { isValidationDone } from "src/sections/oss-first-run/ossFlowState";

const OAuthConsent = lazyWithRetry(() => import("src/pages/mcp/OAuthConsent"));
const SharedView = lazyWithRetry(() => import("src/pages/shared/SharedView"));
const OssSetupView = lazyWithRetry(
  () => import("src/sections/oss-first-run/OssSetupView"),
);

// ----------------------------------------------------------------------

export default function Router() {
  const { user } = useAuthContext();
  const { currentWorkspaceRole } = useWorkspace();
  const {
    isOSS,
    isSuccess: isDeploymentModeConfirmed,
    isLoading: isDeploymentModeLoading,
    isCloud,
  } = useDeploymentMode();
  const postLoginPath = usePostLoginPath();

  const dashboardRoutesArray = useMemo(
    () => dashboardRoutes(user, currentWorkspaceRole, { isCloud }),
    [user, currentWorkspaceRole, isCloud],
  );

  // Confirmed read required, or a failed probe sends cloud users to /setup.
  let rootTarget = postLoginPath;
  if (isDeploymentModeConfirmed && isOSS && !isValidationDone()) {
    rootTarget = paths.ossSetup;
  }

  const element = useRoutes([
    {
      path: "/",
      element: <Navigate to={rootTarget} replace />,
    },
    {
      path: paths.ossSetup,
      element: (
        <Suspense fallback={<SplashScreen />}>
          <OssSetupView />
        </Suspense>
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
  // Otherwise the first render uses the hook's default (self-hosted), which
  // omits cloud routes (billing/pricing/etc.). Stripe Checkout redirects
  // back to /dashboard/settings/pricing?upgrade=success&session_id=... — if
  // that route isn't registered yet, the catch-all sends users to /404 and
  // the session_id is lost before PricingPage can confirm the upgrade.
  if (isDeploymentModeLoading) {
    return <SplashScreen />;
  }

  return element;
}
