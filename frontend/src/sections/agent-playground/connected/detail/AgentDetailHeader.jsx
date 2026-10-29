import PropTypes from "prop-types";
import React from "react";
import {
  Box,
  Button,
  IconButton,
  Skeleton,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { sourceLabel } from "../connectedAgentLabels";
import AgentMoreMenu from "./AgentMoreMenu";
import { LineSkeleton, Pill } from "./bits";
import { MONO, ago } from "./format";

const environmentsText = (count) => {
  if (count === 0) return "No environment yet";
  return `${count} environment${count === 1 ? "" : "s"}`;
};

// How a new version would arrive from each source. None of them is wired
// yet, so the button says what it will do and stays disabled.
export const newVersionAction = (source) => {
  if (source === "archive") {
    return { label: "Upload new version", icon: "solar:cloud-upload-linear" };
  }
  if (source === "github" || source === "vapi" || source === "retell") {
    return { label: "Sync", icon: "solar:refresh-linear" };
  }
  return { label: "Add version", icon: "solar:add-circle-linear" };
};

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
  const isCode = agent.agentKind === "source";
  const { source } = agent;
  const where = isCode
    ? source
      ? sourceLabel(source)
      : null
    : agent.assistantId;
  const envs = environmentsText(agent.environmentCount);
  const next = newVersionAction(source);
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
  ].filter(Boolean);

  return (
    <Stack direction="row" alignItems="center" spacing={1.5} sx={{ mb: 1.5 }}>
      <IconButton size="small" onClick={onBack} aria-label="Back">
        <Iconify icon="solar:alt-arrow-left-linear" width={18} />
      </IconButton>
      <Box flex={1} minWidth={0}>
        <Stack direction="row" alignItems="center" spacing={1}>
          <Typography
            component="h1"
            noWrap
            sx={{ typography: "s1_2", fontWeight: 600 }}
          >
            {agent.name}
          </Typography>
          {source && <Pill>{sourceLabel(source)}</Pill>}
        </Stack>
        <Typography
          data-testid="agent-detail-subline"
          noWrap
          sx={{ typography: "s2", color: "text.secondary", mt: 0.25 }}
        >
          {pieces.map((p, i) => (
            // eslint-disable-next-line react/no-array-index-key
            <React.Fragment key={i}>
              {i > 0 && " · "}
              {p}
            </React.Fragment>
          ))}
        </Typography>
      </Box>
      {action}
      <Tooltip arrow title="Coming soon">
        <span>
          <Button
            variant="outlined"
            size="small"
            color="inherit"
            disabled
            startIcon={<Iconify icon={next.icon} width={16} />}
          >
            {next.label}
          </Button>
        </span>
      </Tooltip>
      <AgentMoreMenu agent={agent} />
    </Stack>
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
    <Stack
      data-testid="agent-header-skeleton"
      direction="row"
      alignItems="center"
      spacing={1.5}
      sx={{ mb: 1.5 }}
    >
      <IconButton size="small" onClick={onBack} aria-label="Back">
        <Iconify icon="solar:alt-arrow-left-linear" width={18} />
      </IconButton>
      <Box flex={1} minWidth={0}>
        <Stack direction="row" alignItems="center" spacing={1}>
          <Typography sx={{ typography: "s1_2" }}>
            <LineSkeleton width={240} />
          </Typography>
          <Skeleton variant="rounded" width={48} height={20} />
        </Stack>
        <Typography sx={{ typography: "s2", mt: 0.25 }}>
          <LineSkeleton width={220} />
        </Typography>
      </Box>
      <Skeleton variant="rounded" width={140} height={32} />
      <Skeleton variant="rounded" width={88} height={32} />
      <Skeleton variant="circular" width={28} height={28} />
    </Stack>
  );
}

AgentDetailHeaderSkeleton.propTypes = { onBack: PropTypes.func.isRequired };
