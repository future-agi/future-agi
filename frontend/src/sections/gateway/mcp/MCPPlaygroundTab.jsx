/* eslint-disable react/prop-types */
import React, { useState, useMemo } from "react";
import {
  Alert,
  Autocomplete,
  Box,
  Button,
  Chip,
  CircularProgress,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Section from "src/components/section";
import Iconify from "src/components/iconify";
import { useTestMCPTool } from "./hooks/useMCPConfig";

const MCPPlaygroundTab = ({ mcpTools, gatewayId }) => {
  const testMutation = useTestMCPTool();
  const [selectedTool, setSelectedTool] = useState(null);
  const [argsJson, setArgsJson] = useState("{}");
  const [jsonError, setJsonError] = useState("");
  const [result, setResult] = useState(null);

  const tools = useMemo(() => mcpTools || [], [mcpTools]);
  const toolNames = useMemo(() => tools.map((t) => t.name).sort(), [tools]);

  const selectedToolObj = useMemo(
    () => tools.find((t) => t.name === selectedTool),
    [tools, selectedTool],
  );

  const handleToolSelect = (_, value) => {
    setSelectedTool(value);
    setResult(null);
    setJsonError("");
    // Pre-fill arguments from schema.
    if (value) {
      const tool = tools.find((t) => t.name === value);
      if (tool?.input_schema) {
        try {
          const schema =
            typeof tool.input_schema === "string"
              ? JSON.parse(tool.input_schema)
              : tool.input_schema;
          if (schema.properties) {
            const defaults = {};
            for (const [key, prop] of Object.entries(schema.properties)) {
              if (prop.type === "string") defaults[key] = "";
              else if (prop.type === "number" || prop.type === "integer")
                defaults[key] = 0;
              else if (prop.type === "boolean") defaults[key] = false;
              else if (prop.type === "array") defaults[key] = [];
              else if (prop.type === "object") defaults[key] = {};
              else defaults[key] = null;
            }
            setArgsJson(JSON.stringify(defaults, null, 2));
            return;
          }
        } catch {
          // ignore
        }
      }
      setArgsJson("{}");
    }
  };

  const handleExecute = () => {
    let args;
    try {
      args = JSON.parse(argsJson);
      setJsonError("");
    } catch (e) {
      setJsonError("Invalid JSON: " + e.message);
      return;
    }

    testMutation.mutate(
      { gatewayId, name: selectedTool, arguments: args },
      {
        onSuccess: (data) => setResult(data),
        onError: (err) => {
          setResult({
            error:
              err?.response?.data?.message || err.message || "Request failed",
            is_error: true,
          });
        },
      },
    );
  };

  const schemaStr = useMemo(() => {
    if (!selectedToolObj?.input_schema) return null;
    try {
      const schema =
        typeof selectedToolObj.input_schema === "string"
          ? JSON.parse(selectedToolObj.input_schema)
          : selectedToolObj.input_schema;
      return JSON.stringify(schema, null, 2);
    } catch {
      return String(selectedToolObj.input_schema);
    }
  }, [selectedToolObj]);

  return (
    <Box>
      <Stack spacing={3}>
        <Section variant="panel">
          <Typography variant="subtitle1" fontWeight={600} gutterBottom>
            Tool Selection
          </Typography>
          <Autocomplete
            options={toolNames}
            value={selectedTool}
            onChange={handleToolSelect}
            renderInput={(params) => (
              <TextField
                {...params}
                size="small"
                placeholder="Select a tool to test..."
              />
            )}
          />
          {selectedToolObj && (
            <Stack spacing={1} sx={{ mt: 2 }}>
              <Typography variant="body2" color="text.secondary">
                {selectedToolObj.description || "No description"}
              </Typography>
              <Stack direction="row" spacing={1}>
                <Chip
                  label={selectedToolObj.server}
                  size="small"
                  color="primary"
                  variant="outlined"
                />
                {selectedToolObj.deprecated && (
                  <Chip label="Deprecated" size="small" color="warning" />
                )}
                {selectedToolObj.version && (
                  <Typography variant="caption" color="text.tertiary">
                    {`v${selectedToolObj.version}`}
                  </Typography>
                )}
              </Stack>
            </Stack>
          )}
        </Section>

        {selectedTool && (
          <>
            {schemaStr && (
              <Section variant="panel">
                <Typography variant="subtitle1" fontWeight={600} gutterBottom>
                  Input Schema
                </Typography>
                <Box
                  sx={{
                    p: 1.5,
                    borderRadius: 1,
                    bgcolor: "background.neutral",
                    fontFamily: "monospace",
                    fontSize: 12,
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-all",
                    maxHeight: 200,
                    overflow: "auto",
                  }}
                >
                  {schemaStr}
                </Box>
              </Section>
            )}

            <Section variant="panel">
              <Typography variant="subtitle1" fontWeight={600} gutterBottom>
                Arguments (JSON)
              </Typography>
              <TextField
                multiline
                minRows={4}
                maxRows={12}
                fullWidth
                value={argsJson}
                onChange={(e) => {
                  setArgsJson(e.target.value);
                  setJsonError("");
                }}
                error={Boolean(jsonError)}
                helperText={jsonError}
                InputProps={{
                  sx: { fontFamily: "monospace", fontSize: 13 },
                }}
              />
              <Box sx={{ mt: 2, display: "flex", justifyContent: "flex-end" }}>
                <Button
                  variant="contained"
                  startIcon={
                    testMutation.isPending ? (
                      <CircularProgress size={16} color="inherit" />
                    ) : (
                      <Iconify icon="solar:play-linear" width={20} />
                    )
                  }
                  onClick={handleExecute}
                  disabled={testMutation.isPending}
                >
                  {testMutation.isPending ? "Executing..." : "Execute"}
                </Button>
              </Box>
            </Section>

            {result && (
              <Section variant="panel">
                <Typography variant="subtitle1" fontWeight={600} gutterBottom>
                  Result
                </Typography>

                <Stack spacing={2}>
                  <Stack direction="row" spacing={1} flexWrap="wrap">
                    {result.duration_ms != null && (
                      <Chip
                        label={`${Number(result.duration_ms).toFixed(1)} ms`}
                        size="small"
                        color="info"
                        variant="outlined"
                      />
                    )}
                    {result.server && (
                      <Chip
                        label={result.server}
                        size="small"
                        variant="outlined"
                      />
                    )}
                    {result.guardrail_pre &&
                      result.guardrail_pre !== "skipped" && (
                        <Chip
                          label={`Pre: ${result.guardrail_pre}`}
                          size="small"
                          color={
                            result.guardrail_pre === "pass"
                              ? "success"
                              : "error"
                          }
                          variant="outlined"
                        />
                      )}
                    {result.guardrail_post &&
                      result.guardrail_post !== "skipped" && (
                        <Chip
                          label={`Post: ${result.guardrail_post}`}
                          size="small"
                          color={
                            result.guardrail_post === "pass"
                              ? "success"
                              : "error"
                          }
                          variant="outlined"
                        />
                      )}
                    {result.is_error && (
                      <Chip label="Error" size="small" color="error" />
                    )}
                  </Stack>

                  {result.error && (
                    <Alert severity="error">{result.error}</Alert>
                  )}

                  {result.content && result.content.length > 0 && (
                    <Box>
                      {result.content.map((part, i) => (
                        <Box
                          key={i}
                          sx={{
                            p: 1.5,
                            borderRadius: 1,
                            bgcolor: "background.neutral",
                            fontFamily: "monospace",
                            fontSize: 12,
                            whiteSpace: "pre-wrap",
                            wordBreak: "break-all",
                            maxHeight: 400,
                            overflow: "auto",
                            mb: 1,
                          }}
                        >
                          {part.type === "text"
                            ? part.text
                            : JSON.stringify(part, null, 2)}
                        </Box>
                      ))}
                    </Box>
                  )}
                </Stack>
              </Section>
            )}
          </>
        )}

        {!selectedTool && (
          <Section variant="panel">
            <Typography
              variant="body2"
              color="text.secondary"
              align="center"
              sx={{ py: 4 }}
            >
              Select a tool above to test it. Arguments will be pre-filled from
              the tool&apos;s input schema.
            </Typography>
          </Section>
        )}
      </Stack>
    </Box>
  );
};

export default MCPPlaygroundTab;
