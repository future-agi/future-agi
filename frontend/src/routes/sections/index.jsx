import React, { Suspense, useMemo } from "react";
import lazyWithRetry from "src/utils/lazyWithRetry";
import { Navigate, useRoutes } from "react-router-dom";

import { mainRoutes } from "./main";
import { authRoutes } from "./auth";
import { dashboardRoutes } from "./dashboard";
import { omegaStandaloneRoutes } from "./omega";
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
import { useFeatureRouteAnswer } from "src/routes/hooks/use-feature-route-answer";
import { CAPABILITY } from "src/hooks/useCapabilities";
import { isValidationDone } from "src/sections/oss-first-run/ossFlowState";

const OAuthConsent = lazyWithRetry(() => import("src/pages/mcp/OAuthConsent"));
const GatewayOAuthConsent = lazyWithRetry(
  () => import("src/pages/mcp/GatewayOAuthConsent"),
);
const SharedView = lazyWithRetry(() => import("src/pages/shared/SharedView"));
// HO S2 (prototype-off hygiene): the outside reviewer's page reads only
// prototype paths (the review-links entries of DSX_PROTOTYPE in
// api/contracts/api-contract-exceptions.js), so it loads, and its route
// exists, only in a prototype build. The env check is static.
const PROTOTYPE_BUILD =
  import.meta.env.VITE_ENVIRONMENT === "local" &&
  import.meta.env.VITE_PROTOTYPE_MODE === "true";
const ReviewLinkPage = PROTOTYPE_BUILD
  ? lazyWithRetry(() => import("src/pages/review-link/ReviewLinkPage"))
  : null;
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
  // Falcon AI Connectors is a route only where the deployment allows Falcon
  // AI; before sign-in and until the capabilities answer, its URL is kept.
  const falconAI = useFeatureRouteAnswer(CAPABILITY.FALCON_AI, {
    enabled: Boolean(user),
  });

  const dashboardRoutesArray = useMemo(
    () => dashboardRoutes(user, currentWorkspaceRole, { isCloud, falconAI }),
    [user, currentWorkspaceRole, isCloud, falconAI],
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

    // Gateway authorization server; distinct from the platform MCP flow.
    {
      path: "/gateway/mcp/authorize",
      element: (
        <AuthGuard>
          <Suspense fallback={<SplashScreen />}>
            <GatewayOAuthConsent />
          </Suspense>
        </AuthGuard>
      ),
    },

    // Auth routes
    ...authRoutes,

    // Dashboard routes
    ...dashboardRoutesArray,

    // Omega Console: full width, outside the dashboard layout (flagged)
    ...omegaStandaloneRoutes(),

    // Shared resource viewer (public — no dashboard layout, no auth guard)
    {
      path: "/shared/:token",
      element: (
        <Suspense fallback={<SplashScreen />}>
          <SharedView />
        </Suspense>
      ),
    },
    // Outside reviewer's review link (public — W4-4; prototype builds only)
    ...(ReviewLinkPage
      ? [
          {
            path: "/review/:token",
            element: (
              <Suspense fallback={<SplashScreen />}>
                <ReviewLinkPage />
              </Suspense>
            ),
          },
        ]
      : []),

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
