import React from "react";
import { Helmet } from "react-helmet-async";
import {
  Link as RouterLink,
  useParams,
  useSearchParams,
} from "react-router-dom";
import { Box, Button, Typography } from "@mui/material";
import { NOT_IN_RELEASE } from "src/components/NotInRelease/notInRelease";
import { taskCompareServed } from "src/api/develop/taskCompareServed";
import TaskCompareWorkspace from "src/sections/task-compare/TaskCompareWorkspace";
import {
  compareFallbackPath,
  useDsxOnlyCompareRedirect,
} from "src/sections/experiment-detail-classic/dsxOnlyRoute";

// The link's `from` as a path in this app, read the way a browser reads an
// address: it starts with one slash, has no backslash and stays on this site.
// Anything else (another site, or nothing) gives null.
function inAppPath(value) {
  if (typeof value !== "string" || !/^\/(?![/\\])/.test(value)) return null;
  if (value.includes("\\")) return null;
  try {
    const { origin } = window.location;
    const url = new URL(value, origin);
    if (url.origin !== origin || url.username || url.password) return null;
    return `${url.pathname}${url.search}${url.hash}`;
  } catch {
    return null;
  }
}

/**
 * Side by side (R3 §3, implementation): `/dashboard/develop/:dataset/compare/tasks`.
 * Full width: inside DashboardLayout but NOT CompareWrapperView (no compare
 * tabs). Links come from analysisLinks.taskCompareHref.
 * Prototype builds and builds with SHOW_UNRELEASED_PARTS select this surface.
 * Other builds land on the dataset page; experimentsBasic does not select it.
 * Where the server has no side-by-side read (taskCompareServed), the page
 * shows the shared line and Back, and mounts no workspace, so it asks for
 * nothing. Back goes where the link came from (`from`, a path in this app),
 * else to the dataset page.
 */
export default function TaskCompare() {
  const { dataset } = useParams();
  const [searchParams] = useSearchParams();
  const hidden = useDsxOnlyCompareRedirect();
  if (hidden) return hidden;
  const from = searchParams.get("from");
  const back = inAppPath(from) ?? compareFallbackPath(dataset);
  return (
    <>
      <Helmet>
        <title>Side by side</title>
      </Helmet>
      {taskCompareServed() ? (
        <TaskCompareWorkspace datasetId={dataset} />
      ) : (
        <Box data-testid="task-compare-not-in-release" sx={{ p: 3 }}>
          <Button
            component={RouterLink}
            to={back}
            size="small"
            color="inherit"
            sx={{ mb: 1 }}
          >
            Back
          </Button>
          <Typography variant="h6" component="h1" sx={{ mb: 1 }}>
            Side by side
          </Typography>
          <Typography
            variant="body2"
            color="text.secondary"
            data-not-in-release=""
          >
            {NOT_IN_RELEASE}
          </Typography>
        </Box>
      )}
    </>
  );
}
