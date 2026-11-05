import React, { useMemo } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { Box, Button, Skeleton, Stack, Typography } from "@mui/material";
import EmptyState from "src/sections/simulate/environments/components/EmptyState";
import StatusChip from "src/sections/simulate/environments/workspace/runs/StatusChip";
import { runStateFor } from "src/sections/simulate/environments/workspace/runs/runs.constants";
import { useAgentRuns } from "src/api/agents/agentDetail";
import {
  isHiddenFromRestrictedOrgs,
  useRestrictSimulateToEnvironments,
} from "src/hooks/useRestrictSimulateToEnvironments";
import FitDataTable from "./FitDataTable";
import { LineSkeleton, QuietTag } from "./bits";
import { agentDetailPath } from "./agentDetailTabs";
import { MONO, ago } from "./format";
import { PassRateCell, runPath } from "./runCells";

const RECENT_RUNS = 15;
const SKELETON_ROWS = 8;

// What each column shows while the runs load, sized like the real cell.
const text = (typography, width, props) => (
  <Typography sx={{ typography, width: "100%" }}>
    <LineSkeleton width={width} {...props} />
  </Typography>
);
const SKELETON_CELLS = {
  run: text("s2", 44, { "data-testid": "run-row-skeleton" }),
  status: <Skeleton variant="rounded" width={88} height={22} />,
  environment: text("s2", 120),
  version: text("s3", 20),
  passRate: (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1}
      sx={{ width: "100%" }}
    >
      <Skeleton variant="rounded" height={6} sx={{ flex: 1 }} />
      <LineSkeleton width={56} />
    </Stack>
  ),
  when: text("s3", 48),
};
const SKELETON_DATA = Array.from({ length: SKELETON_ROWS }, (_, i) => ({
  testExecutionId: `skeleton-${i}`,
}));

/** Overview: the agent's newest runs, across environments and Simulate. */
export default function AgentOverviewTab() {
  const { agentId } = useParams();
  const navigate = useNavigate();
  const { search } = useLocation();
  const restrictSimulateToEnvironments = useRestrictSimulateToEnvironments();
  // A Simulate run's page is not registered for an org restricted to
  // Environments, so its row stays put instead of leading to the 404.
  const openRun = (run) => {
    const path = runPath(run);
    if (restrictSimulateToEnvironments && isHiddenFromRestrictedOrgs(path)) {
      return;
    }
    navigate(path);
  };
  const {
    data: runs,
    isLoading,
    isError,
    error,
    refetch,
  } = useAgentRuns(agentId, { limit: RECENT_RUNS });

  const columns = useMemo(
    () => [
      {
        id: "run",
        header: "Run",
        size: 100,
        cell: ({ row: { original: r } }) => (
          <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>
            {`Run ${r.runNumber}`}
          </Typography>
        ),
      },
      {
        id: "status",
        header: "Status",
        size: 140,
        cell: ({ row: { original: r } }) => (
          <Box
            data-testid={`run-status-${r.testExecutionId}`}
            sx={{ display: "flex" }}
          >
            <StatusChip status={runStateFor(r.status)} />
          </Box>
        ),
      },
      {
        id: "environment",
        header: "Environment",
        meta: { flex: 1.2 },
        minSize: 180,
        cell: ({ row: { original: r } }) => {
          const name = r.environment?.name ?? r.runTestName;
          return (
            <Stack
              direction="row"
              alignItems="center"
              spacing={0.75}
              sx={{ minWidth: 0 }}
            >
              <Typography noWrap title={name} sx={{ typography: "s2" }}>
                {name || "—"}
              </Typography>
              {r.kind === "simulate" && <QuietTag>Simulate</QuietTag>}
            </Stack>
          );
        },
      },
      {
        id: "version",
        header: "Agent version",
        size: 140,
        cell: ({ row: { original: r } }) => (
          <Typography
            noWrap
            sx={{ typography: "s3", fontWeight: 600, fontFamily: MONO }}
          >
            {r.agentVersion ? `v${r.agentVersion.number}` : "—"}
          </Typography>
        ),
      },
      {
        id: "passRate",
        header: "Pass rate",
        size: 200,
        cell: ({ row: { original: r } }) => <PassRateCell run={r} />,
      },
      {
        id: "when",
        header: "When",
        size: 130,
        cell: ({ row: { original: r } }) => (
          <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
            {r.completedAt ? ago(r.completedAt) : `started ${ago(r.startedAt)}`}
          </Typography>
        ),
      },
    ],
    [],
  );

  let body;
  if (isError && !runs) {
    body = (
      <EmptyState
        icon="solar:danger-triangle-linear"
        title="Couldn't load recent runs"
        body={error?.message}
        action={
          <Button variant="outlined" size="small" onClick={() => refetch()}>
            Retry
          </Button>
        }
      />
    );
  } else if (!isLoading && !runs?.length) {
    body = (
      <EmptyState
        icon="solar:play-circle-linear"
        title="No runs yet"
        body="Runs of this agent from any environment will show up here."
        action={
          <Button
            variant="outlined"
            size="small"
            onClick={() =>
              navigate(`${agentDetailPath(agentId, "environments")}${search}`)
            }
          >
            View environments
          </Button>
        }
      />
    );
  } else {
    body = (
      <FitDataTable
        columns={
          isLoading
            ? columns.map((c) => ({ ...c, cell: () => SKELETON_CELLS[c.id] }))
            : columns
        }
        data={isLoading ? SKELETON_DATA : runs}
        getRowId={(r) => r.testExecutionId}
        onRowClick={isLoading ? undefined : openRun}
      />
    );
  }

  return (
    <Stack spacing={0.5}>
      <Typography sx={{ typography: "s1", fontWeight: 700 }}>
        Recent runs
      </Typography>
      {body}
    </Stack>
  );
}
