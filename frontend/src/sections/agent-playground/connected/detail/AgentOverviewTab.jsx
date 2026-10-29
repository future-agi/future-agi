import React, { useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import {
  Box,
  Button,
  Skeleton,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import EmptyState from "src/sections/simulate/environments/components/EmptyState";
import StatusChip from "src/sections/simulate/environments/workspace/runs/StatusChip";
import { runStateFor } from "src/sections/simulate/environments/workspace/runs/runs.constants";
import { useAgentRuns } from "src/api/agents/agentDetail";
import {
  isHiddenFromRestrictedOrgs,
  useRestrictSimulateToEnvironments,
} from "src/hooks/useRestrictSimulateToEnvironments";
import AgentNextSteps from "./AgentNextSteps";
import FitDataTable from "./FitDataTable";
import { LineSkeleton, QuietTag } from "./bits";
import { agentDetailPath } from "./agentDetailTabs";
import { MONO, ago } from "./format";
import { PassRateCell, isRunning, pairLabel, runPath } from "./runCells";
import useAgentRoute from "./useAgentRoute";

const RECENT_RUNS = 5;
// The most the runs endpoint returns; there is no full runs list for an agent yet.
const MORE_RUNS = 20;
const SKELETON_ROWS = 5;

// What each column shows while the runs load, sized like the real cell.
const text = (typography, width, props) => (
  <Typography sx={{ typography, width: "100%" }}>
    <LineSkeleton width={width} {...props} />
  </Typography>
);
const SKELETON_CELLS = {
  run: text("s2_1", 44, { "data-testid": "run-row-skeleton" }),
  status: <Skeleton variant="rounded" width={88} height={22} />,
  environment: text("s2_1", 120),
  pair: text("s2", 160),
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
  when: text("s2", 48),
};
const SKELETON_DATA = Array.from({ length: SKELETON_ROWS }, (_, i) => ({
  testExecutionId: `skeleton-${i}`,
}));

/**
 * Overview: Next steps (production traces and an environment), then the
 * agent's newest runs across environments and Simulate, five at first.
 */
export default function AgentOverviewTab() {
  const { agentId, agent } = useAgentRoute();
  const navigate = useNavigate();
  const { search } = useLocation();
  const [limit, setLimit] = useState(RECENT_RUNS);
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
  } = useAgentRuns(agentId, { limit });

  const columns = useMemo(
    () => [
      {
        id: "run",
        header: "Run",
        size: 150,
        cell: ({ row: { original: r } }) => (
          <Stack
            direction="row"
            alignItems="center"
            spacing={0.75}
            sx={{ minWidth: 0 }}
            data-testid={`run-cell-${r.testExecutionId}`}
          >
            <Typography noWrap sx={{ typography: "s2_1", fontWeight: 500 }}>
              {`Run ${r.runNumber}`}
            </Typography>
            {!isRunning(r) && r.partial && (
              <Tooltip
                arrow
                title="Only some of the environment's scenarios ran"
              >
                <Box component="span" sx={{ display: "inline-flex" }}>
                  <QuietTag>Partial</QuietTag>
                </Box>
              </Tooltip>
            )}
          </Stack>
        ),
      },
      {
        id: "status",
        header: "Status",
        size: 130,
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
        minSize: 160,
        cell: ({ row: { original: r } }) => {
          const name = r.environment?.name ?? r.runTestName;
          return (
            <Stack
              direction="row"
              alignItems="center"
              spacing={0.75}
              sx={{ minWidth: 0 }}
            >
              <Typography noWrap title={name} sx={{ typography: "s2_1" }}>
                {name || "—"}
              </Typography>
              {r.kind === "simulate" && <QuietTag>Simulate</QuietTag>}
            </Stack>
          );
        },
      },
      {
        id: "pair",
        header: "Agent × environment",
        size: 250,
        cell: ({ row: { original: r } }) => (
          <Typography
            noWrap
            title={pairLabel(r) ?? undefined}
            sx={{ typography: "s2", fontFamily: MONO }}
          >
            {pairLabel(r) ?? "—"}
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
        size: 120,
        cell: ({ row: { original: r } }) => (
          <Typography noWrap sx={{ typography: "s2", color: "text.secondary" }}>
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
          <Button
            variant="outlined"
            color="inherit"
            size="small"
            onClick={() => refetch()}
          >
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
            color="inherit"
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

  const canShowMore =
    limit === RECENT_RUNS && !isLoading && runs?.length === RECENT_RUNS;

  return (
    <Stack spacing={3}>
      {agent ? (
        <AgentNextSteps agent={agent} />
      ) : (
        <Skeleton
          variant="rounded"
          height={164}
          data-testid="next-steps-skeleton"
        />
      )}
      <Stack spacing={1}>
        <Typography sx={{ typography: "s2_1", fontWeight: 500 }}>
          Recent runs
        </Typography>
        {body}
        {canShowMore && (
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            onClick={() => setLimit(MORE_RUNS)}
            sx={{ alignSelf: "flex-start" }}
          >
            Show more runs
          </Button>
        )}
      </Stack>
    </Stack>
  );
}
