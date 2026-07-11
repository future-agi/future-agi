import React, { useState, useEffect, useCallback } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import Alert from "@mui/material/Alert";
import TextField from "@mui/material/TextField";
import IconButton from "@mui/material/IconButton";
import Tooltip from "@mui/material/Tooltip";
import Table from "@mui/material/Table";
import TableBody from "@mui/material/TableBody";
import TableCell from "@mui/material/TableCell";
import TableHead from "@mui/material/TableHead";
import TableRow from "@mui/material/TableRow";
import { useTheme } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import { fDateTime } from "src/utils/format-time";
import {
  fetchMemories,
  saveMemory,
  deleteMemory,
} from "src/sections/falcon-ai/hooks/useFalconAPI";
import { getSourceBadge } from "./utils";

function getActionErrorMessage(error, fallback) {
  return (
    error?.response?.data?.detail ||
    error?.response?.data?.error ||
    error?.response?.data?.message ||
    error?.message ||
    fallback
  );
}

// ---------------------------------------------------------------------------
// Main Page — Settings → Falcon AI Memory
// ---------------------------------------------------------------------------
export default function MemorySettingsPage() {
  const [memories, setMemories] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [actionError, setActionError] = useState(null);
  const [editingId, setEditingId] = useState(null);
  const [isAdding, setIsAdding] = useState(false);

  const loadMemories = useCallback(async ({ showSpinner = true } = {}) => {
    try {
      if (showSpinner) setLoading(true);
      const data = await fetchMemories();
      const results = data?.results || data || [];
      setMemories(Array.isArray(results) ? results : []);
      setError(null);
    } catch {
      setError("Failed to load memories.");
    } finally {
      if (showSpinner) setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadMemories();
  }, [loadMemories]);

  const handleSave = async (key, value) => {
    setActionError(null);
    try {
      await saveMemory(key, value);
      setEditingId(null);
      setIsAdding(false);
      await loadMemories({ showSpinner: false });
    } catch (err) {
      setActionError(getActionErrorMessage(err, "Failed to save memory."));
    }
  };

  const handleDelete = async (id) => {
    setActionError(null);
    try {
      await deleteMemory(id);
      setMemories((prev) => prev.filter((m) => m.id !== id));
    } catch (err) {
      setActionError(getActionErrorMessage(err, "Failed to delete memory."));
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
            Falcon AI Memory
          </Typography>
          <Typography
            sx={{
              typography: "s1",
              fontWeight: "fontWeightRegular",
              color: "text.secondary",
              mt: 0.5,
            }}
          >
            Everything Falcon remembers about this workspace. Memories marked
            &ldquo;Falcon&rdquo; were saved by the agent during chat — you can
            edit or delete any of them.
          </Typography>
        </Box>
        <Button
          type="button"
          variant="contained"
          startIcon={<Iconify icon="mdi:plus" width={18} />}
          onClick={() => {
            setIsAdding(true);
            setEditingId(null);
          }}
          sx={{ flexShrink: 0 }}
        >
          Add Memory
        </Button>
      </Box>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}
      {actionError && (
        <Alert
          severity="error"
          sx={{ mb: 2, fontSize: 12 }}
          onClose={() => setActionError(null)}
        >
          {actionError}
        </Alert>
      )}

      {isAdding && (
        <MemoryForm
          onSave={handleSave}
          onCancel={() => setIsAdding(false)}
          existingKeys={memories.map((m) => m.key)}
        />
      )}

      {memories.length === 0 && !isAdding ? (
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            py: 8,
            px: 4,
            border: (t) => `1px solid ${t.palette.divider}`,
            borderRadius: "12px",
            bgcolor: "background.paper",
          }}
        >
          <Iconify
            icon="mdi:brain"
            width={48}
            sx={{ color: "text.disabled", mb: 2 }}
          />
          <Typography sx={{ fontSize: 15, fontWeight: 600, mb: 0.5 }}>
            No memories yet
          </Typography>
          <Typography
            sx={{
              fontSize: 13,
              color: "text.secondary",
              textAlign: "center",
              maxWidth: 380,
            }}
          >
            Falcon saves durable preferences and workspace facts as you chat
            (&ldquo;remember that&hellip;&rdquo;). Anything it saves shows up
            here, attributed and editable.
          </Typography>
        </Box>
      ) : (
        memories.length > 0 && (
          <Box
            sx={{
              border: (t) => `1px solid ${t.palette.divider}`,
              borderRadius: "12px",
              overflow: "hidden",
              bgcolor: "background.paper",
            }}
          >
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell sx={{ fontSize: 12, fontWeight: 600 }}>
                    Key
                  </TableCell>
                  <TableCell sx={{ fontSize: 12, fontWeight: 600 }}>
                    Value
                  </TableCell>
                  <TableCell sx={{ fontSize: 12, fontWeight: 600 }}>
                    Source
                  </TableCell>
                  <TableCell sx={{ fontSize: 12, fontWeight: 600 }}>
                    Updated
                  </TableCell>
                  <TableCell align="right" sx={{ width: 96 }} />
                </TableRow>
              </TableHead>
              <TableBody>
                {memories.map((memory) => (
                  <MemoryRow
                    key={memory.id}
                    memory={memory}
                    isEditing={editingId === memory.id}
                    onEdit={() => {
                      setEditingId(memory.id);
                      setIsAdding(false);
                    }}
                    onCancelEdit={() => setEditingId(null)}
                    onSave={handleSave}
                    onDelete={() => handleDelete(memory.id)}
                  />
                ))}
              </TableBody>
            </Table>
          </Box>
        )
      )}
    </Box>
  );
}

// ---------------------------------------------------------------------------
// Memory row (read + inline value edit)
// ---------------------------------------------------------------------------
function MemoryRow({
  memory,
  isEditing,
  onEdit,
  onCancelEdit,
  onSave,
  onDelete,
}) {
  const theme = useTheme();
  const badge = getSourceBadge(memory.source);
  const [value, setValue] = useState(memory.value || "");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (isEditing) setValue(memory.value || "");
  }, [isEditing, memory.value]);

  const handleSave = async () => {
    if (!value.trim()) return;
    setSaving(true);
    try {
      // Same key → server upsert; source flips to "user".
      await onSave(memory.key, value.trim());
    } finally {
      setSaving(false);
    }
  };

  return (
    <TableRow
      hover
      sx={{ "&:hover": { bgcolor: theme.palette.action.hover } }}
    >
      <TableCell
        sx={{
          fontSize: 12,
          fontFamily: "monospace",
          fontWeight: 600,
          whiteSpace: "nowrap",
          verticalAlign: "top",
          py: 1.25,
        }}
      >
        {memory.key}
      </TableCell>
      <TableCell sx={{ fontSize: 13, py: 1.25 }}>
        {isEditing ? (
          <TextField
            size="small"
            fullWidth
            multiline
            maxRows={4}
            autoFocus
            value={value}
            onChange={(e) => setValue(e.target.value)}
            inputProps={{ "aria-label": `Edit value for ${memory.key}` }}
            sx={{
              "& .MuiInputBase-root": { fontSize: 13, borderRadius: "8px" },
            }}
          />
        ) : (
          memory.value
        )}
      </TableCell>
      <TableCell sx={{ verticalAlign: "top", py: 1.25 }}>
        <Chip
          label={badge.label}
          size="small"
          variant="outlined"
          color={badge.color}
          sx={{ height: 20, fontSize: 10, fontWeight: 600 }}
        />
      </TableCell>
      <TableCell
        sx={{
          fontSize: 12,
          color: "text.secondary",
          whiteSpace: "nowrap",
          verticalAlign: "top",
          py: 1.25,
        }}
      >
        {memory.updated_at ? fDateTime(memory.updated_at) : "—"}
      </TableCell>
      <TableCell align="right" sx={{ whiteSpace: "nowrap", py: 1.25 }}>
        {isEditing ? (
          <>
            <Tooltip title="Save">
              <span>
                <IconButton
                  size="small"
                  color="primary"
                  onClick={handleSave}
                  disabled={saving || !value.trim()}
                  aria-label={`Save ${memory.key}`}
                >
                  <Iconify icon="mdi:check" width={16} />
                </IconButton>
              </span>
            </Tooltip>
            <Tooltip title="Cancel">
              <IconButton
                size="small"
                onClick={onCancelEdit}
                aria-label={`Cancel editing ${memory.key}`}
              >
                <Iconify icon="mdi:close" width={16} />
              </IconButton>
            </Tooltip>
          </>
        ) : (
          <>
            <Tooltip title="Edit">
              <IconButton
                size="small"
                onClick={onEdit}
                aria-label={`Edit ${memory.key}`}
              >
                <Iconify icon="mdi:pencil-outline" width={16} />
              </IconButton>
            </Tooltip>
            <Tooltip title="Delete">
              <IconButton
                size="small"
                color="error"
                onClick={onDelete}
                aria-label={`Delete ${memory.key}`}
              >
                <Iconify icon="mdi:delete-outline" width={16} />
              </IconButton>
            </Tooltip>
          </>
        )}
      </TableCell>
    </TableRow>
  );
}

MemoryRow.propTypes = {
  memory: PropTypes.shape({
    id: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
    key: PropTypes.string,
    value: PropTypes.string,
    source: PropTypes.string,
    updated_at: PropTypes.string,
  }).isRequired,
  isEditing: PropTypes.bool.isRequired,
  onEdit: PropTypes.func.isRequired,
  onCancelEdit: PropTypes.func.isRequired,
  onSave: PropTypes.func.isRequired,
  onDelete: PropTypes.func.isRequired,
};

// ---------------------------------------------------------------------------
// Add-memory inline form
// ---------------------------------------------------------------------------
function MemoryForm({ onSave, onCancel, existingKeys }) {
  const [key, setKey] = useState("");
  const [value, setValue] = useState("");
  const [saving, setSaving] = useState(false);

  const trimmedKey = key.trim();
  const keyExists = existingKeys.includes(trimmedKey);
  const canSave = Boolean(trimmedKey) && Boolean(value.trim());

  const handleSave = async () => {
    if (!canSave) return;
    setSaving(true);
    try {
      await onSave(trimmedKey, value.trim());
    } finally {
      setSaving(false);
    }
  };

  return (
    <Box
      sx={{
        p: 2,
        mb: 2,
        border: (t) => `1px solid ${t.palette.divider}`,
        borderRadius: "12px",
        bgcolor: "background.paper",
      }}
    >
      <Typography sx={{ fontSize: 13, fontWeight: 700, mb: 1.5 }}>
        Add Memory
      </Typography>
      <Box sx={{ display: "flex", gap: 1.5, alignItems: "flex-start" }}>
        <TextField
          size="small"
          placeholder="key_in_snake_case"
          value={key}
          onChange={(e) => setKey(e.target.value)}
          inputProps={{ "aria-label": "Memory key" }}
          helperText={keyExists ? "Existing key — saving will overwrite it" : " "}
          sx={{
            width: 240,
            "& .MuiInputBase-root": {
              fontSize: 13,
              borderRadius: "8px",
              fontFamily: "monospace",
            },
          }}
        />
        <TextField
          size="small"
          fullWidth
          multiline
          maxRows={4}
          placeholder="What should Falcon remember?"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          inputProps={{ "aria-label": "Memory value" }}
          sx={{
            "& .MuiInputBase-root": { fontSize: 13, borderRadius: "8px" },
          }}
        />
        <Button
          type="button"
          variant="contained"
          size="small"
          onClick={handleSave}
          disabled={saving || !canSave}
          sx={{ textTransform: "none", borderRadius: "8px", flexShrink: 0 }}
        >
          {saving ? "Saving..." : "Save"}
        </Button>
        <Button
          type="button"
          variant="outlined"
          size="small"
          onClick={onCancel}
          sx={{ textTransform: "none", borderRadius: "8px", flexShrink: 0 }}
        >
          Cancel
        </Button>
      </Box>
    </Box>
  );
}

MemoryForm.propTypes = {
  onSave: PropTypes.func.isRequired,
  onCancel: PropTypes.func.isRequired,
  existingKeys: PropTypes.arrayOf(PropTypes.string).isRequired,
};
