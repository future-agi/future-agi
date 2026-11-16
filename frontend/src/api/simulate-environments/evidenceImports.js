import { useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";
import { harnessIdempotencyKey } from "src/api/harness/harness";
import { refreshDrafts, refreshFollowers } from "./versions";

// Observe sessions or calls frozen as evidence for an environment's tests, and
// the review that drafts and adds them (simulate/serializers/evidence_import.py).

// The backend takes 1 to 20 sources per import.
export const MAX_EVIDENCE_SOURCES = 20;
// How often the review asks again while the builder drafts.
const DRAFTING_POLL_MS = 10_000;

const importsPath = (envId) =>
  apiPath("/simulate/api/harness-environments/{id}/evidence-imports/", {
    id: envId,
  });
const importPath = (envId, importId) =>
  apiPath(
    "/simulate/api/harness-environments/{id}/evidence-imports/{import_id}/",
    {
      id: envId,
      import_id: importId,
    },
  );
const draftPath = (envId, importId) =>
  apiPath(
    "/simulate/api/harness-environments/{id}/evidence-imports/{import_id}/draft/",
    { id: envId, import_id: importId },
  );
const addPath = (envId, importId) =>
  apiPath(
    "/simulate/api/harness-environments/{id}/evidence-imports/{import_id}/add/",
    { id: envId, import_id: importId },
  );

export const evidenceImportKey = (envId, importId) => [
  "harness-evidence-import",
  envId,
  importId,
];

export const createEvidenceImport = async (
  envId,
  { projectId, sourceKind, ids },
) =>
  (
    await axios.post(importsPath(envId), {
      project_id: projectId,
      source_kind: sourceKind,
      ids,
    })
  ).data;

export const getEvidenceImport = async (envId, importId) =>
  (await axios.get(importPath(envId, importId))).data;

export const draftEvidenceCases = async (envId, importId, { caseIds, shows }) =>
  (await axios.post(draftPath(envId, importId), { case_ids: caseIds, shows }))
    .data;

export const addEvidenceCases = async (
  envId,
  importId,
  { caseIds, expectedDrafts, idempotencyKey },
) =>
  (
    await axios.post(addPath(envId, importId), {
      case_ids: caseIds,
      expected_drafts: expectedDrafts,
      idempotency_key: idempotencyKey,
    })
  ).data;

// The drafts the review saw, as the add expects them: a piece with no open
// draft is `{id: null, revision: 0}`.
export const expectedDraftsOf = (drafts) =>
  Object.fromEntries(
    ["environment", "suite"].map((piece) => [
      piece,
      drafts?.[piece]
        ? { id: drafts[piece].id, revision: drafts[piece].revision ?? 0 }
        : { id: null, revision: 0 },
    ]),
  );

export const isEvidenceCaseWaiting = (one) => one?.status === "pending";
export const isEvidenceCaseDrafted = (one) => one?.status === "drafted";

// Read once, and again only while the builder drafts: the bundle re-reads
// Observe and runs redaction, so a focus or remount must not ask for it.
export function useEvidenceImport(envId, importId) {
  return useQuery({
    queryKey: evidenceImportKey(envId, importId),
    queryFn: () => getEvidenceImport(envId, importId),
    enabled: Boolean(envId && importId),
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchInterval: (query) =>
      query.state.data?.drafting ? DRAFTING_POLL_MS : false,
    retry: false,
    meta: { errorHandled: true },
  });
}

export function useCreateEvidenceImport() {
  const queryClient = useQueryClient();
  return useMutation({
    meta: { errorHandled: true },
    mutationFn: ({ envId, ...body }) => createEvidenceImport(envId, body),
    onSuccess: (data, { envId }) => {
      queryClient.setQueryData(evidenceImportKey(envId, data.id), data);
    },
  });
}

export function useDraftEvidenceCases(envId, importId) {
  const queryClient = useQueryClient();
  return useMutation({
    meta: { errorHandled: true },
    mutationFn: (body) => draftEvidenceCases(envId, importId, body),
    onSuccess: (data) => {
      queryClient.setQueryData(evidenceImportKey(envId, importId), data);
    },
  });
}

// One click keeps its key while the same cases are sent again after a failure
// the server may not have seen; a refusal or other cases mint a new one.
export function useAddEvidenceCases(envId, importId) {
  const queryClient = useQueryClient();
  const pending = useRef(null);
  return useMutation({
    meta: { errorHandled: true },
    mutationFn: ({ caseIds, drafts }) => {
      const expectedDrafts = expectedDraftsOf(drafts);
      const signature = JSON.stringify({ caseIds, expectedDrafts });
      if (pending.current?.signature !== signature) {
        pending.current = { signature, key: harnessIdempotencyKey() };
      }
      return addEvidenceCases(envId, importId, {
        caseIds,
        expectedDrafts,
        idempotencyKey: pending.current.key,
      });
    },
    onSuccess: (data) => {
      pending.current = null;
      queryClient.setQueryData(
        evidenceImportKey(envId, importId),
        data.evidence_import,
      );
      refreshDrafts(queryClient, envId);
      refreshFollowers(queryClient, envId);
    },
    onError: (error) => {
      const status = error?.statusCode ?? error?.response?.status;
      if (status >= 400 && status < 500) pending.current = null;
      if (error?.error === "draft_changed") refreshDrafts(queryClient, envId);
    },
  });
}
