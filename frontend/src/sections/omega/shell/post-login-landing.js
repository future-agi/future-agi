// Where a person lands after signing in (DEMO-FIX-2 item 8).
//
// A workspace the server admits to the Omega Console (READINESS 3.13: the
// VITE_OMEGA_CONSOLE switch on AND /api/capabilities/ allowing omega_console)
// lands on Console Chat. Everyone else lands where Future AGI always sent
// them. The decision needs the signed-in workspace's capabilities, which are
// not known on the sign-in page, so sign-in goes to one landing route
// (OMEGA_LANDING_PATH, registered only when the switch is on) that waits for
// the answer and then moves on. It fails closed: loading waits; paused, hidden
// or an unreadable answer go to the default page, never to the Console.

import { paths } from "src/routes/paths";

import { CONSOLE_BASE_PATH } from "../console/console-config";
import { PRODUCT_STATUS } from "./rollout";

export const OMEGA_LANDING_PATH = paths.dashboard.omegaLanding;
export const CONSOLE_CHAT_PATH = `${CONSOLE_BASE_PATH}/chat`;

/** Future AGI's own landing page: Get started on a self-hosted build, Falcon AI elsewhere. */
export const defaultLandingPath = (isOSS) =>
  isOSS ? paths.dashboard.getstarted : paths.dashboard.falconAI;

/**
 * Where the landing route goes, or null while the answer is still loading.
 * @param {string} consoleStatus PRODUCT_STATUS of the Console for this workspace
 * @param {boolean} isOSS
 */
export function landingTarget(consoleStatus, isOSS) {
  if (consoleStatus === PRODUCT_STATUS.LOADING) return null;
  return consoleStatus === PRODUCT_STATUS.ADMITTED
    ? CONSOLE_CHAT_PATH
    : defaultLandingPath(isOSS);
}
