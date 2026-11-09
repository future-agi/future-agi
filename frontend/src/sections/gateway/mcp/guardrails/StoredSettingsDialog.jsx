import React, { useId, useState } from "react";
import PropTypes from "prop-types";
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  Stack,
  Switch,
  TextField,
  Typography,
} from "@mui/material";

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
const listText = (value) => (Array.isArray(value) ? value.join("\n") : "");

// Restore v1's storage editor, not an enforcement switch. Only fields changed
// by the user replace stored values; legacy and unknown fields are preserved.
// Mounted fresh on each explicit open, so background refetches do not erase a draft.
export default function StoredSettingsDialog({
  stored,
  busy,
  canEdit,
  onClose,
  onSave,
}) {
  const titleId = useId();
  const noteId = useId();
  const [draft, setDraft] = useState(() => ({ ...(stored || {}) }));
  const [lists, setLists] = useState(() =>
    Object.fromEntries(LISTS.map(([key]) => [key, listText(stored?.[key])])),
  );
  const [limits, setLimits] = useState(() =>
    JSON.stringify(stored?.tool_rate_limits || {}, null, 2),
  );
  const [invalidLimits, setInvalidLimits] = useState(false);
  const locked = busy || !canEdit;
  const set = (key, value) =>
    setDraft((previous) => ({ ...previous, [key]: value }));
  const changeLimits = (value) => {
    setLimits(value);
    try {
      const parsed = JSON.parse(value);
      if (
        !parsed ||
        Array.isArray(parsed) ||
        typeof parsed !== "object" ||
        Object.values(parsed).some(
          (limit) => !Number.isSafeInteger(limit) || limit <= 0,
        )
      ) {
        setInvalidLimits(true);
        return;
      }
      set("tool_rate_limits", parsed);
      setInvalidLimits(false);
    } catch {
      setInvalidLimits(true);
    }
  };

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
            <TextField
              key={key}
              label={label}
              multiline
              minRows={2}
              value={lists[key]}
              disabled={locked}
              helperText="One name per line."
              onChange={(event) => {
                const value = event.target.value;
                setLists((previous) => ({ ...previous, [key]: value }));
                set(
                  key,
                  value
                    .split("\n")
                    .map((name) => name.trim())
                    .filter(Boolean),
                );
              }}
            />
          ))}
          <TextField
            label="Stored per-tool limits"
            multiline
            minRows={3}
            value={limits}
            disabled={locked}
            error={invalidLimits}
            // NEW: match v1's strictly positive per-tool limits.
            helperText={
              invalidLimits
                ? "Enter a JSON object with whole-number limits greater than zero."
                : "Tool names and per-minute limits as a JSON object."
            }
            onChange={(event) => changeLimits(event.target.value)}
          />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>
          Cancel
        </Button>
        <Button
          variant="contained"
          disabled={locked || invalidLimits || Object.keys(draft).length === 0}
          onClick={() => !locked && !invalidLimits && onSave(draft)}
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
  onClose: PropTypes.func.isRequired,
  onSave: PropTypes.func.isRequired,
};
