import { useCallback, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import axios from "src/utils/axios";
import {
  fetchNextReviewItemId,
  useExperimentReviewQueues,
} from "src/api/develop/review";
import { annotationLabelEndpoints } from "src/api/annotation-labels/annotation-labels";
import notify from "src/sections/common/ux/toast";
import {
  openRegressionQueue,
  PAIRWISE_LABEL,
  queueMetadata,
  regressionQueueName,
  reviewItems,
  reviewPageUrl,
} from "./reviewModel";
import {
  addQueueItemsQuiet,
  createQueueQuiet,
  knownPairwiseLabelId,
  refreshQueues,
  setQueueStatusQuiet,
} from "./reviewQueueApi";

/**
 * `Start here: Review the 14 regressions (about 10 min)` (SVP-4.11): one
 * click creates — or reopens, when one with work left exists — a blind
 * pairwise queue of exactly the tasks that got worse (baseline = A,
 * candidate = B), biggest drop first, then opens the review page at its
 * first pending item. One toast for the whole action (RW#3).
 *
 * @returns {{start: (args) => Promise<void>, busy: boolean, existing: Function}}
 *   args `{runA, runB, labelA, labelB, rowIds, from}`
 */
export function useStartRegressionReview(datasetId, { enabled = true } = {}) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState(false);
  const queuesQ = useExperimentReviewQueues(datasetId, { enabled });

  const existing = useCallback(
    ({ runA, runB }) => openRegressionQueue(queuesQ.data ?? [], { runA, runB }),
    [queuesQ.data],
  );

  const start = useCallback(
    async ({ runA, runB, labelA, labelB, rowIds, from }) => {
      if (busy || !runA || !runB || !rowIds?.length) return;
      setBusy(true);
      try {
        const reuse = existing({ runA, runB });
        let queueId = reuse?.id;
        let title = reuse?.name;
        if (!queueId) {
          let labelId = knownPairwiseLabelId(queuesQ.data);
          if (!labelId) {
            const res = await axios.post(
              annotationLabelEndpoints.create,
              PAIRWISE_LABEL,
            );
            labelId = (res?.data?.result ?? res?.data)?.id ?? null;
          }
          title = regressionQueueName({ labelA, labelB });
          const queue = await createQueueQuiet({
            name: title,
            description: `Blind pairwise review of the ${rowIds.length} ${rowIds.length === 1 ? "task" : "tasks"} where ${labelB} did worse than ${labelA}, biggest drop first.`,
            instructions:
              "Pick the better reply for the customer. Run names are hidden until you finish.",
            assignment_strategy: "manual",
            annotations_required: 1,
            reservation_timeout_minutes: 60,
            requires_review: false,
            auto_assign: false,
            label_ids: labelId ? [labelId] : [],
            annotator_ids: [],
            annotator_roles: {},
            dataset: datasetId,
            metadata: {
              ...queueMetadata({
                mode: "pairwise",
                runA,
                runB,
                blind: true,
                datasetId,
              }),
              focus: "regressions",
            },
          });
          queueId = queue?.id;
          title = queue?.name ?? title;
          if (!queueId) throw new Error("Queue not created");
          await setQueueStatusQuiet(queueId, "active");
          await addQueueItemsQuiet(
            queueId,
            reviewItems(rowIds, {
              mode: "pairwise",
              runA,
              runB,
              blind: true,
              seed: title,
            }),
          );
          refreshQueues(queryClient, datasetId);
        }
        const itemId = await fetchNextReviewItemId(queueId);
        navigate(reviewPageUrl(datasetId, queueId, { itemId, from }));
        notify(
          reuse
            ? `Continuing “${title}”`
            : `Queue “${title}” · ${rowIds.length} ${rowIds.length === 1 ? "task" : "tasks"}, blind. Starts at the biggest drop.`,
          {
            variant: "success",
            key: "start-regression-review",
            // The review page's pager and progress live in its bottom bar:
            // this toast opens at the top so it never covers them.
            anchorOrigin: { vertical: "top", horizontal: "center" },
          },
        );
      } catch {
        notify("Couldn't start the review. Try again.", {
          variant: "error",
          key: "start-regression-review",
        });
      } finally {
        setBusy(false);
      }
    },
    [busy, existing, queuesQ.data, datasetId, navigate, queryClient],
  );

  return { start, busy, existing };
}
