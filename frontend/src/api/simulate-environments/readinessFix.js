import { useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";
import { harnessIdempotencyKey } from "src/api/harness/harness";
import {
  draftsKey,
  refreshDrafts,
  refreshFollowers,
  versionPickFields,
} from "./versions";
import * as fixDummy from "./readinessFix.dummy"; // dummy:

/**
 * The builder chat's fix run for an environment: Fix all starts it, the
 * server saves and re-checks, and the popup follows its state.
 */

const POLL_MS = 5000;

// shortcut: the fix route isn't in the API contract yet; switch to apiPath once the backend ships it and the contract is regenerated.
const fixPath = (envId) =>
  `${apiPath("/simulate/api/harness-environments/{id}/capabilities/", { id: envId })}fix/`;

export const FIX_ACTIVE_STATES = new Set([
  "queued",
  "fixing",
  "waiting_on_you",
  "saving",
  "checking",
]);

export const fixRunKey = (envId) => ["harness-readiness-fix", envId];

const errorStatus = (error) => error?.statusCode ?? error?.response?.status;
const retryServerFailures = (failureCount, error) => {
  const status = errorStatus(error);
  return (status === undefined || status >= 500) && failureCount < 2;
};

export const getFixRun = async (envId) => {
  if (fixDummy.dummyOn()) return fixDummy.dummyFixRun(envId); // dummy:
  return (await axios.get(fixPath(envId))).data?.fix ?? null;
};

export const startGroupFix = async (envId, vars) => {
  if (fixDummy.dummyOn()) return fixDummy.dummyStartFix(envId, vars); // dummy:
  const { group, rows, picks, idempotencyKey } = vars;
  return (
    await axios.post(fixPath(envId), {
      group,
      // Leaving rows out fixes the whole group.
      ...(rows?.length ? { rows } : {}),
      idempotency_key: idempotencyKey,
      ...versionPickFields(picks),
    })
  ).data;
};

// The environment's latest fix run. Polls every 5 s while it is active.
export function useFixRun(envId, { enabled = true } = {}) {
  return useQuery({
    queryKey: fixRunKey(envId),
    queryFn: () => getFixRun(envId),
    enabled: Boolean(envId) && enabled,
    retry: retryServerFailures,
    refetchInterval: (query) =>
      FIX_ACTIVE_STATES.has(query.state.data?.state) ? POLL_MS : false,
    meta: { errorHandled: true },
  });
}

// Call as mutate({ group: "builder", picks }). The server posts the group's
// message to the chat itself, so the chat's job is read again.
export function useStartGroupFix(envId) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (vars) =>
      startGroupFix(envId, {
        ...vars,
        idempotencyKey: harnessIdempotencyKey(),
        ...(fixDummy.dummyOn() && fixDummy.dummyContext(queryClient, envId)), // dummy:
      }),
    onSuccess: (data) => {
      queryClient.setQueryData(fixRunKey(envId), data.fix);
      queryClient.invalidateQueries({ queryKey: ["harness-job", envId] });
      queryClient.invalidateQueries({ queryKey: ["harness-readiness", envId] });
    },
    onError: (error) => {
      const code = error?.error;
      if (code === "fix_running") {
        queryClient.invalidateQueries({ queryKey: fixRunKey(envId) });
      } else if (code === "unsaved_changes") {
        queryClient.invalidateQueries({ queryKey: draftsKey(envId) });
      } else if (code === "nothing_to_fix") {
        queryClient.invalidateQueries({
          queryKey: ["harness-readiness", envId],
        });
      }
    },
    meta: { errorHandled: true },
  });
}

const savedIds = (run) =>
  `${run?.saved?.environment?.id || ""}|${run?.saved?.suite?.id || ""}`;

// Follows a run's changes (edges only, so the first run a fresh tab sees
// triggers nothing). Call once per popup.
export function useFollowFixRun(envId, fixRun) {
  const queryClient = useQueryClient();
  const prev = useRef(null);
  const state = fixRun?.state;
  const updatedAt = fixRun?.updated_at;
  const saved = fixRun ? savedIds(fixRun) : null;
  const draft = Boolean(fixRun?.draft);
  useEffect(() => {
    if (saved === null) return;
    const was = prev.current;
    prev.current = { state, updatedAt, saved, draft };
    if (!was) return;
    if (was.state !== state || was.updatedAt !== updatedAt) {
      queryClient.invalidateQueries({ queryKey: ["harness-readiness", envId] });
    }
    if (saved !== was.saved && saved !== "|") {
      refreshFollowers(queryClient, envId);
      refreshDrafts(queryClient, envId);
    }
    if (draft && !was.draft) refreshDrafts(queryClient, envId);
  }, [state, updatedAt, saved, draft, envId, queryClient]);
}
