import React, { useState, useEffect } from "react";
import PropTypes from "prop-types";
import Dialog from "@mui/material/Dialog";
import DialogTitle from "@mui/material/DialogTitle";
import DialogContent from "@mui/material/DialogContent";
import DialogActions from "@mui/material/DialogActions";
import Button from "@mui/material/Button";
import TextField from "@mui/material/TextField";
import MenuItem from "@mui/material/MenuItem";
import Select from "@mui/material/Select";
import FormControl from "@mui/material/FormControl";
import InputLabel from "@mui/material/InputLabel";
import Box from "@mui/material/Box";
import Alert from "@mui/material/Alert";
import CircularProgress from "@mui/material/CircularProgress";
import Typography from "@mui/material/Typography";
import Iconify from "src/components/iconify";
import {
  createConnector,
  updateConnector,
  testConnector,
} from "src/sections/falcon-ai/hooks/useFalconAPI";

const INITIAL_STATE = {
  name: "",
  server_url: "",
  transport: "sse",
  auth_type: "none",
  auth_header: "",
  auth_value: "",
  auth_token: "",
};

export default function ConnectorForm({ open, connector, onClose }) {
  const [form, setForm] = useState(INITIAL_STATE);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [error, setError] = useState(null);

  const isEdit = !!connector;

  useEffect(() => {
    if (connector) {
      setForm({
        name: connector.name || "",
        server_url: connector.server_url || "",
        transport: connector.transport || "sse",
        auth_type: connector.auth_type || "none",
        auth_header: connector.auth_header || "",
        auth_value: connector.auth_value || "",
        auth_token: connector.auth_token || "",
      });
    } else {
      setForm(INITIAL_STATE);
    }
    setTestResult(null);
    setError(null);
  }, [connector, open]);

  const handleChange = (field) => (e) => {
    setForm((prev) => ({ ...prev, [field]: e.target.value }));
  };

  const handleTest = async () => {
    if (!connector?.id) return;
    setTesting(true);
    setTestResult(null);
    try {
      const result = await testConnector(connector.id);
      setTestResult(result.success ? "success" : "error");
    } catch {
      setTestResult("error");
    } finally {
      setTesting(false);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      const payload = {
        name: form.name,
        server_url: form.server_url,
        transport: form.transport,
        auth_type: form.auth_type,
      };
      if (form.auth_type === "api_key") {
        payload.auth_header = form.auth_header;
        payload.auth_value = form.auth_value;
      } else if (form.auth_type === "bearer") {
        payload.auth_token = form.auth_token;
      }

      if (isEdit) {
        await updateConnector(connector.id, payload);
      } else {
        await createConnector(payload);
      }
      onClose(true);
    } catch (err) {
      setError(
        err?.response?.data?.detail ||
          err?.message ||
          "Failed to save connector.",
      );
    } finally {
      setSaving(false);
    }
  };

  const canSave = form.name.trim() && form.server_url.trim();

  return (
    <Dialog
      open={open}
      onClose={() => onClose(false)}
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 720 } }}
      fullWidth
    >
      <DialogTitle sx={{ pb: 1 }}>
        <Typography variant="h6" component="span">
          {isEdit ? "Edit Connector" : "Add Connector"}
        </Typography>
      </DialogTitle>

      <DialogContent>
        <Box sx={{ display: "flex", flexDirection: "column", gap: 2.5, pt: 1 }}>
          <TextField
            label="Name"
            value={form.name}
            onChange={handleChange("name")}
            fullWidth
            size="small"
            placeholder="e.g. Sentry, GitHub"
          />

          <TextField
            label="Server URL"
            value={form.server_url}
            onChange={handleChange("server_url")}
            fullWidth
            size="small"
            placeholder="https://mcp.example.com/sse"
          />

          <FormControl size="small" fullWidth>
            <InputLabel>Transport</InputLabel>
            <Select
              value={form.transport}
              onChange={handleChange("transport")}
              label="Transport"
            >
              <MenuItem value="sse">SSE (Server-Sent Events)</MenuItem>
              <MenuItem value="streamable_http">Streamable HTTP</MenuItem>
            </Select>
          </FormControl>

          <FormControl size="small" fullWidth>
            <InputLabel>Auth Type</InputLabel>
            <Select
              value={form.auth_type}
              onChange={handleChange("auth_type")}
              label="Auth Type"
            >
              <MenuItem value="none">None</MenuItem>
              <MenuItem value="api_key">API Key</MenuItem>
              <MenuItem value="bearer">Bearer Token</MenuItem>
            </Select>
          </FormControl>

          {form.auth_type === "api_key" && (
            <>
              <TextField
                label="Header Name"
                value={form.auth_header}
                onChange={handleChange("auth_header")}
                fullWidth
                size="small"
                placeholder="X-API-Key"
              />
              <TextField
                label="Header Value"
                value={form.auth_value}
                onChange={handleChange("auth_value")}
                fullWidth
                size="small"
                type="password"
                placeholder="Your API key"
              />
            </>
          )}

          {form.auth_type === "bearer" && (
            <TextField
              label="Bearer Token"
              value={form.auth_token}
              onChange={handleChange("auth_token")}
              fullWidth
              size="small"
              type="password"
              placeholder="Your bearer token"
            />
          )}

          {testResult && (
            <Alert severity={testResult === "success" ? "success" : "error"}>
              {testResult === "success"
                ? "Connection successful!"
                : "Connection failed. Check the URL and credentials."}
            </Alert>
          )}

          {error && <Alert severity="error">{error}</Alert>}
        </Box>
      </DialogContent>

      <DialogActions sx={{ px: 3, pb: 2, gap: 1 }}>
        {isEdit && (
          <Button
            onClick={handleTest}
            disabled={testing}
            startIcon={
              testing ? (
                <CircularProgress size={16} />
              ) : (
                <Iconify icon="solar:plug-circle-linear" width={20} />
              )
            }
            sx={{ mr: "auto" }}
          >
            Test Connection
          </Button>
        )}
        <Button onClick={() => onClose(false)} color="inherit">
          Cancel
        </Button>
        <Button
          onClick={handleSave}
          variant="contained"
          disabled={!canSave || saving}
          startIcon={
            saving ? <CircularProgress size={16} color="inherit" /> : null
          }
        >
          {isEdit ? "Save" : "Add"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

ConnectorForm.propTypes = {
  open: PropTypes.bool.isRequired,
  connector: PropTypes.object,
  onClose: PropTypes.func.isRequired,
};
