import React from "react";
import { Navigate, useParams } from "react-router";
import { useExperimentSurface } from "./experimentsBasic";

// Compare routes use the shared experiment surface: prototype mode or
// SHOW_UNRELEASED_PARTS selects DSX, regardless of experimentsBasic.
// The explicit classic profile redirects these deep links to the dataset.
// Route visibility does not imply that every backend read is available;
// the side-by-side page separately checks its task-read contract.

/** The dataset page a hidden compare deep link lands on. Pure. */
export function compareFallbackPath(datasetId) {
  return datasetId ? `/dashboard/develop/${datasetId}` : "/dashboard/develop";
}

/**
 * `null` for the DSX surface (prototype or show-unreleased); otherwise the
 * redirect element returned by a page in the explicit classic profile.
 */
export function useDsxOnlyCompareRedirect() {
  const surface = useExperimentSurface();
  const { dataset } = useParams();
  if (surface === "dsx") return null;
  return <Navigate to={compareFallbackPath(dataset)} replace />;
}
