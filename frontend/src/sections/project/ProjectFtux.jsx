import { Box } from "@mui/material";
import React, { useEffect, useState } from "react";
import { ShowComponent } from "src/components/show";
import PageHeader from "src/components/page-header";
import StatusBadge from "src/components/status-badge/StatusBadge";
import { useLocation } from "react-router-dom";

import NewExperiment from "./NewProject/NewExperiment";
import NewObserve from "./NewProject/NewObserve";

const WAITING_FOR_FIRST_TRACE =
  "Waiting for your first trace. This page updates when it arrives.";

const ProjectFtux = () => {
  const location = useLocation();
  const currentPath = location.pathname;
  const [isObserve, setIsObserve] = useState(currentPath.includes("observe"));

  useEffect(() => {
    const isObserve = currentPath.includes("observe");
    setIsObserve(isObserve);
  }, [currentPath]);

  // A work-screen header in the same place and type as the project list's
  // header: title and one-line purpose on the left, the live waiting status
  // on the right, the setup steps below.
  return (
    <Box
      sx={{
        backgroundColor: "background.paper",
        padding: 2,
        display: "flex",
        flexDirection: "column",
        gap: 4, // the 32px the setup steps use between their own sections
      }}
    >
      <PageHeader
        title={`Welcome to ${isObserve ? "Observe" : "Prototype"}`}
        description="Send traces from your agents to inspect every step, debug failures and track cost and latency."
        actions={
          isObserve ? (
            // ProjectWrapperView re-reads the project list every 5 s while
            // this page is shown and swaps in the list once the first trace
            // lands. flexShrink 0 keeps the chip whole; the title column
            // wraps first on narrow screens.
            <Box
              role="status"
              sx={{ display: "flex", flexShrink: 0, maxWidth: "100%" }}
            >
              <StatusBadge status="syncing" label={WAITING_FOR_FIRST_TRACE} />
            </Box>
          ) : null
        }
        sx={{ mb: 0 }}
      />
      <ShowComponent condition={!isObserve}>
        <NewExperiment />
      </ShowComponent>
      <ShowComponent condition={isObserve}>
        <NewObserve />
      </ShowComponent>
    </Box>
  );
};

export default ProjectFtux;
