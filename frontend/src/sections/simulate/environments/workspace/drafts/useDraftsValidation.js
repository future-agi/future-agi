import { useQueryClient } from "@tanstack/react-query";
import {
  draftReviewKey,
  refreshDrafts,
  useDraftReview,
  useDrafts,
  useSaveCandidates,
  useValidateDrafts,
} from "src/api/simulate-environments/versions";
import { DRAFTS_COPY as COPY } from "../workspace.constants";

const SAVABLE = new Set(["ready", "check_unavailable"]);
// Refusals that mean what this page holds is behind the server: the drafts and the review are read again, so the
// next press sends what is there now.
const BEHIND = new Set([
  "draft_changed",
  "review_stale",
  "review_in_progress",
  "review_undecided",
]);

// Where the validation of the unsaved changes stands, in words; null before any.
export const validationStateNote = (validation) => {
  if (validation?.state === "failed") {
    return COPY.validation.failed(validation.affectedScenarios.length);
  }
  return COPY.validation[validation?.state] ?? null;
};

// Validate the unsaved changes as candidate versions, and save those candidates once validated.
// The draft bar and a drafted scenario's Validate share it, so both validate the same thing.
export function useDraftsValidation(envId, { enabled = true } = {}) {
  const { data: drafts } = useDrafts(envId, { enabled });
  const { data: review } = useDraftReview(envId, {
    enabled: enabled && Boolean(drafts?.any),
  });
  const queryClient = useQueryClient();
  const validate = useValidateDrafts(envId);
  const saveValidated = useSaveCandidates(envId);
  const state = drafts?.validation?.state ?? null;
  return {
    drafts,
    review,
    validate,
    saveValidated,
    state,
    validating: state === "validating" || validate.isPending,
    canSaveValidated: SAVABLE.has(state) && Boolean(drafts?.candidates),
    startValidation: () =>
      validate.mutate(
        {
          expectedRevisions: Object.fromEntries(
            drafts.pieces.map((piece) => [piece, drafts[piece].revision]),
          ),
          expectedReviewDigest: review?.review_digest,
        },
        {
          onError: (error) => {
            if (!BEHIND.has(error?.error)) return;
            refreshDrafts(queryClient, envId);
            queryClient.invalidateQueries({ queryKey: draftReviewKey(envId) });
          },
        },
      ),
    saveCandidates: (name) =>
      saveValidated.mutate({
        candidates: drafts.candidates,
        expectedReviewDigest: review?.review_digest,
        name: name.trim(),
      }),
  };
}
