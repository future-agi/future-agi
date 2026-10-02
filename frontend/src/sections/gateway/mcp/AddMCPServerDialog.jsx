/* eslint-disable react/prop-types */
import React, { useState, useEffect } from "react";
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  MenuItem,
  Stack,
  TextField,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";
import { useUpdateMCPServer } from "./hooks/useMCPConfig";

const TRANSPORT_OPTIONS = [
  { value: "http", label: "HTTP" },
  { value: "stdio", label: "Stdio" },
];

const AUTH_OPTIONS = [
  { value: "none", label: "None" },
  { value: "bearer", label: "Bearer Token" },
  { value: "api_key", label: "API Key" },
];

const AddMCPServerDialog = ({ open, onClose, gatewayId, editServer }) => {
  const isEditing = Boolean(editServer);
  const updateMutation = useUpdateMCPServer();

  const [form, setForm] = useState({
    server_id: "",
    url: "",
    transport: "http",
    authType: "none",
    token: "",
    header: "",
    apiKey: "",
    command: "",
    args: "",
    toolsCacheTtl: "",
  });

  useEffect(() => {
    if (editServer) {
      const cfg = editServer.config || {};
      const auth = cfg.auth || {};
      setForm({
        server_id: editServer.serverId || editServer.server_id || "",
        url: cfg.url || "",
        transport: cfg.transport || "http",
        authType: auth.type || "none",
        token: auth.token || "",
        header: auth.header || "",
        apiKey: auth.key || "",
        command: cfg.command || "",
        args: (cfg.args || []).join(" "),
        toolsCacheTtl: cfg.tools_cache_ttl || "",
      });
    } else {
      setForm({
        server_id: "",
        url: "",
        transport: "http",
        authType: "none",
        token: "",
        header: "",
        apiKey: "",
        command: "",
        args: "",
        toolsCacheTtl: "",
      });
    }
  }, [editServer, open]);

  const handleChange = (field) => (e) => {
    setForm((prev) => ({ ...prev, [field]: e.target.value }));
  };

  const handleSave = () => {
    if (!form.server_id.trim()) {
      enqueueSnackbar("Server ID is required", { variant: "warning" });
      return;
    }
    if (form.transport === "http" && !form.url.trim()) {
      enqueueSnackbar("URL is required for HTTP transport", {
        variant: "warning",
      });
      return;
    }
    if (form.transport === "stdio" && !form.command.trim()) {
      enqueueSnackbar("Command is required for Stdio transport", {
        variant: "warning",
      });
      return;
    }

    const serverConfig = { transport: form.transport };

    if (form.transport === "http") {
      serverConfig.url = form.url.trim();
    } else {
      serverConfig.command = form.command.trim();
      if (form.args.trim()) {
        serverConfig.args = form.args.trim().split(/\s+/);
      }
    }

    if (form.authType !== "none") {
      serverConfig.auth = { type: form.authType };
      if (form.authType === "bearer") {
        serverConfig.auth.token = form.token;
      } else if (form.authType === "api_key") {
        serverConfig.auth.header = form.header || "X-API-Key";
        serverConfig.auth.key = form.apiKey;
      }
    }

    if (form.toolsCacheTtl.trim()) {
      serverConfig.tools_cache_ttl = form.toolsCacheTtl.trim();
    }

    updateMutation.mutate(
      {
        gatewayId,
        serverId: form.server_id.trim(),
        config: serverConfig,
      },
      {
        onSuccess: () => {
          enqueueSnackbar(
            isEditing
              ? `Server "${form.server_id}" updated`
              : `Server "${form.server_id}" added`,
            { variant: "success" },
          );
          onClose();
        },
        onError: () => {
          enqueueSnackbar("Failed to save server configuration", {
            variant: "error",
          });
        },
      },
    );
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>
        {isEditing ? "Edit MCP Server" : "Add MCP Server"}
      </DialogTitle>
      <DialogContent>
        <Stack spacing={2.5} sx={{ mt: 1 }}>
          <TextField
            label="Server ID"
            value={form.server_id}
            onChange={handleChange("server_id")}
            disabled={isEditing}
            required
            fullWidth
            size="small"
            helperText="Unique identifier for this server (e.g., github, slack)"
          />

          <TextField
            label="Transport"
            value={form.transport}
            onChange={handleChange("transport")}
            select
            fullWidth
            size="small"
          >
            {TRANSPORT_OPTIONS.map((opt) => (
              <MenuItem key={opt.value} value={opt.value}>
                {opt.label}
              </MenuItem>
            ))}
          </TextField>

          {form.transport === "http" && (
            <TextField
              label="URL"
              value={form.url}
              onChange={handleChange("url")}
              required
              fullWidth
              size="small"
              placeholder="http://mcp-server:8080"
            />
          )}

          {form.transport === "stdio" && (
            <>
              <TextField
                label="Command"
                value={form.command}
                onChange={handleChange("command")}
                required
                fullWidth
                size="small"
                placeholder="/usr/local/bin/mcp-tool"
              />
              <TextField
                label="Arguments"
                value={form.args}
                onChange={handleChange("args")}
                fullWidth
                size="small"
                placeholder="--port 8080 --verbose"
                helperText="Space-separated arguments"
              />
            </>
          )}

          <TextField
            label="Auth Type"
            value={form.authType}
            onChange={handleChange("authType")}
            select
            fullWidth
            size="small"
          >
            {AUTH_OPTIONS.map((opt) => (
              <MenuItem key={opt.value} value={opt.value}>
                {opt.label}
              </MenuItem>
            ))}
          </TextField>

          {form.authType === "bearer" && (
            <TextField
              label="Bearer Token"
              value={form.token}
              onChange={handleChange("token")}
              fullWidth
              size="small"
              type="password"
              placeholder="Enter token or ${ENV_VAR}"
            />
          )}

          {form.authType === "api_key" && (
            <>
              <TextField
                label="Header Name"
                value={form.header}
                onChange={handleChange("header")}
                fullWidth
                size="small"
                placeholder="X-API-Key"
              />
              <TextField
                label="API Key"
                value={form.apiKey}
                onChange={handleChange("apiKey")}
                fullWidth
                size="small"
                type="password"
                placeholder="Enter key or ${ENV_VAR}"
              />
            </>
          )}

          <TextField
            label="Tools Cache TTL"
            value={form.toolsCacheTtl}
            onChange={handleChange("toolsCacheTtl")}
            fullWidth
            size="small"
            placeholder="5m"
            helperText="How long to cache the tool list (e.g., 5m, 1h)"
          />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="contained"
          onClick={handleSave}
          disabled={updateMutation.isPending}
        >
          {updateMutation.isPending
            ? "Saving..."
            : isEditing
              ? "Update Server"
              : "Add Server"}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default AddMCPServerDialog;
