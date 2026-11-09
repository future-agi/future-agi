import React from "react";
import { Navigate, useParams } from "react-router";
import { useExperimentSurface } from "./experimentsBasic";

// L3 (the capability contract): the DSX compare pages, Compare runs
// (/dashboard/develop/:dataset/compare/data|matrix|paths|summary) and Side by
// side (/dashboard/develop/:dataset/compare/tasks), read DSX-only endpoints
// (datasets/{id}/runs/, results/) that dev does not serve. Only prototype
// builds mount them. Every other build, with experimentsBasic on or off,
// sends the deep link to the dataset page, where L6's exp.compare route gate
// sends it too (launch/launchSurfaces.js, fallback /dashboard/develop/$1).

/** The dataset page a hidden compare deep link lands on. Pure. */
export function compareFallbackPath(datasetId) {
  return datasetId ? `/dashboard/develop/${datasetId}` : "/dashboard/develop";
}

/**
 * `null` in prototype builds (the page mounts); otherwise the redirect element
 * the page returns instead of mounting.
 */
export function useDsxOnlyCompareRedirect() {
  const surface = useExperimentSurface();
  const { dataset } = useParams();
  if (surface === "dsx") return null;
  return <Navigate to={compareFallbackPath(dataset)} replace />;
}
