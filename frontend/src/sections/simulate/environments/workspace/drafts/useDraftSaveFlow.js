import { useCallback, useEffect, useRef } from "react";

import {
  useDiscardDraft,
  useDraftReview,
  useDrafts,
  useSaveCandidates,
  useSaveDrafts,
  useValidateDrafts,
} from "src/api/simulate-environments/versions";
import { useRunningScenarioValidation } from "src/api/simulate-environments/scenariosHooks";
import { refusalText } from "../evals/refusalText";
import { DRAFTS_COPY as COPY } from "../workspace.constants";

// The piece a stale refusal names, else the first stale one.
export const staleText = (drafts, piece) => {
  const stalePiece =
    (piece && drafts[piece] && piece) ||
    drafts.pieces.find((one) => drafts[one].stale) ||
    drafts.pieces[0];
  const draft = drafts[stalePiece];
  return COPY.stale(
    COPY.pieces[stalePiece],
    draft.parentLabel,
    draft.defaultLabel,
  );
};

const errorText = (error, drafts, fallback) => {
  if (error?.error === "draft_stale") return staleText(drafts, error.piece);
  return COPY.errors[error?.error] || refusalText(error, fallback);
};

// Validate states that let Save go ahead: ready, or a check that can't run on the platform's side.
const SAVEABLE = ["ready", "check_unavailable"];

/**
 * One environment's unsaved changes and what can be done with them: a plain Save when nothing was reviewed, else
 * Validate the reviewed set, then Save the versions it built; or Discard. The bar and the review drawer both read
 * it, so they agree on what Save does and why it waits.
 */
export default function useDraftSaveFlow(env, { reviewEnabled = true } = {}) {
  const enabled = Boolean(env?.versioning?.enabled);
  const { data: drafts } = useDrafts(env?.id, { enabled });
  const review = useDraftReview(env?.id, {
    enabled: enabled && reviewEnabled && Boolean(drafts?.any),
  });
  const save = useSaveDrafts(env?.id);
  const discard = useDiscardDraft(env?.id);
  const validate = useValidateDrafts(env?.id);
  const saveValidated = useSaveCandidates(env?.id);
  // The server refuses a Save, never a run, while scenarios are validated on the saved versions.
  const { data: running } = useRunningScenarioValidation(
    enabled && reviewEnabled && drafts?.any ? env?.id : null,
  );

  // A refusal is about the drafts it was sent for, so it goes when they change. draft_changed stays: it asks to
  // review the drafts it refetched.
  const signature = drafts?.any
    ? drafts.pieces.map((p) => `${drafts[p].id}:${drafts[p].revision}`).join()
    : "";
  const failed = useRef({});
  failed.current = {
    save: save.isError && save.error?.error !== "draft_changed",
    // needsValidate reads validation_required off the save, so a decision keeps that one.
    saveRefused: save.isError && save.error?.error !== "validation_required",
    discard: discard.isError,
    validate: validate.isError,
    saveValidated: saveValidated.isError,
  };
  const { reset: resetSave } = save;
  const { reset: resetDiscard } = discard;
  useEffect(() => {
    if (failed.current.save) resetSave();
    if (failed.current.discard) resetDiscard();
  }, [signature, resetSave, resetDiscard]);
  // Validate and the validated Save refetch the drafts when refused, so those refusals stay over the refetch: they go
  // with the person's next decision, or with the drafts. One still being sent is left alone.
  const { reset: resetValidate } = validate;
  const { reset: resetSaveValidated } = saveValidated;
  const clearRefusals = useCallback(() => {
    if (failed.current.saveRefused) resetSave();
    if (failed.current.discard) resetDiscard();
    if (failed.current.validate) resetValidate();
    if (failed.current.saveValidated) resetSaveValidated();
  }, [resetSave, resetDiscard, resetValidate, resetSaveValidated]);
  const gone = drafts ? !drafts.any : false;
  useEffect(() => {
    if (gone) clearRefusals();
  }, [gone, clearRefusals]);

  if (!enabled || !drafts?.any)
    return { open: false, drafts, review: review.data, clearRefusals };

  // The server says when a Save must go through Validate (a reviewed change, where checks can run). Where they
  // can't, reviewed changes save as they are.
  const validation = drafts.validation;
  const needsValidate =
    drafts.validationRequired ||
    save.error?.error === "validation_required" ||
    Boolean(validation);
  const validationState = validation?.state ?? null;
  const validating = validationState === "validating" || validate.isPending;
  const canSaveValidated =
    SAVEABLE.includes(validationState) && Boolean(drafts.candidates);
  const pending =
    save.isPending ||
    discard.isPending ||
    saveValidated.isPending ||
    validating;
  const digest = review.data?.review_digest;

  let error = null;
  if (saveValidated.error)
    error = errorText(saveValidated.error, drafts, COPY.saveError);
  else if (validate.error)
    error = errorText(validate.error, drafts, COPY.validateError);
  else if (save.error && !needsValidate)
    error = errorText(save.error, drafts, COPY.saveError);
  else if (discard.error)
    error = errorText(discard.error, drafts, COPY.discardError);

  const failedScenarios =
    validationState === "failed" ? validation.affectedScenarios : [];
  let validationNote = null;
  if (needsValidate && !validationState && !error)
    validationNote = COPY.errors.validation_required;
  else if (validationState === "failed")
    validationNote = COPY.validation.failed(
      failedScenarios.length,
      validation.reason,
    );
  else if (COPY.validation[validationState])
    validationNote = COPY.validation[validationState];

  return {
    open: true,
    drafts,
    review: review.data,
    reviewQuery: review,
    needsValidate,
    validationState,
    validating,
    canSaveValidated,
    scenariosValidating: Boolean(running?.job_id),
    pending,
    savePending: save.isPending || saveValidated.isPending,
    discardPending: discard.isPending,
    stale: drafts.stale ? staleText(drafts) : null,
    error,
    validationNote,
    failedScenarios,
    onValidate: () => {
      discard.reset();
      save.reset();
      saveValidated.reset();
      validate.mutate({
        expectedRevisions: Object.fromEntries(
          drafts.pieces.map((piece) => [piece, drafts[piece].revision]),
        ),
        expectedReviewDigest: digest,
      });
    },
    // Resolves with the save's answer, so the caller can say what won't run yet.
    onSaveValidated: (name, options) => {
      discard.reset();
      const vars = {
        candidates: drafts.candidates,
        expectedReviewDigest: digest,
        name: name.trim(),
      };
      if (options) saveValidated.mutate(vars, options);
      else saveValidated.mutate(vars);
    },
    // A plain Save; from the review it carries the digest the person saw and the version's name.
    onSave: (name, options) => {
      discard.reset();
      const vars = {
        drafts: drafts.pieces.map((piece) => ({
          piece,
          parentId: drafts[piece].parentId,
          revision: drafts[piece].revision,
        })),
        reviewDigest: digest,
        name: name?.trim() || undefined,
      };
      if (options) save.mutate(vars, options);
      else save.mutate(vars);
    },
    clearRefusals,
    onDiscard: (options) => {
      clearRefusals();
      if (options) discard.mutate(drafts.pieces, options);
      else discard.mutate(drafts.pieces);
    },
  };
}
