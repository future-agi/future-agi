import PropTypes from "prop-types";
import React, { useState } from "react";
import { useSnackbar } from "notistack";
import { useNavigate } from "react-router-dom";
import { Box, Button, IconButton, Skeleton, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import PageHeader from "src/components/page-header";
import CustomTooltip from "src/components/tooltip";
import { useChangeVersion } from "src/api/agents/agentDetail";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { errorMessage } from "src/pages/dashboard/harness/harnessShared";
import { paths } from "src/routes/paths";
import { sourceLabel } from "../connectedAgentLabels";
import AddVersionDialog from "./AddVersionDialog";
import SyncAgentDialog from "./SyncAgentDialog";
import UploadVersionDialog from "./UploadVersionDialog";
import { newVersionAction } from "./newVersionAction";
import AgentMoreMenu from "./AgentMoreMenu";
import { LineSkeleton, Pill } from "./bits";
import { MONO, ago } from "./format";

const environmentsText = (count) => {
  if (count === 0) return "No environment yet";
  return `${count} environment${count === 1 ? "" : "s"}`;
};

function NewVersionButton({ agent }) {
  const { enqueueSnackbar } = useSnackbar();
  const { role } = useAuthContext();
  const canEdit = !!RolePermission.SIMULATION_AGENT[PERMISSIONS.UPDATE][role];
  const change = useChangeVersion(agent.id);
  const [adding, setAdding] = useState(false);
  const next = newVersionAction(agent);

  const sync = () =>
    change.mutate(
      { kind: "sync" },
      {
        onSuccess: (data) => {
          const label = data.version.version_name_display;
          enqueueSnackbar(
            data.up_to_date
              ? `${label} is already at the latest commit`
              : `${label} created; it is being checked`,
            { variant: data.up_to_date ? "info" : "success" },
          );
        },
        onError: (e) => enqueueSnackbar(errorMessage(e), { variant: "error" }),
      },
    );

  const disabled = !next.kind || !canEdit || change.isPending;
  const reason = !next.kind
    ? "Coming soon"
    : !canEdit
      ? "You don't have permission to add versions."
      : next.hint;
  const button = (
    <Button
      variant="outlined"
      size="small"
      color="inherit"
      disabled={disabled}
      startIcon={<Iconify icon={next.icon} width={16} />}
      onClick={() => (next.kind === "sync" ? sync() : setAdding(true))}
    >
      {next.label}
    </Button>
  );
  return (
    <>
      <CustomTooltip show={!!reason} title={reason} size="small" arrow>
        <span>{button}</span>
      </CustomTooltip>
      {adding && next.kind === "upload" && (
        <UploadVersionDialog
          agent={agent}
          open
          onClose={() => setAdding(false)}
        />
      )}
      {adding && next.kind === "manual" && (
        <AddVersionDialog agent={agent} open onClose={() => setAdding(false)} />
      )}
      {adding && next.kind === "edit" && (
        <SyncAgentDialog agent={agent} onClose={() => setAdding(false)} />
      )}
    </>
  );
}

NewVersionButton.propTypes = { agent: PropTypes.object.isRequired };

/**
 * Back, name and source pill; then where the agent lives · when it was
 * connected · how many environments test it. On the right: the version the
 * page shows, how a new version arrives (coming soon) and the More menu.
 */
export default function AgentDetailHeader({
  agent,
  onBack,
  onOpenEnvironments,
  action,
}) {
  const navigate = useNavigate();
  const isCode = agent.agentKind === "source";
  const { source, managedBy } = agent;
  const openManager = () =>
    navigate(paths.dashboard.simulate.environments.detail(managedBy.id));
  const where = isCode
    ? source
      ? sourceLabel(source)
      : null
    : agent.assistantId;
  const envs = environmentsText(agent.environmentCount);
  const pieces = [
    where && (
      <Box
        component="span"
        key="where"
        sx={isCode ? undefined : { fontFamily: MONO }}
      >
        {where}
      </Box>
    ),
    agent.createdAt && `Connected ${ago(agent.createdAt)}`,
    envs &&
      (onOpenEnvironments && agent.environmentCount ? (
        <Box
          component="span"
          key="envs"
          role="link"
          tabIndex={0}
          onClick={onOpenEnvironments}
          onKeyDown={(e) => e.key === "Enter" && onOpenEnvironments()}
          sx={{
            cursor: "pointer",
            "&:hover": { color: "text.primary", textDecoration: "underline" },
          }}
        >
          {envs}
        </Box>
      ) : (
        envs
      )),
    managedBy && (
      <Box
        component="span"
        key="managed"
        role="link"
        tabIndex={0}
        data-testid="agent-managed-by"
        onClick={openManager}
        onKeyDown={(e) => e.key === "Enter" && openManager()}
        sx={{
          cursor: "pointer",
          "&:hover": { color: "text.primary", textDecoration: "underline" },
        }}
      >
        {`Managed by ${managedBy.name}`}
      </Box>
    ),
  ].filter(Boolean);

  return (
    <PageHeader
      back={
        <IconButton size="small" onClick={onBack} aria-label="Back">
          <Iconify icon="solar:alt-arrow-left-linear" width={20} />
        </IconButton>
      }
      title={agent.name}
      badge={source ? <Pill>{sourceLabel(source)}</Pill> : null}
      description={
        <Box component="span" data-testid="agent-detail-subline">
          {pieces.map((p, i) => (
            // eslint-disable-next-line react/no-array-index-key
            <React.Fragment key={i}>
              {i > 0 && " · "}
              {p}
            </React.Fragment>
          ))}
        </Box>
      }
      actions={
        <>
          {action}
          <NewVersionButton agent={agent} />
          <AgentMoreMenu agent={agent} />
        </>
      }
    />
  );
}

AgentDetailHeader.propTypes = {
  agent: PropTypes.object.isRequired,
  onBack: PropTypes.func.isRequired,
  onOpenEnvironments: PropTypes.func,
  action: PropTypes.node,
};

/** The header while the agent loads: Back works, the rest holds its place. */
export function AgentDetailHeaderSkeleton({ onBack }) {
  return (
    <Box data-testid="agent-header-skeleton">
      <PageHeader
        back={
          <IconButton size="small" onClick={onBack} aria-label="Back">
            <Iconify icon="solar:alt-arrow-left-linear" width={20} />
          </IconButton>
        }
        title={
          <Typography component="span" sx={{ typography: "m1" }}>
            <LineSkeleton width={240} />
          </Typography>
        }
        badge={<Skeleton variant="rounded" width={48} height={20} />}
        description={<LineSkeleton width={220} />}
        actions={
          <>
            <Skeleton variant="rounded" width={140} height={32} />
            <Skeleton variant="rounded" width={88} height={32} />
            <Skeleton variant="circular" width={28} height={28} />
          </>
        }
      />
    </Box>
  );
}

AgentDetailHeaderSkeleton.propTypes = { onBack: PropTypes.func.isRequired };
