// Signing out always lands on the sign-in page (DEMO-FIX-2 item 7).
//
// Every sign-out used to navigate to "/". On a self-hosted build "/" goes to
// the first-run setup wizard in any browser that never ran its checks
// (routes/sections/index.jsx), which is every browser of a seeded or invited
// account: the owner demo signed out into "set up Future AGI". The sign-in
// page is where a signed-out person can do something.

import { paths } from "src/routes/paths";

export const SIGN_OUT_LANDING = paths.auth.jwt.login;

/**
 * Sign out, then land on the sign-in page (replacing the page signed out of,
 * so Back does not return to it). `logout` clears the session before its
 * first await, so the navigation renders together with the signed-out state;
 * a failed server call is already logged by `logout` and never keeps the
 * person on the page.
 */
export function signOutToSignIn(logout, navigate) {
  // Called first and not awaited: its synchronous part signs this tab out, so
  // the sign-in page never renders for a still-signed-in session (GuestGuard
  // would send that one straight back to the dashboard).
  let pending;
  try {
    pending = logout();
  } catch {
    pending = undefined;
  }
  navigate(SIGN_OUT_LANDING, { replace: true });
  return Promise.resolve(pending).catch(() => undefined);
}
