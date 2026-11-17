// src/sections/omega/shared with the Omega session probe answered "ready",
// as the page tests mock it: the harness has no Future AGI to exchange with.
// Everything else is the real module.
export * from "src/sections/omega/shared/index.js";

export const useOmegaSession = () => ({
  state: "ready",
  code: null,
  message: null,
  recheck: () => undefined,
});
