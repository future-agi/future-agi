import React, { useId, useRef, useState } from "react";
import PropTypes from "prop-types";
import { isEqual } from "lodash";
import {
  Alert,
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  IconButton,
  Stack,
  Switch,
  TextField,
  Typography,
} from "@mui/material";

import { GatewayIcon } from "../../shell/gateway-icons";
import { GATEWAY_MONO } from "../../utils/fonts";
import { STORED_MCP_NOTICE } from "./notices";

// NEW: site claims review required for the stored-settings labels and help.
const SWITCHES = [
  ["enabled", "Enabled in stored settings"],
  ["validate_inputs", "Validate tool inputs"],
  ["validate_outputs", "Validate tool outputs"],
];
const LISTS = [
  ["blocked_tools", "Blocked tools"],
  ["allowed_servers", "Allowed servers"],
];
const SWITCH_KEYS = new Set(SWITCHES.map(([key]) => key));
const LIST_KEYS = new Set(LISTS.map(([key]) => key));
const listText = (value) => (Array.isArray(value) ? value.join("\n") : "");
// One name per line, as the save sends a list.
const listOf = (text) =>
  text
    .split("\n")
    .map((name) => name.trim())
    .filter(Boolean);
const isPlainObject = (value) =>
  value !== null && typeof value === "object" && !Array.isArray(value);
// The stored per-tool limits as rows. A stored value of another shape (the
// API stores any JSON there) has no rows: it is shown as stored, read-only,
// and kept unless a row is added, which replaces it with the rows.
const limitsOf = (value) => (isPlainObject(value) ? value : {});
const isUnreadable = (value) => value != null && !isPlainObject(value);
const LIMITS_UNREADABLE =
  "The stored per-tool limits are not in a form this page can edit. Adding a limit here replaces them.";
// A per-minute limit as typed: a whole number greater than zero, or null.
const limitFrom = (text) => {
  const trimmed = text.trim();
  if (!/^\d+$/.test(trimmed)) return null;
  const limit = Number(trimmed);
  return Number.isSafeInteger(limit) && limit > 0 ? limit : null;
};
const LIMIT_INVALID = "Enter a whole number greater than zero.";
const TOOL_MISSING = "Enter the tool's name.";
const LIMIT_PENDING = "Add this limit, or clear it, before saving.";
// A field as the dialog shows it and the save would send it: a switch is on
// only when true, a list is one name per line, the limits are an object.
const asSent = (key, value) => {
  if (SWITCH_KEYS.has(key)) return value === true;
  if (LIST_KEYS.has(key)) return listOf(listText(value));
  if (key === "tool_rate_limits") return value || {};
  return value;
};
// NEW: site claims review required for this refusal sentence.
const CHANGED_SINCE_OPENED =
  "Someone changed these guardrail settings after you opened them. Reload to see the saved version; your unsaved edits here are kept until you close.";

// Restore v1's storage editor, not an enforcement switch. Only fields changed
// by the user replace stored values; legacy and unknown fields are preserved.
// Mounted fresh on each explicit open, so background refetches do not erase a draft.
// `changed`: the last save was refused because the stored settings changed
// after the dialog opened. Reload calls onReload, which re-reads them and
// resolves to { stored } (or null when the re-read failed); the draft is then
// rebased on what was read: the fields the user changed keep the edits, every
// other field takes the re-read value, a field changed back to the value the
// draft was made on included. Only that explicit Reload rebases.
export default function StoredSettingsDialog({
  stored,
  busy,
  canEdit,
  changed,
  connectedServers = [],
  onReload,
  onClose,
  onSave,
}) {
  const titleId = useId();
  const noteId = useId();
  const limitsId = useId();
  const [draft, setDraft] = useState(() => ({ ...(stored || {}) }));
  const [lists, setLists] = useState(() =>
    Object.fromEntries(LISTS.map(([key]) => [key, listText(stored?.[key])])),
  );
  // The per-tool limit being typed, not yet added as a row.
  const [newTool, setNewTool] = useState("");
  const [newLimit, setNewLimit] = useState("");
  const [limitError, setLimitError] = useState(null);
  // The fields the user changed since the draft was made, each with the
  // value the save would send.
  const touched = useRef(new Map());
  // The stored settings the draft was made on: the ones the dialog opened
  // on, then the ones each Reload read.
  const opened = useRef(stored);
  const locked = busy || !canEdit;
  const set = (key, value) => {
    touched.current.set(key, value);
    setDraft((previous) => ({ ...previous, [key]: value }));
  };
  const rebase = (next) => {
    const base = isPlainObject(next) ? next : {};
    // A field changed back to the value the draft was made on, as the save
    // would send it, is not a change: it takes the re-read value, so the old
    // value is not written over another admin's change to that field.
    for (const [key, value] of touched.current) {
      const unchanged =
        value !== null &&
        isEqual(asSent(key, value), asSent(key, opened.current?.[key]));
      if (unchanged) touched.current.delete(key);
    }
    opened.current = base;
    const kept = (key) => touched.current.has(key);
    setDraft((previous) => {
      const rebased = { ...base };
      for (const key of touched.current.keys()) {
        if (key in previous) rebased[key] = previous[key];
      }
      return rebased;
    });
    setLists((previous) =>
      Object.fromEntries(
        LISTS.map(([key]) => [
          key,
          kept(key) ? previous[key] : listText(base[key]),
        ]),
      ),
    );
  };
  const reload = async () => {
    const reread = await onReload?.();
    if (reread) rebase(reread.stored);
  };
  const limits = limitsOf(draft.tool_rate_limits);
  const pendingLimit = Boolean(newTool.trim() || newLimit.trim());
  // A tool already limited takes the new limit.
  const addLimit = () => {
    const tool = newTool.trim();
    const limit = limitFrom(newLimit);
    if (!tool) {
      setLimitError(TOOL_MISSING);
      return;
    }
    if (limit === null) {
      setLimitError(LIMIT_INVALID);
      return;
    }
    set("tool_rate_limits", { ...limits, [tool]: limit });
    setNewTool("");
    setNewLimit("");
    setLimitError(null);
  };
  const removeLimit = (tool) => {
    const next = { ...limits };
    delete next[tool];
    set("tool_rate_limits", next);
  };
  const changeList = (key, value) => {
    setLists((previous) => ({ ...previous, [key]: value }));
    set(key, listOf(value));
  };
  // The organization's connected servers not yet in Allowed servers, offered
  // as one-click additions.
  const allowed = listOf(lists.allowed_servers);
  const suggested = [...new Set(connectedServers)].filter(
    (id) => typeof id === "string" && id && !allowed.includes(id),
  );
  const blocked = locked || pendingLimit || Object.keys(draft).length === 0;

  return (
    <Dialog
      open
      onClose={busy ? undefined : onClose}
      fullWidth
      maxWidth="sm"
      aria-labelledby={titleId}
      aria-describedby={noteId}
    >
      <DialogTitle id={titleId}>Stored MCP guardrail settings</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ pt: 1 }}>
          {changed && (
            <Alert
              severity="warning"
              action={
                <Button
                  color="inherit"
                  size="small"
                  disabled={busy}
                  onClick={reload}
                >
                  Reload
                </Button>
              }
            >
              {CHANGED_SINCE_OPENED}
            </Alert>
          )}
          <Typography id={noteId} typography="s2" color="text.secondary">
            {STORED_MCP_NOTICE}
          </Typography>
          {SWITCHES.map(([key, label]) => (
            <FormControlLabel
              key={key}
              label={label}
              control={
                <Switch
                  checked={draft[key] === true}
                  disabled={locked}
                  onChange={(event) => set(key, event.target.checked)}
                />
              }
            />
          ))}
          {LISTS.map(([key, label]) => (
            <Stack key={key} spacing={1}>
              <TextField
                label={label}
                multiline
                minRows={2}
                value={lists[key]}
                disabled={locked}
                helperText="One name per line."
                onChange={(event) => changeList(key, event.target.value)}
              />
              {key === "allowed_servers" && suggested.length > 0 && (
                <Box
                  sx={{
                    display: "flex",
                    flexWrap: "wrap",
                    alignItems: "center",
                    gap: 0.75,
                  }}
                >
                  <Typography typography="s2" color="text.secondary">
                    Connected servers:
                  </Typography>
                  {suggested.map((id) => (
                    <Chip
                      key={id}
                      size="small"
                      variant="outlined"
                      label={id}
                      aria-label={`Add ${id} to Allowed servers`}
                      disabled={locked}
                      onClick={() =>
                        changeList(key, [...allowed, id].join("\n"))
                      }
                      sx={{ fontFamily: GATEWAY_MONO }}
                    />
                  ))}
                </Box>
              )}
            </Stack>
          ))}
          <Stack component="section" spacing={1} aria-labelledby={limitsId}>
            <Typography id={limitsId} typography="s1">
              Stored per-tool limits
            </Typography>
            <Typography typography="s2" color="text.secondary">
              Calls per minute for one tool.
            </Typography>
            {Object.keys(limits).length > 0 ? (
              <Stack component="ul" spacing={0.5} sx={{ m: 0, p: 0 }}>
                {Object.entries(limits).map(([tool, limit]) => (
                  <Box
                    component="li"
                    key={tool}
                    sx={{
                      display: "flex",
                      alignItems: "center",
                      gap: 1,
                      px: 1.25,
                      py: 0.5,
                      borderRadius: 1,
                      bgcolor: "background.neutral",
                      listStyle: "none",
                    }}
                  >
                    <Typography
                      typography="s2"
                      sx={{
                        flex: 1,
                        minWidth: 0,
                        fontFamily: GATEWAY_MONO,
                        overflowWrap: "anywhere",
                      }}
                    >
                      {tool}
                    </Typography>
                    <Typography typography="s2" color="text.secondary">
                      {`${limit} per minute`}
                    </Typography>
                    <IconButton
                      size="small"
                      aria-label={`Remove the limit for ${tool}`}
                      disabled={locked}
                      onClick={() => removeLimit(tool)}
                    >
                      <GatewayIcon name="close" size={16} />
                    </IconButton>
                  </Box>
                ))}
              </Stack>
            ) : isUnreadable(draft.tool_rate_limits) ? (
              <Stack spacing={0.5}>
                <Typography
                  typography="s2"
                  sx={{ fontFamily: GATEWAY_MONO, overflowWrap: "anywhere" }}
                >
                  {JSON.stringify(draft.tool_rate_limits)}
                </Typography>
                <Typography typography="s2" color="text.secondary">
                  {LIMITS_UNREADABLE}
                </Typography>
              </Stack>
            ) : (
              <Typography typography="s2" color="text.secondary">
                No per-tool limits stored.
              </Typography>
            )}
            <Stack
              direction={{ xs: "column", sm: "row" }}
              spacing={1}
              alignItems={{ sm: "flex-start" }}
            >
              <TextField
                size="small"
                label="Tool name"
                placeholder="server__tool"
                value={newTool}
                disabled={locked}
                error={limitError === TOOL_MISSING}
                onChange={(event) => {
                  setNewTool(event.target.value);
                  setLimitError(null);
                }}
                sx={{ flex: 2 }}
              />
              <TextField
                size="small"
                label="Per minute"
                placeholder="10"
                value={newLimit}
                disabled={locked}
                error={limitError === LIMIT_INVALID}
                inputProps={{ inputMode: "numeric" }}
                onChange={(event) => {
                  setNewLimit(event.target.value);
                  setLimitError(null);
                }}
                sx={{ flex: 1 }}
              />
              <Button
                size="small"
                variant="outlined"
                disabled={locked}
                onClick={addLimit}
                sx={{ alignSelf: { xs: "flex-start", sm: "center" } }}
              >
                Add limit
              </Button>
            </Stack>
            {(limitError || pendingLimit) && (
              <Typography
                typography="s2"
                color={limitError ? "error.main" : "text.secondary"}
                role={limitError ? "alert" : undefined}
              >
                {limitError || LIMIT_PENDING}
              </Typography>
            )}
          </Stack>
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>
          Cancel
        </Button>
        <Button
          variant="contained"
          disabled={blocked}
          onClick={() => !blocked && onSave(draft)}
        >
          Save stored settings
        </Button>
      </DialogActions>
    </Dialog>
  );
}

StoredSettingsDialog.propTypes = {
  stored: PropTypes.object,
  busy: PropTypes.bool,
  canEdit: PropTypes.bool,
  changed: PropTypes.bool,
  // Server ids from the MCP status, suggested for Allowed servers.
  connectedServers: PropTypes.arrayOf(PropTypes.string),
  onReload: PropTypes.func,
  onClose: PropTypes.func.isRequired,
  onSave: PropTypes.func.isRequired,
};
