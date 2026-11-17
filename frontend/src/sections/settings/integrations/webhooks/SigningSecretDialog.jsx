import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  MenuItem,
  Stack,
  Tab,
  Tabs,
  TextField,
  Typography,
} from "@mui/material";
import { useSendTest } from "src/api/webhooks";
import { copyToClipboard } from "src/utils/utils";
import { NO_REPLAY, RAW_BODY_NOTE, SECRET_ONCE, VERIFY_SNIPPETS } from "./copy";
import { apiError } from "./form";
import TestResult from "./TestResult";
import HttpUrlWarning from "./HttpUrlWarning";
import { downloadEnvLine } from "./text";

const TABS = [
  { id: "sdk", label: "Python SDK" },
  { id: "standard", label: "Standard Webhooks library" },
  { id: "stdlib", label: "10 lines, no library" },
];

export const COPY_FAILED =
  "The secret was not copied: this browser blocked the clipboard. Select it and copy it yourself, or download it as an .env line, before you close this dialog.";

// Shown once, after Save or a rotation (DESIGN.md section 11.2). The secret is
// held only in this dialog's props and is gone when it closes. Session replays
// never record it (NO_REPLAY).
export default function SigningSecretDialog({
  open,
  secret,
  endpoint,
  onClose,
  title,
}) {
  const [tab, setTab] = useState("sdk");
  // "", "copied" or "failed": "Copied" only after the clipboard took it.
  const [copied, setCopied] = useState("");
  const firstType = endpoint?.subscriptions?.[0]?.event_type;
  const types = (endpoint?.subscriptions || [])
    .map((s) => s.event_type)
    .filter((t) => !t.endsWith("*"));
  const [eventType, setEventType] = useState("");
  const test = useSendTest();
  const { reset: resetTest } = test;

  // The dialog stays mounted between webhooks and rotations: a new secret starts
  // clean, never with the last one's "Copied" or the last test's answer.
  useEffect(() => {
    setCopied("");
    setEventType("");
    setTab("sdk");
    resetTest();
  }, [secret, resetTest]);

  const runTest = () =>
    test.mutate({
      id: endpoint.id,
      eventType: eventType || types[0] || firstType,
    });

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>{title || "Signing secret"}</DialogTitle>
      <DialogContent dividers>
        <HttpUrlWarning urlScheme={endpoint?.url_scheme} />
        <Alert severity="warning" sx={{ mb: 2 }}>
          {SECRET_ONCE}
        </Alert>
        <Stack direction="row" spacing={1} alignItems="center">
          <TextField
            value={secret || ""}
            size="small"
            fullWidth
            className={NO_REPLAY}
            InputProps={{ readOnly: true, sx: { fontFamily: "monospace" } }}
            inputProps={{ "aria-label": "Signing secret" }}
          />
          <Button
            variant="outlined"
            onClick={async () => {
              const ok = await copyToClipboard(secret);
              setCopied(ok ? "copied" : "failed");
            }}
          >
            {copied === "copied" ? "Copied" : "Copy"}
          </Button>
          <Button
            variant="outlined"
            onClick={() => downloadEnvLine(secret)}
            sx={{ whiteSpace: "nowrap" }}
          >
            Download as .env line
          </Button>
        </Stack>
        {copied === "failed" && (
          <Alert severity="error" sx={{ mt: 1 }} role="alert">
            {COPY_FAILED}
          </Alert>
        )}

        <Tabs value={tab} onChange={(_e, v) => setTab(v)} sx={{ mt: 2 }}>
          {TABS.map((t) => (
            <Tab key={t.id} value={t.id} label={t.label} />
          ))}
        </Tabs>
        <Box
          component="pre"
          sx={{
            p: 1.5,
            m: 0,
            bgcolor: "background.neutral",
            fontSize: 12,
            overflow: "auto",
            fontFamily: "monospace",
          }}
        >
          {VERIFY_SNIPPETS[tab]}
        </Box>
        <Typography variant="caption" color="text.secondary">
          {RAW_BODY_NOTE}
        </Typography>

        {endpoint && types.length > 0 && (
          <Stack direction="row" spacing={1} alignItems="center" sx={{ mt: 2 }}>
            {types.length > 1 && (
              <TextField
                select
                size="small"
                label="Event"
                value={eventType || types[0]}
                onChange={(e) => setEventType(e.target.value)}
                sx={{ minWidth: 240 }}
              >
                {types.map((t) => (
                  <MenuItem key={t} value={t}>
                    {t}
                  </MenuItem>
                ))}
              </TextField>
            )}
            <Button
              variant="contained"
              onClick={runTest}
              disabled={test.isPending}
            >
              {test.isPending ? "Sending…" : "Send a test now"}
            </Button>
          </Stack>
        )}
        {test.isError && (
          <Alert severity="error" sx={{ mt: 1 }}>
            {apiError(test.error, "The test could not be sent.").message}
          </Alert>
        )}
        {test.data && (
          <TestResult
            result={test.data.data.result}
            status={test.data.status}
          />
        )}
      </DialogContent>
      <DialogActions>
        <Button variant="contained" onClick={onClose}>
          Done
        </Button>
      </DialogActions>
    </Dialog>
  );
}

SigningSecretDialog.propTypes = {
  open: PropTypes.bool,
  secret: PropTypes.string,
  endpoint: PropTypes.object,
  onClose: PropTypes.func,
  title: PropTypes.string,
};
