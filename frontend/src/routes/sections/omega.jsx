/* eslint-disable react-refresh/only-export-components */
import React, { Suspense } from "react";
import { Navigate } from "react-router-dom";

import { AuthGuard } from "src/auth/guard";
import RoleProtection from "src/routes/components/role-protection";
import { LoadingScreen } from "src/components/loading-screen";
import lazyWithRetry from "src/utils/lazyWithRetry";
import { paths } from "src/routes/paths";
import { isOmegaConsoleEnabled } from "src/sections/omega/shell/flags";
import {
  GATEWAY_STATE,
  gatewayProbeState,
} from "src/sections/omega/shell/gatewayProbe";
import OmegaConsoleLayout from "src/sections/omega/shell/OmegaConsoleLayout";
import OmegaNotEnabledPage from "src/sections/omega/shell/OmegaNotEnabledPage";
import ProductGate from "src/sections/omega/shell/ProductGate";
import { OMEGA_PRODUCT } from "src/sections/omega/shell/rollout";
import { HUB_ROUTES } from "src/sections/omega/hub/routes";

// Every Omega route in Future AGI. The products render only when the Omega
// switch is on (sections/omega/shell/flags.js: VITE_OMEGA_CONSOLE=1, or unset
// and the gateway answered). While the gateway probe is still out (`pending`),
// each path is registered with the loading screen instead, so a deep link
// keeps its URL rather than falling to the 404; the router builds the table
// again once it answers (routes/sections/index.jsx), as it does for Falcon
// AI's "pending". With the switch off ("0", or unset and no gateway), the same
// paths stay registered with the not-enabled page, so a deep link says why
// instead of a 404; only sign-in's landing ("home") stays Future AGI's.
// Each product route also renders only when the server admits the org and
// workspace to that product (READINESS 3.13): otherwise the standard 404, or
// the paused copy for an admitted org whose product is paused (ProductGate).
//
// Each route lazy-loads a tab entry file owned by its tab lane:
//   sections/omega/studio/StudioPage.jsx                    (studio-tab)
//   sections/omega/deployments/DeploymentsListPage.jsx      (deployments-tab)
//   sections/omega/deployments/DeploymentNewPage.jsx        (deployments-tab)
//   sections/omega/deployments/DeploymentDetailPage.jsx     (deployments-tab)
//   sections/omega/deployments/DeploymentSecretsPage.jsx    (vault-ui)
//   sections/omega/sandboxes/SandboxesListPage.jsx          (sandboxes-tab)
//   sections/omega/sandboxes/SandboxDetailPage.jsx          (sandboxes-tab)
//   sections/omega/console/ConsoleFramePage.jsx             (console-frame)
//   sections/omega/hub/routes.js (HUB_ROUTES: /dashboard/hub/**)  (hub-frontend)

const StudioPage = lazyWithRetry(
  () => import("src/sections/omega/studio/StudioPage"),
);
const DeploymentsListPage = lazyWithRetry(
  () => import("src/sections/omega/deployments/DeploymentsListPage"),
);
const DeploymentNewPage = lazyWithRetry(
  () => import("src/sections/omega/deployments/DeploymentNewPage"),
);
const DeploymentDetailPage = lazyWithRetry(
  () => import("src/sections/omega/deployments/DeploymentDetailPage"),
);
const DeploymentSecretsPage = lazyWithRetry(
  () => import("src/sections/omega/deployments/DeploymentSecretsPage"),
);
const SandboxesListPage = lazyWithRetry(
  () => import("src/sections/omega/sandboxes/SandboxesListPage"),
);
const SandboxDetailPage = lazyWithRetry(
  () => import("src/sections/omega/sandboxes/SandboxDetailPage"),
);
const ConsoleFramePage = lazyWithRetry(
  () => import("src/sections/omega/console/ConsoleFramePage"),
);
const SandboxSettingsPage = lazyWithRetry(
  () => import("src/sections/omega/sandboxes/settings/SandboxSettingsPage"),
);
const EarlyAccessPage = lazyWithRetry(
  () => import("src/sections/omega/shell/EarlyAccessPage"),
);
const PostLoginLanding = lazyWithRetry(
  () => import("src/sections/omega/shell/PostLoginLanding"),
);

const gated = (product, element) => (
  <ProductGate product={product}>{element}</ProductGate>
);

const gateRoute = (product) => (route) => ({
  ...route,
  ...(route.element ? { element: gated(product, route.element) } : {}),
  ...(route.children
    ? { children: route.children.map(gateRoute(product)) }
    : {}),
});

// The probe is out (VITE_OMEGA_CONSOLE unset): hold every Omega path.
const isProbePending = () => gatewayProbeState() === GATEWAY_STATE.CHECKING;

const HELD = <LoadingScreen sx={undefined} />;

// The same path (and children), every page swapped for the loading screen.
const holdRoute = (route) => ({
  ...route,
  ...(route.element ? { element: HELD } : {}),
  ...(route.children ? { children: route.children.map(holdRoute) } : {}),
});

// The switch is off: the same path (and children), every page swapped for
// the not-enabled page. No product gate: the product does not run here.
const offRoute = (element) => (route) => ({
  ...route,
  ...(route.element ? { element } : {}),
  ...(route.children ? { children: route.children.map(offRoute(element)) } : {}),
});

const NOT_ENABLED = <OmegaNotEnabledPage />;

const switched = (enabled, pending, routes, offElement = NOT_ENABLED) => {
  if (enabled) return routes;
  if (pending) return routes.map(holdRoute);
  return routes.map(offRoute(offElement));
};

/** Roles that may open Settings → Sandboxes (EE re-checks owner/admin on save). */
export const SANDBOX_SETTINGS_ROLES = ["Owner", "Admin", "workspace_admin"];

// Children of the "dashboard" route: they render inside DashboardLayout, with
// the Future AGI sidebar ("Agent infrastructure" group).
export function omegaDashboardRoutes({
  enabled = isOmegaConsoleEnabled(),
  pending = isProbePending(),
} = {}) {
  // Where sign-in lands (DEMO-FIX-2 item 8): Console Chat for a workspace
  // admitted to the Console, else Future AGI's usual page. Not gated itself:
  // it reads the same rollout answer and fails closed to the usual page. With
  // the switch off it is not registered: sign-in never lands on the
  // not-enabled page.
  const landing =
    enabled || pending ? [{ path: "home", element: <PostLoginLanding /> }] : [];
  return switched(enabled, pending, [
    ...landing,
    // "studio/*" lets the Studio tab mirror the framed Studio's own route.
    ...[
      { path: "studio", element: <StudioPage /> },
      { path: "studio/*", element: <StudioPage /> },
    ].map(gateRoute(OMEGA_PRODUCT.STUDIO_TAB)),
    gateRoute(OMEGA_PRODUCT.DEPLOYMENTS)({
      path: "deployments",
      children: [
        { index: true, element: <DeploymentsListPage /> },
        { path: "new", element: <DeploymentNewPage /> },
        { path: "secrets", element: <DeploymentSecretsPage /> },
        { path: ":id/:tab?", element: <DeploymentDetailPage /> },
      ],
    }),
    gateRoute(OMEGA_PRODUCT.SANDBOXES)({
      path: "sandboxes",
      children: [
        { index: true, element: <SandboxesListPage /> },
        { path: ":id/:tab?", element: <SandboxDetailPage /> },
      ],
    }),
    // Omega Hub (SPEC §7.1): browse, entries, publishers, publish,
    // submissions, settings and the maintainer review pages.
    ...HUB_ROUTES.map(gateRoute(OMEGA_PRODUCT.HUB)),
    // Squad was renamed Omega Console; old links land on the Console, whose
    // own gate decides.
    {
      path: "squad/*",
      element: <Navigate to={paths.dashboard.console.root} replace />,
    },
  ]);
}

// Children of the "settings" route (inside SettingsLayout): Settings → Sandboxes.
export function omegaSettingsRoutes({
  enabled = isOmegaConsoleEnabled(),
  pending = isProbePending(),
} = {}) {
  return switched(enabled, pending, [
    {
      path: "sandboxes",
      element: gated(
        OMEGA_PRODUCT.SANDBOXES,
        <RoleProtection allowedRoles={SANDBOX_SETTINGS_ROLES}>
          <SandboxSettingsPage />
        </RoleProtection>,
      ),
    },
    // Settings → Early access: the waitlist for the hosted Omega products.
    {
      path: "early-access",
      element: (
        <Suspense fallback={<LoadingScreen sx={undefined} />}>
          <EarlyAccessPage />
        </Suspense>
      ),
    },
  ]);
}

// Top-level routes outside DashboardLayout. /dashboard/console/* is a
// full-width page without the Future AGI sidebar; the framed EE Console draws
// its own sidebar and its own way back to Future AGI.
export function omegaStandaloneRoutes({
  enabled = isOmegaConsoleEnabled(),
  pending = isProbePending(),
} = {}) {
  return switched(
    enabled,
    pending,
    [
      {
        path: `${paths.dashboard.console.root}/*`,
        element: (
          <AuthGuard>
            {gated(
              OMEGA_PRODUCT.CONSOLE,
              <OmegaConsoleLayout>
                <Suspense fallback={<LoadingScreen sx={undefined} />}>
                  <ConsoleFramePage />
                </Suspense>
              </OmegaConsoleLayout>,
            )}
          </AuthGuard>
        ),
      },
    ],
    // Off: the plain page (outside DashboardLayout), still behind sign-in.
    <AuthGuard>
      <OmegaNotEnabledPage standalone />
    </AuthGuard>,
  );
}
