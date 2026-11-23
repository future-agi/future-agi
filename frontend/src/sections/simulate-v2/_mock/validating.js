/*
  Validate in progress (Karthik's review, 23 Nov, points 8 and 12). The
  backend can say which scenarios Validate is proving and how many, not how
  many are done, so the UI says "Validating 12 scenarios" — never "N of M".
  Runs are blocked while it runs. It lives on the environment's state, so it
  survives leaving the page and coming back.

  envState.validating = { ids: [scenario ids], startedAt, from: "reprove" | "review" }
  envState.validateFailed = { at, message, ids } — the whole Validate failed
  (not one scenario's proof): the last attempt's error, until the next one.
*/

/** How long the prototype pretends a Validate takes. */
export const VALIDATE_MS = 6000;

export const validatingOf = (envState) => envState?.validating || null;

export const validatingLabel = (v) =>
  v ? `Validating ${v.ids.length} scenario${v.ids.length === 1 ? "" : "s"}` : "";

/** Why Run simulation is off while Validate runs. */
export const validatingRunBlock = (v) =>
  v ? `${validatingLabel(v)} — runs start once it finishes.` : null;

/** Start: patch is the env-state patcher (useEnvState().patch). */
export const startValidating = (patch, ids, from) =>
  patch({
    validating: { ids: [...ids], startedAt: new Date().toISOString(), from },
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
