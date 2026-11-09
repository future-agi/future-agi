import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Autocomplete from "@mui/material/Autocomplete";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import CircularProgress from "@mui/material/CircularProgress";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { useSearchParams } from "react-router-dom";

import { GatewayEmptyState, GatewayStatusChip } from "../../components/frame";
import GatewayAdminOnly from "../../components/GatewayAdminOnly";
import { useGatewayAdmin } from "../../hooks/useGatewayAdmin";
import { formatLatency } from "../../utils/formatters";
import { useMCPTools, useTestMCPTool } from "../hooks/useMCPConfig";
import JsonBlock from "../components/JsonBlock";
import McpIcon from "../components/McpIcon";
import SectionTitle from "../components/SectionTitle";
import { GATEWAY_MONO } from "../../utils/fonts";
import RuntimeNotice from "../runtimeNotice";
import { MCP_PREVIOUS_RESULT } from "../runtimeStrings";

function Panel({ children }) {
  return (
    <Box
      sx={{
        p: 1.5,
        borderRadius: 1,
        border: "1px solid",
        borderColor: "divider",
        bgcolor: "background.paper",
        display: "flex",
        flexDirection: "column",
        gap: 1.25,
        minWidth: 0,
      }}
    >
      {children}
    </Box>
  );
}

Panel.propTypes = { children: PropTypes.node };

function guardTone(value) {
  return value === "pass" ? "healthy" : "error";
}

// Run one of the org's tools through the gateway's admin test path
// (test-mcp-tool) and see its answer. The tools API gives each tool's name
// and server only, so arguments are typed as JSON. A ?tool= link (a server's
// "Try a tool") opens on that tool. Running a tool goes through the
// gateway's admin path: Owner or Admin only (useGatewayAdmin); picking a
// tool and reading it stay open.
const PlaygroundTab = ({ gatewayId, runtimeOff = false }) => {
  const [searchParams] = useSearchParams();
  const { canEdit } = useGatewayAdmin();
  const { data: mcpTools, isLoading } = useMCPTools(gatewayId, {
    enabled: !runtimeOff,
  });
  const testMutation = useTestMCPTool();
  const [selectedTool, setSelectedTool] = useState(
    () => searchParams.get("tool") || null,
  );
  const [argsJson, setArgsJson] = useState("{}");
  const [jsonError, setJsonError] = useState("");
  const [result, setResult] = useState(null);

  const tools = useMemo(() => mcpTools || [], [mcpTools]);
  const toolNames = useMemo(() => tools.map((t) => t.name).sort(), [tools]);
  // A ?tool= link to a tool no server lists (renamed, removed) opens with no
  // tool chosen rather than an invalid value.
  const toolName = toolNames.includes(selectedTool) ? selectedTool : null;
  const selected = tools.find((t) => t.name === toolName);

  const handleExecute = () => {
    if (runtimeOff || !canEdit || !toolName || testMutation.isPending) return;
    let args;
    try {
      args = JSON.parse(argsJson);
      if (!args || typeof args !== "object" || Array.isArray(args)) {
        throw new Error("arguments must be a JSON object");
      }
      setJsonError("");
    } catch (e) {
      setJsonError(`Invalid JSON: ${e.message}`);
      return;
    }
    setResult(null);
    testMutation.mutate(
      { gatewayId, name: toolName, arguments: args },
      {
        onSuccess: (data) => setResult(data),
        onError: (err) =>
          setResult({
            error:
              err?.response?.data?.result ||
              err?.response?.data?.message ||
              err?.message ||
              "Request failed",
            is_error: true,
          }),
      },
    );
  };

  if (!runtimeOff && !isLoading && tools.length === 0) {
    return (
      <GatewayEmptyState
        icon="mcp"
        title="No tools to run"
        description="Tools appear here once the gateway connects to one of the organization's MCP servers."
      />
    );
  }

  return (
    <Box
      sx={{ display: "flex", flexDirection: "column", gap: 2, maxWidth: 960 }}
    >
      {runtimeOff && <RuntimeNotice />}
      <Panel>
        <SectionTitle
          title="Run a tool"
          note="Choose a tool and edit its arguments here. Running a tool requires MCP to be on and an organization Owner or Admin. A run invokes the tool on its server."
        />
        <Autocomplete
          options={toolNames}
          value={toolName}
          onChange={(_, value) => {
            setSelectedTool(value);
            setResult(null);
            setJsonError("");
          }}
          loading={isLoading}
          size="small"
          renderInput={(params) => (
            <TextField {...params} label="Tool" placeholder="Choose a tool" />
          )}
        />
        {selected && (
          <Typography typography="s2" color="text.secondary">
            {`Server ${selected.server_id}${selected.upstream_name ? `, upstream name ${selected.upstream_name}` : ""}${selected.quarantined ? ". Marked quarantined in this inventory." : ""}`}
          </Typography>
        )}
        <TextField
          label="Arguments (JSON)"
          multiline
          minRows={4}
          maxRows={14}
          fullWidth
          value={argsJson}
          onChange={(e) => {
            setArgsJson(e.target.value);
            setJsonError("");
          }}
          error={Boolean(jsonError)}
          helperText={
            jsonError || 'An object, for example {"query": "refund"}.'
          }
          InputProps={{ sx: { fontFamily: GATEWAY_MONO, fontSize: 13 } }}
        />
        <Box sx={{ display: "flex", justifyContent: "flex-end" }}>
          <GatewayAdminOnly allowed={canEdit}>
            <Button
              variant="contained"
              color="primary"
              size="small"
              startIcon={
                testMutation.isPending ? (
                  <CircularProgress size={14} color="inherit" />
                ) : (
                  <McpIcon name="play" size={14} />
                )
              }
              onClick={handleExecute}
              disabled={
                runtimeOff || !toolName || testMutation.isPending || !canEdit
              }
            >
              {testMutation.isPending ? "Running" : "Run"}
            </Button>
          </GatewayAdminOnly>
        </Box>
      </Panel>

      {result && (
        <Panel>
          <SectionTitle title={runtimeOff ? MCP_PREVIOUS_RESULT : "Result"} />
          <Box sx={{ display: "flex", gap: 1, flexWrap: "wrap" }}>
            <GatewayStatusChip
              tone={result.is_error || result.error ? "error" : "healthy"}
              label={result.is_error || result.error ? "Error" : "Success"}
            />
            {result.duration_ms != null && (
              <GatewayStatusChip
                tone="neutral"
                label={formatLatency(result.duration_ms)}
                showIcon={false}
              />
            )}
            {result.guardrail_pre && result.guardrail_pre !== "skipped" && (
              <GatewayStatusChip
                tone={guardTone(result.guardrail_pre)}
                label={`Operator guard, pre: ${result.guardrail_pre}`}
              />
            )}
            {result.guardrail_post && result.guardrail_post !== "skipped" && (
              <GatewayStatusChip
                tone={guardTone(result.guardrail_post)}
                label={`Operator guard, post: ${result.guardrail_post}`}
              />
            )}
          </Box>
          <Typography typography="s2" color="text.secondary">
            {
              "The guard checks are the gateway operator's. The organization's MCP guardrails are not applied on this gateway."
            }
          </Typography>
          {result.error && (
            <Alert severity="error">{String(result.error)}</Alert>
          )}
          {(result.content || []).map((part, index) => (
            <JsonBlock
              // Content parts have no id; their order is the answer's.
              // eslint-disable-next-line react/no-array-index-key
              key={index}
              value={part.type === "text" ? part.text : part}
              maxHeight={400}
              label={`Result part ${index + 1}`}
            />
          ))}
        </Panel>
      )}
    </Box>
  );
};

PlaygroundTab.propTypes = {
  runtimeOff: PropTypes.bool,
  gatewayId: PropTypes.string,
};

export default PlaygroundTab;
