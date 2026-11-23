import PropTypes from "prop-types";
import { Box, Stack, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import EmptyState from "src/components/empty-state";
import CapabilityGraph from "../overview/CapabilityGraph";
import ConnectOnlyWorld from "./ConnectOnlyWorld";
import ToolsCard from "./ToolsCard";
import WorldFactsCard from "./WorldFactsCard";
import WorldMapCard from "./WorldMapCard";
import {
  MONO,
  WORLD_COPY,
  hasNoWorld,
  repoOriginOf,
  toolNamesOf,
  withToolEvidence,
  withTypedColumns,
} from "./world.constants";

export default function WorldPanel({ env, envState, graphData }) {
  const toolStatus = Array.isArray(env?.toolStatus) ? env.toolStatus : [];
  const tables = withTypedColumns(
    Array.isArray(env?.worldTables) ? env.worldTables : [],
    env?.dataSchemaColumns,
  );
  const tools = withToolEvidence(toolStatus, env);
  const hasFacts = Boolean(
    env?.dataStore || env?.refusalSignature || env?.runtime,
  );
  const noWorld = hasNoWorld(env);
  const origin = repoOriginOf(env);
  const toolCount = toolStatus.length || toolNamesOf(env).length;
  const answered = toolStatus.filter((t) => t.status === "simulated").length;
  const showGraph = Boolean(envState?.agent) || toolCount > 0;

  if (
    !noWorld &&
    !tables.length &&
    !toolStatus.length &&
    !showGraph &&
    !hasFacts
  ) {
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
            sx={{ typography: "s1", color: "text.secondary", minWidth: 0 }}
          >
            {WORLD_COPY.summary(toolStatus.length, answered)}
          </Typography>
        )}
        <Box flex={1} />
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

      {showGraph && (
        <Box sx={{ mb: 2 }}>
          <CapabilityGraph env={env} envState={envState} data={graphData} />
        </Box>
      )}

      {tables.length > 0 && (
        <WorldMapCard
          tables={tables}
          totalRows={env.worldTotalRows}
          tools={toolStatus}
          sx={{ mb: 2 }}
        />
      )}

      {hasFacts && (
        <WorldFactsCard
          dataStore={env.dataStore}
          refusalSignature={env.refusalSignature}
          runtime={env.runtime}
          sx={{ mb: 2 }}
        />
      )}

      {tools.length > 0 && <ToolsCard tools={tools} sx={{ mb: 2 }} />}
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
    dataStore: PropTypes.object,
    refusalSignature: PropTypes.string,
    runtime: PropTypes.object,
    toolEntrypoints: PropTypes.arrayOf(PropTypes.object),
    stores: PropTypes.array,
    detailReady: PropTypes.bool,
    source: PropTypes.shape({
      repository: PropTypes.string,
      ref: PropTypes.string,
    }),
  }).isRequired,
  envState: PropTypes.shape({
    agent: PropTypes.object,
  }),
  graphData: PropTypes.object,
};
