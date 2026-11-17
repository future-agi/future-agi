// W7-4 remote scorers: the platform-made signing secret, shown exactly once
// (after create and after Rotate). The page keeps it only while open.
import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Typography,
} from "@mui/material";

export default function SigningSecretDialog({
  open,
  secret,
  rotatedUntil,
  onClose,
}) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(secret);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };
  return (
    <Dialog
      open={open}
      // The only time the secret shows: it closes on Done, never on a
      // click outside or Escape.
      onClose={(_, reason) => {
        if (reason === "backdropClick" || reason === "escapeKeyDown") return;
        onClose?.();
      }}
      disableEscapeKeyDown
      maxWidth="sm"
      fullWidth
      data-testid="signing-secret-dialog"
    >
      <DialogTitle sx={{ fontSize: 16 }}>Signing secret</DialogTitle>
      <DialogContent>
        <Typography variant="body2" sx={{ mb: 1.5 }}>
          Copy it now. You won&apos;t see it again.
        </Typography>
        <Box
          data-testid="signing-secret-value"
          sx={{
            fontFamily: "monospace",
            fontSize: 13,
            p: 1.25,
            borderRadius: 1,
            bgcolor: "background.neutral",
            wordBreak: "break-all",
            userSelect: "all",
          }}
        >
          {secret}
        </Box>
        <Typography
          variant="caption"
          color="text.secondary"
          component="div"
          sx={{ mt: 1.5 }}
        >
          Future AGI signs each call as X-FutureAGI-Signature:
          t=&lt;unix&gt;,v1=&lt;hex&gt;, an HMAC-SHA256 of
          &quot;&lt;t&gt;.&lt;body&gt;&quot; with this secret. Reject a t older
          than 5 minutes.
          {rotatedUntil
            ? ` The old secret still signs too, as a second v1, until ${new Date(rotatedUntil).toLocaleString()}.`
            : ""}
        </Typography>
      </DialogContent>
      <DialogActions>
        <Button onClick={copy} data-testid="signing-secret-copy">
          {copied ? "Copied" : "Copy"}
        </Button>
        <Button
          variant="contained"
          onClick={onClose}
          data-testid="signing-secret-done"
        >
          Done
        </Button>
      </DialogActions>
    </Dialog>
  );
}

SigningSecretDialog.propTypes = {
  open: PropTypes.bool,
  secret: PropTypes.string,
  rotatedUntil: PropTypes.string,
  onClose: PropTypes.func,
};
