// The words the Console frame shows while it is not usable. They are passed to
// EmbeddedSurface's `copy` prop, so the gate panel itself stays the shared one.
import { DIAGNOSIS } from "src/sections/omega/embed";

import { MINT_STATE } from "./session-assertion";

export const CONSOLE_LABEL = "Omega Console";

// Why Future AGI could not mint the Console's key.
const MINT_FAILURE_COPY = {
  [MINT_STATE.SIGNED_OUT]: {
    title: "Your Future AGI session has ended",
    body: "Sign in again, then open Omega Console.",
  },
  [MINT_STATE.REFUSED]: {
    title: "Omega Console isn't available for your role",
    body: "Your role in this workspace doesn't include Omega Console. Ask a workspace admin for access, or switch to another workspace.",
  },
  // Shown only for a moment: a viewer's refusal replaces the frame with the
  // viewer panel (VIEWER_COPY).
  [MINT_STATE.VIEWER]: {
    title: "Viewers can't use Omega Console",
    body: "Ask a workspace owner or admin for member access, or switch to another workspace.",
  },
  [MINT_STATE.UNAVAILABLE]: {
    title: "Omega Console can't sign you in right now",
    body: "Future AGI couldn't start an Omega Console session. Try again in a moment.",
  },
};

// The frame asked for a key and none came, without a known reason.
const NEEDS_KEY_COPY = {
  title: "Omega Console needs to reconnect",
  body: "Your Omega Console session didn't start. Reconnect to try again.",
};

// The Console turned the key down (it expired or was already used).
const REFUSED_COPY = {
  title: "Your Omega Console session ended",
  body: "Omega Console didn't accept this session. Reconnect to start a new one.",
};

// No hello in time. Browser-specific causes keep the shared wording; they
// only apply when the Console is pointed at a server on this computer.
const BROWSER_CAUSES = new Set([
  DIAGNOSIS.localNetworkDenied,
  DIAGNOSIS.localNetworkPrompt,
  DIAGNOSIS.safari,
  DIAGNOSIS.brave,
]);

const NOT_RESPONDING_COPY = {
  title: "Omega Console isn't available right now",
  body: "It didn't respond. It may still be starting. Try again in a moment.",
};

const diagnosisCopy = (cause) =>
  BROWSER_CAUSES.has(cause) ? undefined : NOT_RESPONDING_COPY;

/**
 * @param {string | null} mintFailure  a MINT_STATE value, or null
 */
export function consoleGateCopy(mintFailure) {
  return {
    connecting: "Opening Omega Console…",
    needsKey: MINT_FAILURE_COPY[mintFailure] || NEEDS_KEY_COPY,
    refused: REFUSED_COPY,
    diagnosis: diagnosisCopy,
  };
}

// A viewer (CP P10, D6): no frame, one way to check again after a role change.
export const VIEWER_COPY = Object.freeze({
  title: "Viewers can't use Omega Console",
  body: "Your role in this workspace is Viewer. Ask a workspace owner or admin for member access, or switch to another workspace.",
  check: "Check again",
  checking: "Checking…",
  still: "You're still a viewer in this workspace.",
  failed:
    "Future AGI couldn't check your access just now. Try again in a moment.",
});
