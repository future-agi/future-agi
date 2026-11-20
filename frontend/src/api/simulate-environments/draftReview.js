import { useMutation, useQueryClient } from "@tanstack/react-query";
import axios from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";
import {
  draftReviewKey,
  draftsKey,
  useDraftReview as useReviewRead,
} from "./versions";

// The draft review the server keeps: every change the open drafts make, with the decision taken on each.
const decisionsPath = (envId) =>
  apiPath("/simulate/api/harness-environments/{id}/drafts/review/decisions/", {
    id: envId,
  });

export { draftReviewKey };

export function useDraftReview(envId, { enabled = true } = {}) {
  return useReviewRead(envId, { enabled });
}

// Accept or reject changes (`{ id | kind | remaining, decision }`), at the draft revisions the review was read at.
// One at a time, so a slower answer can't overwrite a newer decision. The answer is the review again.
export function useDecideChanges(envId) {
  const queryClient = useQueryClient();
  return useMutation({
    scope: { id: `draft-review-${envId}` },
    meta: { errorHandled: true },
    mutationFn: async (decisions) => {
      const review = queryClient.getQueryData(draftReviewKey(envId));
      const expectedRevisions = Object.fromEntries(
        Object.entries(review?.drafts || {}).map(([piece, draft]) => [
          piece,
          draft.revision,
        ]),
      );
      return (
        await axios.post(decisionsPath(envId), {
          expected_revisions: expectedRevisions,
          decisions,
        })
      ).data;
    },
    onSuccess: (review) => {
      if (review) queryClient.setQueryData(draftReviewKey(envId), review);
      queryClient.invalidateQueries({ queryKey: draftsKey(envId) });
    },
    onError: () => {
      // A refusal (the drafts moved, a change another needs was rejected): read the review again.
      queryClient.invalidateQueries({ queryKey: draftReviewKey(envId) });
    },
  });
}

// (failed change id, dismissed): set a change that could not apply aside, or bring it back. Kept in this
// browser only: the server keeps no dismissal.
export function useDismissFailedChange(envId) {
  const queryClient = useQueryClient();
  return (id, dismissed = true) =>
    queryClient.setQueryData(draftReviewKey(envId), (review) =>
      review
        ? {
            ...review,
            failed: (review.failed || []).map((one) =>
              one.id === id ? { ...one, dismissed } : one,
            ),
          }
        : review,
    );
}
