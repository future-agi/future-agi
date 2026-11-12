import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import {
  isoToLocalInput,
  localInputToIso,
  timeZoneLabel,
} from "../logs/explorerFilters";
import { MAX_ANALYTICS_WINDOW_MS } from "./queryState";

// Mounted only while open: Cancel discards the draft, reopening reads the URL.
export default function CustomAnalyticsRangeDialog({
  start,
  end,
  onApply,
  onClose,
}) {
  const [from, setFrom] = useState(() => isoToLocalInput(start));
  const [to, setTo] = useState(() => isoToLocalInput(end));
  const asInstant = (value, original) => {
    if (!value) return "";
    if (value === isoToLocalInput(original)) return original;
    const iso = localInputToIso(value);
    // Reject normalized invalid dates or local times skipped by a DST change.
    return iso && isoToLocalInput(iso) === value ? iso : "";
  };
  const nextStart = asInstant(from, start);
  const nextEnd = asInstant(to, end);
  const span = Date.parse(nextEnd) - Date.parse(nextStart);
  const error =
    !nextStart || !nextEnd
      ? "Choose valid From and To times."
      : span <= 0
        ? "To must be after From."
        : span > MAX_ANALYTICS_WINDOW_MS
          ? "Choose an interval of at most 1,999 days."
          : "";

  return (
    <Dialog
      open
      onClose={onClose}
      fullWidth
      maxWidth="sm"
      aria-labelledby="custom-analytics-title"
    >
      <DialogTitle id="custom-analytics-title">Custom interval</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ pt: 1 }}>
          <Typography typography="s2" color="text.secondary">
            Times are in {timeZoneLabel()}. This creates a fixed snapshot;
            Refresh keeps these bounds.
          </Typography>
          <TextField
            label="From"
            type="datetime-local"
            value={from}
            onChange={(event) => setFrom(event.target.value)}
            InputLabelProps={{ shrink: true }}
          />
          <TextField
            label="To"
            type="datetime-local"
            value={to}
            onChange={(event) => setTo(event.target.value)}
            InputLabelProps={{ shrink: true }}
            error={Boolean(error)}
            helperText={error || " "}
          />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="contained"
          disabled={Boolean(error)}
          onClick={() => {
            if (!error) onApply({ start: nextStart, end: nextEnd });
          }}
        >
          Apply interval
        </Button>
      </DialogActions>
    </Dialog>
  );
}

CustomAnalyticsRangeDialog.propTypes = {
  start: PropTypes.string.isRequired,
  end: PropTypes.string.isRequired,
  onApply: PropTypes.func.isRequired,
  onClose: PropTypes.func.isRequired,
};
