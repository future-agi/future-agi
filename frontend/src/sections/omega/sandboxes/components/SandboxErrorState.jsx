/* eslint-disable react-refresh/only-export-components */
import React from "react";
import PropTypes from "prop-types";
import Button from "@mui/material/Button";

import {
  OMEGA_STATE,
  isCustomerSafe,
  isSignInUnconfirmed,
  refusalEntry,
} from "src/sections/omega/shared";
import { defaultMessage } from "src/sections/omega/shared/omegaStates";

import StatePanel from "./StatePanel";

/**
 * Sandboxes are off on this deployment (EE answers the list and settings 200
 * with a create refusal coded `sandboxes_unavailable` and every other sandbox
 * route 409 with that code; an older EE answered 503). These messages name no
 * cause: EE's default, Future AGI's copy for the code (omegaReasons.js) and
 * the stock line an error with no message carries. The page then says what
 * the deployment needs; a message that names a cause a customer may read is
 * shown instead.
 */
const GENERIC_OFF_MESSAGES = new Set([
  "Sandboxes are not enabled on this deployment.",
  refusalEntry("sandboxes_unavailable")?.message,
  defaultMessage(OMEGA_STATE.UNAVAILABLE),
]);
const OFF_NEEDS =
  "An administrator enables a sandbox host for this deployment.";

function offReason(message) {
  return isCustomerSafe(message) && !GENERIC_OFF_MESSAGES.has(message.trim())
    ? message
    : OFF_NEEDS;
}

/**
 * Sandboxes are not set up on this deployment, in the same words wherever it
 * shows: the list, Settings, a sandbox's page or its Files tab. `message` is
 * the deployment's reason, kept only when a customer may read it.
 */
export function unavailableCopy(message) {
  return {
    icon: "cloudOff",
    title: "Sandboxes are not available on this deployment.",
    description: offReason(message),
  };
}

/**
 * Failures that carry no answer from Sandboxes itself: the browser got no
 * reply (`network_error`), the gateway could not reach the sandbox service and
 * said so, or whatever answered sent no code at all (a bare 502 from a proxy).
 * A coded answer (a refusal, sandboxes off, a rate limit) keeps its own copy.
 */
const NO_ANSWER_CODES = new Set([
  "network_error",
  "omega_remote_unreachable",
  "omega_remote_timeout",
  "omega_gateway_failure",
]);
const isNoAnswer = (error) => !error?.code || NO_ANSWER_CODES.has(error.code);

/**
 * Refusals whose copy says to reload the page: a reload mints a fresh key for
 * the current role and workspace. Matched on the code, never on the words, so
 * a copy edit cannot drop the button. `omega_scope_mismatch` narrowed by a
 * reason reads differently (ROLE_FLOOR, OTHER_WORKSPACE) and asks for no
 * reload.
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
 * The sign-in copy says to try again in a moment, so its action asks again
 * (each request carries a fresh key) instead of reloading the page. EE's
 * workspace routes send `unauthenticated` as a 401 or a 403 with that copy;
 * the 401 is caught by isSignInUnconfirmed, the 403 only here.
 */
const signInCopy = (error, action) => ({
  icon: "lock",
  title: error.title || "Omega couldn't confirm your sign-in",
  description: error.message || null,
  ...action,
});

/**
 * Copy for a failed Omega call. The heading is the failure's own Future AGI
 * title when it has one ("Viewers can't use Omega", "This sandbox is private
 * to its creator"), else ours; the body is the failure's message, so the page
 * never paraphrases a refusal.
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
  // A 401 that a fresh key did not cure: the sign-in, not the role. One whose
  // own copy says to reload keeps Reload.
  if (isSignInUnconfirmed(error)) {
    return signInCopy(
      error,
      RELOAD_CODES.has(error.code) ? { reload: true } : { retry: true },
    );
  }
  if (state === OMEGA_STATE.REFUSED && error?.code === "unauthenticated") {
    return signInCopy(error, { retry: true });
  }
  if (state === OMEGA_STATE.REFUSED) {
    return {
      icon: "lock",
      title: error?.title || "You don't have access to Sandboxes here",
      description: message,
      reload: asksForReload(error),
    };
  }
  if (state === OMEGA_STATE.NOT_FOUND) {
    return {
      icon: "box",
      title: notFoundTitle,
      description: "It doesn't exist in this workspace, or it was deleted.",
    };
  }
  // Keyed on the code alone: EE answers it 409 (an older EE, 503), and both
  // mean the same thing, which waiting will not change.
  if (error?.code === "sandboxes_unavailable") return unavailableCopy(message);
  if (state === OMEGA_STATE.UNAVAILABLE && isNoAnswer(error)) {
    return {
      icon: "cloudOff",
      title: "Sandboxes can't be reached right now.",
      description: "This page didn't get an answer. Try again in a moment.",
      retry: true,
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
    title: error?.title || "Sandboxes couldn't complete this request",
    description:
      message ||
      "Try again. If it keeps happening, contact Future AGI support.",
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
