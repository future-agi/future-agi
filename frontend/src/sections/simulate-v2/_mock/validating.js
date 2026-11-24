/*
  Validate in progress (Karthik's review, 23 Nov, points 8 and 12). The
  backend can say which scenarios Validate is proving and how many, not how
  many are done, so the UI says "Validating 12 scenarios" — never "N of M".
  Only Save waits for it (Karthik, 24 Nov): runs use the last saved version,
  so they can start anytime. It lives on the environment's state, so it
  survives leaving the page and coming back.

  envState.validating = { ids: [scenario ids], startedAt, from: "reprove" | "review" }
  envState.validateFailed = { at, message, ids } — the whole Validate failed
  (not one scenario's proof): the last attempt's error, until the next one.
*/

import { knownToolsFor, requiredToolsOf } from "./toolFit";
import { reproveScenarios } from "./scenarioStates";

/** How long the prototype pretends a Validate takes. */
export const VALIDATE_MS = 6000;

export const validatingOf = (envState) => envState?.validating || null;

export const validatingLabel = (v) =>
  v ? `Validating ${v.ids.length} scenario${v.ids.length === 1 ? "" : "s"}` : "";

/** Why Save is off while Validate runs. Runs are not held. */
export const VALIDATE_SAVE_WAIT = "Save waits until Validate finishes";
export const validatingSaveBlock = (v) => (v ? VALIDATE_SAVE_WAIT : null);

/** Start: patch is the env-state patcher (useEnvState().patch). */
export const startValidating = (patch, ids, from, extra = {}) =>
  patch({
    validating: {
      ids: [...ids],
      startedAt: new Date().toISOString(),
      from,
      ...extra,
    },
    validateFailed: null,
  });

export const finishValidating = (patch) => patch({ validating: null });

export const failValidating = (patch, v, message) =>
  patch({
    validating: null,
    validateFailed: {
      at: new Date().toISOString(),
      message,
      ids: v?.ids || [],
    },
  });

/* Demo knob ?validate=fail: the next Validate on this environment fails as a
   whole, once. */
const FAIL_NEXT = new Map();
export const VALIDATE_FAIL_MESSAGE = "the test world couldn't be prepared";
export const failNextValidate = (envId) =>
  FAIL_NEXT.set(envId, VALIDATE_FAIL_MESSAGE);

/** Milliseconds until a Validate in progress lands (0 when it is due). */
export const validateLeftMs = (v) =>
  Math.max(0, (Date.parse(v?.startedAt) || 0) + VALIDATE_MS - Date.now());

/*
  Lands a Validate that has had its time, whatever tab is open: the
  environment page calls this, so Save waits until it really ends.
  Re-proving: the rows as they stand now are proved again (anything removed
  meanwhile is skipped). From the review drawer: the change list it passed
  for is recorded. Returns what happened, for the toast, or null.
*/
export const settleValidating = ({ env, envState, patch }) => {
  const v = validatingOf(envState);
  if (!v || !env) return null;
  if (FAIL_NEXT.has(env.id)) {
    const message = FAIL_NEXT.get(env.id);
    FAIL_NEXT.delete(env.id);
    failValidating(patch, v, message);
    return { failed: true, message };
  }
  if (v.from === "review") {
    patch({
      validating: null,
      ...(v.checkKey && { reviewValidated: v.checkKey }),
    });
    return { review: true, count: v.ids.length };
  }
  const ids = new Set(v.ids);
  const all = envState.scenarios || [];
  const rows = all.filter((r) => ids.has(r.id));
  if (!rows.length) {
    patch({ validating: null });
    return { count: 0 };
  }
  const known = knownToolsFor(env, envState);
  const next = reproveScenarios(rows, {
    env,
    envState,
    toolsOf: (r) => requiredToolsOf(r, known),
  });
  const byId = new Map(next.map((r) => [r.id, r]));
  patch({
    validating: null,
    scenarios: all.map((s) => byId.get(s.id) || s),
  });
  return { count: rows.length };
};
