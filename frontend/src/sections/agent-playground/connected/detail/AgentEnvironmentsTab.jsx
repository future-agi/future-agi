import PropTypes from "prop-types";
import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useSnackbar } from "notistack";
import { Box, Button, Skeleton, Stack, Typography } from "@mui/material";
import {
  useAgentEnvironments,
  useSetEnvironmentPolicy,
} from "src/api/agents/agentDetail";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { paths } from "src/routes/paths";
import { errorMessage } from "src/pages/dashboard/harness/harnessShared";
import EmptyState from "src/sections/simulate/environments/components/EmptyState";
import StatusPill from "src/sections/simulate/environments/components/StatusPill";
import BuildEnvironmentDialog from "./BuildEnvironmentDialog";
import { Card, LineSkeleton, Row } from "./bits";
import { MONO, ago, rateTone } from "./format";
import { isRunning, pairLabel } from "./runCells";
import useAgentRoute from "./useAgentRoute";

const COLUMNS = ["Environment", "Testing", "Last result", "Status", ""];
const TEMPLATE =
  "minmax(0, 1.4fr) minmax(0, 0.6fr) minmax(0, 1.8fr) minmax(0, 1.3fr) 180px";
const SKELETON_ROWS = 3;

// The production version, when it has moved past the one this environment
// is pinned to; null when the environment tests the latest in production.
export const productionAhead = (env, agent) => {
  const pin = env.agentVersion;
  const production = agent?.activeVersionNumber ?? null;
  if (!pin || production == null || production <= pin.number) return null;
  return production;
};

// What the environment does when production moves: follows it, or stays on
// its pin; "behind" when pinned and production has moved past it.
export const policyLabel = (env, agent) => {
  const ahead = productionAhead(env, agent);
  if (env.policy === "pinned") {
    const pin = env.agentVersion
      ? `pinned v${env.agentVersion.number}`
      : "pinned";
    return ahead != null ? `${pin} · v${ahead} in production` : pin;
  }
  return "follows production";
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

function BuildEnvironmentButton({ agent, canEdit }) {
  const [open, setOpen] = useState(false);
  const isCode = agent?.agentKind === "source";
  return (
    <>
      <Button
        size="small"
        variant="outlined"
        color="inherit"
        disabled={!agent || !isCode || !canEdit}
        onClick={() => setOpen(true)}
      >
        Build environment
      </Button>
      {open && agent && (
        <BuildEnvironmentDialog
          agent={agent}
          open
          onClose={() => setOpen(false)}
        />
      )}
    </>
  );
}

BuildEnvironmentButton.propTypes = {
  agent: PropTypes.object,
  canEdit: PropTypes.bool,
};

/** Environments: every environment testing this agent and how it last did. */
export default function AgentEnvironmentsTab() {
  const { agentId, agent } = useAgentRoute();
  const navigate = useNavigate();
  const { enqueueSnackbar } = useSnackbar();
  const { role } = useAuthContext();
  const canEdit = !!RolePermission.SIMULATION_AGENT[PERMISSIONS.UPDATE][role];
  const setPolicy = useSetEnvironmentPolicy(agentId);
  const {
    data: envs,
    isLoading,
    isError,
    error,
  } = useAgentEnvironments(agentId);
  const open = (env) =>
    navigate(paths.dashboard.simulate.environments.detail(env.id));

  const changePolicy = (env, policy) =>
    setPolicy.mutate(
      {
        environmentId: env.id,
        policy,
        versionId: policy === "pinned" ? env.agentVersion?.id : undefined,
      },
      {
        onSuccess: () =>
          enqueueSnackbar(
            policy === "pinned"
              ? `${env.name} stays on v${env.agentVersion?.number}`
              : `${env.name} follows production`,
            { variant: "success" },
          ),
        onError: (e) =>
          enqueueSnackbar(errorMessage(e) || "Couldn't change the policy", {
            variant: "error",
          }),
      },
    );

  if (!isLoading && !isError && !envs?.length) {
    return (
      <EmptyState
        icon="solar:box-linear"
        title="Not used in any environment yet"
        body="An environment gives this agent a world to act in: tools, seeded data, rules and scenarios to run."
        action={<BuildEnvironmentButton agent={agent} canEdit={canEdit} />}
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
          const pinned = env.policy === "pinned";
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
                  <Box
                    component="span"
                    data-testid={`policy-${env.id}`}
                    sx={{
                      color: "text.secondary",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                    }}
                  >
                    {policyLabel(env, agent)}
                  </Box>
                </Stack>,
                <Stack
                  key="open"
                  direction="row"
                  justifyContent="flex-end"
                  spacing={0.75}
                >
                  {canEdit && env.agentVersion && (
                    <Button
                      size="small"
                      variant="outlined"
                      color="inherit"
                      disabled={setPolicy.isPending}
                      onClick={(e) => {
                        e.stopPropagation();
                        changePolicy(env, pinned ? "follow" : "pinned");
                      }}
                      onKeyDown={(e) => e.stopPropagation()}
                    >
                      {pinned
                        ? ahead != null
                          ? "Default to production"
                          : "Follow production"
                        : "Pin"}
                    </Button>
                  )}
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
              Each environment follows production or stays on its pin
            </Typography>
            <BuildEnvironmentButton agent={agent} canEdit={canEdit} />
          </Stack>
        }
      >
        {body}
      </Card>
    </Box>
  );
}
