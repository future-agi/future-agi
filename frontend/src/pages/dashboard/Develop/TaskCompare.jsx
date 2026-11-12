import React from "react";
import { Helmet } from "react-helmet-async";
import { useParams } from "react-router-dom";
import TaskCompareWorkspace from "src/sections/task-compare/TaskCompareWorkspace";
import { useDsxOnlyCompareRedirect } from "src/sections/experiment-detail-classic/dsxOnlyRoute";

/**
 * Side by side (R3 §3, implementation): `/dashboard/develop/:dataset/compare/tasks`.
 * Full width: inside DashboardLayout but NOT CompareWrapperView (no compare
 * tabs). Links come from analysisLinks.taskCompareHref.
 * Prototype builds only (L3, the capability contract): every other build lands on the
 * dataset page instead.
 */
export default function TaskCompare() {
  const { dataset } = useParams();
  const hidden = useDsxOnlyCompareRedirect();
  if (hidden) return hidden;
  return (
    <>
      <Helmet>
        <title>Side by side</title>
      </Helmet>
      <TaskCompareWorkspace datasetId={dataset} />
    </>
  );
}
