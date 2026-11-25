import { useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";
import { harnessIdempotencyKey } from "src/api/harness/harness";
import { refreshReadiness } from "./readiness";
import {
  draftsKey,
  readinessFixKey,
  refreshDrafts,
  refreshFollowers,
  retryServerFailures,
  versionPickFields,
} from "./versions";

/**
 * The builder chat's fix run for an environment: the run plan starts it with
 * the ticked lines, the server saves and re-checks, and the page follows its
 * state.
 */

const POLL_MS = 5000;

const fixPath = (envId) =>
  apiPath("/simulate/api/harness-environments/{id}/capabilities/fix/", {
    id: envId,
  });

// The server says whether a run is still working.
export const fixIsActive = (run) => Boolean(run?.active);

export const getFixRun = async (envId) => {
  return (await axios.get(fixPath(envId))).data?.fix ?? null;
};

export const startGroupFix = async (envId, vars) => {
  const { group, rows, picks, idempotencyKey } = vars;
  return (
    await axios.post(fixPath(envId), {
      group,
      rows,
      idempotency_key: idempotencyKey,
      ...versionPickFields(picks),
    })
  ).data;
};

// The environment's latest fix run. Polls every 5 s while it is active.
export function useFixRun(envId, { enabled = true } = {}) {
  return useQuery({
    queryKey: readinessFixKey(envId),
    queryFn: () => getFixRun(envId),
    enabled: Boolean(envId) && enabled,
    retry: retryServerFailures,
    refetchInterval: (query) =>
      fixIsActive(query.state.data) ? POLL_MS : false,
    meta: { errorHandled: true },
  });
}

// Call as mutate({ group: "builder", rows, picks }). The server posts the group's
// message to the chat itself, so the chat's job is read again.
export function useStartGroupFix(envId) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (vars) =>
      startGroupFix(envId, {
        ...vars,
        idempotencyKey: harnessIdempotencyKey(),
      }),
    onSuccess: (data) => {
      queryClient.setQueryData(readinessFixKey(envId), data.fix);
      queryClient.invalidateQueries({ queryKey: ["harness-job", envId] });
      refreshReadiness(queryClient, envId);
    },
    onError: (error) => {
      const code = error?.error;
      if (code === "fix_running") {
        queryClient.invalidateQueries({ queryKey: readinessFixKey(envId) });
      } else if (code === "unsaved_changes") {
        queryClient.invalidateQueries({ queryKey: draftsKey(envId) });
      } else if (code === "nothing_to_fix") {
        refreshReadiness(queryClient, envId);
      }
    },
    meta: { errorHandled: true },
  });
}

const savedIds = (run) =>
  `${run?.saved?.environment?.id || ""}|${run?.saved?.suite?.id || ""}`;

// Follows a run's changes (edges only, so the first run a fresh tab sees
// triggers nothing). Call once per page.
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
      refreshReadiness(queryClient, envId);
      // The server posts the fix's turn after the POST returns; an idle
      // chat isn't polling, so read it again as the run moves.
      queryClient.invalidateQueries({ queryKey: ["harness-job", envId] });
    }
    if (saved !== was.saved && saved !== "|") {
      refreshFollowers(queryClient, envId);
      refreshDrafts(queryClient, envId);
    }
    if (draft && !was.draft) refreshDrafts(queryClient, envId);
  }, [state, updatedAt, saved, draft, envId, queryClient]);
}
