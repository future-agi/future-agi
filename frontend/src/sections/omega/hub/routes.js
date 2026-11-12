// HUB_ROUTES (SPEC §7.1): children of the "dashboard" route. The integrator
// spreads them in one line:  dashboardChildren.push(...HUB_ROUTES);
// Static segments (publish, publishers, submissions, settings, review) are
// listed before `:publisher/:name`, and SPEC §2.3 reserves them as handles.
import { createElement, Suspense } from "react";
import Box from "@mui/material/Box";
import Skeleton from "@mui/material/Skeleton";

import lazyWithRetry from "src/utils/lazyWithRetry";

const page = (load) => lazyWithRetry(load);

const HubBrowsePage = page(() => import("./HubBrowsePage"));
const HubEntryPage = page(() => import("./HubEntryPage"));
const HubPublishersPage = page(() => import("./HubPublishersPage"));
const HubPublisherPage = page(() => import("./HubPublisherPage"));
const HubPublishPage = page(() => import("./HubPublishPage"));
const HubSubmissionsPage = page(() => import("./HubSubmissionsPage"));
const HubSubmissionPage = page(() => import("./HubSubmissionPage"));
const HubSettingsPage = page(() => import("./HubSettingsPage"));
const HubVerifyEmailPage = page(() => import("./HubVerifyEmailPage"));
const HubReviewQueuePage = page(() => import("./HubReviewQueuePage"));
const HubReviewPage = page(() => import("./HubReviewPage"));
const HubReportsPage = page(() => import("./HubReportsPage"));
const HubPicksPage = page(() => import("./HubPicksPage"));
const HubPublishersReviewPage = page(() => import("./HubPublishersReviewPage"));
const HubBlocklistPage = page(() => import("./HubBlocklistPage"));
const HubAuditPage = page(() => import("./HubAuditPage"));

function Fallback() {
  return createElement(
    Box,
    {
      sx: {
        p: { xs: 2, md: 3 },
        display: "flex",
        flexDirection: "column",
        gap: 2,
      },
      "data-testid": "hub-route-loading",
    },
    createElement(Skeleton, { variant: "rounded", height: 40, width: 280 }),
    createElement(Skeleton, { variant: "rounded", height: 320 }),
  );
}

const lazyElement = (Component) =>
  createElement(
    Suspense,
    { fallback: createElement(Fallback) },
    createElement(Component),
  );

export const HUB_ROUTES = [
  { path: "hub", element: lazyElement(HubBrowsePage) },
  // Publish is always a page: on a server with no Hub checker it says what is
  // missing instead of answering 404.
  { path: "hub/publish", element: lazyElement(HubPublishPage) },
  { path: "hub/publishers", element: lazyElement(HubPublishersPage) },
  { path: "hub/publishers/:handle", element: lazyElement(HubPublisherPage) },
  { path: "hub/submissions", element: lazyElement(HubSubmissionsPage) },
  { path: "hub/submissions/:id", element: lazyElement(HubSubmissionPage) },
  { path: "hub/settings", element: lazyElement(HubSettingsPage) },
  { path: "hub/verify-email", element: lazyElement(HubVerifyEmailPage) },
  { path: "hub/review", element: lazyElement(HubReviewQueuePage) },
  { path: "hub/review/reports", element: lazyElement(HubReportsPage) },
  { path: "hub/review/picks", element: lazyElement(HubPicksPage) },
  {
    path: "hub/review/publishers",
    element: lazyElement(HubPublishersReviewPage),
  },
  { path: "hub/review/blocklist", element: lazyElement(HubBlocklistPage) },
  { path: "hub/review/audit", element: lazyElement(HubAuditPage) },
  { path: "hub/review/:id", element: lazyElement(HubReviewPage) },
  { path: "hub/:publisher/:name/:tab?", element: lazyElement(HubEntryPage) },
];

export default HUB_ROUTES;
