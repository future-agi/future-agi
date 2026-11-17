/* eslint-disable react-refresh/only-export-components */

import React from "react";
import { Suspense } from "react";
import lazyWithRetry from "src/utils/lazyWithRetry";
import { Navigate, Outlet } from "react-router-dom";

import {
  AuthGuard,
  GuestGuard,
  OssRestrictedGuard,
  OssSetupGuard,
} from "src/auth/guard";
import AuthClassicLayout from "src/layouts/auth/classic";

import { SplashScreen } from "src/components/loading-screen";

// ----------------------------------------------------------------------

// JWT
const JwtLoginPage = lazyWithRetry(() => import("src/pages/auth/jwt/login"));
const JwtRegisterPage = lazyWithRetry(
  () => import("src/pages/auth/jwt/register"),
);
const ForgetPassword = lazyWithRetry(
  () => import("src/pages/auth/jwt/forget-password"),
);
const ResetPassword = lazyWithRetry(
  () => import("src/pages/auth/jwt/reset-password"),
);
const SSOLogin = lazyWithRetry(() => import("src/sections/auth/jwt/sso-login"));
const SetupOrg = lazyWithRetry(() => import("src/sections/auth/jwt/setup-org"));
const OrgRemoved = lazyWithRetry(
  () => import("src/sections/auth/jwt/org-removed"),
);
const TwoFactorPage = lazyWithRetry(
  () => import("src/pages/auth/jwt/two-factor"),
);
const InviteAccepted = lazyWithRetry(
  () => import("src/sections/auth/jwt/invite-accepted"),
);

// ----------------------------------------------------------------------

// Static gate (HO S2 leak scan): other builds keep every auth page.
const PROTOTYPE_BUILD =
  import.meta.env.VITE_ENVIRONMENT === "local" &&
  import.meta.env.VITE_PROTOTYPE_MODE === "true";

/**
 * L7c g1 (switcher:logout): the prototype signs in with email and password
 * only (POST /accounts/token/ is mocked). Sign up, password reset and SSO
 * are not mocked, so their deep links land on sign-in instead.
 */
const protoSignIn = (element) =>
  PROTOTYPE_BUILD ? <Navigate to="/auth/jwt/login" replace /> : element;

const authJwt = {
  path: "jwt",
  element: (
    <GuestGuard>
      <Suspense fallback={<SplashScreen />}>
        <Outlet />
      </Suspense>
    </GuestGuard>
  ),
  children: [
    {
      path: "invitation/accept/:uuid/:token",
      element: (
        <AuthClassicLayout>
          <JwtLoginPage />
        </AuthClassicLayout>
      ),
    },
    {
      path: "invitation/set-password/:uuid/:token",
      element: <InviteAccepted />,
    },
    {
      // Deliberately not behind OssSetupGuard: completion is per-browser, so
      // guarding login would throw an existing user into the setup wizard.
      path: "login",
      element: (
        <AuthClassicLayout>
          <JwtLoginPage />
        </AuthClassicLayout>
      ),
    },
    {
      path: "forget-password",
      element: protoSignIn(
        <AuthClassicLayout>
          <ForgetPassword />
        </AuthClassicLayout>,
      ),
    },
    {
      path: "verify/:uuid/:token",
      element: protoSignIn(
        <AuthClassicLayout>
          <ResetPassword />
        </AuthClassicLayout>,
      ),
    },
    {
      path: "register",
      element: protoSignIn(
        <OssSetupGuard>
          <JwtRegisterPage />
        </OssSetupGuard>,
      ),
    },
    {
      path: "sso-sml",
      element: protoSignIn(
        <OssRestrictedGuard>
          <AuthClassicLayout>
            <SSOLogin />
          </AuthClassicLayout>
        </OssRestrictedGuard>,
      ),
    },
    {
      path: "setup-org",
      element: (
        <AuthClassicLayout>
          <AuthGuard>
            <SetupOrg />
          </AuthGuard>
        </AuthClassicLayout>
      ),
    },
    {
      path: "org-removed",
      element: (
        <AuthGuard>
          <OrgRemoved />
        </AuthGuard>
      ),
    },
    {
      path: "two-factor",
      element: (
        <AuthClassicLayout>
          <TwoFactorPage />
        </AuthClassicLayout>
      ),
    },
  ],
};

export const authRoutes = [
  {
    path: "auth",
    children: [authJwt],
  },
];
