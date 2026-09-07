import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Collapse,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { LoadingButton } from "@mui/lab";
import Iconify from "src/components/iconify";
import { extractVariablesFromContent } from "./promptNodeFormUtils";

/**
 * Collapsible "Test this node" panel shown inside the node drawer.
 *
 * Lets the user run the node they're currently editing — using whatever is
 * in the form right now, saved or not — against sample input values, and
 * see the output before deciding to save. Running a test never saves
 * anything; that stays entirely the responsibility of the Save button.
 */
export default function TestNodePanel({ messages, onRunTest, disabled }) {
  const [open, setOpen] = useState(false);
  const [inputValues, setInputValues] = useState({});
  const [result, setResult] = useState(null);
  const [isRunning, setIsRunning] = useState(false);

  const variables = useMemo(() => {
    const names = new Set();
    (messages || []).forEach((message) => {
      const content = Array.isArray(message?.content)
        ? message.content
        : [{ type: "text", text: message?.content || "" }];
      extractVariablesFromContent(content).forEach((name) => names.add(name));
    });
    return Array.from(names);
  }, [messages]);

  const handleToggle = () => {
    setOpen((prev) => !prev);
  };

  const handleInputChange = (name, value) => {
    setInputValues((prev) => ({ ...prev, [name]: value }));
  };

  const handleRunTest = async () => {
    setIsRunning(true);
    setResult(null);
    try {
      const response = await onRunTest(inputValues);
      setResult(response);
    } finally {
      setIsRunning(false);
    }
  };

  const responseOutput = result?.outputs?.response;
  const displayOutput =
    typeof responseOutput === "string"
      ? responseOutput
      : responseOutput != null
        ? JSON.stringify(responseOutput, null, 2)
        : null;

  return (
    <Box sx={{ borderTop: 1, borderColor: "divider", pt: 1 }}>
      <Button
        size="small"
        onClick={handleToggle}
        disabled={disabled}
        startIcon={
          <Iconify
            icon={open ? "eva:chevron-up-fill" : "eva:chevron-down-fill"}
            width={16}
          />
        }
        sx={{ color: "text.secondary", fontWeight: "fontWeightMedium" }}
      >
        Test this node
      </Button>

      <Collapse in={open && !disabled} unmountOnExit>
        <Stack spacing={1.25} sx={{ mt: 1 }}>
          {variables.length > 0 && (
            <Stack spacing={1}>
              <Typography variant="caption" color="text.secondary">
                Sample input values
              </Typography>
              {variables.map((name) => (
                <TextField
                  key={name}
                  size="small"
                  label={name}
                  value={inputValues[name] ?? ""}
                  onChange={(e) => handleInputChange(name, e.target.value)}
                  fullWidth
                />
              ))}
            </Stack>
          )}

          <LoadingButton
            variant="outlined"
            size="small"
            loading={isRunning}
            onClick={handleRunTest}
            sx={{ alignSelf: "flex-start" }}
          >
            Run test
          </LoadingButton>

          {result?.status === "SUCCESS" && (
            <Alert severity="success" sx={{ whiteSpace: "pre-wrap" }}>
              {displayOutput}
            </Alert>
          )}

          {result?.status === "FAILED" && (
            <Alert severity="error">
              {result.error || "Test run failed"}
            </Alert>
          )}
        </Stack>
      </Collapse>
    </Box>
  );
}

TestNodePanel.propTypes = {
  messages: PropTypes.array,
  onRunTest: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
};
