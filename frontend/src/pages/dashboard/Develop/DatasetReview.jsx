import React from "react";
import { Helmet } from "react-helmet-async";
import { useParams, useSearchParams } from "react-router-dom";
import AnnotateWorkspaceView from "src/sections/annotations/queues/annotate/annotate-workspace-view";
import { exitLabelFor } from "src/sections/develop-detail/Review/reviewModel";

// `from` is an encoded in-app return URL (compare page, experiment page);
// anything else falls back to the dataset's Experiments tab.
function reviewExit(datasetId, from) {
  const safe = typeof from === "string" && from.startsWith("/dashboard/");
  return {
    exitTo: safe ? from : `/dashboard/develop/${datasetId}?tab=experiments`,
    exitLabel: exitLabelFor(safe ? from : null),
  };
}

/**
 * Focused review (PRODUCT-SPEC §2.1, §3.12):
 * `/dashboard/develop/:dataset/review/:queueId` renders the existing annotate
 * workspace, which reads `queueId` with useParams() and `itemId` from the
 * query. Header, progress, reservation, skip, paused / not-assigned states,
 * footer and comments are the workspace's own; this page only sets where
 * Back goes.
 */
export default function DatasetReview() {
  const { dataset } = useParams();
  const [searchParams] = useSearchParams();
  const { exitTo, exitLabel } = reviewExit(dataset, searchParams.get("from"));

  return (
    <>
      <Helmet>
        <title>Review | FutureAGI</title>
      </Helmet>
      <AnnotateWorkspaceView exitTo={exitTo} exitLabel={exitLabel} />
    </>
  );
}
