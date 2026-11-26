import { isVersioningDisabled } from "src/api/simulate-environments/versions";

const INLINE_ERRORS = new Set([
  "version_not_pickable",
  "version_not_runnable",
  "scenario_ungradable",
  "production_only",
  "scenario_selection_unknown",
  "idempotency_key_reused",
  "pair_validating",
  "pair_failed",
  // Every selected case held back: the server names each reason, read in place.
  "all_cases_blocked",
]);

const isClientError = (error) => {
  const status = error?.statusCode;
  return status >= 400 && status < 500;
};

// The server's message when it refused the seed, else null.
export const seedRefusal = (error) =>
  isClientError(error) && error?.attr === "seed"
    ? error.details.seed.join(" ")
    : null;

// A run refusal the run dialog explains in place, or null.
export const inlineRunError = (error) =>
  isClientError(error) &&
  (INLINE_ERRORS.has(error?.error) || seedRefusal(error))
    ? error
    : null;

// A run plan the server refused for these picks, or null. Versioning switched
// off is not a refusal: the run then uses the current versions.
export const planRefusal = (error) =>
  isClientError(error) && !isVersioningDisabled(error) ? error : null;
