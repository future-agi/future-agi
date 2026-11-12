// Outside reviewers by link (W4-SCOPE §8 C3; MOCK/CONTRACT.md "W4"
// ReviewInvite). The queue side (invites: list, send, approve, revoke) and
// the token page an outside reviewer opens without signing in.
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { annotationQueueEndpoints } from "src/api/annotation-queues/annotation-queues";

export const inviteKeys = {
  all: ["develop", "review-invites"],
  queue: (queueId) => ["develop", "review-invites", queueId],
  policy: ["develop", "review-invites", "policy"],
};
export const reviewLinkKeys = {
  all: ["review-link"],
  view: (token) => ["review-link", token],
  item: (token, itemId) => ["review-link", token, itemId],
};

const result = (res) => res?.data?.result ?? null;

/** `{invites, pending_approval, policy, sampling, can_invite, can_approve}`. */
export function useQueueInvites(queueId, options = {}) {
  const { enabled = true, ...queryOptions } = options;
  return useQuery({
    queryKey: inviteKeys.queue(queueId),
    queryFn: () => axios.get(endpoints.develop.w4.invites(queueId)),
    select: result,
    enabled: Boolean(queueId) && enabled,
    staleTime: 5_000,
    ...queryOptions,
  });
}

/**
 * The organization's review-link policy before a queue exists (Start
 * review): the queue list reports it as `review_link_policy`.
 */
export function useReviewLinkPolicy(options = {}) {
  const { enabled = true } = options;
  return useQuery({
    queryKey: inviteKeys.policy,
    queryFn: () =>
      axios.get(annotationQueueEndpoints.list, { params: { limit: 1 } }),
    select: (res) => res?.data?.review_link_policy ?? null,
    enabled,
    staleTime: 60_000,
  });
}

/** POST `{emails, sample, expires_in_days, message?}` -> `{invites, pending_approval}`. */
export async function sendInvites(queueId, body) {
  return result(await axios.post(endpoints.develop.w4.invites(queueId), body));
}

/** PATCH `{approve: true}`. */
export async function approveInvite(queueId, inviteId) {
  return result(
    await axios.patch(endpoints.develop.w4.invite(queueId, inviteId), {
      approve: true,
    }),
  );
}

/** DELETE: revoke the link. */
export async function revokeInvite(queueId, inviteId) {
  return result(
    await axios.delete(endpoints.develop.w4.invite(queueId, inviteId)),
  );
}

// ------------------------------------------------------------ token page
/** The review behind a token: header, labels, progress, next item. */
export function useReviewLink(token) {
  return useQuery({
    queryKey: reviewLinkKeys.view(token),
    queryFn: () => axios.get(endpoints.develop.w4.reviewLink(token)),
    select: result,
    enabled: Boolean(token),
    retry: false,
    meta: { errorHandled: true },
  });
}

/** One sampled item behind a token. */
export function useReviewLinkItem(token, itemId) {
  return useQuery({
    queryKey: reviewLinkKeys.item(token, itemId),
    queryFn: () =>
      axios.get(endpoints.develop.w4.reviewLinkItem(token, itemId)),
    select: result,
    enabled: Boolean(token && itemId),
    retry: false,
    meta: { errorHandled: true },
  });
}

/** POST `{values, notes?, corrected_expected?}` -> `{saved, progress, next_item_id}`. */
export async function submitReviewLinkItem(token, itemId, body) {
  return result(
    await axios.post(endpoints.develop.w4.reviewLinkItem(token, itemId), body),
  );
}

/** The status and message of an axios error (the interceptor's shape). */
export function errorOf(error) {
  const status =
    error?.statusCode ?? error?.response?.status ?? error?.status ?? null;
  const message =
    error?.response?.data?.result ??
    (typeof error?.result === "string" ? error.result : null) ??
    null;
  return { status, message };
}
