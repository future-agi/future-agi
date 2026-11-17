// Feature flag for Omega inside Future AGI.
//
// The Omega switch turns on the "Agent infrastructure" sidebar group, every
// Omega route and the Omega entries in Settings and the command palette.
// VITE_OMEGA_CONSOLE sets it:
//   "1" or "true"   on, as before: the operator says Omega runs here (Helm
//                   sets it with omega.enabled or hub.enabled, the standalone
//                   image with its hub profile). Nothing is asked.
//   "0" or "false"  off: the operator's off switch. Nothing is asked.
//                   ("off" and "no" are off too.)
//   unset or ""     on wherever this origin's Omega gateway answers
//                   <base>/healthz (gatewayProbe.js). main.jsx asks once as
//                   the app boots and does not wait: until the answer the
//                   switch is off and Omega paths show the loading screen
//                   (routes/sections/omega.jsx); then the router and the nav
//                   build again (useOmegaConsole.js). No gateway: off.
// Any other value counts as unset.
//
// Runtime config (window.__FUTURE_AGI_CONFIG__, written by the container
// entrypoint) wins over the build-time value, matching src/config-global.js.

import {
  GATEWAY_STATE,
  gatewayProbeState,
  settleGatewayProbe,
} from "./gatewayProbe";

export const OMEGA_SWITCH = Object.freeze({
  ON: "on",
  OFF: "off",
  AUTO: "auto",
});

const ON_VALUES = new Set(["1", "true"]);
const OFF_VALUES = new Set(["0", "false", "off", "no"]);

/** OMEGA_SWITCH.ON, OFF or AUTO (unset: follow the gateway). */
export function readOmegaConsoleSwitch(runtimeConfig, buildEnv) {
  const runtimeValue = runtimeConfig?.VITE_OMEGA_CONSOLE;
  const raw =
    runtimeValue !== undefined && runtimeValue !== null && runtimeValue !== ""
      ? runtimeValue
      : buildEnv?.VITE_OMEGA_CONSOLE;
  if (raw === undefined || raw === null) return OMEGA_SWITCH.AUTO;
  const value = String(raw).trim().toLowerCase();
  if (ON_VALUES.has(value)) return OMEGA_SWITCH.ON;
  if (OFF_VALUES.has(value)) return OMEGA_SWITCH.OFF;
  return OMEGA_SWITCH.AUTO;
}

/** True only when the value itself says on ("1" or "true"); the gateway is not read. */
export function readOmegaConsoleFlag(runtimeConfig, buildEnv) {
  return readOmegaConsoleSwitch(runtimeConfig, buildEnv) === OMEGA_SWITCH.ON;
}

/** This deployment's switch: OMEGA_SWITCH.ON, OFF or AUTO. */
export function omegaConsoleSwitch() {
  const runtimeConfig =
    (typeof window !== "undefined" && window.__FUTURE_AGI_CONFIG__) || {};
  return readOmegaConsoleSwitch(runtimeConfig, import.meta.env);
}

/**
 * Whether the Omega tabs show: the switch is on, or it is unset and this
 * page's gateway probe answered (false until it has). Synchronous; a
 * component that must follow the answer reads it through useOmegaConsole(),
 * which re-renders it when the probe settles.
 */
export function isOmegaConsoleEnabled() {
  const mode = omegaConsoleSwitch();
  if (mode === OMEGA_SWITCH.AUTO) {
    return gatewayProbeState() === GATEWAY_STATE.UP;
  }
  return mode === OMEGA_SWITCH.ON;
}

/**
 * main.jsx, as the app boots (not awaited): with the switch unset, ask the
 * gateway once (gatewayProbe.js); with it set, ask nothing. The returned
 * promise resolves once the answer is in. Never rejects or throws.
 */
export function settleOmegaConsoleSwitch(options) {
  try {
    if (omegaConsoleSwitch() !== OMEGA_SWITCH.AUTO) return Promise.resolve();
    return settleGatewayProbe(options).then(
      () => undefined,
      () => undefined,
    );
  } catch {
    return Promise.resolve();
  }
}
