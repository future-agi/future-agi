import React, { useState, useEffect, useCallback } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Drawer,
  Stack,
  Typography,
  IconButton,
  Tabs,
  Tab,
  Chip,
  CircularProgress,
  Alert,
  Button,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Skeleton,
  Tooltip,
  Card,
  CardContent,
} from "@mui/material";
import Iconify from "src/components/iconify";
import useRequestDetail from "./hooks/useRequestDetail";
import FeedbackWidget from "../guardrails/FeedbackWidget";
import { formatCost } from "../utils/formatters";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DRAWER_WIDTH = { xs: "100vw", sm: "60vw" };
const DRAWER_MIN_WIDTH = 400;
const DRAWER_MAX_WIDTH = 800;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function getStatusChipColor(code) {
  if (code >= 200 && code < 300) return "success";
  if (code >= 400 && code < 500) return "warning";
  if (code >= 500 && code < 600) return "error";
  return "default";
}

function getLatencyColor(ms) {
  if (ms < 500) return "success.main";
  if (ms <= 2000) return "warning.main";
  return "error.main";
}

function formatFullTimestamp(iso) {
  if (!iso) return "N/A";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return "N/A";
  }
}

// ---------------------------------------------------------------------------
// JSON viewer sub-component
// ---------------------------------------------------------------------------

JsonViewer.propTypes = {
  data: PropTypes.any,
  label: PropTypes.string,
};

function JsonViewer({ data, label }) {
  const raw = typeof data === "string" ? data : JSON.stringify(data, null, 2);

  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(raw).catch(() => {});
  }, [raw]);

  return (
    <Box sx={{ position: "relative" }}>
      {label && (
        <Typography variant="subtitle2" gutterBottom>
          {label}
        </Typography>
      )}
      <Box
        sx={{
          bgcolor: "background.neutral",
          p: 2,
          borderRadius: 1,
          overflow: "auto",
          maxHeight: 400,
          position: "relative",
        }}
      >
        <Tooltip title="Copy" placement="top" arrow>
          <IconButton
            size="small"
            onClick={handleCopy}
            sx={{ position: "absolute", top: 8, right: 8 }}
          >
            <Iconify icon="mdi:content-copy" width={18} />
          </IconButton>
        </Tooltip>
        <pre
          style={{
            margin: 0,
            fontFamily: "monospace",
            fontSize: "0.8rem",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
          }}
        >
          <code>{raw || "null"}</code>
        </pre>
      </Box>
    </Box>
  );
}

// ---------------------------------------------------------------------------
// Headers accordion sub-component
// ---------------------------------------------------------------------------

HeadersAccordion.propTypes = {
  title: PropTypes.string.isRequired,
  headers: PropTypes.object,
};

function HeadersAccordion({ title, headers }) {
  if (!headers || Object.keys(headers).length === 0) {
    return (
      <Accordion>
        <AccordionSummary
          expandIcon={<Iconify icon="mdi:chevron-down" width={20} />}
        >
          <Typography variant="subtitle2">{title}</Typography>
        </AccordionSummary>
        <AccordionDetails>
          <Typography variant="body2" color="text.secondary">
            No headers available
          </Typography>
        </AccordionDetails>
      </Accordion>
    );
  }

  return (
    <Accordion>
      <AccordionSummary
        expandIcon={<Iconify icon="mdi:chevron-down" width={20} />}
      >
        <Typography variant="subtitle2">{title}</Typography>
      </AccordionSummary>
      <AccordionDetails sx={{ p: 0 }}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell sx={{ fontWeight: 600 }}>Header</TableCell>
              <TableCell sx={{ fontWeight: 600 }}>Value</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {Object.entries(headers).map(([key, value]) => (
              <TableRow key={key}>
                <TableCell sx={{ fontFamily: "monospace", fontSize: "0.8rem" }}>
                  {key}
                </TableCell>
                <TableCell
                  sx={{
                    fontFamily: "monospace",
                    fontSize: "0.8rem",
                    wordBreak: "break-all",
                  }}
                >
                  {typeof value === "object"
                    ? JSON.stringify(value)
                    : String(value)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </AccordionDetails>
    </Accordion>
  );
}

// ---------------------------------------------------------------------------
// Tab panels
// ---------------------------------------------------------------------------

OverviewTab.propTypes = { log: PropTypes.object.isRequired };

function OverviewTab({ log }) {
  const flags = [
    {
      key: "isStream",
      label: "Streaming",
      value: log.is_stream ?? log.isStream,
      icon: <Iconify icon="mdi:arrow-right-bold-outline" width={18} />,
    },
    {
      key: "cacheHit",
      label: "Cache Hit",
      value: log.cache_hit,
      icon: <Iconify icon="mdi:cached" width={18} />,
    },
    {
      key: "fallbackUsed",
      label: "Fallback Used",
      value: log.fallback_used ?? log.fallbackUsed,
      icon: <Iconify icon="mdi:swap-horizontal" width={18} />,
    },
    {
      key: "guardrailTriggered",
      label: "Guardrail Triggered",
      value: log.guardrail_triggered ?? log.guardrailTriggered,
      icon: <Iconify icon="mdi:shield-outline" width={18} />,
    },
    {
      key: "isError",
      label: "Error",
      value: log.is_error,
      icon: <Iconify icon="mdi:alert-circle-outline" width={18} />,
    },
  ];

  return (
    <Stack spacing={3} p={2}>
      {/* Flags */}
      <Box>
        <Typography variant="subtitle2" gutterBottom>
          Flags
        </Typography>
        <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
          {flags.map((f) => (
            <Chip
              key={f.key}
              icon={f.icon}
              label={`${f.label}: ${f.value ? "Yes" : "No"}`}
              size="small"
              variant="outlined"
              color={f.value ? "success" : "default"}
            />
          ))}
        </Stack>
      </Box>

      {/* Routing info */}
      <Box>
        <Typography variant="subtitle2" gutterBottom>
          Routing
        </Typography>
        <Stack spacing={0.5}>
          <Typography variant="body2">
            <strong>Strategy:</strong>{" "}
            {log.routing_strategy || log.routingStrategy || "Default"}
          </Typography>
          <Typography variant="body2">
            <strong>Resolved Model:</strong>{" "}
            {log.resolved_model || log.resolvedModel || log.model || "N/A"}
          </Typography>
        </Stack>
        {log.is_error && log.error_message && (
          <Alert severity="error" sx={{ mt: 1 }}>
            {log.error_message}
          </Alert>
        )}
      </Box>

      {/* Identity */}
      <Box>
        <Typography variant="subtitle2" gutterBottom>
          Identity
        </Typography>
        <Stack spacing={0.5}>
          <Typography variant="body2">
            <strong>API Key:</strong> {log.api_key_id || log.apiKeyId || "N/A"}
          </Typography>
          <Typography variant="body2">
            <strong>User ID:</strong> {log.user_id || log.userId || "N/A"}
          </Typography>
          <Typography variant="body2">
            <strong>Session ID:</strong> {log.session_id || "N/A"}
          </Typography>
        </Stack>
      </Box>
    </Stack>
  );
}

RequestTab.propTypes = { log: PropTypes.object.isRequired };

function RequestTab({ log }) {
  return (
    <Stack spacing={2} p={2}>
      <JsonViewer data={log.request_body} label="Request Body" />
      <HeadersAccordion
        title="Request Headers"
        headers={log.request_headers ?? log.requestHeaders}
      />
    </Stack>
  );
}

ResponseTab.propTypes = { log: PropTypes.object.isRequired };

function ResponseTab({ log }) {
  return (
    <Stack spacing={2} p={2}>
      <JsonViewer data={log.response_body} label="Response Body" />
      <HeadersAccordion
        title="Response Headers"
        headers={log.response_headers ?? log.responseHeaders}
      />
    </Stack>
  );
}

MetadataTab.propTypes = { log: PropTypes.object.isRequired };

function MetadataTab({ log }) {
  const entries = Object.entries(log.metadata || {});

  if (entries.length === 0) {
    return (
      <Stack alignItems="center" py={4}>
        <Typography variant="body2" color="text.secondary">
          No metadata
        </Typography>
      </Stack>
    );
  }

  return (
    <Box p={2}>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell sx={{ fontWeight: 600 }}>Key</TableCell>
            <TableCell sx={{ fontWeight: 600 }}>Value</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {entries.map(([key, value]) => (
            <TableRow key={key}>
              <TableCell>{key}</TableCell>
              <TableCell sx={{ wordBreak: "break-all" }}>
                {typeof value === "object"
                  ? JSON.stringify(value)
                  : String(value)}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Box>
  );
}

GuardrailsTab.propTypes = { log: PropTypes.object.isRequired };

function GuardrailsTab({ log }) {
  let raw = log.guardrail_results ?? log.guardrailResults;
  if (typeof raw === "string") {
    try {
      raw = JSON.parse(raw);
    } catch {
      raw = null;
    }
  }

  // Support both formats:
  // 1. Object: { action: "block", checks: [{name, action, score, ...}] }
  // 2. Array (legacy): [{name, action, score, ...}]
  let overallAction = null;
  let checks = [];

  if (raw && typeof raw === "object" && !Array.isArray(raw)) {
    overallAction = raw.action;
    checks = Array.isArray(raw.checks) ? raw.checks : [];
  } else if (Array.isArray(raw)) {
    checks = raw;
  }

  if (checks.length === 0) {
    return (
      <Box p={2}>
        <Alert severity="info">
          A guardrail was triggered on this request but no detailed check
          results are available.
        </Alert>
      </Box>
    );
  }

  return (
    <Stack spacing={2} p={2}>
      {/* Overall action banner */}
      {overallAction && (
        <Alert
          severity={overallAction === "block" ? "error" : "warning"}
          icon={
            <Iconify
              icon={
                overallAction === "block"
                  ? "mdi:shield-off-outline"
                  : "mdi:shield-alert-outline"
              }
              width={22}
            />
          }
        >
          Request was{" "}
          <strong>{overallAction === "block" ? "blocked" : "warned"}</strong> by
          guardrails
        </Alert>
      )}

      {/* Individual check cards */}
      {checks.map((gr, idx) => (
        <Card key={idx} variant="outlined">
          <CardContent>
            <Stack spacing={1}>
              <Stack
                direction="row"
                spacing={1}
                alignItems="center"
                justifyContent="space-between"
              >
                <Typography variant="subtitle2">{gr.name}</Typography>
                <Chip
                  label={gr.action}
                  size="small"
                  color={gr.action === "block" ? "error" : "warning"}
                  variant="outlined"
                />
              </Stack>
              {(gr.score != null || gr.confidence != null) && (
                <Typography variant="body2" color="text.secondary">
                  Score: {gr.score ?? gr.confidence}
                  {gr.threshold != null ? ` / Threshold: ${gr.threshold}` : ""}
                </Typography>
              )}
              {(gr.latencyMs ?? gr.latency_ms) != null && (
                <Typography variant="body2" color="text.secondary">
                  Latency: {gr.latencyMs ?? gr.latency_ms}ms
                </Typography>
              )}
              {(gr.message || gr.details) && (
                <Typography variant="body2" color="text.secondary">
                  {gr.message || gr.details}
                </Typography>
              )}
              <FeedbackWidget requestLogId={log.id} checkName={gr.name} />
            </Stack>
          </CardContent>
        </Card>
      ))}
    </Stack>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

const RequestDetailDrawer = ({ logId, open, onClose }) => {
  const { data, isLoading, error, refetch } = useRequestDetail(logId);
  const [activeTab, setActiveTab] = useState(0);

  // Reset tab when a new log is selected
  useEffect(() => {
    setActiveTab(0);
  }, [logId]);

  const log = data?.result ?? data ?? null;

  // Build the dynamic tab list -- conditionally include Guardrails
  const showGuardrails = log?.guardrailTriggered;
  const tabs = [
    { label: "Overview", value: 0 },
    { label: "Request", value: 1 },
    { label: "Response", value: 2 },
    { label: "Metadata", value: 3 },
  ];
  if (showGuardrails) {
    tabs.push({ label: "Guardrails", value: 4 });
  }

  // =========================================================================
  // Render
  // =========================================================================

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      sx={{
        "& .MuiDrawer-paper": {
          width: DRAWER_WIDTH,
          minWidth: DRAWER_MIN_WIDTH,
          maxWidth: DRAWER_MAX_WIDTH,
        },
      }}
    >
      {/* ---- Header ---- */}
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        p={2}
        borderBottom={1}
        borderColor="divider"
      >
        <Stack spacing={0.5} sx={{ minWidth: 0 }}>
          {isLoading ? (
            <>
              <Skeleton variant="text" width={200} />
              <Skeleton variant="text" width={140} />
            </>
          ) : log ? (
            <>
              <Tooltip
                title={log.request_id || log.requestId || log.id}
                placement="top"
                arrow
              >
                <Typography variant="h6" noWrap>
                  {(log.request_id || log.requestId || log.id || "").slice(
                    0,
                    20,
                  )}
                  {(log.request_id || log.requestId || log.id || "").length > 20
                    ? "..."
                    : ""}
                </Typography>
              </Tooltip>
              <Stack direction="row" spacing={1} alignItems="center">
                <Typography variant="body2" color="text.secondary">
                  {log.model}
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  {log.provider}
                </Typography>
                <Chip
                  label={log.status_code}
                  size="small"
                  variant="outlined"
                  color={getStatusChipColor(log.status_code)}
                />
              </Stack>
            </>
          ) : null}
        </Stack>

        <IconButton onClick={onClose} edge="end">
          <Iconify icon="mdi:close" width={20} />
        </IconButton>
      </Stack>

      {/* ---- Summary bar ---- */}
      {isLoading ? (
        <Stack direction="row" spacing={3} p={2}>
          {Array.from({ length: 4 }).map((_, i) => (
            <Box key={i}>
              <Skeleton variant="text" width={60} />
              <Skeleton variant="text" width={80} />
            </Box>
          ))}
        </Stack>
      ) : log ? (
        <Stack
          direction="row"
          spacing={3}
          p={2}
          sx={{ bgcolor: "action.hover" }}
        >
          <Box>
            <Typography variant="caption" color="text.secondary">
              Latency
            </Typography>
            <Typography
              variant="body2"
              fontWeight={600}
              sx={{
                color:
                  (log.latency_ms ?? log.latencyMs) != null
                    ? getLatencyColor(log.latency_ms ?? log.latencyMs)
                    : undefined,
              }}
            >
              {(log.latency_ms ?? log.latencyMs) != null
                ? `${log.latency_ms ?? log.latencyMs}ms`
                : "N/A"}
            </Typography>
          </Box>
          <Box>
            <Typography variant="caption" color="text.secondary">
              Tokens
            </Typography>
            <Typography variant="body2" fontWeight={600}>
              {log.input_tokens ?? 0} in / {log.output_tokens ?? 0} out
            </Typography>
          </Box>
          <Box>
            <Typography variant="caption" color="text.secondary">
              Cost
            </Typography>
            <Typography variant="body2" fontWeight={600}>
              {formatCost(log.cost)}
            </Typography>
          </Box>
          <Box>
            <Typography variant="caption" color="text.secondary">
              Started
            </Typography>
            <Typography variant="body2" fontWeight={600}>
              {formatFullTimestamp(log.started_at ?? log.startedAt)}
            </Typography>
          </Box>
        </Stack>
      ) : null}

      {/* ---- Error state ---- */}
      {error && (
        <Box p={2}>
          <Alert
            severity="error"
            action={
              <Button color="inherit" size="small" onClick={() => refetch()}>
                Retry
              </Button>
            }
          >
            Failed to load request details: {error.message || "Unknown error"}
          </Alert>
        </Box>
      )}

      {/* ---- Loading spinner ---- */}
      {isLoading && (
        <Box
          display="flex"
          justifyContent="center"
          alignItems="center"
          flex={1}
          py={8}
        >
          <CircularProgress />
        </Box>
      )}

      {/* ---- Detail content ---- */}
      {!isLoading && log && (
        <>
          <Tabs
            value={activeTab}
            onChange={(_e, v) => setActiveTab(v)}
            variant="scrollable"
            scrollButtons="auto"
            sx={{ px: 2, borderBottom: 1, borderColor: "divider" }}
          >
            {tabs.map((t) => (
              <Tab key={t.value} label={t.label} value={t.value} />
            ))}
          </Tabs>

          <Box sx={{ flex: 1, overflowY: "auto" }}>
            {activeTab === 0 && <OverviewTab log={log} />}
            {activeTab === 1 && <RequestTab log={log} />}
            {activeTab === 2 && <ResponseTab log={log} />}
            {activeTab === 3 && <MetadataTab log={log} />}
            {activeTab === 4 && showGuardrails && <GuardrailsTab log={log} />}
          </Box>
        </>
      )}
    </Drawer>
  );
};

RequestDetailDrawer.propTypes = {
  logId: PropTypes.string,
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
};

export default RequestDetailDrawer;
