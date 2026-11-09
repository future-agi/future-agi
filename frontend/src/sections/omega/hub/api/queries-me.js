// Signed-in hooks (SPEC §9.3): me, likes, publishers, tokens, submissions and
// reports. Writes invalidate the reads they change.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { hubDelete, hubGet, hubPatch, hubPost, hubUpload } from "./client";
import { hubApi } from "./endpoints";
import { hubKeys, noRetryOn4xx, quiet } from "./queries-public";

// Live status: poll while the checker works, slower while a person reviews.
export const POLL_FAST_MS = 3000;
export const POLL_SLOW_MS = 15000;
export function submissionPollInterval(status) {
  if (status === "received" || status === "checking") return POLL_FAST_MS;
  if (status === "in_review") return POLL_SLOW_MS;
  return false;
}

export const meKeys = {
  likes: () => ["omega-hub", "me", "likes"],
  tokens: () => ["omega-hub", "me", "tokens"],
  submissions: (status) => ["omega-hub", "submissions", status || "all"],
  submission: (id) => ["omega-hub", "submission", id],
};

export function useHubMe() {
  return useQuery({
    queryKey: hubKeys.me(),
    queryFn: () => hubGet(hubApi.me),
    staleTime: 60 * 1000,
    retry: noRetryOn4xx,
    meta: quiet,
  });
}

export function useMyLikes() {
  return useQuery({
    queryKey: meKeys.likes(),
    queryFn: () => hubGet(hubApi.myLikes),
    retry: noRetryOn4xx,
    meta: quiet,
  });
}

export function useMyTokens() {
  return useQuery({
    queryKey: meKeys.tokens(),
    queryFn: () => hubGet(hubApi.myTokens),
    retry: noRetryOn4xx,
    meta: quiet,
  });
}

export function useMySubmissions(status) {
  return useQuery({
    queryKey: meKeys.submissions(status),
    queryFn: () => hubGet(hubApi.submissions, { mine: 1, status }),
    refetchInterval: (query) => {
      const rows = query.state.data || [];
      const live = rows.map((row) => submissionPollInterval(row.status));
      if (live.includes(POLL_FAST_MS)) return POLL_FAST_MS;
      return live.includes(POLL_SLOW_MS) ? POLL_SLOW_MS : false;
    },
    retry: noRetryOn4xx,
    meta: quiet,
  });
}

export function useSubmission(id) {
  return useQuery({
    queryKey: meKeys.submission(id),
    queryFn: () => hubGet(hubApi.submission(id)),
    enabled: Boolean(id),
    refetchInterval: (query) =>
      submissionPollInterval(query.state.data?.status),
    retry: noRetryOn4xx,
    meta: quiet,
  });
}

function useHubMutation(mutationFn, invalidate = []) {
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

export function useLike(publisher, name) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (liked) =>
      liked
        ? hubDelete(hubApi.like(publisher, name))
        : hubPost(hubApi.like(publisher, name)),
    meta: quiet,
    onSuccess: (result) => {
      client.setQueryData(hubKeys.entry(publisher, name), (old) =>
        old
          ? { ...old, likes: result.likes, likedByMe: result.likedByMe }
          : old,
      );
      client.invalidateQueries({ queryKey: meKeys.likes() });
    },
  });
}

export function useUploadSubmission() {
  return useHubMutation(
    ({ file, publisher, notes, onProgress }) => {
      const form = new FormData();
      form.append("publisher", publisher);
      form.append("channel", "web");
      if (notes) form.append("notes", notes);
      form.append("bundle", file, file.name);
      return hubUpload(hubApi.submissions, form, (event) => {
        if (onProgress && event.total) onProgress(event.loaded / event.total);
      });
    },
    [["omega-hub", "submissions"]],
  );
}

export function useWithdraw(id) {
  return useHubMutation(
    () => hubPost(hubApi.withdraw(id)),
    [meKeys.submission(id), ["omega-hub", "submissions"]],
  );
}

export function useCreatePublisher() {
  return useHubMutation(
    (data) => hubPost(hubApi.publishers, data),
    [hubKeys.me()],
  );
}

export function useUpdatePublisher(handle) {
  return useHubMutation(
    (data) => hubPatch(hubApi.publisher(handle), data),
    [hubKeys.me(), hubKeys.publisher(handle)],
  );
}

export function useUploadAvatar(handle) {
  return useHubMutation(
    (file) => {
      const form = new FormData();
      form.append("avatar", file, file.name);
      return hubUpload(hubApi.publisherAvatar(handle), form);
    },
    [hubKeys.me(), hubKeys.publisher(handle)],
  );
}

export function useRequestVerification(handle) {
  return useHubMutation(
    (evidence) => hubPost(hubApi.verificationRequest(handle), { evidence }),
    [hubKeys.me()],
  );
}

export function useCreateToken() {
  return useHubMutation(
    (data) => hubPost(hubApi.myTokens, data),
    [meKeys.tokens()],
  );
}

export function useRevokeToken() {
  return useHubMutation(
    (id) => hubDelete(hubApi.myToken(id)),
    [meKeys.tokens()],
  );
}

// Likes, reports and new publishers need a confirmed email (SPEC §6.1, decision 13).
export const EMAIL_UNVERIFIED = "OMEGA_HUB_EMAIL_UNVERIFIED";

export function useSendEmailVerification() {
  return useHubMutation(
    () => hubPost(hubApi.emailVerification),
    [hubKeys.me()],
  );
}

export function useConfirmEmail() {
  return useHubMutation(
    (token) => hubPost(hubApi.emailVerificationConfirm, { token }),
    [hubKeys.me()],
  );
}

export function useReport() {
  return useHubMutation((data) => hubPost(hubApi.reports, data));
}

export function useYankOwn(publisher, name) {
  return useHubMutation(
    ({ version, reason }) =>
      hubPost(hubApi.yank(publisher, name, version), { reason }),
    [hubKeys.entry(publisher, name), hubKeys.versions(publisher, name)],
  );
}
