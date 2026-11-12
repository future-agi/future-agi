// W4-5 per-dataset access, W4-7 audit and the W4-6 workspace sharing policy
// (W4-SCOPE §6; MOCK/CONTRACT.md "W4"). One query key per resource, so the
// Access section, the audit drawer and every gate that reads
// useDatasetAccess() share one fetch.
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

// api/shared-links.js keys its queries under "shared-links" (not exported).
const SHARED_LINKS_KEY = "shared-links";

export const datasetAccessKey = (datasetId) => [
  "w4",
  "dataset-access",
  datasetId,
];
export const sharingPolicyKey = (workspaceId) => [
  "w4",
  "sharing-policy",
  workspaceId,
];
export const auditKey = (params) => ["w4", "audit", params];

/** GET /model-hub/datasets/{id}/access/ */
export const useDatasetAccessQuery = (datasetId, options = {}) =>
  useQuery({
    queryKey: datasetAccessKey(datasetId),
    queryFn: () => axios.get(endpoints.develop.w4.access(datasetId)),
    select: (res) => res.data,
    enabled: Boolean(datasetId),
    staleTime: 15_000,
    meta: { errorHandled: true },
    ...options,
  });

/** PUT {workspace_level?, grants?, dismiss_requests?} -> the access view. */
export const useUpdateDatasetAccess = (datasetId) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body) =>
      axios.put(endpoints.develop.w4.access(datasetId), body),
    meta: { errorHandled: true },
    onSuccess: (res) => {
      qc.setQueryData(datasetAccessKey(datasetId), res);
      qc.invalidateQueries({ queryKey: ["w4", "audit"] });
    },
  });
};

/** POST {request: perm} -> {requested}. */
export const useRequestDatasetAccess = (datasetId) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (perm) =>
      axios.post(endpoints.develop.w4.access(datasetId), { request: perm }),
    meta: { errorHandled: true },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: datasetAccessKey(datasetId) });
      qc.invalidateQueries({ queryKey: ["w4", "audit"] });
    },
  });
};

/** GET /model-hub/audit/?scope=&action=&actor=&since=&until=&cursor=&page_size= */
export const useAuditQuery = (params, options = {}) =>
  useQuery({
    queryKey: auditKey(params),
    queryFn: () => axios.get(endpoints.develop.w4.audit, { params }),
    select: (res) => res.data,
    enabled: Boolean(params?.scope),
    meta: { errorHandled: true },
    ...options,
  });

/** GET /model-hub/workspaces/{id}/sharing-policy/ */
export const useSharingPolicyQuery = (workspaceId, options = {}) =>
  useQuery({
    queryKey: sharingPolicyKey(workspaceId),
    queryFn: () => axios.get(endpoints.develop.w4.sharingPolicy(workspaceId)),
    select: (res) => res.data,
    enabled: Boolean(workspaceId),
    meta: { errorHandled: true },
    ...options,
  });

/** PUT {public_links?, max_days?} -> the policy. */
export const useUpdateSharingPolicy = (workspaceId) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body) =>
      axios.put(endpoints.develop.w4.sharingPolicy(workspaceId), body),
    meta: { errorHandled: true },
    onSuccess: (res) => {
      qc.setQueryData(sharingPolicyKey(workspaceId), res);
      qc.invalidateQueries({ queryKey: ["w4", "audit"] });
      // The policy governs every link: whether it opens, when it closes.
      qc.invalidateQueries({ queryKey: [SHARED_LINKS_KEY] });
    },
  });
};

/** The audit read page by page (the server's `next_cursor`). */
export const useAuditInfinite = (params, options = {}) =>
  useInfiniteQuery({
    queryKey: [...auditKey(params), "pages"],
    queryFn: ({ pageParam }) =>
      axios.get(endpoints.develop.w4.audit, {
        params: { ...params, ...(pageParam ? { cursor: pageParam } : {}) },
      }),
    initialPageParam: null,
    getNextPageParam: (last) => last?.data?.next_cursor ?? undefined,
    enabled: Boolean(params?.scope),
    meta: { errorHandled: true },
    ...options,
  });

/**
 * Every entry the audit read answers for these filters, page by page
 * (Export CSV; W4 §9, M6). Stops after `maxPages` pages.
 */
export async function fetchAllAudit(params, { maxPages = 50 } = {}) {
  const out = [];
  let cursor = null;
  for (let i = 0; i < maxPages; i += 1) {
    // eslint-disable-next-line no-await-in-loop
    const res = await axios.get(endpoints.develop.w4.audit, {
      params: { ...params, page_size: 100, ...(cursor ? { cursor } : {}) },
    });
    const data = res?.data ?? {};
    out.push(...(data.entries ?? []));
    cursor = data.next_cursor ?? null;
    if (!cursor) break;
  }
  return out;
}
