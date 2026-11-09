import React, { useState } from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import IconButton from "@mui/material/IconButton";
import Stack from "@mui/material/Stack";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import { enqueueSnackbar } from "notistack";

import FaIcon from "../providers/ui/FaIcon";
import { GATEWAY_MONO } from "../utils/fonts";

async function copyText(text) {
  // navigator.clipboard is missing on plain-http hosts other than localhost.
  if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
  await navigator.clipboard.writeText(text);
}

function Secret({ label, value, copyLabel }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = async () => {
    try {
      await copyText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      enqueueSnackbar("Copy did not work here: select the text and copy it.", {
        variant: "warning",
      });
    }
  };
  return (
    <Box>
      <Typography typography="s2" color="text.secondary" mb={0.5}>
        {label}
      </Typography>
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 1,
          px: 1.5,
          py: 1,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          bgcolor: "background.neutral",
        }}
      >
        <Typography
          component="code"
          typography="s2_1"
          sx={{
            flex: 1,
            minWidth: 0,
            fontFamily: GATEWAY_MONO,
            overflowWrap: "anywhere",
            userSelect: "all",
            color: "text.primary",
          }}
        >
          {value}
        </Typography>
        <Tooltip title={copied ? "Copied" : copyLabel}>
          <IconButton size="small" aria-label={copyLabel} onClick={handleCopy}>
            <FaIcon name={copied ? "check" : "copy"} />
          </IconButton>
        </Tooltip>
      </Box>
    </Box>
  );
}

Secret.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.string.isRequired,
  copyLabel: PropTypes.string.isRequired,
};

// The one time a new API key's secret is on screen (after Create or Rotate).
// Escape and the backdrop do not close it: only Done does, so the only copy
// of the key is never thrown away by a stray click.
export default function OneTimeKeyReveal({ reveal, baseUrl, onDone }) {
  const open = Boolean(reveal);
  return (
    <Dialog
      open={open}
      onClose={() => {}}
      disableEscapeKeyDown
      maxWidth="sm"
      fullWidth
    >
      <DialogTitle>Copy your new key</DialogTitle>
      {reveal && (
        <DialogContent>
          <Stack spacing={2} mt={0.5}>
            <Typography typography="s1" color="text.secondary">
              This is the only time the secret for{" "}
              <strong>{reveal.name}</strong> is shown. Store it somewhere safe:
              it will not be shown again.
            </Typography>
            <Secret
              label="API key"
              value={reveal.secret}
              copyLabel="Copy key"
            />
            {baseUrl && (
              <Secret
                label="Base URL (OpenAI-compatible)"
                value={baseUrl}
                copyLabel="Copy base URL"
              />
            )}
            {(reveal.notes || []).map((note) => (
              <Alert key={note.text} severity={note.severity}>
                {note.text}
              </Alert>
            ))}
          </Stack>
        </DialogContent>
      )}
      <DialogActions>
        <Button variant="contained" color="primary" onClick={onDone}>
          Done
        </Button>
      </DialogActions>
    </Dialog>
  );
}

OneTimeKeyReveal.propTypes = {
  reveal: PropTypes.shape({
    name: PropTypes.string.isRequired,
    secret: PropTypes.string.isRequired,
    notes: PropTypes.arrayOf(
      PropTypes.shape({
        severity: PropTypes.oneOf(["info", "warning", "success"]).isRequired,
        text: PropTypes.string.isRequired,
      }),
    ),
  }),
  baseUrl: PropTypes.string,
  onDone: PropTypes.func.isRequired,
};
