// Dataset organisation writes (W9-SCOPE §5 A1, A6, A7): metadata, folder
// and attached agents ride `PUT edit_dataset_behavior` as top-level keys
// (its request schema accepts extra keys; W9-SCOPE finding 4), one PUT per
// dataset, in sequence, so a partial failure names the dataset and the
// server's sentence. No new path.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

/** The server's sentence for a failed request. */
export const errorSentence = (e) =>
  e?.response?.data?.result ??
  e?.result ??
  (typeof e?.message === "string" && e.message
    ? e.message
    : "The request failed.");

/** PUT one dataset's organisation keys. */
export const putOrg = (datasetId, patch) =>
  axios.put(endpoints.develop.updateDataset(datasetId), patch);

/**
 * Runs one write per dataset, in order, and collects what saved and what
 * failed (with each failure's sentence). Pure apart from `put`.
 * @param {{id: string, name?: string}[]} datasets
 * @param {(d) => object} patchFor  the body for one dataset
 * @param {(id, body) => Promise} put
 */
export async function saveInSequence(datasets, patchFor, put = putOrg) {
  const saved = [];
  const failed = [];
  for (const d of datasets ?? []) {
    try {
      // eslint-disable-next-line no-await-in-loop
      await put(d.id, patchFor(d));
      saved.push(d);
    } catch (e) {
      failed.push({ ...d, error: errorSentence(e) });
    }
  }
  return { saved, failed, total: (datasets ?? []).length };
}

const plural = (n, noun) => `${n} ${noun}${n === 1 ? "" : "s"}`;

/**
 * The toast after a sequence: `Saved metadata on 3 datasets`; on a
 * partial failure `Saved on 2 of 3 · Refund policy review: <sentence>`.
 * @param {{saved, failed, total}} result
 * @param {string} done  e.g. "Saved metadata on", "Moved", "Attached"
 * @param {string} [tail]  e.g. " to support/billing"
 */
export function sequenceToast(result, done, tail = "") {
  const { saved, failed, total } = result;
  if (!failed.length)
    return {
      variant: "success",
      message: `${done} ${plural(saved.length, "dataset")}${tail}`,
    };
  const first = failed[0];
  const why = `${first.name ?? first.id}: ${first.error}`;
  if (!saved.length)
    return {
      variant: "error",
      message: total === 1 ? first.error : `Nothing saved · ${why}`,
    };
  return {
    variant: "warning",
    message: `Saved on ${saved.length} of ${total} · ${why}`,
  };
}

/** Invalidate every list and picker that shows organisation fields. */
export function invalidateDatasetLists(queryClient) {
  queryClient.invalidateQueries({ queryKey: ["datasets", "list"] });
  queryClient.invalidateQueries({ queryKey: ["datasets", "query"] });
  queryClient.invalidateQueries({ queryKey: ["develop", "dataset-name-list"] });
}

/**
 * Save organisation keys on several datasets.
 * mutateAsync({datasets, patchFor}) → {saved, failed, total}
 */
export function useSaveDatasetOrg(options = {}) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ datasets, patchFor }) => saveInSequence(datasets, patchFor),
    ...options,
    onSettled: (...args) => {
      invalidateDatasetLists(queryClient);
      options.onSettled?.(...args);
    },
  });
}

/** Star or unstar one dataset for the signed-in person (W9-SCOPE §5 A4). */
export const putStar = (datasetId, on) =>
  on
    ? axios.post(endpoints.develop.w9.datasetStar(datasetId))
    : axios.delete(endpoints.develop.w9.datasetStar(datasetId));

/**
 * Star / unstar: mutateAsync({id, on}) → {dataset_id, starred}. Every list
 * and picker refetches, so the server's starred-first order shows.
 */
export function useDatasetStar(options = {}) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, on }) => (await putStar(id, on)).data?.result,
    ...options,
    onSettled: (...args) => {
      invalidateDatasetLists(queryClient);
      options.onSettled?.(...args);
    },
  });
}

/**
 * Agent Playground graphs for the Agents dialog (read only), every page:
 * [{id, name, active_version_number}].
 */
export function useAgentGraphOptions(options = {}) {
  return useQuery({
    queryKey: ["datasets", "org", "agent-graphs"],
    queryFn: async () => {
      const out = [];
      for (let page = 1; ; page += 1) {
        // eslint-disable-next-line no-await-in-loop
        const res = await axios.get(endpoints.agentPlayground.listGraphs, {
          params: { page_number: page },
        });
        const result = res.data?.result ?? {};
        out.push(...(result.graphs ?? []));
        if (page >= (result.metadata?.total_pages ?? 1)) break;
      }
      return out.map((g) => ({
        id: g.id,
        name: g.name,
        active_version_number: g.active_version_number ?? null,
      }));
    },
    staleTime: 30 * 1000,
    ...options,
  });
}
