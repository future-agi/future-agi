/* eslint-disable react-refresh/only-export-components */

import React from "react";
import { Suspense } from "react";
import lazyWithRetry from "src/utils/lazyWithRetry";
import { Outlet } from "react-router-dom";

import { AuthGuard, GuestGuard, OssRestrictedGuard } from "src/auth/guard";
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
      path: "login",
      element: (
        <AuthClassicLayout>
          <JwtLoginPage />
        </AuthClassicLayout>
      ),
    },
    {
      path: "forget-password",
      element: (
        <OssRestrictedGuard redirectHint="reset" requiresEmail>
          <AuthClassicLayout>
            <ForgetPassword />
          </AuthClassicLayout>
        </OssRestrictedGuard>
      ),
    },
    {
      path: "verify/:uuid/:token",
      element: (
        <AuthClassicLayout>
          <ResetPassword />
        </AuthClassicLayout>
      ),
    },
    {
      // Signup works in OSS: the password is set on this screen and the new
      // admin is signed straight in. No CLI diversion.
      path: "register",
      element: <JwtRegisterPage />,
    },
    {
      // SSO has no IdP on self-hosted. Send them back to login, but without a
      // modal hint — the CLI modal is about passwords, not SSO.
      path: "sso-sml",
      element: (
        <OssRestrictedGuard>
          <AuthClassicLayout>
            <SSOLogin />
          </AuthClassicLayout>
        </OssRestrictedGuard>
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
