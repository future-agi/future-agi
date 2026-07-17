import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import LoadingButton from "@mui/lab/LoadingButton";
import Editor from "@monaco-editor/react";
import { Controller, useFormContext, useWatch } from "react-hook-form";
import { enqueueSnackbar } from "notistack";
import { useTestNodeExecution } from "src/api/agent-playground/agent-playground";
import {
  CODE_EXECUTION_DEFAULT_CONFIG,
  CODE_EXECUTION_LANGUAGES,
} from "../../../utils/constants";
import { useAgentPlaygroundStoreShallow } from "../../../store";
import usePartialNodeUpdate from "../../hooks/usePartialNodeUpdate";
import { useSaveDraftContext } from "../../saveDraftContext";
import { getCodeExecutionEditorLanguage } from "./codeExecutionNodeFormUtils";

function buildConfig(values) {
  return {
    ...CODE_EXECUTION_DEFAULT_CONFIG,
    language: values.language ?? CODE_EXECUTION_DEFAULT_CONFIG.language,
    code: values.code ?? CODE_EXECUTION_DEFAULT_CONFIG.code,
    timeout_ms: Number(
      values.timeout_ms ?? CODE_EXECUTION_DEFAULT_CONFIG.timeout_ms,
    ),
    memory_mb: Number(
      values.memory_mb ?? CODE_EXECUTION_DEFAULT_CONFIG.memory_mb,
    ),
  };
}

export default function CodeExecutionNodeForm({ nodeId }) {
  const { control, handleSubmit, getValues } = useFormContext();
  const [testResult, setTestResult] = useState(null);
  const language = useWatch({ control, name: "language" }) || "python";

  const { currentAgent, updateNodeData, clearSelectedNode } =
    useAgentPlaygroundStoreShallow((state) => ({
      currentAgent: state.currentAgent,
      updateNodeData: state.updateNodeData,
      clearSelectedNode: state.clearSelectedNode,
    }));
  const { partialUpdate, isPending } = usePartialNodeUpdate();
  const { ensureDraft } = useSaveDraftContext();
  const { mutateAsync: testNode, isPending: isTesting } =
    useTestNodeExecution();

  const editorLanguage = getCodeExecutionEditorLanguage(language);
  const [testInputs, setTestInputs] = useState("{}");

  const saveConfig = handleSubmit(async (values) => {
    const config = buildConfig(values);
    updateNodeData(nodeId, { config });

    const draftResult = await ensureDraft({ skipDirtyCheck: true });
    if (draftResult === false) return;

    if (draftResult !== "created") {
      try {
        await partialUpdate(nodeId, { config });
      } catch {
        enqueueSnackbar("Failed to save code node", { variant: "error" });
        return;
      }
    }

    clearSelectedNode();
  });

  const runTest = async () => {
    const config = buildConfig(getValues());
    let inputs;
    try {
      inputs = JSON.parse(testInputs || "{}");
    } catch {
      enqueueSnackbar("Test inputs must be valid JSON", { variant: "error" });
      return;
    }
    if (!inputs || Array.isArray(inputs) || typeof inputs !== "object") {
      enqueueSnackbar("Test inputs must be a JSON object", {
        variant: "error",
      });
      return;
    }

    try {
      const result = await testNode({
        graphId: currentAgent?.id,
        versionId: currentAgent?.version_id,
        nodeId,
        data: {
          config,
          inputs,
        },
      });
      setTestResult(result?.result || result);
    } catch {
      enqueueSnackbar("Failed to test code node", { variant: "error" });
    }
  };

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: 1.5,
        height: "100%",
      }}
    >
      <Box
        sx={{
          flex: 1,
          minHeight: 0,
          overflowY: "auto",
          display: "flex",
          flexDirection: "column",
          gap: 2,
        }}
      >
        <Stack direction="row" spacing={1.5}>
          <Controller
            name="language"
            control={control}
            render={({ field }) => (
              <TextField
                {...field}
                select
                label="Language"
                size="small"
                fullWidth
              >
                {CODE_EXECUTION_LANGUAGES.map((option) => (
                  <MenuItem key={option.value} value={option.value}>
                    {option.label}
                  </MenuItem>
                ))}
              </TextField>
            )}
          />
          <Controller
            name="timeout_ms"
            control={control}
            render={({ field }) => (
              <TextField
                {...field}
                type="number"
                label="Timeout"
                size="small"
                fullWidth
                inputProps={{ min: 100, max: 30000, step: 100 }}
              />
            )}
          />
          <Controller
            name="memory_mb"
            control={control}
            render={({ field }) => (
              <TextField
                {...field}
                type="number"
                label="Memory"
                size="small"
                fullWidth
                inputProps={{ min: 32, max: 512, step: 32 }}
              />
            )}
          />
        </Stack>

        <Controller
          name="code"
          control={control}
          render={({ field }) => (
            <Box sx={{ border: 1, borderColor: "divider", minHeight: 360 }}>
              <Editor
                height="360px"
                language={editorLanguage}
                theme="vs-dark"
                value={field.value || ""}
                onChange={(value) => field.onChange(value || "")}
                options={{
                  minimap: { enabled: false },
                  fontSize: 13,
                  scrollBeyondLastLine: false,
                  wordWrap: "on",
                }}
              />
            </Box>
          )}
        />

        <TextField
          label="Test inputs"
          value={testInputs}
          onChange={(event) => setTestInputs(event.target.value)}
          multiline
          minRows={4}
          size="small"
          inputProps={{ spellCheck: false }}
        />

        {testResult && (
          <Alert severity={testResult.ok ? "success" : "warning"}>
            <Typography variant="caption" component="pre" sx={{ m: 0 }}>
              {JSON.stringify(testResult, null, 2)}
            </Typography>
          </Alert>
        )}
      </Box>

      <Box
        sx={{
          display: "flex",
          justifyContent: "flex-end",
          gap: 1,
          pt: 1.5,
          borderTop: 1,
          borderColor: "divider",
        }}
      >
        <LoadingButton
          type="button"
          size="small"
          variant="text"
          loading={isTesting}
          onClick={runTest}
        >
          Test
        </LoadingButton>
        <LoadingButton
          type="button"
          size="small"
          variant="outlined"
          loading={isPending}
          onClick={saveConfig}
        >
          Save
        </LoadingButton>
      </Box>
    </Box>
  );
}

CodeExecutionNodeForm.propTypes = {
  nodeId: PropTypes.string.isRequired,
};
