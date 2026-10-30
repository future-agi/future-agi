import { useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { camelCase } from "lodash";
import axios from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";
import { harnessIdempotencyKey } from "src/api/harness/harness";

/**
 * The version history of one environment: its agent, environment and scenario
 * suite versions, each version's diff, and a suite version's scenarios.
 *
 * The endpoints answer 404 while versioning is switched off, so every read is
 * gated on `enabled` — the workspace passes `env.versioning.enabled`. The writes
 * move a default to another version, and set aside, re-check or re-credential an
 * agent version. Nudges propose rebuilding the environment (or its scenarios)
 * after its agent (or environment) changed; they can be listed and skipped.
 * Edits made while versioning is on wait as unsaved changes (drafts) until
 * they are saved together or discarded.
 */

export const VERSION_PIECES = ["agent", "environment", "suite"];
export const MOVABLE_PIECES = ["agent", "environment", "suite"];

const versionsPath = (envId) =>
  apiPath("/simulate/api/harness-environments/{id}/versions/", { id: envId });
const versionDiffPath = (envId, piece, versionId) =>
  apiPath(
    "/simulate/api/harness-environments/{id}/versions/{piece}/{version_id}/diff/",
    { id: envId, piece, version_id: versionId },
  );
const suiteMembersPath = (envId, versionId) =>
  apiPath(
    "/simulate/api/harness-environments/{id}/versions/suite/{version_id}/members/",
    { id: envId, version_id: versionId },
  );

const moveDefaultPath = (envId, piece, versionId) =>
  apiPath(
    "/simulate/api/harness-environments/{id}/versions/{piece}/{version_id}/default/",
    { id: envId, piece, version_id: versionId },
  );

const agentVersionsPath = (envId) =>
  apiPath("/simulate/api/harness-environments/{id}/versions/agent/", {
    id: envId,
  });
const discardAgentPath = (envId, versionId) =>
  apiPath(
    "/simulate/api/harness-environments/{id}/versions/agent/{version_id}/discard/",
    { id: envId, version_id: versionId },
  );
const retryAgentPath = (envId, versionId) =>
  apiPath(
    "/simulate/api/harness-environments/{id}/versions/agent/{version_id}/retry/",
    { id: envId, version_id: versionId },
  );
const secretValuesPath = () =>
  apiPath("/simulate/api/harness-jobs/secret-values/");

const nudgesPath = (envId) =>
  apiPath("/simulate/api/harness-environments/{id}/nudges/", { id: envId });
const answerNudgePath = (envId, nudgeId) =>
  apiPath("/simulate/api/harness-environments/{id}/nudges/{nudge_id}/answer/", {
    id: envId,
    nudge_id: nudgeId,
  });
const pendingCommitPath = (envId) =>
  apiPath("/simulate/api/harness-environments/{id}/pending-commit/", {
    id: envId,
  });
const stopNudgePath = (envId, nudgeId) =>
  apiPath("/simulate/api/harness-environments/{id}/nudges/{nudge_id}/stop/", {
    id: envId,
    nudge_id: nudgeId,
  });

const draftsPath = (envId) =>
  apiPath("/simulate/api/harness-environments/{id}/drafts/", { id: envId });
const saveDraftsPath = (envId) =>
  apiPath("/simulate/api/harness-environments/{id}/drafts/save/", {
    id: envId,
  });
const discardDraftPath = (envId, piece) =>
  apiPath("/simulate/api/harness-environments/{id}/drafts/{piece}/", {
    id: envId,
    piece,
  });

const runPlanPath = (envId) =>
  apiPath("/simulate/api/harness-environments/{id}/run-plan/", { id: envId });

// A run's version picks ({agent, environment, suite} → version id) as request
// fields; a piece left unpicked is left out, so the server uses its default.
export const versionPickFields = (picks = {}) =>
  Object.fromEntries(
    VERSION_PIECES.filter((piece) => picks?.[piece]).map((piece) => [
      `${piece}_version_id`,
      picks[piece],
    ]),
  );

export const getEnvironmentVersions = async (envId) =>
  (await axios.get(versionsPath(envId))).data;

export const getVersionDiff = async (envId, piece, versionId, against) =>
  (
    await axios.get(versionDiffPath(envId, piece, versionId), {
      params: against ? { against } : undefined,
    })
  ).data;

export const getSuiteMembers = async (envId, versionId) =>
  (await axios.get(suiteMembersPath(envId, versionId))).data;

// The default the user saw rides along; a newer one refuses the move (409).
export const moveDefault = async (envId, piece, versionId, expectedDefaultId) =>
  (
    await axios.post(moveDefaultPath(envId, piece, versionId), {
      expected_default_id: expectedDefaultId ?? null,
    })
  ).data;

export const discardAgentVersion = async (envId, versionId) =>
  (await axios.post(discardAgentPath(envId, versionId), {})).data;

export const retryAgentVersion = async (envId, versionId) =>
  (await axios.post(retryAgentPath(envId, versionId), {})).data;

// New credential values (and staged credential files) for the environment's
// agent: the answer is the new agent version, checked next.
export const changeCredentials = async (envId, environmentValues, files) =>
  (
    await axios.post(secretValuesPath(), {
      environment_id: envId,
      environment_values: environmentValues,
      ...(files?.length ? { files } : {}),
    })
  ).data;

// A new upload, prompt or settings for the environment's agent: the answer is
// the new agent version, checked next.
export const createAgentVersion = async (envId, body) =>
  (await axios.post(agentVersionsPath(envId), body)).data;

// What a run of these picks would use and what to warn about.
export const getRunPlan = async (envId, picks) =>
  (await axios.get(runPlanPath(envId), { params: versionPickFields(picks) }))
    .data;

export const getNudges = async (envId) =>
  (await axios.get(nudgesPath(envId))).data;

export const answerNudge = async (envId, nudgeId, answer) =>
  (await axios.post(answerNudgePath(envId, nudgeId), { answer })).data;

export const stopNudge = async (envId, nudgeId) =>
  (await axios.post(stopNudgePath(envId, nudgeId), {})).data;

export const answerPendingCommit = async (envId, answer) =>
  (await axios.post(pendingCommitPath(envId), { answer })).data;

export const getDrafts = async (envId) =>
  (await axios.get(draftsPath(envId))).data;

// Each draft carries the default it started from and the revision the user
// saw; a newer edit or default refuses the save (409).
export const saveDrafts = async (envId, { drafts, idempotencyKey }) =>
  (
    await axios.post(saveDraftsPath(envId), {
      drafts,
      idempotency_key: idempotencyKey,
    })
  ).data;

export const discardDraft = async (envId, piece) =>
  (await axios.delete(discardDraftPath(envId, piece))).data;

export const environmentVersionsKey = (envId) => [
  "harness-env-versions",
  envId,
];
export const versionDiffKey = (envId, piece, versionId, against) => [
  ...environmentVersionsKey(envId),
  "diff",
  piece,
  versionId,
  against || null,
];
export const suiteMembersKey = (envId, versionId) => [
  ...environmentVersionsKey(envId),
  "members",
  versionId,
];

export const nudgesKey = (envId) => [
  ...environmentVersionsKey(envId),
  "nudges",
];

export const draftsKey = (envId) => [
  ...environmentVersionsKey(envId),
  "drafts",
];

export const runPlanKey = (envId, picks = {}) => [
  ...environmentVersionsKey(envId),
  "run-plan",
  ...VERSION_PIECES.map((piece) => picks?.[piece] || null),
];

const errorStatus = (error) => error?.statusCode ?? error?.response?.status;

// The server switched versioning off after this page loaded: treat it as off,
// not as a failure worth retrying.
export const isVersioningDisabled = (error) =>
  errorStatus(error) === 404 && error?.error === "versioning_disabled";
// A 404 means versioning is off or the environment is gone; asking again won't help.
const retryUnlessNotFound = (failureCount, error) =>
  errorStatus(error) !== 404 && failureCount < 2;
// A refused plan answers the same way every time; only a server or network
// failure is worth asking again.
const retryServerFailures = (failureCount, error) => {
  const status = errorStatus(error);
  return (status === undefined || status >= 500) && failureCount < 2;
};

export const versionFromApi = (item = {}) => ({
  id: item.id,
  label: item.label || null,
  status: item.status || "active",
  isDefault: Boolean(item.is_default),
  createdAt: item.created_at || null,
  createdBy: item.created_by?.name || null,
  origin: item.origin || null,
  summary: item.summary || "",
  hash: item.hash || null,
  parentId: item.parent_id || null,
  derivedFromAgentVersionId: item.derived_from_agent_version_id || null,
  scenarioCount: item.scenario_count ?? null,
  source: item.source || null,
  prompt: item.prompt ?? null,
  preflightStatus: item.preflight_status || null,
  preflightReport: item.preflight_report || null,
  preflightedAt: item.preflighted_at || null,
  checkStartedAt: item.check_started_at || null,
  credentialNames: Array.isArray(item.credential_names)
    ? item.credential_names
    : null,
  requiredVariableNames: Array.isArray(item.required_variable_names)
    ? item.required_variable_names
    : null,
  activationOutcome: item.activation_outcome || null,
  requestedHere: Boolean(item.requested_here),
  pickable: Boolean(item.pickable),
  discardable: Boolean(item.discardable),
});

// A check still pending after this long is stuck: offered again, not polled.
export const STUCK_AFTER_MS = 15 * 60 * 1000;
// The check's latest start: the version's creation, its last check, or the
// latest time its check was asked for again, as the server recorded it.
export const lastCheckStart = (version) =>
  Math.max(
    0,
    ...[version.createdAt, version.preflightedAt, version.checkStartedAt]
      .filter(Boolean)
      .map((at) => new Date(at).getTime()),
  );
export const isStuck = (version) => {
  if (version.preflightStatus !== "pending") return false;
  const started = lastCheckStart(version);
  return started > 0 && Date.now() - started > STUCK_AFTER_MS;
};

// A check this environment asked for that is still running and not stuck.
export const checkRunning = (version) =>
  version.preflightStatus === "pending" &&
  version.requestedHere &&
  !isStuck(version);
// A check this environment waits on: one still running, or one that passed
// and whose move of the default is not recorded yet.
const awaitedCheck = (version) =>
  checkRunning(version) ||
  (version.preflightStatus === "passed" &&
    version.requestedHere &&
    !version.isDefault &&
    !version.activationOutcome &&
    Boolean(version.preflightedAt) &&
    Date.now() - new Date(version.preflightedAt).getTime() < STUCK_AFTER_MS);

// The list is asked again until every awaited check lands.
const PREFLIGHT_POLL_MS = 5000;
const awaitsCheck = (data) =>
  Array.isArray(data?.agent) &&
  data.agent.some((item) => awaitedCheck(versionFromApi(item)));

// Only an active version that is not already the default can become it; an
// agent version only when the server says it can be picked here.
export const canMakeDefault = (piece, version) =>
  MOVABLE_PIECES.includes(piece) &&
  version?.status === "active" &&
  (piece !== "agent" || version.pickable) &&
  !version.isDefault;

// The three lists plus each piece's default, so readers never search for it.
export const toVersions = (data = {}) => {
  const lists = Object.fromEntries(
    VERSION_PIECES.map((piece) => [
      piece,
      (Array.isArray(data?.[piece]) ? data[piece] : []).map(versionFromApi),
    ]),
  );
  const defaults = Object.fromEntries(
    VERSION_PIECES.map((piece) => [
      piece,
      lists[piece].find((version) => version.isDefault) || null,
    ]),
  );
  const empty = VERSION_PIECES.every((piece) => lists[piece].length === 0);
  return { ...lists, defaults, empty };
};

export function useEnvironmentVersions(envId, { enabled = true } = {}) {
  return useQuery({
    queryKey: environmentVersionsKey(envId),
    queryFn: () => getEnvironmentVersions(envId),
    select: toVersions,
    enabled: Boolean(envId) && enabled,
    retry: retryUnlessNotFound,
    refetchInterval: (query) =>
      awaitsCheck(query.state.data) ? PREFLIGHT_POLL_MS : false,
    meta: { errorHandled: true },
  });
}

export function useVersionDiff(
  envId,
  piece,
  versionId,
  against,
  { enabled = true } = {},
) {
  return useQuery({
    queryKey: versionDiffKey(envId, piece, versionId, against),
    queryFn: () => getVersionDiff(envId, piece, versionId, against),
    enabled: Boolean(envId && piece && versionId) && enabled,
    retry: retryUnlessNotFound,
    meta: { errorHandled: true },
  });
}

export function useSuiteMembers(envId, versionId, { enabled = true } = {}) {
  return useQuery({
    queryKey: suiteMembersKey(envId, versionId),
    queryFn: () => getSuiteMembers(envId, versionId),
    select: (data) => (Array.isArray(data) ? data : []),
    enabled: Boolean(envId && versionId) && enabled,
    retry: retryUnlessNotFound,
    meta: { errorHandled: true },
  });
}

export function useRunPlan(envId, picks, { enabled = true } = {}) {
  return useQuery({
    queryKey: runPlanKey(envId, picks),
    queryFn: () => getRunPlan(envId, picks),
    enabled: Boolean(envId) && enabled,
    retry: retryServerFailures,
    meta: { errorHandled: true },
  });
}

// The versions (with the drafts) and what follows the defaults: the
// environment detail, the job and the scenarios. Keys are environment.js's and
// scenariosHooks.js's literals, which import this module.
export const refreshFollowers = (queryClient, envId) => {
  queryClient.invalidateQueries({ queryKey: environmentVersionsKey(envId) });
  queryClient.invalidateQueries({ queryKey: ["harness-environment", envId] });
  queryClient.invalidateQueries({ queryKey: ["harness-job", envId] });
  queryClient.invalidateQueries({ queryKey: ["harness-scenarios", envId] });
};

// A draft keeps its id as it is edited, so its diff and members are asked
// again with the drafts.
export const refreshDrafts = (queryClient, envId) => {
  queryClient.invalidateQueries({ queryKey: draftsKey(envId) });
  queryClient.invalidateQueries({
    queryKey: [...environmentVersionsKey(envId), "diff"],
  });
  queryClient.invalidateQueries({
    queryKey: [...environmentVersionsKey(envId), "members"],
  });
};

// Sync the new commit offered to the environment as its next agent version, or
// skip it. A sync's version is checked next, then raises the rebuild card.
export function useAnswerPendingCommit(envId) {
  const queryClient = useQueryClient();
  return useMutation({
    meta: { errorHandled: true },
    mutationFn: (answer) => answerPendingCommit(envId, answer),
    onSettled: () => {
      refreshFollowers(queryClient, envId);
      queryClient.invalidateQueries({ queryKey: nudgesKey(envId) });
    },
  });
}

// Make a version the default. The answer is every version, so it seeds the list;
// what follows the default is asked again too. A conflict means the default
// moved elsewhere: refresh so the user picks again from what is current.
export function useMoveDefault(envId) {
  const queryClient = useQueryClient();
  const refresh = () => refreshFollowers(queryClient, envId);
  return useMutation({
    meta: { errorHandled: true },
    mutationFn: ({ piece, versionId, expectedDefaultId }) =>
      moveDefault(envId, piece, versionId, expectedDefaultId),
    onSuccess: (data) => {
      if (data) queryClient.setQueryData(environmentVersionsKey(envId), data);
      refresh();
    },
    onError: (error) => {
      if (errorStatus(error) === 409 && error?.error === "version_conflict") {
        refresh();
      } else if (
        errorStatus(error) === 422 &&
        error?.error === "version_not_pickable"
      ) {
        // Discarded meanwhile: refresh the list so the action goes away.
        queryClient.invalidateQueries({
          queryKey: environmentVersionsKey(envId),
        });
      }
    },
  });
}

// The agent version actions answer with every version: seed the list with it,
// then ask again for the queries nested under it.
const useAgentVersionAction = (envId, action) => {
  const queryClient = useQueryClient();
  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: environmentVersionsKey(envId) });
  return useMutation({
    meta: { errorHandled: true },
    mutationFn: ({ versionId }) => action(envId, versionId),
    onSuccess: (data) => {
      if (data) queryClient.setQueryData(environmentVersionsKey(envId), data);
      refresh();
    },
    onError: (error) => {
      // The version changed meanwhile (discarded, made default, re-checked).
      if (errorStatus(error) === 422) refresh();
    },
  });
};

export const useDiscardAgentVersion = (envId) =>
  useAgentVersionAction(envId, discardAgentVersion);

export const useRetryAgentVersion = (envId) =>
  useAgentVersionAction(envId, retryAgentVersion);

const invalidateAgentChange = (queryClient, envId) => {
  queryClient.invalidateQueries({
    queryKey: environmentVersionsKey(envId),
  });
  queryClient.invalidateQueries({
    queryKey: ["harness-environment", envId],
  });
  queryClient.invalidateQueries({ queryKey: ["harness-job", envId] });
};

// New credential values make the agent's next version, checked next; the
// versions list then polls until the check lands, and the environment detail
// and job are asked again for what follows it. Values only ride the request:
// the mutation is dropped from the cache once it settles.
export function useChangeCredentials(envId) {
  const queryClient = useQueryClient();
  return useMutation({
    meta: { errorHandled: true },
    gcTime: 0,
    mutationFn: ({ values, files }) => changeCredentials(envId, values, files),
    onSuccess: () => invalidateAgentChange(queryClient, envId),
  });
}

// A new upload, prompt or settings make the agent's next version, checked
// next; as with credentials, the list then polls until the check lands.
export function useCreateAgentVersion(envId) {
  const queryClient = useQueryClient();
  return useMutation({
    meta: { errorHandled: true },
    mutationFn: (body) => createAgentVersion(envId, body),
    onSuccess: () => invalidateAgentChange(queryClient, envId),
  });
}

// A check that lands may have made its version the default: once no awaited
// check is pending any more, ask again for what follows the default.
export function useRefreshWhenChecked(envId, agentVersions) {
  const queryClient = useQueryClient();
  const pending = (agentVersions || []).some(awaitedCheck);
  const wasPending = useRef(pending);
  useEffect(() => {
    if (wasPending.current && !pending) {
      queryClient.invalidateQueries({
        queryKey: ["harness-environment", envId],
      });
      queryClient.invalidateQueries({ queryKey: ["harness-job", envId] });
      queryClient.invalidateQueries({ queryKey: nudgesKey(envId) });
    }
    wasPending.current = pending;
  }, [pending, envId, queryClient]);
}

// The diff and summary of a suite nudge are keyed by contract paths, tool
// names and sub-goal keys, so their keys stay as the server wrote them.
// A landed rebuild's diffs are version diffs, read as VersionDiff reads them.
const VERBATIM_FACTS = new Set([
  "environment_diff",
  "environment_summary",
  "update",
  "draft_edits",
]);

const camelDeep = (value) => {
  if (Array.isArray(value)) return value.map(camelDeep);
  if (!value || typeof value !== "object") return value;
  return Object.fromEntries(
    Object.entries(value).map(([key, inner]) => [
      camelCase(key),
      VERBATIM_FACTS.has(key) ? inner : camelDeep(inner),
    ]),
  );
};

// A nudge in camelCase. Its facts carry names, paths, statuses and counts
// only; the server never sends a value or a patch.
export const nudgeFromApi = (item = {}) => {
  const nudge = camelDeep(item);
  return { ...nudge, facts: nudge.facts || {} };
};

export const NUDGE_ACTIVE_STATES = new Set(["queued", "running"]);
const NUDGE_ACTIVE_POLL_MS = 5000;
const NUDGE_POLL_MS = 30000;

export const toNudges = (data) => {
  const list = (Array.isArray(data) ? data : []).map(nudgeFromApi);
  const pending = (target) =>
    list.find(
      (nudge) => nudge.target === target && nudge.state === "pending",
    ) || null;
  return {
    list,
    byId: Object.fromEntries(list.map((nudge) => [nudge.id, nudge])),
    pendingEnvironment: pending("environment"),
    pendingSuite: pending("suite"),
    // The queued or running rebuild; it holds the environment's drafts.
    active: list.find((nudge) => NUDGE_ACTIVE_STATES.has(nudge.state)) || null,
  };
};

export function useNudges(envId, { enabled = true } = {}) {
  return useQuery({
    queryKey: nudgesKey(envId),
    queryFn: () => getNudges(envId),
    select: toNudges,
    enabled: Boolean(envId) && enabled,
    retry: retryUnlessNotFound,
    refetchInterval: (query) => {
      if (errorStatus(query.state.error) === 404) return false;
      return Array.isArray(query.state.data) &&
        query.state.data.some((item) => NUDGE_ACTIVE_STATES.has(item?.state))
        ? NUDGE_ACTIVE_POLL_MS
        : NUDGE_POLL_MS;
    },
    meta: { errorHandled: true },
  });
}

// Answer or stop a nudge. The nudge it answers with replaces its cached row;
// the run plan's warnings and the job's conversation follow it, so both are
// asked again. A nudge closed meanwhile refreshes the list so the card shows
// its state.
const useNudgeMutation = (envId, mutationFn) => {
  const queryClient = useQueryClient();
  const refreshNudges = () =>
    queryClient.invalidateQueries({ queryKey: nudgesKey(envId) });
  return useMutation({
    meta: { errorHandled: true },
    mutationFn,
    onSuccess: (data) => {
      if (data?.id) {
        queryClient.setQueryData(nudgesKey(envId), (prev) =>
          Array.isArray(prev)
            ? prev.map((item) => (item?.id === data.id ? data : item))
            : prev,
        );
      }
      refreshNudges();
      queryClient.invalidateQueries({
        queryKey: [...environmentVersionsKey(envId), "run-plan"],
      });
      queryClient.invalidateQueries({ queryKey: ["harness-job", envId] });
    },
    onError: (error) => {
      if (errorStatus(error) === 409 && error?.error === "nudge_closed") {
        refreshNudges();
      }
    },
  });
};

// "yes" queues a rebuild, "no" skips.
export const useAnswerNudge = (envId) =>
  useNudgeMutation(envId, ({ nudgeId, answer }) =>
    answerNudge(envId, nudgeId, answer),
  );

// Stop a queued or running rebuild.
export const useStopNudge = (envId) =>
  useNudgeMutation(envId, ({ nudgeId }) => stopNudge(envId, nudgeId));

// The pieces that can hold unsaved changes, in the order they are saved.
export const DRAFT_PIECES = ["environment", "suite"];

// An unsaved draft: a version row plus what the save checks and the bar shows.
// `changes` holds counts only.
export const draftFromApi = (item = {}) => ({
  ...versionFromApi(item),
  stale: Boolean(item.stale),
  revision: item.revision ?? null,
  lastEditedBy: item.last_edited_by?.name || null,
  updatedAt: item.updated_at || null,
  parentLabel: item.parent_label || null,
  defaultLabel: item.default_label || null,
  changes: item.changes || {},
});

export const toDrafts = (data = {}) => {
  const drafts = Object.fromEntries(
    DRAFT_PIECES.map((piece) => [
      piece,
      data?.[piece] ? draftFromApi(data[piece]) : null,
    ]),
  );
  const pieces = DRAFT_PIECES.filter((piece) => drafts[piece]);
  return {
    ...drafts,
    setAside: (Array.isArray(data?.set_aside) ? data.set_aside : []).map(
      draftFromApi,
    ),
    pieces,
    any: pieces.length > 0,
    stale: pieces.some((piece) => drafts[piece].stale),
  };
};

// Not polled: the edits that change it (a chat turn, a Scenarios-tab edit, a
// save or discard) ask for it again.
export function useDrafts(envId, { enabled = true } = {}) {
  return useQuery({
    queryKey: draftsKey(envId),
    queryFn: () => getDrafts(envId),
    select: toDrafts,
    enabled: Boolean(envId) && enabled,
    retry: retryUnlessNotFound,
    meta: { errorHandled: true },
  });
}

const DRAFTS_REFRESH_CODES = new Set([
  "draft_stale",
  "draft_changed",
  "draft_not_found",
]);

// Save every open draft at once. A click keeps its key while the same drafts
// are sent again after a failure the server may not have seen; a refusal or
// other drafts mint a new one. The answer is every version, so it seeds the
// list; what follows the defaults is asked again.
export function useSaveDrafts(envId) {
  const queryClient = useQueryClient();
  const pending = useRef(null);
  return useMutation({
    meta: { errorHandled: true },
    mutationFn: (drafts) => {
      const body = drafts.map(({ piece, parentId, revision }) => ({
        piece,
        expected_parent_id: parentId ?? null,
        ...(revision != null ? { expected_revision: revision } : {}),
      }));
      const signature = JSON.stringify(body);
      if (pending.current?.signature !== signature) {
        pending.current = { signature, key: harnessIdempotencyKey() };
      }
      return saveDrafts(envId, {
        drafts: body,
        idempotencyKey: pending.current.key,
      });
    },
    onSuccess: (data) => {
      pending.current = null;
      if (data?.versions) {
        queryClient.setQueryData(environmentVersionsKey(envId), data.versions);
      }
      refreshFollowers(queryClient, envId);
    },
    onError: (error) => {
      const status = errorStatus(error);
      if (status >= 400 && status < 500) pending.current = null;
      if (DRAFTS_REFRESH_CODES.has(error?.error)) {
        queryClient.invalidateQueries({ queryKey: draftsKey(envId) });
      } else if (error?.error === "version_conflict") {
        queryClient.invalidateQueries({
          queryKey: environmentVersionsKey(envId),
        });
      }
    },
  });
}

// Discard the given pieces' drafts, one request each. Whatever was discarded
// before a refusal is gone, so the drafts and what they overlay are asked
// again either way.
export function useDiscardDraft(envId) {
  const queryClient = useQueryClient();
  return useMutation({
    meta: { errorHandled: true },
    mutationFn: async (pieces) => {
      for (const piece of pieces) {
        // eslint-disable-next-line no-await-in-loop
        await discardDraft(envId, piece);
      }
    },
    onSettled: () => refreshFollowers(queryClient, envId),
  });
}
