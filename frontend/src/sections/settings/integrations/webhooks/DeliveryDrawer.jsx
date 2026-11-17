import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Collapse,
  Divider,
  Drawer,
  IconButton,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { useRedeliver, useWebhookDelivery } from "src/api/webhooks";
import { copyToClipboard } from "src/utils/utils";
import { DELIVERY_STATUS, attemptLine } from "./copy";
import { apiError } from "./form";
import { curlOf, prettyJson, visibleControls } from "./text";

const mono = {
  fontFamily: "monospace",
  fontSize: 12,
  whiteSpace: "pre-wrap",
  wordBreak: "break-all",
  m: 0,
};

function AttemptRow({ attempt, host }) {
  const [open, setOpen] = useState(false);
  const hasExcerpt =
    attempt.response_body_excerpt != null &&
    attempt.response_body_excerpt !== "";
  return (
    <Box component="li" sx={{ listStyle: "none", py: 0.75 }}>
      <Stack direction="row" alignItems="center" spacing={1}>
        <Typography variant="body2" sx={{ flex: 1 }}>
          {attemptLine(attempt, host)}
        </Typography>
        {hasExcerpt && (
          <Button
            size="small"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
          >
            From your endpoint
          </Button>
        )}
      </Stack>
      {attempt.error_detail && (
        <Typography variant="caption" color="text.secondary">
          {attempt.error_detail}
        </Typography>
      )}
      {hasExcerpt && (
        <Collapse in={open}>
          {/* Rendered as text, never as HTML: React escapes it, and nothing here is a link. */}
          <Box
            component="pre"
            aria-label={`Response excerpt, attempt ${attempt.n}`}
            sx={{ ...mono, p: 1, mt: 0.5, bgcolor: "background.neutral" }}
          >
            {visibleControls(attempt.response_body_excerpt)}
          </Box>
        </Collapse>
      )}
    </Box>
  );
}

AttemptRow.propTypes = {
  attempt: PropTypes.object.isRequired,
  host: PropTypes.string,
};

export default function DeliveryDrawer({ deliveryId, endpoint, onClose }) {
  const {
    data: d,
    isLoading,
    isError,
  } = useWebhookDelivery(deliveryId, {
    refetchInterval: (q) => {
      const s = q.state.data?.data?.result?.status;
      return ["pending", "sending", "retrying"].includes(s) ? 2000 : false;
    },
  });
  const redeliver = useRedeliver(endpoint?.id);
  const [copied, setCopied] = useState("");
  const { reset: resetRedeliver } = redeliver;
  // The drawer stays mounted from one delivery to the next: each opens clean.
  useEffect(() => {
    setCopied("");
    resetRedeliver();
  }, [deliveryId, resetRedeliver]);
  const host = endpoint?.url_host;
  const status = d ? DELIVERY_STATUS[d.status] || { label: d.status } : null;

  // "Copied" only once the clipboard took it; a blocked clipboard says so.
  const copy = async (what, text) => {
    const ok = await copyToClipboard(text);
    setCopied(ok ? what : "failed");
  };

  return (
    <Drawer
      anchor="right"
      open={!!deliveryId}
      onClose={onClose}
      PaperProps={{ sx: { width: 720, maxWidth: "100vw" } }}
    >
      <Box sx={{ p: 2.5 }} role="dialog" aria-label="Delivery">
        <Stack
          direction="row"
          alignItems="center"
          justifyContent="space-between"
        >
          <Typography variant="h6">
            {d ? `${d.event_type}${d.seq ? ` · #${d.seq}` : ""}` : "Delivery"}
          </Typography>
          <IconButton aria-label="Close" onClick={onClose}>
            <Iconify icon="mingcute:close-line" />
          </IconButton>
        </Stack>
        {isLoading && <Typography variant="body2">Loading…</Typography>}
        {isError && (
          <Alert severity="error">This delivery could not be loaded.</Alert>
        )}
        {d && (
          <>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
              {status.label}
              {d.is_test ? " · Test" : ""} · {d.id}
            </Typography>

            <Typography variant="subtitle2">Request</Typography>
            <Box
              component="pre"
              sx={{ ...mono, p: 1, bgcolor: "background.neutral" }}
              aria-label="Request headers"
            >
              {Object.entries(d.request_headers || {})
                .map(([k, v]) => `${k}: ${v}`)
                .join("\n")}
            </Box>
            {d.body_hidden && (
              <Alert severity="info" sx={{ my: 1 }}>
                This body includes prompt text, which only admins can read.
              </Alert>
            )}
            {d.body_expired && (
              <Alert severity="info" sx={{ my: 1 }}>
                This body is no longer kept.
              </Alert>
            )}
            {d.body != null && (
              <Box
                component="pre"
                sx={{
                  ...mono,
                  p: 1,
                  mt: 1,
                  bgcolor: "background.neutral",
                  maxHeight: 320,
                  overflow: "auto",
                }}
                aria-label="Request body"
              >
                {prettyJson(d.body)}
              </Box>
            )}
            <Stack direction="row" spacing={1} sx={{ my: 1 }}>
              <Button
                size="small"
                disabled={d.body == null}
                onClick={() => copy("body", d.body)}
              >
                {copied === "body" ? "Copied" : "Copy body"}
              </Button>
              <Button
                size="small"
                disabled={d.body == null}
                onClick={() =>
                  copy("curl", curlOf(endpoint?.url, d.request_headers, d.body))
                }
              >
                {copied === "curl" ? "Copied" : "Copy as curl"}
              </Button>
            </Stack>
            {copied === "failed" && (
              <Alert severity="error" sx={{ mb: 1 }} role="alert">
                Nothing was copied: this browser blocked the clipboard.
              </Alert>
            )}

            <Divider sx={{ my: 2 }} />
            <Typography variant="subtitle2">Attempts</Typography>
            {d.attempts.length === 0 && (
              <Typography variant="body2" color="text.secondary">
                No attempt yet.
              </Typography>
            )}
            <Box component="ol" sx={{ p: 0, m: 0 }} aria-label="Attempts">
              {d.attempts.map((a) => (
                <AttemptRow key={a.id} attempt={a} host={host} />
              ))}
            </Box>

            <Divider sx={{ my: 2 }} />
            {redeliver.isError && (
              <Alert severity="error" sx={{ mb: 1 }}>
                {apiError(redeliver.error, "Could not redeliver.").message}
              </Alert>
            )}
            {redeliver.isSuccess && (
              <Alert severity="success" sx={{ mb: 1 }} role="status">
                Redelivery queued as attempt{" "}
                {redeliver.data.data.result.attempt}.
              </Alert>
            )}
            <Button
              variant="contained"
              disabled={
                !endpoint?.can_manage ||
                d.status === "cancelled" ||
                redeliver.isPending
              }
              onClick={() => redeliver.mutate(d.id)}
            >
              Redeliver
            </Button>
          </>
        )}
      </Box>
    </Drawer>
  );
}

DeliveryDrawer.propTypes = {
  deliveryId: PropTypes.string,
  endpoint: PropTypes.object,
  onClose: PropTypes.func,
};
