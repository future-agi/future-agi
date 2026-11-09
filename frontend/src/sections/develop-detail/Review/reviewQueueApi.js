// Silent queue calls for the review flows (RW#3): the shared annotation-queue
// hooks toast on every step (`Queue created successfully`, `Queue status
// updated`), so starting one review showed three toasts. These call the same
// endpoints with no toast; the caller says one sentence for the whole action
// and refreshes the same caches.
import axios from "src/utils/axios";
import {
  annotationQueueEndpoints,
  annotationQueueKeys,
  postAddQueueItems,
} from "src/api/annotation-queues/annotation-queues";
import { reviewKeys } from "src/api/develop/review";

const body = (res) => res?.data?.result ?? res?.data;

export async function createQueueQuiet(payload) {
  return body(await axios.post(annotationQueueEndpoints.create, payload));
}

export async function setQueueStatusQuiet(id, status) {
  return body(
    await axios.post(annotationQueueEndpoints.updateStatus(id), { status }),
  );
}

export async function addQueueItemsQuiet(queueId, items) {
  return body(await postAddQueueItems({ queueId, items }));
}

/** Refresh every list that shows queues or their items. */
export function refreshQueues(queryClient, datasetId) {
  queryClient.invalidateQueries({ queryKey: annotationQueueKeys.all });
  if (datasetId)
    queryClient.invalidateQueries({ queryKey: reviewKeys.queues(datasetId) });
}

/**
 * The pairwise label's id without listing labels: any pairwise review queue
 * already carries it. Null when none is known (create it then).
 */
export function knownPairwiseLabelId(queues) {
  const q = (queues ?? []).find(
    (x) => x?.metadata?.mode === "pairwise" && x?.label_ids?.length,
  );
  return q?.label_ids?.[0] ?? null;
}
