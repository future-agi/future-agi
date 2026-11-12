// Maintainer hooks (SPEC §9.4). Only pages behind `isMaintainer` use them; the
// server enforces every action regardless.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { hubDelete, hubGet, hubGetText, hubPost } from "./client";
import { hubApi } from "./endpoints";
import { hubKeys, noRetryOn4xx, quiet } from "./queries-public";

const R = hubApi.review;

export const reviewKeys = {
  queue: (params) => ["omega-hub", "review", "queue", params],
  submission: (id) => ["omega-hub", "review", "submission", id],
  diff: (id) => ["omega-hub", "review", "diff", id],
  file: (id, path) => ["omega-hub", "review", "file", id, path],
  reports: (status) => ["omega-hub", "review", "reports", status],
  picks: () => ["omega-hub", "review", "picks"],
  publishers: (params) => ["omega-hub", "review", "publishers", params],
  blocklist: () => ["omega-hub", "review", "blocklist"],
  audit: (params) => ["omega-hub", "review", "audit", params],
  likers: (p, n) => ["omega-hub", "review", "likers", p, n],
};

const useRead = (queryKey, queryFn, options = {}) =>
  useQuery({ queryKey, queryFn, retry: noRetryOn4xx, meta: quiet, ...options });

export const useReviewQueue = (params, enabled = true) =>
  useRead(reviewKeys.queue(params), () => hubGet(R.queue, params), {
    enabled,
    refetchInterval: 20000,
  });

export const useReviewSubmission = (id, enabled = true) =>
  useRead(reviewKeys.submission(id), () => hubGet(R.submission(id)), {
    enabled: Boolean(enabled && id),
  });

export const useReviewDiff = (id, enabled = true) =>
  useRead(reviewKeys.diff(id), () => hubGet(R.diff(id)), {
    enabled: Boolean(enabled && id),
  });

export const useReviewFile = (id, path) =>
  useRead(reviewKeys.file(id, path), () => hubGetText(R.file(id, path)), {
    enabled: Boolean(id && path),
    staleTime: Infinity,
  });

export const useReviewReports = (status = "open", enabled = true) =>
  useRead(reviewKeys.reports(status), () => hubGet(R.reports, { status }), { enabled });

export const useReviewPicks = (enabled = true) =>
  useRead(reviewKeys.picks(), () => hubGet(R.picks), { enabled });

export const useReviewPublishers = (params, enabled = true) =>
  useRead(reviewKeys.publishers(params), () => hubGet(R.publishers, params), { enabled });

export const useReviewBlocklist = (enabled = true) =>
  useRead(reviewKeys.blocklist(), () => hubGet(R.blocklist), { enabled });

export const useReviewAudit = (params, enabled = true) =>
  useRead(reviewKeys.audit(params), () => hubGet(R.audit, params), { enabled });

export const useLikers = (publisher, name, enabled) =>
  useRead(reviewKeys.likers(publisher, name), () => hubGet(R.likers(publisher, name)), {
    enabled: Boolean(enabled),
  });

function useReviewMutation(mutationFn, invalidate) {
  const client = useQueryClient();
  return useMutation({
    mutationFn,
    meta: quiet,
    onSuccess: () =>
      Promise.all(
        invalidate.map((queryKey) => client.invalidateQueries({ queryKey })),
      ),
  });
}

const reviewAll = ["omega-hub", "review"];
const publicAll = hubKeys.all;

export const useStartReview = (id) =>
  useReviewMutation(
    (takeOver) => hubPost(takeOver ? R.takeOver(id) : R.start(id)),
    [reviewKeys.submission(id), ["omega-hub", "review", "queue"]],
  );

export const useDecision = (id) =>
  useReviewMutation((payload) => hubPost(R.decision(id), payload), [reviewAll, publicAll]);

export const useRecheck = (id) =>
  useReviewMutation(() => hubPost(R.recheck(id)), [reviewAll]);

export const useResolveReport = () =>
  useReviewMutation(({ id, ...data }) => hubPost(R.resolveReport(id), data), [
    reviewAll,
    publicAll,
  ]);

export const useEntryAction = (publisher, name) =>
  useReviewMutation(
    ({ action, ...data }) => hubPost(R.entryAction(publisher, name, action), data),
    [publicAll],
  );

export const useMaintainerYank = (publisher, name) =>
  useReviewMutation(
    ({ version, reason }) => hubPost(R.yank(publisher, name, version), { reason }),
    [publicAll],
  );

export const useAddPick = () =>
  useReviewMutation((data) => hubPost(R.picks, data), [reviewKeys.picks(), hubKeys.picks()]);

export const useRemovePick = () =>
  useReviewMutation((id) => hubDelete(R.pick(id)), [reviewKeys.picks(), hubKeys.picks()]);

export const usePublisherAction = () =>
  useReviewMutation(
    ({ handle, action, note }) => hubPost(R.publisherAction(handle, action), { note }),
    [reviewAll, publicAll],
  );

export const useAddBlock = () =>
  useReviewMutation((data) => hubPost(R.blocklist, data), [reviewKeys.blocklist()]);

export const useLiftBlock = () =>
  useReviewMutation((id) => hubPost(R.liftBlock(id)), [reviewKeys.blocklist()]);
