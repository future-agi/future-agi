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
  // (HIDE-UNFINISHED-FA). The Future AGI session still works, so a reload mints
  // a new key.
  if (isSignInUnconfirmed(error)) {
    return {
      icon: "lock",
      title: error.title || "Omega couldn't confirm your sign-in",
      description: message,
      reload: true,
    };
  }
  // Any other 401: the browser session is no longer usable (DS-FA-DEPLOY-UX).
  if (error?.status === 401) return SESSION_ENDED;
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
  return {
    icon: "alert",
    title: "Something went wrong",
    description: message,
    retry: true,
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
