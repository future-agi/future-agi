import PropTypes from "prop-types";
import React from "react";
import { useNavigate } from "react-router-dom";
import {
  Box,
  Button,
  Skeleton,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import { useAgentEnvironments } from "src/api/agents/agentDetail";
import { paths } from "src/routes/paths";
import ComingSoonChip from "src/sections/simulate/environments/components/ComingSoonChip";
import EmptyState from "src/sections/simulate/environments/components/EmptyState";
import StatusPill from "src/sections/simulate/environments/components/StatusPill";
import { Card, LineSkeleton, Row } from "./bits";
import { MONO, ago, rateTone } from "./format";
import { isRunning, pairLabel } from "./runCells";
import useAgentRoute from "./useAgentRoute";

const COLUMNS = ["Environment", "Testing", "Last result", "Status", ""];
const TEMPLATE =
  "minmax(0, 1.4fr) minmax(0, 0.6fr) minmax(0, 1.8fr) minmax(0, 1.3fr) 72px";
const SKELETON_ROWS = 3;

// The production version, when it has moved past the one this environment
// is pinned to; null when the environment tests the latest in production.
export const productionAhead = (env, agent) => {
  const pin = env.agentVersion;
  const production = agent?.activeVersionNumber ?? null;
  if (!pin || production == null || production <= pin.number) return null;
  return production;
};

function LastResult({ env }) {
  const run = env.lastRun;
  if (!run) {
    return (
      <Box component="span" sx={{ color: "text.secondary" }}>
        No runs yet
      </Box>
    );
  }
  const rest = [
    pairLabel(run),
    run.completedAt ? ago(run.completedAt) : `started ${ago(run.startedAt)}`,
  ].filter(Boolean);
  // Like Overview: a run still going has no final rate yet.
  const rate = isRunning(run) ? null : run.passRate;
  return (
    <>
      {rate != null && (
        <Box component="span" sx={{ fontWeight: 500, color: rateTone(rate) }}>
          {`${rate}%`}
        </Box>
      )}
      <Box component="span" sx={{ color: "text.secondary" }}>
        {`${rate != null ? " · " : ""}${rest.join(" · ")}`}
      </Box>
    </>
  );
}

LastResult.propTypes = { env: PropTypes.object.isRequired };

function BuildEnvironmentButton() {
  return (
    <Stack direction="row" alignItems="center" spacing={1}>
      <Tooltip arrow title="Coming soon">
        <span>
          <Button size="small" variant="outlined" color="inherit" disabled>
            Build environment
          </Button>
        </span>
      </Tooltip>
      <ComingSoonChip compact />
    </Stack>
  );
}

/** Environments: every environment testing this agent and how it last did. */
export default function AgentEnvironmentsTab() {
  const { agentId, agent } = useAgentRoute();
  const navigate = useNavigate();
  const {
    data: envs,
    isLoading,
    isError,
    error,
  } = useAgentEnvironments(agentId);
  const open = (env) =>
    navigate(paths.dashboard.simulate.environments.detail(env.id));

  if (!isLoading && !isError && !envs?.length) {
    return (
      <EmptyState
        icon="solar:box-linear"
        title="Not used in any environment yet"
        body="An environment gives this agent a world to act in: tools, seeded data, rules and scenarios to run."
        action={<BuildEnvironmentButton />}
      />
    );
  }

  let body;
  if (isError && !envs) {
    body = (
      <Typography
        sx={{ px: 2, py: 1.5, typography: "s2_1", color: "text.secondary" }}
      >
        {error?.message || "Couldn't load environments"}
      </Typography>
    );
  } else if (isLoading) {
    body = (
      <>
        <Row header cells={COLUMNS} template={TEMPLATE} />
        {Array.from({ length: SKELETON_ROWS }, (_, i) => (
          <Row
            // eslint-disable-next-line react/no-array-index-key
            key={i}
            template={TEMPLATE}
            cells={[
              <LineSkeleton
                key="name"
                width={140}
                data-testid="environment-row-skeleton"
              />,
              <LineSkeleton key="testing" width={24} />,
              <LineSkeleton key="last" width={150} />,
              <Skeleton
                key="status"
                variant="rounded"
                width={62}
                height={24}
                sx={{ borderRadius: 999 }}
              />,
              <Skeleton key="open" variant="rounded" width={56} height={24} />,
            ]}
          />
        ))}
      </>
    );
  } else {
    body = (
      <>
        <Row header cells={COLUMNS} template={TEMPLATE} />
        {envs.map((env) => {
          const ahead = productionAhead(env, agent);
          return (
            <Row
              key={env.id}
              template={TEMPLATE}
              onClick={() => open(env)}
              cells={[
                <Box
                  component="span"
                  key="name"
                  sx={{
                    fontWeight: 500,
                    display: "block",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                  }}
                >
                  {env.name}
                </Box>,
                <Box
                  component="span"
                  key="testing"
                  data-testid={`testing-${env.id}`}
                  sx={{ fontFamily: MONO }}
                >
                  {env.agentVersion ? `v${env.agentVersion.number}` : "—"}
                </Box>,
                <Box
                  component="span"
                  key="last"
                  data-testid={`last-result-${env.id}`}
                >
                  <LastResult env={env} />
                </Box>,
                <Stack
                  key="status"
                  direction="row"
                  alignItems="center"
                  spacing={1}
                  sx={{ minWidth: 0 }}
                >
                  <Box
                    data-testid={`env-status-${env.id}`}
                    sx={{ display: "flex", flexShrink: 0 }}
                  >
                    <StatusPill status={env.status} />
                  </Box>
                  {ahead != null && (
                    <Box
                      component="span"
                      data-testid={`production-ahead-${env.id}`}
                      sx={{
                        color: "text.secondary",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                      }}
                    >
                      {`v${ahead} in production`}
                    </Box>
                  )}
                </Stack>,
                <Stack key="open" direction="row" justifyContent="flex-end">
                  <Button
                    size="small"
                    variant="outlined"
                    color="inherit"
                    onClick={(e) => {
                      e.stopPropagation();
                      open(env);
                    }}
                    onKeyDown={(e) => e.stopPropagation()}
                  >
                    Open
                  </Button>
                </Stack>,
              ]}
            />
          );
        })}
      </>
    );
  }

  // Wrapped, so the page's bottom gap falls below the card, not inside it.
  return (
    <Box>
      <Card
        title="Environments testing this agent"
        action={
          <Stack direction="row" alignItems="center" spacing={1.5}>
            <Typography
              noWrap
              sx={{ typography: "s2", color: "text.secondary" }}
            >
              Each environment tests the version it is pinned to
            </Typography>
            <BuildEnvironmentButton />
          </Stack>
        }
      >
        {body}
      </Card>
    </Box>
  );
}
