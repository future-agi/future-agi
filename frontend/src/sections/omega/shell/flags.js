// Feature flag for Omega inside Future AGI.
//
// VITE_OMEGA_CONSOLE=1 turns on the product switcher, the "Agent
// infrastructure" sidebar group and every Omega route. With the flag unset the
// private repo builds plain Future AGI: no switcher, no extra nav, no routes.
//
// Runtime config (window.__FUTURE_AGI_CONFIG__, written by the container
// entrypoint) wins over the build-time value, matching src/config-global.js.

const ENABLED_VALUES = new Set(["1", "true"]);

export function readOmegaConsoleFlag(runtimeConfig, buildEnv) {
  const runtimeValue = runtimeConfig?.VITE_OMEGA_CONSOLE;
  const raw =
    runtimeValue !== undefined && runtimeValue !== null && runtimeValue !== ""
      ? runtimeValue
      : buildEnv?.VITE_OMEGA_CONSOLE;
  if (raw === undefined || raw === null) return false;
  return ENABLED_VALUES.has(String(raw).trim().toLowerCase());
}

export function isOmegaConsoleEnabled() {
  const runtimeConfig =
    (typeof window !== "undefined" && window.__FUTURE_AGI_CONFIG__) || {};
  return readOmegaConsoleFlag(runtimeConfig, import.meta.env);
}
