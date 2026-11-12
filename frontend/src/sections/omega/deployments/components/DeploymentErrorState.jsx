/* eslint-disable react-refresh/only-export-components */
import React from "react";
import PropTypes from "prop-types";
import Button from "@mui/material/Button";
import { useLocation } from "react-router-dom";

import { OMEGA_STATE, isSignInUnconfirmed } from "src/sections/omega/shared";
import { paths } from "src/routes/paths";

import { isRateLimited } from "../lib/rateLimit";
import StatePanel from "./StatePanel";

/**
 * Copy for a failed Omega call. Titles are ours; the body is EE's own message
 * when it sent one (why deployments are off, which limit was reached, ...),
 * so the page never paraphrases a refusal.
 */
function retryWait(error) {
  const seconds = Number(error?.retryAfter);
  if (!Number.isFinite(seconds) || seconds <= 0) return "a few seconds";
  const whole = Math.ceil(seconds);
  if (whole < 60) return whole === 1 ? "1 second" : `${whole} seconds`;
  const minutes = Math.ceil(whole / 60);
  return minutes === 1 ? "1 minute" : `${minutes} minutes`;
}

const SESSION_ENDED = {
  icon: "lock",
  title: "Your session has ended",
  description: "Sign in again to continue.",
  signIn: true,
};

/**
 * Refusals whose Future AGI copy (shared/omegaReasons.js) asks the reader to
 * reload the page: a reload mints a fresh key for the current role and
 * workspace. Matched on the code, never on the words, as Sandboxes does
 * (SandboxErrorState.jsx). `omega_scope_mismatch` narrowed to a role floor or
 * a workspace switch reads differently and asks for no reload.
 */
const RELOAD_CODES = new Set([
  "role_out_of_date",
  "omega_scope_mismatch",
  "session_exchange_refused",
  "unauthorized",
]);
const NO_RELOAD_REASONS = new Set(["role_below_floor", "scope_pivot"]);
const asksForReload = (error) =>
  RELOAD_CODES.has(error?.code) && !NO_RELOAD_REASONS.has(error?.reason);

/**
 * The sign-in copy says to try again in a moment, so its action asks Omega
 * again (each request carries a fresh key) instead of reloading the page. EE's
 * deployment routes send `unauthenticated` as a 403 with that copy, which
 * isSignInUnconfirmed (a 401) does not catch.
 */
const signInCopy = (error, action) => ({
  icon: "lock",
  title: error.title || "Omega couldn't confirm your sign-in",
  description: error.message || null,
  ...action,
});

export function errorCopy(
  error,
  { notFoundTitle = "Deployment not found" } = {},
) {
  const state = error?.state;
  const code = error?.code;
  const message = error?.message || null;
  // The gateway's own 429. A throttled session exchange keeps the tabs'
  // "Omega isn't responding" heading above its wait, as Sandboxes does
  // (shared/__tests__/sessionAssertion.test.js), via the UNAVAILABLE branch.
  if (isRateLimited(error) && code !== "session_exchange_throttled") {
    return {
      icon: "pulse",
      title: "Too many requests",
      description:
        message && /try again|retry|wait/i.test(message)
          ? message
          : `Omega is rate limiting this workspace. Try again in ${retryWait(error)}.`,
      retry: true,
    };
  }
  // The Future AGI session itself ended (the session exchange answered 401:
  // sessionAssertion.js). Keep this separate from role and route refusals so
  // the page finishes on a sign-in action, back to this page, never a skeleton.
  if (state === OMEGA_STATE.SIGNED_OUT) return SESSION_ENDED;
  // EE refused the delegated Future AGI session for this route family (its
  // host-assertion route cap): a gap in this Omega deployment, not the role.
  if (
    state === OMEGA_STATE.REFUSED &&
    (code === "host_delegation_scope_denied" ||
      code === "host_delegation_scope_unavailable")
  ) {
    return {
      icon: "cloudOff",
      title: "Deployments can't be opened in this workspace",
      description:
        "Omega refused this Future AGI session for deployments. Nothing was deployed or changed. If this keeps happening, contact Future AGI support.",
    };
  }
  // A 401 from Omega that a fresh key did not cure: the sign-in, not the role
  // (HIDE-UNFINISHED-FA). The Future AGI session still works, so asking again
  // sends a new key; a 401 whose own copy says to reload keeps Reload.
  if (isSignInUnconfirmed(error)) {
    return signInCopy(
      error,
      RELOAD_CODES.has(code) ? { reload: true } : { retry: true },
    );
  }
  // Any other 401: the browser session is no longer usable (DS-FA-DEPLOY-UX).
  if (error?.status === 401) return SESSION_ENDED;
  if (state === OMEGA_STATE.REFUSED && code === "unauthenticated") {
    return signInCopy(error, { retry: true });
  }
  // A refusal whose own copy asks for a reload (a role or workspace that
  // changed a moment ago) is the session, not the role, so the page keeps
  // that copy's heading and offers Reload.
  if (state === OMEGA_STATE.REFUSED && asksForReload(error)) {
    return signInCopy(error, { reload: true });
  }
  if (state === OMEGA_STATE.REFUSED && code === "role_forbidden") {
    return {
      icon: "lock",
      title: "Only workspace owners and admins can do this",
      description: message,
    };
  }
  if (state === OMEGA_STATE.REFUSED) {
    return {
      icon: "lock",
      title: "Deployments aren't available to your role",
      description: message,
    };
  }
  if (state === OMEGA_STATE.NOT_FOUND) {
    return {
      icon: "rocket",
      title: notFoundTitle,
      description: "It doesn't exist in this workspace, or it was deleted.",
    };
  }
  if (state === OMEGA_STATE.UNAVAILABLE && code === "deployments_unavailable") {
    return {
      icon: "cloudOff",
      title: "Deployments aren't enabled here",
      description: message,
    };
  }
  if (state === OMEGA_STATE.UNAVAILABLE) {
    return {
      icon: "cloudOff",
      title: "Omega isn't responding",
      description: message,
      retry: true,
    };
  }
  // Nothing above names this failure: say what failed and what to do next,
  // under EE's own message when it sent one. `fallback` marks this heading
  // as the general one, which the deploy form leaves out above EE's words.
  return {
    icon: "alert",
    title: "Deployments couldn't complete this request",
    description:
      message ||
      "Try again. If it keeps happening, contact Future AGI support.",
    retry: true,
    fallback: true,
  };
}

export default function DeploymentErrorState({
  error,
  onRetry,
  notFoundTitle,
  action,
  compact,
}) {
  const location = useLocation();
  const copy = errorCopy(error, { notFoundTitle });
  const returnTo = `${location.pathname}${location.search}${location.hash}`;
  const signInHref = `${paths.auth.jwt.login}?returnTo=${encodeURIComponent(returnTo)}`;
  return (
    <StatePanel
      testId="deployment-error-state"
      compact={compact}
      icon={copy.icon}
      title={copy.title}
      description={copy.description}
      action={
        action ||
        (copy.signIn ? (
          <Button
            component="a"
            href={signInHref}
            size="small"
            variant="contained"
            data-testid="deployment-sign-in"
          >
            Sign in
          </Button>
        ) : copy.reload ? (
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            onClick={() => window.location.reload()}
          >
            Reload
          </Button>
        ) : copy.retry && onRetry ? (
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            onClick={onRetry}
          >
            Try again
          </Button>
        ) : null)
      }
    />
  );
}

DeploymentErrorState.propTypes = {
  error: PropTypes.object,
  onRetry: PropTypes.func,
  notFoundTitle: PropTypes.string,
  action: PropTypes.node,
  compact: PropTypes.bool,
};
