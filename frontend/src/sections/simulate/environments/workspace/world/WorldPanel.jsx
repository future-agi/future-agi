import PropTypes from "prop-types";
import { useState } from "react";
import { Box, ButtonBase, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import {
  SAMPLE_AGENT_VERSION,
  SAMPLE_SCHEMA,
  SAMPLE_WORLD_TOOLS,
} from "src/api/simulate-environments/_fixtures/detail/world";
import useUiSample from "../../hooks/useUiSample";
import CapabilityGraph from "../overview/CapabilityGraph";
import { RUN_MODE_CHIP } from "../workspace.constants";
import SchemaCard, { schemaRows } from "./SchemaCard";
import ToolsCard, { NoToolsCard } from "./ToolsCard";
import ScenarioChecksCard from "./ScenarioChecksCard";
import AddMcpToolsDrawer from "./AddMcpToolsDrawer";

const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;

const entrypointsOf = (env) => {
  const out = {};
  for (const e of env?.toolEntrypoints || []) if (e?.tool) out[e.tool] = e;
  return out;
};

// The World tab, as Vel lays it out: what the agent calls, the capability
// graph folded, the world's tables, its tools, and the checks that prove a
// scenario. The tools, their arguments, where each runs, the tables and the
// graph are real; tiers, faults, handler code and checks are not in the API,
// so they are soon chips, and ?uiSample=world shows Vel's.
export default function WorldPanel({ env, envState, onGo, graphData }) {
  const sample = useUiSample("world");
  const [mcpOpen, setMcpOpen] = useState(false);
  const openMcp = () => setMcpOpen(true);

  const realTools = env?.tools || [];
  const tools = sample ? SAMPLE_WORLD_TOOLS : realTools;
  const schema = sample ? SAMPLE_SCHEMA : schemaRows(env);
  const version = sample ? SAMPLE_AGENT_VERSION : env?.testSubject?.active_version;
  const agentName = envState?.agent?.name || env?.testSubject?.name || null;
  const hasWorld = !!envState?.agent || realTools.length > 0;
  const mode = RUN_MODE_CHIP[env?.agentMode];

  const summary = sample
    ? `Agent ${version} calls ${plural(tools.length, "tool")}. The world answers every one.`
    : version
      ? `Agent ${version} calls ${plural(tools.length, "tool")}.`
      : tools.length
        ? `No agent connected yet. The world answers the ${plural(tools.length, "tool")} it was built for.`
        : "No agent connected yet, and no tools in the world yet.";

  return (
    <Box sx={{ p: 2 }}>
      <Stack direction="row" alignItems="center" spacing={1.5} sx={{ mb: 2, px: 0.5, minHeight: 24, flexWrap: "wrap", rowGap: 0.75 }}>
        <Typography sx={{ typography: "s2", color: "text.secondary", minWidth: 0 }}>{summary}</Typography>
        <ButtonBase
          onClick={openMcp}
          sx={{
            typography: "s2",
            fontWeight: 600,
            color: "text.primary",
            textDecoration: "underline",
            textUnderlineOffset: 3,
            textDecorationColor: (t) => t.palette.text.disabled,
            borderRadius: "4px",
            flexShrink: 0,
            "&:hover": { textDecorationColor: "currentColor" },
          }}
        >
          Add tools from an MCP server
        </ButtonBase>
        <Box flex={1} />
        {mode && (
          <CustomTooltip show arrow size="small" title={mode.tip}>
            <Stack direction="row" alignItems="center" spacing={0.5} sx={{ cursor: "default" }}>
              <Iconify icon={mode.icon} width={14} sx={{ color: "text.subtitle" }} />
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{mode.label}</Typography>
            </Stack>
          </CustomTooltip>
        )}
      </Stack>

      {/* Folded to one line until asked for; the graph folds itself. */}
      {hasWorld && (
        <Box sx={{ mb: 2 }}>
          <CapabilityGraph env={env} envState={envState} data={graphData} />
        </Box>
      )}

      <Stack spacing={2}>
        {schema.length > 0 && <SchemaCard schema={schema} sample={sample} />}
        {tools.length > 0 ? (
          <ToolsCard key={sample ? "sample" : "real"} tools={tools} entrypoints={entrypointsOf(env)} sample={sample} onAddMcp={openMcp} />
        ) : (
          <NoToolsCard agentName={agentName} onAddMcp={openMcp} />
        )}
        <ScenarioChecksCard sample={sample} onGo={onGo} />
      </Stack>

      <AddMcpToolsDrawer open={mcpOpen} onClose={() => setMcpOpen(false)} sample={sample} />
    </Box>
  );
}

WorldPanel.propTypes = {
  env: PropTypes.object.isRequired,
  envState: PropTypes.object,
  onGo: PropTypes.func,
  graphData: PropTypes.object,
};
