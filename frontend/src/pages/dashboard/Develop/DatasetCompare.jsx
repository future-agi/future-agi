import React from "react";
import { Helmet } from "react-helmet-async";
import { Outlet, useLocation } from "react-router-dom";
import CompareWrapperView from "src/sections/experiment-detail/CompareWrapperView";
import RunActionConfirmHost from "src/sections/develop-detail/Experiment/RunActionConfirmHost";
import { useDsxOnlyCompareRedirect } from "src/sections/experiment-detail-classic/dsxOnlyRoute";

/**
 * Compare runs page (PRODUCT-SPEC §2.1, L6):
 * `/dashboard/develop/:dataset/compare/data|matrix|summary`. CompareWrapperView
 * renders the header, the Data · Matrix · Summary tabs and the body of the tab in
 * the last path segment.
 * Prototype builds only (L3, the capability contract): every other build lands on the
 * dataset page instead.
 */
export default function DatasetCompare() {
  const { pathname } = useLocation();
  const hidden = useDsxOnlyCompareRedirect();
  if (hidden) return hidden;
  // R3: `/compare/tasks` is the full-width side-by-side page (its own route,
  // pages/dashboard/Develop/TaskCompare.jsx); never wrap it in compare tabs.
  if (/\/compare\/tasks\/?$/.test(pathname)) return null;
  // Bare `/compare`: let the index route redirect to the default tab first.
  const match = /\/compare\/(data|matrix|paths|summary)\/?$/.exec(pathname); // R4-F frozen line
  if (!match) return <Outlet />;
  const tab = match[1];
  return (
    <>
      <Helmet>
        <title>Compare runs</title>
      </Helmet>
      <CompareWrapperView tab={tab} />
      {/* Stop / Re-run confirms (L5) for the run chips on this page. */}
      <RunActionConfirmHost />
    </>
  );
}
