// The words the Console frame shows while it is not usable. They are passed to
// EmbeddedSurface's `copy` prop, so the gate panel itself stays the shared one.
import { DIAGNOSIS } from "src/sections/omega/embed";

import { DEFAULT_CONSOLE_SRC, consoleAddress } from "./console-config";
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

// Every other cause: nothing at the frame's address said hello in time. A
// deployment that does not route that address to the Console (a 404, often
// one that refuses to be framed) looks the same from here as a Console that
// is down or slow, so the words claim neither. An admin gets the address and
// what to check there; anyone else is asked to tell their admin.
const UNREACHABLE_TITLE = "Omega Console isn't reachable from this deployment";

const UNREACHABLE_MEMBER_BODY =
  "Ask your workspace admin to check the Omega Console service.";

const unreachableAdminBody = ({ address, crossOrigin }) =>
  crossOrigin
    ? `Omega Console didn't answer at ${address}. If you run this deployment, check that the Omega Console service there is healthy and lets this site show it in a frame. Then try again.`
    : `Omega Console didn't answer at ${address}. If you run this deployment, route ${address} to the Omega Console service, or check that the service is healthy. Then try again.`;

const unreachableCopy = (where, admin) => ({
  title: UNREACHABLE_TITLE,
  body: admin ? unreachableAdminBody(where) : UNREACHABLE_MEMBER_BODY,
});

const diagnosisCopy = (where, admin) => (cause) =>
  BROWSER_CAUSES.has(cause) ? undefined : unreachableCopy(where, admin);

/**
 * @param {string | null} mintFailure  a MINT_STATE value, or null
 * @param {{ src?: string, admin?: boolean }} [options]  `src`: the frame's
 *   Console URL, which the no-answer panel names for an admin (defaults to
 *   DEFAULT_CONSOLE_SRC). `admin`: the viewer is a workspace or organization
 *   admin; anyone else, or a role not known yet, gets the member line.
 */
export function consoleGateCopy(
  mintFailure,
  { src = DEFAULT_CONSOLE_SRC, admin = false } = {},
) {
  return {
    connecting: "Opening Omega Console…",
    needsKey: MINT_FAILURE_COPY[mintFailure] || NEEDS_KEY_COPY,
    refused: REFUSED_COPY,
    diagnosis: diagnosisCopy(consoleAddress(src), admin === true),
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
