import React from "react";
import PropTypes from "prop-types";
import { useState, useEffect, useCallback, useRef } from "react";
import axiosPackage from "axios";

import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import Button from "@mui/material/Button";
import Typography from "@mui/material/Typography";

import { paths } from "src/routes/paths";
import { useRouter } from "src/routes/hooks";
import { HOST_API } from "src/config-global";

import Section from "src/components/section";
import { SplashScreen } from "src/components/loading-screen";

import { useAuthContext } from "../hooks";
import {
  clearOnboardingRecovery,
  useAuthRedirectParams,
} from "../auth-url-intake";
import {
  SAML_AUTH_GENERATION_KEY,
  SESSION_KEYS_TO_CLEAR,
  isSafeSamlNext,
} from "../saml-bootstrap";
import { useOrganization } from "src/contexts/OrganizationContext";
import { trackSignupConversion } from "src/utils/googleAds";
import { trackRedditSignup } from "src/utils/redditAds";
import { trackTwitterSignup } from "src/utils/twitterAds";
import { ROLES } from "src/utils/rolePermissionMapping";
import { usePostLoginPath } from "src/hooks/useDeploymentMode";

// ----------------------------------------------------------------------

const loginPaths = {
  jwt: paths.auth.jwt.login,
};

// CR-07: a sign-in redirect carries a one-time code, never a
// session. The code is redeemed by a JSON POST and the tokens are taken from
// that response body only. These URL keys once carried a live session; they
// are never read, only removed from the address bar.
const LEGACY_TOKEN_PARAMS = [
  "sso_token",
  "access_token",
  "refresh_token",
  "access",
  "refresh",
];
const HANDOFF_URL_PARAMS = ["code", "kind"];
// SOS codes belong to the /sos page; any other code is some other flow's.
const GUARD_HANDOFF_KINDS = ["sso", "saml", "aws_marketplace"];
const HANDOFF_REDEEM_PATH = "/accounts/handoff/redeem/";
const SAML_NEXT_KEY = "fai_saml_next";

// What the person sees when a sign-in code can't be redeemed. The redeem view
// answers every refusal with one 400; any other failure (no response, a server
// error) never checked the code, so the screen says FutureAGI wasn't reached.
const HANDOFF_FAILED_TITLE = "We couldn't finish signing you in";
const HANDOFF_REFUSED_BODY =
  "This sign-in attempt timed out or was already used. Sign in again to continue.";
const HANDOFF_UNREACHABLE_BODY =
  "We couldn't reach FutureAGI to finish signing you in. Check your connection and sign in again.";

function isHandoffUnreachable(error) {
  return !error?.handoffRefused && error?.response?.status !== 400;
}

function urlParamName(pair) {
  const raw = pair.split("=", 1)[0].replace(/\+/g, " ");
  try {
    return decodeURIComponent(raw);
  } catch {
    return raw;
  }
}

// Remove `keys` from the address bar, keeping every other byte of the URL.
function removeUrlParams(keys) {
  try {
    const url = new URL(window.location.href);
    if (!url.search) return;
    const kept = url.search
      .slice(1)
      .split("&")
      .filter((pair) => !keys.includes(urlParamName(pair)));
    const search = kept.length ? `?${kept.join("&")}` : "";
    if (search === url.search) return;
    window.history.replaceState(
      window.history.state,
      "",
      `${url.origin}${url.pathname}${search}${url.hash}`,
    );
  } catch {
    // A refused history update must never block sign-in.
  }
}

function readHandoff(params) {
  const code = params.get("code");
  const kind = params.get("kind");
  return code && GUARD_HANDOFF_KINDS.includes(kind)
    ? {
        code,
        kind,
        next: params.get("next"),
        isNewUser: params.get("is_new_user") === "true",
        pathname: window.location.pathname,
      }
    : null;
}

// Raw axios, as auth-provider's login does: no stale Authorization header,
// no refresh-on-401 interceptor and no cookies ride along with the code.
async function redeemHandoffCode(code, kind) {
  const response = await axiosPackage.post(
    `${HOST_API}${HANDOFF_REDEEM_PATH}`,
    { code, kind },
    { headers: { "Content-Type": "application/json" } },
  );
  const access = response?.data?.access;
  const refresh = response?.data?.refresh;
  if (typeof access !== "string" || !access) {
    // The server answered without a session: a refusal, not a lost connection.
    throw Object.assign(new Error("Sign-in handoff refused"), {
      handoffRefused: true,
    });
  }
  return {
    access,
    refresh: typeof refresh === "string" && refresh ? refresh : null,
  };
}

// The storage effects of bootstrapSamlSession (saml-bootstrap.js), applied to
// a redeemed token: a SAML sign-in replaces whatever session this browser had.
function establishSamlSession(token, next) {
  clearOnboardingRecovery();
  const generation = crypto.randomUUID();
  sessionStorage.setItem(SAML_AUTH_GENERATION_KEY, generation);
  localStorage.setItem(SAML_AUTH_GENERATION_KEY, generation);

  localStorage.setItem("accessToken", token);
  localStorage.removeItem("refreshToken");
  localStorage.removeItem("rememberMe");
  localStorage.removeItem("sosMode");
  localStorage.removeItem("redirectUrl");

  SESSION_KEYS_TO_CLEAR.forEach((key) => sessionStorage.removeItem(key));
  sessionStorage.removeItem(SAML_NEXT_KEY);
  if (isSafeSamlNext(next)) {
    sessionStorage.setItem(SAML_NEXT_KEY, next);
  }
}

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

  const { authenticated, method, user, initialize, logout } = useAuthContext();
  const postLoginPath = usePostLoginPath();

  const [checked, setChecked] = useState(false);
  const hydrateAttempted = useRef(false);
  const redirecting = useRef(false);
  const queryParams = useAuthRedirectParams();
  // Read once from the reader's snapshot; the address bar loses it at once.
  const [handoff] = useState(() => readHandoff(queryParams));
  const redeeming = useRef(false);
  const [handoffRefused, setHandoffRefused] = useState(false);
  const [handoffUnreachable, setHandoffUnreachable] = useState(false);
  const mounted = useRef(false);
  const superseded = useRef(false);
  // The previous account must neither route nor appear as a completed login
  // while this handoff is pending, refused, or waiting for its full reload.
  const handoffBlocksRouting = Boolean(
    handoff && (handoff.kind !== "sso" || !authenticated || redeeming.current || handoffRefused),
  );

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const {
    currentOrganizationName,
    currentOrganizationDisplayName,
    isReady: isOrgReady,
  } = useOrganization();

  const check = useCallback(() => {
    // A full navigation is pending. Rechecks must not overwrite its returnTo
    // after the first pass has removed redirectUrl from localStorage.
    if (redirecting.current || superseded.current) return;
    const currentCode = new URLSearchParams(window.location.search).get("code");
    if (
      handoffBlocksRouting &&
      (window.location.pathname !== handoff.pathname ||
        (currentCode && currentCode !== handoff.code))
    ) {
      // Do not strip a newer handoff or let this reader commit over it.
      superseded.current = true;
      setHandoffRefused(true);
      return;
    }
    // Legacy token params are dropped unread, before returnTo is built below,
    // so they cannot ride into the login URL, history or page-URL telemetry.
    removeUrlParams(LEGACY_TOKEN_PARAMS);
    if (handoff) removeUrlParams(HANDOFF_URL_PARAMS);
    if (handoffRefused) return;

    // A SAML sign-in replaces any current session (as bootstrapSamlSession
    // did for the old URL token); an OAuth code is used only when signed out.
    if (handoffBlocksRouting) {
      if (redeeming.current) return;
      redeeming.current = true;
      const generation = localStorage.getItem(SAML_AUTH_GENERATION_KEY);
      const tabGeneration = sessionStorage.getItem(SAML_AUTH_GENERATION_KEY);
      const previousAccess = localStorage.getItem("accessToken");
      const isCurrent = () => {
        const currentCode = new URLSearchParams(window.location.search).get(
          "code",
        );
        return (
          mounted.current &&
          !superseded.current &&
          window.location.pathname === handoff.pathname &&
          (!currentCode || currentCode === handoff.code) &&
          localStorage.getItem(SAML_AUTH_GENERATION_KEY) === generation &&
          sessionStorage.getItem(SAML_AUTH_GENERATION_KEY) === tabGeneration &&
          localStorage.getItem("accessToken") === previousAccess
        );
      };
      redeemHandoffCode(handoff.code, handoff.kind).then(
        ({ access, refresh }) => {
          if (!isCurrent()) {
            // A mounted reader must recover, not block its new route forever.
            if (mounted.current) {
              superseded.current = true;
              setHandoffRefused(true);
            }
            return;
          }
          // Keep the handoff until navigation commits (index.html's pagehide
          // cleanup) or this reader actually unmounts.
          redirecting.current = true;
          if (handoff.kind === "saml") {
            establishSamlSession(access, handoff.next);
            // A full load re-initializes the provider with the new session;
            // the authenticated branch below then follows fai_saml_next.
            window.location.href = handoff.pathname;
            return;
          }
          if (handoff.kind === "aws_marketplace") {
            establishSamlSession(access, null);
            if (refresh) localStorage.setItem("refreshToken", refresh);
            // Keep new_org and other non-credential routing metadata.
            window.location.href = `${handoff.pathname}${window.location.search}${window.location.hash}`;
            return;
          }
          if (refresh) {
            localStorage.setItem("refreshToken", refresh);
          }
          localStorage.setItem("accessToken", access);

          if (handoff.isNewUser) {
            localStorage.setItem("isNewOAuthSignup", "1");
            localStorage.setItem("isNewOAuthSignupAt", String(Date.now()));
          }
          const returnTo = localStorage.getItem("redirectUrl");
          const isRelativePath =
            returnTo && returnTo.startsWith("/") && !returnTo.startsWith("//");
          if (isRelativePath) {
            localStorage.setItem("initial-render", "done");
            localStorage.removeItem("redirectUrl");
            window.location.href = returnTo;
          } else {
            localStorage.removeItem("redirectUrl");
            window.location.href = handoff.pathname;
          }
        },
        // Keep the refusal visible even when a previous account is valid.
        (error) => {
          if (mounted.current) {
            if (!isCurrent()) superseded.current = true;
            setHandoffUnreachable(isHandoffUnreachable(error));
            setHandoffRefused(true);
          }
        },
      );
      return;
    }

    if (!authenticated) {
      const existingToken = localStorage.getItem("accessToken");
      if (existingToken && !hydrateAttempted.current) {
        hydrateAttempted.current = true;
        initialize();
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
      queryParams.clear();
      const samlNext = sessionStorage.getItem("fai_saml_next");
      if (samlNext) {
        sessionStorage.removeItem("fai_saml_next");
        router.replace(samlNext);
        return;
      }
      setChecked(true);
    }
  }, [
    authenticated,
    method,
    router,
    initialize,
    queryParams,
    handoff,
    handoffRefused,
    handoffBlocksRouting,
  ]);

  useEffect(() => {
    check();
  }, [check]);

  // Handle initial-render redirect (get-started vs develop) — runs once
  // when user data first becomes available. Also fires the Google Ads
  // signup conversion for new OAuth/SSO users (email signups are fired
  // from jwt-register-view.jsx at API-success time).
  useEffect(() => {
    if (handoffBlocksRouting || redirecting.current) return;
    // Skip redirect logic for standalone pages (e.g. MCP OAuth consent)
    if (
      window.location.pathname.startsWith("/mcp/") ||
      window.location.pathname === "/gateway/mcp/authorize"
    )
      return;
    if (!user) return;
    const isNewOAuthSignup = localStorage.getItem("isNewOAuthSignup") === "1";
    if (isNewOAuthSignup) {
      const stampedAt = Number(localStorage.getItem("isNewOAuthSignupAt") || 0);
      const fresh = Date.now() - stampedAt < 10 * 60 * 1000;
      const provider = localStorage.getItem("signupProvider") || "oauth";
      if (fresh && user?.email && user?.id && provider !== "email") {
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
        trackTwitterSignup({
          email: user.email,
          method: provider,
          userId: String(user.id),
        });
      }
      localStorage.removeItem("isNewOAuthSignup");
      localStorage.removeItem("isNewOAuthSignupAt");
    }

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
        trackTwitterSignup({
          email: user.email,
          method: provider,
          userId: String(user.id),
        });
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
        router.replace(postLoginPath);
      } else {
        router.replace("/dashboard/get-started");
      }
      localStorage.setItem("initial-render", "done");
      localStorage.removeItem("redirectUrl");
    }
  }, [postLoginPath, user, router, handoffBlocksRouting]);

  // Gate "no org → setup-org" redirect on isOrgReady to avoid false
  // redirects while org context is still hydrating.
  useEffect(() => {
    if (handoffBlocksRouting || redirecting.current) return;
    // Skip redirect logic for standalone pages (e.g. MCP OAuth consent)
    if (
      window.location.pathname.startsWith("/mcp/") ||
      window.location.pathname === "/gateway/mcp/authorize"
    )
      return;
    if (!user || !isOrgReady) return;
    if (user?.requires_org_setup) return;
    // Once onboarding is complete, users with an organization_role are fully
    // set up — leave them where they are.
    if (user?.organization_role && user?.onboarding_completed) return;
    if (user?.default_workspace_role === ROLES.WORKSPACE_VIEWER) return;
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
    handoffBlocksRouting,
  ]);

  const signInAgain = async () => {
    redirecting.current = true;
    try {
      // Explicit recovery signs out the old account too; otherwise GuestGuard
      // would send an already-authenticated browser straight back to it.
      await logout();
    } catch {
      // logout clears local credentials before its remote request. A failed
      // request must not strand the user on this consumed-link screen.
    }
    queryParams.clear();
    window.location.href = loginPaths[method] || loginPaths.jwt;
  };

  if (handoffRefused) {
    return (
      <Box
        component="main"
        sx={{
          minHeight: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          px: 2,
          bgcolor: "background.default",
        }}
      >
        <Section
          variant="panel"
          role="alert"
          sx={{ width: 1, maxWidth: 440 }}
        >
          <Stack
            spacing={2}
            alignItems="center"
            sx={{ p: 2, textAlign: "center" }}
          >
            <Box
              component="img"
              src="/favicon/logo.svg"
              alt=""
              sx={{ width: 32, height: 32 }}
            />
            <Typography variant="h4" component="h1" color="text.primary">
              {HANDOFF_FAILED_TITLE}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {handoffUnreachable
                ? HANDOFF_UNREACHABLE_BODY
                : HANDOFF_REFUSED_BODY}
            </Typography>
            <Button
              fullWidth
              size="large"
              variant="contained"
              color="primary"
              onClick={signInAgain}
            >
              Sign in again
            </Button>
          </Stack>
        </Section>
      </Box>
    );
  }

  // The code is being redeemed: show the splash screen, not a blank page.
  if (handoffBlocksRouting) {
    return <SplashScreen />;
  }

  if (!checked) {
    return null;
  }

  return <>{children}</>;
}

Container.propTypes = {
  children: PropTypes.node,
};
