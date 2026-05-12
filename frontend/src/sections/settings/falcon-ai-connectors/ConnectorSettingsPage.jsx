import React, { useState, useEffect, useCallback } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import Alert from "@mui/material/Alert";
import TextField from "@mui/material/TextField";
import InputAdornment from "@mui/material/InputAdornment";
import ListItemButton from "@mui/material/ListItemButton";
import ListItemIcon from "@mui/material/ListItemIcon";
import ListItemText from "@mui/material/ListItemText";
import Switch from "@mui/material/Switch";
import Divider from "@mui/material/Divider";
import MenuItem from "@mui/material/MenuItem";
import Select from "@mui/material/Select";
import FormControl from "@mui/material/FormControl";
import { alpha, useTheme } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import {
  fetchConnectors,
  createConnector,
  updateConnector,
  deleteConnector,
  testConnector,
  discoverConnectorTools,
  authenticateConnector,
  updateConnectorTools,
} from "src/sections/falcon-ai/hooks/useFalconAPI";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
const DEFAULT_CONNECTOR_FORM = {
  name: "",
  server_url: "",
  transport: "streamable_http",
  auth_type: "none",
  auth_header_name: "",
  auth_header_value: "",
};

const CONNECTOR_PRESETS = [
  {
    id: "posthog",
    name: "PostHog",
    icon: "simple-icons:posthog",
    values: {
      ...DEFAULT_CONNECTOR_FORM,
      name: "PostHog",
      server_url: "https://mcp.posthog.com/mcp",
      transport: "streamable_http",
      auth_type: "oauth",
      auth_header_name: "Authorization",
    },
  },
];

function getConnectorIcon(connector) {
  const name = (connector.name || "").toLowerCase();
  if (name.includes("posthog")) return "simple-icons:posthog";
  if (name.includes("github")) return "mdi:github";
  if (name.includes("gitlab")) return "mdi:gitlab";
  if (name.includes("jira")) return "mdi:jira";
  if (name.includes("slack")) return "mdi:slack";
  if (name.includes("notion")) return "simple-icons:notion";
  if (name.includes("linear")) return "mdi:view-kanban-outline";
  if (name.includes("sentry")) return "mdi:bug-outline";
  if (name.includes("postgres")) return "mdi:database";
  return "mdi:puzzle-outline";
}

function getStatusInfo(connector) {
  const isVerified = connector.is_verified;
  const isActive = connector.is_active ?? true;
  if (!isActive) return { label: "Inactive", color: "default" };
  if (isVerified) return { label: "Connected", color: "success" };
  return { label: "Pending", color: "warning" };
}

// ---------------------------------------------------------------------------
// Main Page
// ---------------------------------------------------------------------------
export default function ConnectorSettingsPage() {
  const theme = useTheme();
  const [connectors, setConnectors] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState(null);
  const [isEditing, setIsEditing] = useState(false);
  const [isNew, setIsNew] = useState(false);
  const [newConnectorPreset, setNewConnectorPreset] = useState(null);

  const loadConnectors = useCallback(async () => {
    try {
      setLoading(true);
      const data = await fetchConnectors();
      const results = data.results || data || [];
      setConnectors(Array.isArray(results) ? results : []);
      setError(null);
    } catch {
      setError("Failed to load connectors.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadConnectors();
  }, [loadConnectors]);

  // Listen for OAuth callback
  useEffect(() => {
    const handler = async (event) => {
      if (event.data?.type !== "falcon_oauth_callback") return;
      if (event.data.status !== "success") return;
      if (selectedId) {
        try {
          await discoverConnectorTools(selectedId);
        } catch {
          /* */
        }
      }
      await loadConnectors();
    };
    window.addEventListener("message", handler);
    return () => window.removeEventListener("message", handler);
  }, [loadConnectors, selectedId]);

  const selected = connectors.find((c) => c.id === selectedId) || null;
  const filtered = connectors.filter(
    (c) => !search || c.name?.toLowerCase().includes(search.toLowerCase()),
  );

  const handleSelect = (id) => {
    setSelectedId(id);
    setIsEditing(false);
    setIsNew(false);
    setNewConnectorPreset(null);
  };

  const handleNew = (preset = null) => {
    setSelectedId(null);
    setIsNew(true);
    setIsEditing(true);
    setNewConnectorPreset(preset);
  };

  const handleSaved = async (savedId = null) => {
    await loadConnectors();
    setIsEditing(false);
    setIsNew(false);
    setNewConnectorPreset(null);
    if (savedId) setSelectedId(savedId);
  };

  const handleDelete = async (id) => {
    try {
      await deleteConnector(id);
      setConnectors((prev) => prev.filter((c) => c.id !== id));
      if (selectedId === id) {
        setSelectedId(null);
        setIsEditing(false);
      }
    } catch {
      /* */
    }
  };

  if (loading) {
    return (
      <Box display="flex" justifyContent="center" py={8}>
        <CircularProgress />
      </Box>
    );
  }

  return (
    <Box>
      {/* Header */}
      <Box
        mb={3}
        display="flex"
        alignItems="flex-start"
        justifyContent="space-between"
      >
        <Box>
          <Typography
            sx={{
              typography: "m2",
              fontWeight: "fontWeightSemiBold",
              color: "text.primary",
            }}
          >
            Falcon AI Connectors
          </Typography>
          <Typography
            sx={{
              typography: "s1",
              fontWeight: "fontWeightRegular",
              color: "text.secondary",
              mt: 0.5,
            }}
          >
            Connect external MCP servers to extend Falcon AI with additional
            tools and capabilities.
          </Typography>
        </Box>
        <Button
          variant="contained"
          startIcon={<Iconify icon="mdi:plus" width={18} />}
          onClick={() => handleNew()}
          sx={{ flexShrink: 0 }}
        >
          Add Connector
        </Button>
      </Box>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}

      <Box sx={{ display: "flex", gap: 1, mb: 2, flexWrap: "wrap" }}>
        {CONNECTOR_PRESETS.map((preset) => {
          const exists = connectors.some(
            (connector) =>
              connector.name?.toLowerCase() === preset.name.toLowerCase() ||
              connector.server_url === preset.values.server_url,
          );
          return (
            <Button
              key={preset.id}
              size="small"
              variant="outlined"
              startIcon={<Iconify icon={preset.icon} width={16} />}
              onClick={() => handleNew(preset)}
              disabled={exists}
              sx={{ textTransform: "none", borderRadius: "8px", fontSize: 12 }}
            >
              {exists ? `${preset.name} added` : `Add ${preset.name}`}
            </Button>
          );
        })}
      </Box>

      {/* 2-column layout */}
      <Box
        sx={{
          display: "flex",
          border: (t) => `1px solid ${t.palette.divider}`,
          borderRadius: "12px",
          overflow: "hidden",
          minHeight: 500,
          bgcolor: "background.paper",
        }}
      >
        {/* Left: Connector list */}
        <Box
          sx={{
            width: 300,
            flexShrink: 0,
            borderRight: (t) => `1px solid ${t.palette.divider}`,
            display: "flex",
            flexDirection: "column",
          }}
        >
          {/* Search */}
          <Box sx={{ p: 1.5 }}>
            <TextField
              size="small"
              fullWidth
              placeholder="Search connectors..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <Iconify
                      icon="mdi:magnify"
                      width={16}
                      sx={{ color: "text.disabled" }}
                    />
                  </InputAdornment>
                ),
              }}
              sx={{
                "& .MuiInputBase-root": {
                  fontSize: 13,
                  height: 34,
                  borderRadius: "8px",
                },
              }}
            />
          </Box>

          {/* List */}
          <Box sx={{ flex: 1, overflow: "auto", px: 0.75 }}>
            {filtered.length === 0 && !loading && (
              <Typography
                sx={{
                  textAlign: "center",
                  py: 4,
                  color: "text.disabled",
                  fontSize: 13,
                }}
              >
                {search ? "No matching connectors" : "No connectors yet"}
              </Typography>
            )}
            {filtered.map((conn) => {
              const statusInfo = getStatusInfo(conn);
              const isActive = conn.id === selectedId && !isNew;
              return (
                <ListItemButton
                  key={conn.id}
                  selected={isActive}
                  onClick={() => handleSelect(conn.id)}
                  sx={{
                    borderRadius: "8px",
                    mb: "2px",
                    px: 1.5,
                    py: "7px",
                    minHeight: 0,
                    "&.Mui-selected": {
                      bgcolor: theme.palette.action.selected,
                    },
                    "&:hover": { bgcolor: theme.palette.action.hover },
                  }}
                >
                  <ListItemIcon sx={{ minWidth: 32, color: "text.secondary" }}>
                    <Iconify icon={getConnectorIcon(conn)} width={18} />
                  </ListItemIcon>
                  <ListItemText
                    primary={conn.name}
                    primaryTypographyProps={{
                      noWrap: true,
                      fontSize: 13,
                      fontWeight: isActive ? 600 : 500,
                    }}
                  />
                  <Chip
                    label={statusInfo.label}
                    size="small"
                    variant="outlined"
                    color={statusInfo.color}
                    sx={{ height: 18, fontSize: 9, fontWeight: 600, ml: 0.5 }}
                  />
                </ListItemButton>
              );
            })}
          </Box>
        </Box>

        {/* Right: Detail / Edit / Empty */}
        <Box
          sx={{
            flex: 1,
            display: "flex",
            flexDirection: "column",
            overflow: "auto",
          }}
        >
          {isEditing ? (
            <ConnectorEditor
              connector={isNew ? null : selected}
              preset={newConnectorPreset}
              onSaved={handleSaved}
              onCancel={() => {
                setIsEditing(false);
                setIsNew(false);
                setNewConnectorPreset(null);
              }}
            />
          ) : selected ? (
            <ConnectorDetail
              connector={selected}
              onEdit={() => setIsEditing(true)}
              onDelete={() => handleDelete(selected.id)}
              onRefresh={loadConnectors}
            />
          ) : (
            <Box
              sx={{
                flex: 1,
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                justifyContent: "center",
                px: 4,
              }}
            >
              <Iconify
                icon="mdi:puzzle-outline"
                width={48}
                sx={{ color: "text.disabled", mb: 2 }}
              />
              <Typography sx={{ fontSize: 15, fontWeight: 600, mb: 0.5 }}>
                {connectors.length === 0
                  ? "No connectors configured"
                  : "Select a connector"}
              </Typography>
              <Typography
                sx={{
                  fontSize: 13,
                  color: "text.secondary",
                  textAlign: "center",
                  maxWidth: 320,
                }}
              >
                {connectors.length === 0
                  ? "Add an external MCP server to give Falcon AI access to additional tools."
                  : "Choose a connector from the list to view its details and tools."}
              </Typography>
              {connectors.length === 0 && (
                <Box sx={{ display: "flex", gap: 1, mt: 2, flexWrap: "wrap" }}>
                  <Button
                    variant="outlined"
                    startIcon={<Iconify icon="mdi:plus" width={16} />}
                    onClick={() => handleNew()}
                    sx={{ textTransform: "none", borderRadius: "8px" }}
                  >
                    Add connector
                  </Button>
                  <Button
                    variant="outlined"
                    startIcon={
                      <Iconify icon="simple-icons:posthog" width={16} />
                    }
                    onClick={() => handleNew(CONNECTOR_PRESETS[0])}
                    sx={{ textTransform: "none", borderRadius: "8px" }}
                  >
                    Add PostHog
                  </Button>
                </Box>
              )}
            </Box>
          )}
        </Box>
      </Box>
    </Box>
  );
}

// ---------------------------------------------------------------------------
// Connector Detail (read-only)
// ---------------------------------------------------------------------------
function ConnectorDetail({ connector, onEdit, onDelete, onRefresh }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const statusInfo = getStatusInfo(connector);
  const [testing, setTesting] = useState(false);
  const [discovering, setDiscovering] = useState(false);
  const [reauthing, setReauthing] = useState(false);

  const tools = connector.discovered_tools || [];
  const toolNames = tools.map((tool) => tool.name || tool.id).filter(Boolean);
  const enabledTools =
    connector.enabled_tools || connector.enabled_tool_names || [];

  const isToolEnabled = (toolName) => {
    if (enabledTools.length === 0 && tools.length > 0) return true; // all enabled by default
    return enabledTools.includes(toolName);
  };

  const handleTest = async () => {
    setTesting(true);
    try {
      await testConnector(connector.id);
      onRefresh();
    } catch {
      /* */
    } finally {
      setTesting(false);
    }
  };

  const handleDiscover = async () => {
    setDiscovering(true);
    try {
      await discoverConnectorTools(connector.id);
      onRefresh();
    } catch {
      /* */
    } finally {
      setDiscovering(false);
    }
  };

  const handleReauth = async () => {
    setReauthing(true);
    try {
      const result = await authenticateConnector(connector.id);
      const authUrl = result?.authorization_url;
      if (authUrl) {
        window.open(authUrl, "falcon_oauth", "width=600,height=700,popup=yes");
      } else {
        onRefresh();
      }
    } catch {
      /* */
    } finally {
      setReauthing(false);
    }
  };

  const handleToolToggle = async (toolName) => {
    const currentEnabled =
      enabledTools.length === 0 && toolNames.length > 0
        ? toolNames
        : enabledTools;
    let updated;
    if (isToolEnabled(toolName)) {
      updated = currentEnabled.filter((t) => t !== toolName);
    } else {
      updated = [...new Set([...currentEnabled, toolName])];
    }
    try {
      await updateConnectorTools(connector.id, updated);
      onRefresh();
    } catch {
      /* */
    }
  };

  return (
    <Box
      sx={{
        flex: 1,
        display: "flex",
        flexDirection: "column",
        overflow: "auto",
        p: 3,
      }}
    >
      {/* Header */}
      <Box
        sx={{
          display: "flex",
          alignItems: "flex-start",
          justifyContent: "space-between",
          mb: 2.5,
        }}
      >
        <Box sx={{ display: "flex", alignItems: "center", gap: 1.5 }}>
          <Box
            sx={{
              width: 40,
              height: 40,
              borderRadius: "10px",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              bgcolor: alpha(theme.palette.primary.main, 0.1),
              color: "primary.main",
            }}
          >
            <Iconify icon={getConnectorIcon(connector)} width={22} />
          </Box>
          <Box>
            <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
              <Typography sx={{ fontSize: 16, fontWeight: 700 }}>
                {connector.name}
              </Typography>
              <Chip
                label={statusInfo.label}
                size="small"
                variant="outlined"
                color={statusInfo.color}
                sx={{ height: 20, fontSize: 10, fontWeight: 600 }}
              />
            </Box>
            {connector.description && (
              <Typography
                sx={{ fontSize: 13, color: "text.secondary", mt: 0.25 }}
              >
                {connector.description}
              </Typography>
            )}
          </Box>
        </Box>
      </Box>

      {/* Metadata */}
      <Box
        sx={{
          p: 2,
          borderRadius: "8px",
          mb: 2,
          bgcolor: isDark
            ? alpha(theme.palette.common.white, 0.03)
            : alpha(theme.palette.common.black, 0.02),
          border: (t) => `1px solid ${t.palette.divider}`,
        }}
      >
        <Box sx={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
          <Box>
            <Typography
              sx={{
                fontSize: 11,
                fontWeight: 600,
                color: "text.disabled",
                mb: 0.25,
              }}
            >
              Server URL
            </Typography>
            <Typography sx={{ fontSize: 13, fontFamily: "monospace" }}>
              {connector.server_url}
            </Typography>
          </Box>
          <Box>
            <Typography
              sx={{
                fontSize: 11,
                fontWeight: 600,
                color: "text.disabled",
                mb: 0.25,
              }}
            >
              Authentication
            </Typography>
            <Typography sx={{ fontSize: 13 }}>
              {connector.auth_type || "none"}
            </Typography>
          </Box>
          <Box>
            <Typography
              sx={{
                fontSize: 11,
                fontWeight: 600,
                color: "text.disabled",
                mb: 0.25,
              }}
            >
              Transport
            </Typography>
            <Typography sx={{ fontSize: 13 }}>
              {connector.transport || "sse"}
            </Typography>
          </Box>
        </Box>
      </Box>

      {/* Error */}
      {connector.last_error && (
        <Alert severity="error" sx={{ mb: 2, fontSize: 12 }}>
          {connector.last_error}
        </Alert>
      )}

      {/* Actions */}
      <Box sx={{ display: "flex", gap: 1, mb: 3, flexWrap: "wrap" }}>
        <Button
          size="small"
          variant="outlined"
          startIcon={<Iconify icon="mdi:pencil-outline" width={14} />}
          onClick={onEdit}
          sx={{ textTransform: "none", borderRadius: "8px", fontSize: 12 }}
        >
          Edit
        </Button>
        {connector.auth_type === "oauth" && (
          <Button
            size="small"
            variant="outlined"
            startIcon={<Iconify icon="mdi:refresh" width={14} />}
            onClick={handleReauth}
            disabled={reauthing}
            sx={{ textTransform: "none", borderRadius: "8px", fontSize: 12 }}
          >
            {reauthing ? "Authenticating..." : "Re-authenticate"}
          </Button>
        )}
        <Button
          size="small"
          variant="outlined"
          startIcon={<Iconify icon="mdi:magnify" width={14} />}
          onClick={handleDiscover}
          disabled={discovering}
          sx={{ textTransform: "none", borderRadius: "8px", fontSize: 12 }}
        >
          {discovering ? "Discovering..." : "Discover Tools"}
        </Button>
        <Button
          size="small"
          variant="outlined"
          startIcon={<Iconify icon="mdi:connection" width={14} />}
          onClick={handleTest}
          disabled={testing}
          sx={{ textTransform: "none", borderRadius: "8px", fontSize: 12 }}
        >
          {testing ? "Testing..." : "Test Connection"}
        </Button>
        <Button
          size="small"
          variant="outlined"
          color="error"
          startIcon={<Iconify icon="mdi:delete-outline" width={14} />}
          onClick={onDelete}
          sx={{
            textTransform: "none",
            borderRadius: "8px",
            fontSize: 12,
            ml: "auto",
          }}
        >
          Delete
        </Button>
      </Box>

      {/* Tools */}
      <Divider sx={{ mb: 2 }} />
      <Typography sx={{ fontSize: 13, fontWeight: 700, mb: 1.5 }}>
        Discovered Tools{" "}
        {tools.length > 0 && (
          <Chip
            label={tools.length}
            size="small"
            variant="outlined"
            sx={{ height: 18, fontSize: 10, ml: 0.5 }}
          />
        )}
      </Typography>

      {tools.length === 0 ? (
        <Box sx={{ textAlign: "center", py: 4 }}>
          <Iconify
            icon="mdi:tools"
            width={36}
            sx={{ color: "text.disabled", mb: 1 }}
          />
          <Typography sx={{ fontSize: 13, color: "text.disabled" }}>
            No tools discovered yet. Click &ldquo;Discover Tools&rdquo; or
            &ldquo;Re-authenticate&rdquo; to fetch available tools.
          </Typography>
        </Box>
      ) : (
        <Box
          sx={{
            borderRadius: "8px",
            border: (t) => `1px solid ${t.palette.divider}`,
            overflow: "hidden",
          }}
        >
          {tools.map((tool, i) => {
            const name = typeof tool === "string" ? tool : tool.name;
            const desc = typeof tool === "string" ? "" : tool.description;
            const enabled = isToolEnabled(name);
            return (
              <Box
                key={name}
                sx={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  px: 2,
                  py: 1,
                  borderTop:
                    i > 0 ? (t) => `1px solid ${t.palette.divider}` : "none",
                  "&:hover": { bgcolor: theme.palette.action.hover },
                }}
              >
                <Box sx={{ minWidth: 0, flex: 1 }}>
                  <Typography noWrap sx={{ fontSize: 12, fontWeight: 600 }}>
                    {name}
                  </Typography>
                  {desc && (
                    <Typography
                      noWrap
                      sx={{ fontSize: 11, color: "text.secondary" }}
                    >
                      {desc}
                    </Typography>
                  )}
                </Box>
                <Switch
                  size="small"
                  checked={enabled}
                  onChange={() => handleToolToggle(name)}
                />
              </Box>
            );
          })}
        </Box>
      )}
    </Box>
  );
}

ConnectorDetail.propTypes = {
  connector: PropTypes.shape({
    id: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
    name: PropTypes.string,
    description: PropTypes.string,
    server_url: PropTypes.string,
    auth_type: PropTypes.string,
    transport: PropTypes.string,
    is_verified: PropTypes.bool,
    is_active: PropTypes.bool,
    last_error: PropTypes.string,
    discovered_tools: PropTypes.array,
    enabled_tools: PropTypes.array,
  }).isRequired,
  onEdit: PropTypes.func.isRequired,
  onDelete: PropTypes.func.isRequired,
  onRefresh: PropTypes.func.isRequired,
};

// ---------------------------------------------------------------------------
// Connector Editor (inline)
// ---------------------------------------------------------------------------
function ConnectorEditor({ connector, preset, onSaved, onCancel }) {
  const isEdit = !!connector;
  const [form, setForm] = useState(DEFAULT_CONNECTOR_FORM);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (connector) {
      setForm({
        name: connector.name || "",
        server_url: connector.server_url || "",
        transport: connector.transport || "streamable_http",
        auth_type: connector.auth_type || "none",
        auth_header_name: connector.auth_header_name || "",
        auth_header_value: connector.auth_header_value || "",
      });
    } else if (preset) {
      setForm(preset.values);
    } else {
      setForm(DEFAULT_CONNECTOR_FORM);
    }
    setError(null);
  }, [connector, preset]);

  const handleChange = (field) => (e) =>
    setForm((p) => ({ ...p, [field]: e.target.value }));

  const handleSave = async () => {
    if (!form.name.trim()) {
      setError("Name is required");
      return;
    }
    if (!form.server_url.trim()) {
      setError("Server URL is required");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      let savedId = null;
      if (isEdit) {
        await updateConnector(connector.id, form);
      } else {
        const response = await createConnector(form);
        savedId = response?.result?.id || response?.id || null;
      }
      onSaved(savedId);
    } catch (err) {
      setError(
        err?.response?.data?.detail ||
          err?.response?.data?.error ||
          err?.message ||
          "Failed to save",
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <Box
      sx={{
        flex: 1,
        display: "flex",
        flexDirection: "column",
        overflow: "auto",
        p: 3,
      }}
    >
      <Typography sx={{ fontSize: 16, fontWeight: 700, mb: 2.5 }}>
        {isEdit ? "Edit Connector" : "Add Connector"}
      </Typography>

      {error && (
        <Alert severity="error" sx={{ mb: 2, fontSize: 12 }}>
          {error}
        </Alert>
      )}

      <Typography
        sx={{ fontSize: 12, fontWeight: 600, color: "text.secondary", mb: 0.5 }}
      >
        Name *
      </Typography>
      <TextField
        size="small"
        fullWidth
        placeholder="e.g. Linear, GitHub"
        value={form.name}
        onChange={handleChange("name")}
        sx={{
          mb: 2,
          "& .MuiInputBase-root": { fontSize: 13, borderRadius: "8px" },
        }}
      />

      <Typography
        sx={{ fontSize: 12, fontWeight: 600, color: "text.secondary", mb: 0.5 }}
      >
        Server URL *
      </Typography>
      <TextField
        size="small"
        fullWidth
        placeholder="https://mcp.example.com/mcp"
        value={form.server_url}
        onChange={handleChange("server_url")}
        sx={{
          mb: 2,
          "& .MuiInputBase-root": {
            fontSize: 13,
            borderRadius: "8px",
            fontFamily: "monospace",
          },
        }}
      />

      <Typography
        sx={{ fontSize: 12, fontWeight: 600, color: "text.secondary", mb: 0.5 }}
      >
        Transport
      </Typography>
      <FormControl size="small" fullWidth sx={{ mb: 2 }}>
        <Select
          value={form.transport}
          onChange={handleChange("transport")}
          sx={{ fontSize: 13, borderRadius: "8px" }}
        >
          <MenuItem value="streamable_http">Streamable HTTP</MenuItem>
          <MenuItem value="sse">SSE (Legacy)</MenuItem>
        </Select>
      </FormControl>

      <Typography
        sx={{ fontSize: 12, fontWeight: 600, color: "text.secondary", mb: 0.5 }}
      >
        Authentication
      </Typography>
      <FormControl size="small" fullWidth sx={{ mb: 2 }}>
        <Select
          value={form.auth_type}
          onChange={handleChange("auth_type")}
          sx={{ fontSize: 13, borderRadius: "8px" }}
        >
          <MenuItem value="none">None</MenuItem>
          <MenuItem value="oauth">OAuth 2.0</MenuItem>
          <MenuItem value="api_key">API Key</MenuItem>
          <MenuItem value="bearer">Bearer Token</MenuItem>
        </Select>
      </FormControl>

      {form.auth_type === "api_key" && (
        <>
          <Typography
            sx={{
              fontSize: 12,
              fontWeight: 600,
              color: "text.secondary",
              mb: 0.5,
            }}
          >
            Header Name
          </Typography>
          <TextField
            size="small"
            fullWidth
            placeholder="X-API-Key"
            value={form.auth_header_name}
            onChange={handleChange("auth_header_name")}
            sx={{
              mb: 2,
              "& .MuiInputBase-root": { fontSize: 13, borderRadius: "8px" },
            }}
          />
          <Typography
            sx={{
              fontSize: 12,
              fontWeight: 600,
              color: "text.secondary",
              mb: 0.5,
            }}
          >
            Header Value
          </Typography>
          <TextField
            size="small"
            fullWidth
            type="password"
            placeholder="sk-..."
            value={form.auth_header_value}
            onChange={handleChange("auth_header_value")}
            sx={{
              mb: 2,
              "& .MuiInputBase-root": { fontSize: 13, borderRadius: "8px" },
            }}
          />
        </>
      )}

      {form.auth_type === "bearer" && (
        <>
          <Typography
            sx={{
              fontSize: 12,
              fontWeight: 600,
              color: "text.secondary",
              mb: 0.5,
            }}
          >
            Bearer Token
          </Typography>
          <TextField
            size="small"
            fullWidth
            type="password"
            placeholder="token..."
            value={form.auth_header_value}
            onChange={handleChange("auth_header_value")}
            sx={{
              mb: 2,
              "& .MuiInputBase-root": { fontSize: 13, borderRadius: "8px" },
            }}
          />
        </>
      )}

      <Box sx={{ display: "flex", gap: 1, mt: 1 }}>
        <Button
          variant="contained"
          size="small"
          onClick={handleSave}
          disabled={saving}
          sx={{
            textTransform: "none",
            borderRadius: "8px",
            fontSize: 13,
            px: 3,
          }}
        >
          {saving ? "Saving..." : isEdit ? "Save" : "Create"}
        </Button>
        <Button
          variant="outlined"
          size="small"
          onClick={onCancel}
          sx={{ textTransform: "none", borderRadius: "8px", fontSize: 13 }}
        >
          Cancel
        </Button>
      </Box>
    </Box>
  );
}

ConnectorEditor.propTypes = {
  connector: PropTypes.shape({
    id: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
    name: PropTypes.string,
    server_url: PropTypes.string,
    transport: PropTypes.string,
    auth_type: PropTypes.string,
    auth_header_name: PropTypes.string,
    auth_header_value: PropTypes.string,
  }),
  preset: PropTypes.shape({
    values: PropTypes.object.isRequired,
  }),
  onSaved: PropTypes.func.isRequired,
  onCancel: PropTypes.func.isRequired,
};

ConnectorEditor.defaultProps = {
  connector: null,
  preset: null,
};
