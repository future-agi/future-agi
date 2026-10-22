import PropTypes from "prop-types";
import React from "react";
import { useNavigate } from "react-router-dom";
import { Box, Skeleton, Typography } from "@mui/material";
import { useAgentEnvironments } from "src/api/agents/agentDetail";
import { paths } from "src/routes/paths";
import StatusPill from "src/sections/simulate/environments/components/StatusPill";
import { Card, LineSkeleton, Row } from "./bits";
import { MONO, ago, rateTone } from "./format";
import { isRunning } from "./runCells";
import useAgentRoute from "./useAgentRoute";

const COLUMNS = ["Environment", "Status", "Testing", "Last result"];
const SKELETON_ROWS = 3;

function LastResult({ env }) {
  const run = env.lastRun;
  if (!run) {
    return (
      <Box component="span" sx={{ color: "text.subtitle" }}>
        No runs yet
      </Box>
    );
  }
  const rest = [
    run.agentVersion ? `agent v${run.agentVersion.number}` : null,
    run.completedAt ? ago(run.completedAt) : `started ${ago(run.startedAt)}`,
  ].filter(Boolean);
  // Like Overview: a run still going has no final rate yet.
  const rate = isRunning(run) ? null : run.passRate;
  return (
    <>
      {rate != null && (
        <Box component="span" sx={{ fontWeight: 700, color: rateTone(rate) }}>
          {`${rate}%`}
        </Box>
      )}
      <Box component="span" sx={{ color: "text.subtitle" }}>
        {`${rate != null ? " · " : ""}${rest.join(" · ")}`}
      </Box>
    </>
  );
}

LastResult.propTypes = { env: PropTypes.object.isRequired };

/** Environments: every environment testing this agent and how it last did. */
export default function AgentEnvironmentsTab() {
  const { agentId } = useAgentRoute();
  const navigate = useNavigate();
  const {
    data: envs,
    isLoading,
    isError,
    error,
  } = useAgentEnvironments(agentId);

  let body;
  if (isError && !envs) {
    body = (
      <Typography
        sx={{ px: 2, py: 1.5, typography: "s2", color: "text.secondary" }}
      >
        {error?.message || "Couldn't load environments"}
      </Typography>
    );
  } else if (isLoading) {
    body = (
      <>
        <Row header cells={COLUMNS} />
        {Array.from({ length: SKELETON_ROWS }, (_, i) => (
          <Row
            // eslint-disable-next-line react/no-array-index-key
            key={i}
            cells={[
              <LineSkeleton
                key="name"
                width={140}
                data-testid="environment-row-skeleton"
              />,
              <Skeleton
                key="status"
                variant="rounded"
                width={62}
                height={24}
                sx={{ borderRadius: 999 }}
              />,
              <LineSkeleton key="testing" width={24} />,
              <LineSkeleton key="last" width={150} />,
            ]}
          />
        ))}
      </>
    );
  } else if (!envs?.length) {
    body = (
      <Typography
        sx={{ px: 2, py: 1.5, typography: "s2", color: "text.secondary" }}
      >
        No environments test this agent yet
      </Typography>
    );
  } else {
    body = (
      <>
        <Row header cells={COLUMNS} />
        {envs.map((env) => (
          <Row
            key={env.id}
            onClick={() =>
              navigate(paths.dashboard.simulate.environments.detail(env.id))
            }
            cells={[
              <Box component="span" key="name" sx={{ fontWeight: 600 }}>
                {env.name}
              </Box>,
              <Box
                key="status"
                data-testid={`env-status-${env.id}`}
                sx={{ display: "flex" }}
              >
                <StatusPill status={env.status} />
              </Box>,
              <Box component="span" key="testing" sx={{ fontFamily: MONO }}>
                {env.lastRun?.agentVersion
                  ? `v${env.lastRun.agentVersion.number}`
                  : "—"}
              </Box>,
              <Box
                component="span"
                key="last"
                data-testid={`last-result-${env.id}`}
              >
                <LastResult env={env} />
              </Box>,
            ]}
          />
        ))}
      </>
    );
  }

  // Wrapped, so the page's bottom gap falls below the card, not inside it.
  return (
    <Box>
      <Card title="Environments testing this agent">{body}</Card>
    </Box>
  );
}
