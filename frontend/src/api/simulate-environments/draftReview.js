import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  sampleDraftReview,
  sampleFileEdits,
  withSampleDecisions,
  withSampleEdits,
  withSampleFailureDismissed,
} from "./_fixtures/draftReview";
import {
  DraftReviewSchema,
  ReviewDecisionSchema,
} from "./draftReview.contract";
import { environmentVersionsKey } from "./versions";

const CHECK_CONTRACT = import.meta.env.DEV || import.meta.env.MODE === "test";

// Sample data shaped like the draft review API, until the backend ships it.
// Nothing here reads or writes the real draft.
export const draftReviewKey = (envId) => [
  ...environmentVersionsKey(envId),
  "draft-review",
];

export { sampleFileEdits };

// The sample, keeping what files added: a refetch (a chat nudge, a save) would
// otherwise drop them.
export const freshSampleReview = (cached) => {
  const kept = sampleFileEdits(cached);
  return withSampleEdits(sampleDraftReview(), kept.changes, kept.failed);
};

export function useDraftReview(envId, { enabled = true } = {}) {
  const queryClient = useQueryClient();
  return useQuery({
    queryKey: draftReviewKey(envId),
    queryFn: async () => {
      const review = freshSampleReview(
        queryClient.getQueryData(draftReviewKey(envId)),
      );
      if (CHECK_CONTRACT) DraftReviewSchema.parse(review);
      return review;
    },
    enabled: Boolean(envId) && enabled,
    // A refetch would undo the sample decisions.
    staleTime: Infinity,
    meta: { errorHandled: true },
  });
}

// ({ changes, failed }): edits added from files land in the review, already
// decided, even when no review was loaded yet.
export const addSampleEdits = (
  queryClient,
  envId,
  { changes = [], failed = [] },
) =>
  queryClient.setQueryData(draftReviewKey(envId), (review) => {
    const next = withSampleEdits(review, changes, failed);
    if (CHECK_CONTRACT) DraftReviewSchema.parse(next);
    return next;
  });

// One at a time, so a slower answer can't overwrite a newer decision.
export function useDecideChanges(envId) {
  const queryClient = useQueryClient();
  return useMutation({
    scope: { id: `draft-review-${envId}` },
    meta: { errorHandled: true },
    mutationFn: async (decisions) => {
      if (CHECK_CONTRACT) decisions.forEach(ReviewDecisionSchema.parse);
      return decisions;
    },
    onSuccess: (decisions) => {
      queryClient.setQueryData(draftReviewKey(envId), (review) =>
        review ? withSampleDecisions(review, decisions) : review,
      );
    },
  });
}

// (failed change id, dismissed): set it aside or bring it back. The sample only;
// nothing to call yet.
export function useDismissFailedChange(envId) {
  const queryClient = useQueryClient();
  return (id, dismissed = true) =>
    queryClient.setQueryData(draftReviewKey(envId), (review) =>
      review ? withSampleFailureDismissed(review, id, dismissed) : review,
    );
}
