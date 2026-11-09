import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useWorkspace } from "src/contexts/WorkspaceContext";
import { OMEGA_STATE } from "src/sections/omega/shared";

import { withActorWords } from "../lib/actors";
import { useSandboxesApi } from "./SandboxesApiContext";

/**
 * React Query bindings for the Sandboxes tab. Every key starts with the
 * selected workspace, so switching workspace can never show another tenant's
 * cached rows.
 */
const LIST_POLL_MS = 15_000;
const DETAIL_POLL_MS = 10_000;

export function useScope() {
  const { currentWorkspaceId } = useWorkspace();
  return currentWorkspaceId ?? "none";
}

export const sandboxKeys = {
  all: (scope) => ["omega-sandboxes", scope],
  list: (scope) => ["omega-sandboxes", scope, "list"],
  one: (scope, id) => ["omega-sandboxes", scope, "sandbox", id],
  sub: (scope, id, part, ...rest) => [
    "omega-sandboxes",
    scope,
    "sandbox",
    id,
    part,
    ...rest,
  ],
  environments: (scope) => ["omega-sandboxes", scope, "environments"],
  settings: (scope) => ["omega-sandboxes", scope, "settings"],
};

/**
 * A one-off 401 means that one assertion was not accepted; every attempt mints
 * a fresh one, so asking once more is safe (a truly ended session fails at the
 * mint as signed-out instead). A 403, a missing sandbox or a deployment with
 * sandboxes switched off will not change by asking again.
 */
export function isTransient(error) {
  if (error?.state === OMEGA_STATE.REFUSED) return error.status === 401;
  return (
    error?.state === OMEGA_STATE.UNAVAILABLE &&
    error.code !== "sandboxes_unavailable"
  );
}

const retry = (count, error) =>
  isTransient(error) && count < (error.status === 401 ? 1 : 2);

/** Mutations that carry an Idempotency-Key may be re-sent once, with the same key. */
export const retryIdempotent = (count, error) =>
  count < 1 && isTransient(error);

/** A fresh Idempotency-Key for one user action. */
export function newIdempotencyKey() {
  const random =
    globalThis.crypto?.randomUUID?.() ??
    `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return `fa-sbx-${random}`;
}

export function useSandboxList() {
  const api = useSandboxesApi();
  const scope = useScope();
  return useQuery({
    queryKey: sandboxKeys.list(scope),
    queryFn: () => api.list(),
    refetchInterval: LIST_POLL_MS,
    retry,
  });
}

export function useSandbox(id) {
  const api = useSandboxesApi();
  const scope = useScope();
  return useQuery({
    queryKey: sandboxKeys.one(scope, id),
    queryFn: () => api.get(id),
    enabled: Boolean(id),
    refetchInterval: DETAIL_POLL_MS,
    retry,
  });
}

export function useEnvironments(enabled) {
  const api = useSandboxesApi();
  const scope = useScope();
  return useQuery({
    queryKey: sandboxKeys.environments(scope),
    queryFn: () => api.environments(),
    enabled,
    retry,
  });
}

export function useSandboxSettings() {
  const api = useSandboxesApi();
  const scope = useScope();
  return useQuery({
    queryKey: sandboxKeys.settings(scope),
    queryFn: () => api.settings(),
    retry,
  });
}

export function useSnapshots(id, enabled = true) {
  const api = useSandboxesApi();
  const scope = useScope();
  return useQuery({
    queryKey: sandboxKeys.sub(scope, id, "snapshots"),
    queryFn: () => api.snapshots(id),
    enabled: Boolean(id) && enabled,
    retry,
  });
}

export function useActivity(id) {
  const api = useSandboxesApi();
  const scope = useScope();
  return useQuery({
    queryKey: sandboxKeys.sub(scope, id, "activity"),
    queryFn: () => api.activity(id),
    // EE's account ids and system actor names stay out of the page.
    select: withActorWords,
    enabled: Boolean(id),
    refetchInterval: DETAIL_POLL_MS,
    retry,
  });
}

export function useFileList(id, path, enabled) {
  const api = useSandboxesApi();
  const scope = useScope();
  return useQuery({
    queryKey: sandboxKeys.sub(scope, id, "files", path),
    queryFn: () => api.listFiles(id, path),
    enabled: Boolean(id) && enabled,
    retry,
  });
}

export function useFileContent(id, relPath) {
  const api = useSandboxesApi();
  const scope = useScope();
  return useQuery({
    queryKey: sandboxKeys.sub(scope, id, "file", relPath),
    queryFn: () => api.readFile(id, relPath),
    enabled: Boolean(id) && Boolean(relPath),
    retry,
  });
}

/**
 * One file's bytes (for an inline image preview). Kept briefly: a preview is
 * re-read after any change in the sandbox, like the rest of its keys.
 */
export function useFileBlob(id, relPath, enabled) {
  const api = useSandboxesApi();
  const scope = useScope();
  return useQuery({
    queryKey: sandboxKeys.sub(scope, id, "blob", relPath),
    queryFn: ({ signal }) => api.downloadFile(id, relPath, { signal }),
    enabled: Boolean(id) && Boolean(relPath) && enabled,
    staleTime: 30_000,
    gcTime: 60_000,
    retry,
  });
}

/**
 * One mutation per sandbox verb. Each success refreshes everything under the
 * workspace's sandbox keys (list, detail, files, snapshots, activity).
 */
export function useSandboxMutation(run, options = {}) {
  const api = useSandboxesApi();
  const scope = useScope();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (variables) => run(api, variables),
    // A retried (idempotent) mutation is re-sent soon; the user is waiting on it.
    retryDelay: 400,
    ...options,
    onSuccess: async (data, variables, context) => {
      await queryClient.invalidateQueries({ queryKey: sandboxKeys.all(scope) });
      await options.onSuccess?.(data, variables, context);
    },
  });
}

const startsWith = (key, prefix) =>
  prefix.every((part, index) => key[index] === part);

/**
 * Delete, from the sandbox's own page. `onDeleted` runs FIRST (the page leaves
 * for the list), then every Sandboxes read is refreshed except the deleted
 * sandbox's own record and its parts, which are only marked stale. Re-reading
 * that record while its page was still mounted turned the page into a deleted
 * sandbox's page, whose redirect to Activity raced the list navigation and
 * sometimes won (DEMO-POLISH-3: 2 of 2 times at 1280 in the round-5c demo
 * check). A later visit reads the record fresh.
 */
export function useDeleteSandbox(id, { onDeleted, onError } = {}) {
  const api = useSandboxesApi();
  const scope = useScope();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.destroy(id),
    retryDelay: 400,
    onError,
    onSuccess: async (data) => {
      onDeleted?.(data);
      const own = sandboxKeys.one(scope, id);
      await queryClient.cancelQueries({ queryKey: own });
      await queryClient.invalidateQueries({
        queryKey: own,
        refetchType: "none",
      });
      await queryClient.invalidateQueries({
        queryKey: sandboxKeys.all(scope),
        predicate: (query) => !startsWith(query.queryKey, own),
      });
    },
  });
}
