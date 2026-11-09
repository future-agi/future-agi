/* eslint-disable react-refresh/only-export-components */
import React from "react";
import PropTypes from "prop-types";
import Button from "@mui/material/Button";

import { OMEGA_STATE, isSignInUnconfirmed } from "src/sections/omega/shared";

import StatePanel from "./StatePanel";

/**
 * Copy for a failed Omega call. Titles are ours; the body is the server's own
 * message when it sent one (EE says why sandboxes are off, which limit was
 * exceeded, and so on), so the page never paraphrases a refusal.
 */
export function errorCopy(error, { notFoundTitle = "Sandbox not found" } = {}) {
  const state = error?.state;
  const message = error?.message || null;
  if (state === OMEGA_STATE.SIGNED_OUT) {
    return { icon: "lock", title: "You're signed out", description: message };
  }
  // EE refused the delegated Future AGI session for this route family (its
  // host-assertion route cap), which is a deployment gap, not the user's role.
  if (
    state === OMEGA_STATE.REFUSED &&
    (error?.code === "host_delegation_scope_denied" ||
      error?.code === "host_delegation_scope_unavailable")
  ) {
    return {
      icon: "cloudOff",
      title: "Sandboxes can't be opened in this workspace",
      description:
        "Omega refused this Future AGI session for sandboxes. Nothing was created or changed. If this keeps happening, contact Future AGI support.",
    };
  }
  // A 401 that a fresh key did not cure: the sign-in, not the role.
  if (isSignInUnconfirmed(error)) {
    return {
      icon: "lock",
      title: error.title || "Omega couldn't confirm your sign-in",
      description: message,
      reload: true,
    };
  }
  if (state === OMEGA_STATE.REFUSED) {
    return {
      icon: "lock",
      title: "You don't have access to this sandbox action.",
      description: message,
    };
  }
  if (state === OMEGA_STATE.NOT_FOUND) {
    return {
      icon: "box",
      title: notFoundTitle,
      description: "It doesn't exist in this workspace, or it was deleted.",
    };
  }
  if (
    state === OMEGA_STATE.UNAVAILABLE &&
    error?.code === "sandboxes_unavailable"
  ) {
    return {
      icon: "cloudOff",
      title: "Sandboxes aren't available right now.",
      description: message,
    };
  }
  if (state === OMEGA_STATE.UNAVAILABLE) {
    return {
      icon: "cloudOff",
      title: "Omega can't complete this request right now.",
      description: message,
      retry: true,
    };
  }
  return {
    icon: "box",
    title: "Something went wrong",
    description: message,
    retry: true,
  };
}

export default function SandboxErrorState({
  error,
  onRetry,
  notFoundTitle,
  action,
  compact,
}) {
  const copy = errorCopy(error, { notFoundTitle });
  return (
    <StatePanel
      testId="sandbox-error-state"
      compact={compact}
      icon={copy.icon}
      title={copy.title}
      description={copy.description}
      action={
        action ||
        (copy.reload ? (
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

SandboxErrorState.propTypes = {
  error: PropTypes.object,
  onRetry: PropTypes.func,
  notFoundTitle: PropTypes.string,
  action: PropTypes.node,
  compact: PropTypes.bool,
};
