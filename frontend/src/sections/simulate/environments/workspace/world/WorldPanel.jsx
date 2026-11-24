import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Button, Stack, Tooltip, Typography } from "@mui/material";
import { enqueueSnackbar } from "notistack";
import { ConfirmDialog } from "src/components/custom-dialog";
import Iconify from "src/components/iconify";
import EmptyState from "src/components/empty-state";
import CapabilityGraph from "../overview/CapabilityGraph";
import ConnectOnlyWorld from "./ConnectOnlyWorld";
import ToolsCard from "./ToolsCard";
import ToolDrawer from "./ToolDrawer";
import WorldCard from "./WorldCard";
import WorldFactsCard from "./WorldFactsCard";
import useSimulationAccess from "../../hooks/useSimulationAccess";
import { WORKSPACE_COPY } from "../workspace.constants";
import { toolRemovalInstruction } from "../contract/contractInstructions";
import {
  CONTRACT_EDIT_COPY,
  CONTRACT_LISTS_COPY,
} from "../contract/contract.constants";
import {
  MONO,
  WORLD_COPY,
  WORLD_STRIP_COPY,
  TOOL_DRAWER_COPY,
  hasNoWorld,
  isWritingWorld,
  repoOriginOf,
  toolNamesOf,
  withToolEvidence,
  worldTablesOf,
} from "./world.constants";

const agentLabel = (subject) =>
  subject?.name
    ? [subject.name, subject.active_version].filter(Boolean).join(" ")
    : null;

export default function WorldPanel({ env, envState, graphData, chat = null }) {
  const { canEdit } = useSimulationAccess();
  const [openTool, setOpenTool] = useState(null);
  const [removing, setRemoving] = useState(null);
  const canRemove = typeof chat?.send === "function";
  const blockedReason = !canEdit
    ? WORKSPACE_COPY.viewer.edit
    : !chat?.live
      ? CONTRACT_LISTS_COPY.notLive
      : chat?.busy
        ? CONTRACT_LISTS_COPY.busy
        : null;
  const confirmRemove = () => {
    const name = removing;
    setRemoving(null);
    if (!name || blockedReason) return;
    chat.send(toolRemovalInstruction(name));
    setOpenTool(null);
    enqueueSnackbar(CONTRACT_EDIT_COPY.sent, { variant: "info" });
  };
  const toolStatus = Array.isArray(env?.toolStatus) ? env.toolStatus : [];
  const tables = worldTablesOf(env);
  const tools = withToolEvidence(toolStatus, env);
  const noWorld = hasNoWorld(env) && !tables.length;
  const origin = repoOriginOf(env);
  const toolCount = toolStatus.length || toolNamesOf(env).length;
  const answered = toolStatus.filter((t) => t.status === "simulated").length;
  const showGraph = Boolean(envState?.agent) || toolCount > 0;
  const drawerTool = tools.find((t) => t.tool === openTool) || null;
  const onOpenTool = (tool) =>
    setOpenTool((current) => (current === tool.tool ? null : tool.tool));

  if (!noWorld && !tables.length && !toolStatus.length && !showGraph) {
    return (
      <Box sx={{ p: 2 }}>
        <EmptyState
          icon="solar:global-linear"
          title={WORLD_COPY.empty.title}
          description={WORLD_COPY.empty.description}
        />
      </Box>
    );
  }

  return (
    <Box sx={{ p: 2 }}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{ mb: 2, px: 0.5, minHeight: 24, flexWrap: "wrap", rowGap: 0.75 }}
      >
        {toolStatus.length > 0 && (
          <Typography
            sx={{ typography: "s2", color: "text.secondary", minWidth: 0 }}
          >
            {WORLD_STRIP_COPY.summary(
              agentLabel(env?.testSubject),
              toolStatus.length,
              answered,
            )}
          </Typography>
        )}
        <Box flex={1} />
        {!noWorld && (
          <Tooltip arrow title={WORLD_STRIP_COPY.kindTip}>
            <Stack
              direction="row"
              spacing={0.5}
              alignItems="center"
              sx={{ cursor: "default", flexShrink: 0 }}
            >
              <Iconify
                icon="solar:lock-keyhole-minimalistic-linear"
                width={14}
                sx={{ color: "text.tertiary", flexShrink: 0 }}
              />
              <Typography
                sx={{
                  typography: "s3",
                  fontWeight: 600,
                  color: "text.secondary",
                }}
              >
                {WORLD_STRIP_COPY.kind}
              </Typography>
            </Stack>
          </Tooltip>
        )}
        {origin && (
          <Tooltip arrow title={WORLD_COPY.originTip(origin.repo, origin.ref)}>
            <Stack
              direction="row"
              alignItems="center"
              spacing={0.5}
              sx={{ minWidth: 0, cursor: "default" }}
            >
              <Iconify
                icon="solar:folder-with-files-linear"
                width={14}
                sx={{ color: "text.tertiary", flexShrink: 0 }}
              />
              <Typography
                noWrap
                sx={{
                  typography: "s3",
                  color: "text.tertiary",
                  fontFamily: MONO,
                }}
              >
                {origin.label}
              </Typography>
            </Stack>
          </Tooltip>
        )}
      </Stack>

      {noWorld && <ConnectOnlyWorld env={env} sx={{ mb: 2 }} />}

      {isWritingWorld(env) && !tables.length && (
        <EmptyState
          icon="solar:global-linear"
          title={WORLD_COPY.writing.title}
          description={WORLD_COPY.writing.description}
          sx={{ mb: 2 }}
        />
      )}

      {showGraph && (
        <Box sx={{ mb: 2 }}>
          <CapabilityGraph env={env} envState={envState} data={graphData} />
        </Box>
      )}

      {tables.length > 0 ? (
        <WorldCard
          tables={tables}
          tools={tools}
          totalRows={env.worldTotalRows}
          openTool={openTool}
          onOpenTool={onOpenTool}
          sx={{ mb: 2 }}
        />
      ) : (
        tools.length > 0 && (
          <ToolsCard
            tools={tools}
            openTool={openTool}
            onOpenTool={onOpenTool}
            sx={{ mb: 2 }}
          />
        )
      )}

      {(env?.dataStore || env?.refusalSignature || env?.runtime) && (
        <WorldFactsCard
          dataStore={env.dataStore}
          refusalSignature={env.refusalSignature}
          runtime={env.runtime}
          sx={{ mb: 2 }}
        />
      )}

      <ToolDrawer
        tool={drawerTool}
        onClose={() => setOpenTool(null)}
        onRemove={canRemove ? (tool) => setRemoving(tool.tool) : undefined}
        blockedReason={blockedReason}
        codePending={isWritingWorld(env)}
      />

      <ConfirmDialog
        open={Boolean(removing)}
        onClose={() => setRemoving(null)}
        title={removing ? TOOL_DRAWER_COPY.removeTitle(removing) : ""}
        content={TOOL_DRAWER_COPY.removeContent}
        action={
          <Button variant="contained" color="error" onClick={confirmRemove}>
            {TOOL_DRAWER_COPY.removeConfirm}
          </Button>
        }
      />
    </Box>
  );
}

WorldPanel.propTypes = {
  env: PropTypes.shape({
    tools: PropTypes.arrayOf(PropTypes.shape({ name: PropTypes.string })),
    toolStatus: PropTypes.arrayOf(PropTypes.object),
    worldTables: PropTypes.arrayOf(PropTypes.object),
    worldTotalRows: PropTypes.number,
    dataSchemaColumns: PropTypes.object,
    baseEnvironment: PropTypes.oneOfType([
      PropTypes.object,
      PropTypes.array,
      PropTypes.string,
    ]),
    testSubject: PropTypes.shape({
      name: PropTypes.string,
      active_version: PropTypes.string,
    }),
    toolEntrypoints: PropTypes.arrayOf(PropTypes.object),
    dataStore: PropTypes.object,
    refusalSignature: PropTypes.string,
    runtime: PropTypes.object,
    stores: PropTypes.array,
    detailReady: PropTypes.bool,
    source: PropTypes.shape({
      repository: PropTypes.string,
      ref: PropTypes.string,
      commit_sha: PropTypes.string,
    }),
  }).isRequired,
  envState: PropTypes.shape({
    agent: PropTypes.object,
  }),
  graphData: PropTypes.object,
  chat: PropTypes.shape({
    send: PropTypes.func,
    busy: PropTypes.bool,
    live: PropTypes.bool,
  }),
};
