import React, { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import Button from "@mui/material/Button";
import Collapse from "@mui/material/Collapse";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Chip from "@mui/material/Chip";
import IconButton from "@mui/material/IconButton";
import Link from "@mui/material/Link";
import Skeleton from "@mui/material/Skeleton";
import Stack from "@mui/material/Stack";
import Tab from "@mui/material/Tab";
import Table from "@mui/material/Table";
import TableBody from "@mui/material/TableBody";
import TableCell from "@mui/material/TableCell";
import TableHead from "@mui/material/TableHead";
import TableRow from "@mui/material/TableRow";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";

import { RouterLink } from "src/routes/components";
import { paths } from "src/routes/paths";
import { CustomTabs } from "src/components/tabs/tabs";

import {
  GatewayDetailDrawer,
  GatewayEmptyState,
  GatewayStatusChip,
} from "../components/frame";
import { GatewayIcon } from "../shell/gateway-icons";
import { copyText } from "../models/copyText";
import FeedbackWidget from "../guardrails/FeedbackWidget";
import { EMPTY, NO_PRICE, formatCost, isUnpriced } from "../utils/formatters";
import useRequestDetail from "./hooks/useRequestDetail";
import {
  AutoRoutingSection,
  BillingSection,
  isAutoRouted,
} from "./RequestRoutingDetails";
import { PolicyRows } from "./RoutingContextRows";
import AttemptsTimeline from "./attempts/AttemptsTimeline";
import RequestAgentSection from "./RequestAgentSection";
import CopyIdsButton from "./CopyIdsButton";
import { callEndOf } from "./callEnd";
import { NO_CHARGE, noChargeComputed } from "./logCost";
import RequestTimeline from "./timeline/RequestTimeline";
import { BILLING_MODES, apiKeyLabel } from "./explorerLabels";
import { DetailRow, DetailSection } from "../components/frame/DetailRows";
import { exactLogTime, formatLogTime, relativeLogTime } from "./logDates";
import { formatMs, statusOf } from "./logStatus";
import { GATEWAY_MONO } from "../utils/fonts";
import { BILLING_MODE_LABEL, providerLabel } from "../utils/providerLabel";

// The request drawer: what happened to one request, Vercel-style. A summary
// strip (status, duration, tokens, cost, billing), then the timeline of
// guardrail actions, cache and upstream attempts on one time axis, the
// attempts in detail, cost, routing, agent and identity; request and
// response bodies, headers, metadata and guardrail checks in their own tabs.

const TABS = ["overview", "request", "response", "headers", "metadata"];

// Metadata keys the gateway writes with a credits charge's split: its
// provider part and the markup percent (agentcc-gateway
// internal/plugins/cost/request_billing.go, internal/capability/evidence.go).
// A request's cost is one number, the Billing block's; the Metadata tab
// lists every other key, the caller's included.
const SPLIT_METADATA_KEYS = new Set(["cost_base", "cost_markup_percent"]);
const shownMetadata = (metadata) =>
  Object.entries(metadata || {}).filter(
    ([key]) => !SPLIT_METADATA_KEYS.has(key),
  );
const TAB_LABELS = {
  overview: "Overview",
  request: "Request",
  response: "Response",
  headers: "Headers",
  metadata: "Metadata",
  guardrails: "Guardrails",
};

function Heading({ children }) {
  return (
    <Typography
      component="h3"
      typography="s1"
      fontWeight="fontWeightMedium"
      sx={{ mb: 0.75 }}
    >
      {children}
    </Typography>
  );
}

Heading.propTypes = { children: PropTypes.node };

function CodeBlock({ data, label }) {
  const raw =
    data == null
      ? ""
      : typeof data === "string"
        ? data
        : JSON.stringify(data, null, 2);
  return (
    <Box>
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          mb: 0.5,
        }}
      >
        <Heading>{label}</Heading>
        {raw && (
          <Tooltip title={`Copy ${label.toLowerCase()}`}>
            <IconButton
              size="small"
              aria-label={`Copy ${label.toLowerCase()}`}
              onClick={() => copyText(raw, `${label} copied`)}
            >
              <GatewayIcon name="copy" size={16} />
            </IconButton>
          </Tooltip>
        )}
      </Box>
      {raw ? (
        <Box
          component="pre"
          sx={{
            m: 0,
            p: 1.5,
            borderRadius: 1,
            border: "1px solid",
            borderColor: "divider",
            bgcolor: "background.neutral",
            overflow: "auto",
            maxHeight: 420,
            typography: "s2",
            fontFamily: GATEWAY_MONO,
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
          }}
        >
          <Box component="code" sx={{ fontFamily: GATEWAY_MONO }}>
            {raw}
          </Box>
        </Box>
      ) : (
        <Typography typography="s2" color="text.secondary">
          The gateway did not store this body. Body logging is off for this
          organization, or the request carried none.
        </Typography>
      )}
    </Box>
  );
}

CodeBlock.propTypes = {
  data: PropTypes.any,
  label: PropTypes.string.isRequired,
};

function KeyValueTable({ entries, keyLabel = "Header", empty }) {
  if (!entries.length) {
    return (
      <Typography typography="s2" color="text.secondary">
        {empty}
      </Typography>
    );
  }
  return (
    <Table size="small" sx={{ tableLayout: "fixed" }}>
      <TableHead>
        <TableRow>
          <TableCell sx={{ width: "38%" }}>{keyLabel}</TableCell>
          <TableCell>Value</TableCell>
        </TableRow>
      </TableHead>
      <TableBody>
        {entries.map(([key, value]) => (
          <TableRow key={key}>
            <TableCell
              sx={{
                fontFamily: GATEWAY_MONO,
                typography: "s2",
                wordBreak: "break-all",
              }}
            >
              {key}
            </TableCell>
            <TableCell
              sx={{
                fontFamily: GATEWAY_MONO,
                typography: "s2",
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
  );
}

KeyValueTable.propTypes = {
  entries: PropTypes.array.isRequired,
  keyLabel: PropTypes.string,
  empty: PropTypes.string,
};

function SummaryItem({ label, children }) {
  return (
    <Box component="li" sx={{ minWidth: 0 }}>
      <Typography
        typography="s3"
        color="text.secondary"
        sx={{ display: "block" }}
      >
        {label}
      </Typography>
      <Typography
        typography="s1"
        color="text.primary"
        sx={{ fontVariantNumeric: "tabular-nums", overflowWrap: "anywhere" }}
      >
        {children}
      </Typography>
    </Box>
  );
}

SummaryItem.propTypes = { label: PropTypes.string, children: PropTypes.node };

const FLAGS = [
  ["is_stream", "Streaming"],
  ["cache_hit", "Cache hit"],
  ["fallback_used", "Fallback used"],
  ["guardrail_triggered", "Guardrail triggered"],
];

function logsLink(params) {
  return `${paths.dashboard.gateway.logs}?${new URLSearchParams(params).toString()}`;
}

// The timeline already shows each attempt; the full cards (credential,
// error text, tokens, per-attempt cost) open on request.
function AttemptDetails({ attempts }) {
  const [open, setOpen] = useState(false);
  if (!Array.isArray(attempts) || attempts.length === 0) return null;
  return (
    <Box>
      <Button
        size="small"
        color="inherit"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        endIcon={
          <GatewayIcon name={open ? "chevronUp" : "chevronDown"} size={16} />
        }
        sx={{ px: 0.5, color: "text.secondary" }}
      >
        {open ? "Hide attempt details" : `Attempt details (${attempts.length})`}
      </Button>
      <Collapse in={open} unmountOnExit>
        <Box sx={{ pt: 1 }}>
          <AttemptsTimeline attempts={attempts} />
        </Box>
      </Collapse>
    </Box>
  );
}

AttemptDetails.propTypes = { attempts: PropTypes.array };

// A call that ended before its answer finished reads as how it ended, in
// the same words as its row and timeline (callEnd.js), not as an error
// banner. The gateway's error text stays on the page under it.
function CallEndNote({ callEnd, errorMessage }) {
  return (
    <Box
      component="section"
      aria-label="How this call ended"
      sx={{
        p: 1.5,
        borderRadius: 1,
        border: "1px solid",
        borderColor: "divider",
        display: "flex",
        flexDirection: "column",
        gap: 0.5,
      }}
    >
      <Typography component="h3" typography="s1" fontWeight="fontWeightMedium">
        {callEnd.label}
      </Typography>
      <Typography typography="s2" color="text.secondary">
        {callEnd.description}
      </Typography>
      {errorMessage && (
        <Typography
          typography="s2"
          color="text.secondary"
          sx={{ overflowWrap: "anywhere" }}
        >
          {"Error text: "}
          <Box component="span" sx={{ fontFamily: GATEWAY_MONO }}>
            {errorMessage}
          </Box>
        </Typography>
      )}
    </Box>
  );
}

CallEndNote.propTypes = {
  callEnd: PropTypes.shape({
    label: PropTypes.string.isRequired,
    description: PropTypes.string.isRequired,
  }).isRequired,
  errorMessage: PropTypes.string,
};

function OverviewTab({ log }) {
  const flags = FLAGS.filter(([key]) => log[key]);
  const keyName = apiKeyLabel(log.metadata?.auth_key_name);
  const callEnd = callEndOf(log);
  return (
    <Stack spacing={2.5}>
      {flags.length > 0 && (
        <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
          {flags.map(([key, label]) => (
            <Chip key={key} size="small" variant="outlined" label={label} />
          ))}
        </Stack>
      )}

      {callEnd && (
        <CallEndNote callEnd={callEnd} errorMessage={log.error_message} />
      )}

      {!callEnd && log.is_error && log.error_message && (
        <Box
          role="alert"
          sx={{
            p: 1.5,
            borderRadius: 1,
            border: "1px solid",
            borderColor: "error.main",
            typography: "s2",
            color: "text.primary",
            overflowWrap: "anywhere",
          }}
        >
          {log.error_message}
        </Box>
      )}

      <Box>
        <RequestTimeline log={log} />
        <AttemptDetails attempts={log.attempts} />
      </Box>

      <BillingSection log={log} />

      <DetailSection title="Routing">
        <Box>
          <DetailRow label="Strategy">
            {log.routing_strategy || "Default"}
          </DetailRow>
          <DetailRow label="Resolved model">
            {log.resolved_model || log.model || EMPTY}
          </DetailRow>
          {/* The auto block shows these itself. */}
          {!isAutoRouted(log) && <PolicyRows routing={log.routing} />}
        </Box>
      </DetailSection>

      <AutoRoutingSection log={log} />

      <RequestAgentSection log={log} />

      <DetailSection title="Identity">
        <Box>
          <DetailRow label="API key">
            {log.api_key_id ? (
              <>
                {keyName || log.api_key_id}
                {keyName && (
                  <Box
                    component="span"
                    sx={{ color: "text.secondary", fontFamily: GATEWAY_MONO }}
                  >
                    {` ${log.api_key_id}`}
                  </Box>
                )}
                {/* On its own line, never broken mid-phrase. */}
                <Box component="span" sx={{ display: "block" }}>
                  <Link
                    component={RouterLink}
                    href={logsLink({ api_key_id: log.api_key_id })}
                    typography="s2"
                  >
                    All requests with this key
                  </Link>
                </Box>
              </>
            ) : (
              EMPTY
            )}
          </DetailRow>
          <DetailRow label="User">{log.user_id || EMPTY}</DetailRow>
          <DetailRow label="Session">
            {log.session_id ? (
              <>
                {log.session_id}
                <Box component="span" sx={{ display: "block" }}>
                  <Link
                    component={RouterLink}
                    href={logsLink({ session_id: log.session_id })}
                    typography="s2"
                  >
                    All requests in this session
                  </Link>
                </Box>
              </>
            ) : (
              EMPTY
            )}
          </DetailRow>
        </Box>
      </DetailSection>
    </Stack>
  );
}

OverviewTab.propTypes = { log: PropTypes.object.isRequired };

function readGuardrailChecks(raw) {
  let value = raw;
  if (typeof value === "string") {
    try {
      value = JSON.parse(value);
    } catch {
      value = null;
    }
  }
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return {
      action: value.action || null,
      checks: Array.isArray(value.checks) ? value.checks : [],
    };
  }
  return { action: null, checks: Array.isArray(value) ? value : [] };
}

function GuardrailsTab({ log }) {
  const { action, checks } = readGuardrailChecks(log.guardrail_results);
  const overall =
    action || (checks.some((c) => c.action === "block") ? "block" : null);

  if (checks.length === 0) {
    return (
      <Typography typography="s2" color="text.secondary">
        A guardrail acted on this request, but the gateway sent no check
        details.
      </Typography>
    );
  }

  return (
    <Stack spacing={1.5}>
      {overall && (
        <Typography typography="s1">
          Request was{" "}
          <strong>{overall === "block" ? "blocked" : "warned"}</strong> by
          guardrails.
        </Typography>
      )}
      {checks.map((check, idx) => (
        <Box
          key={`${check.name}-${idx}`}
          sx={{
            p: 1.5,
            borderRadius: 1,
            border: "1px solid",
            borderColor: "divider",
          }}
        >
          <Stack spacing={0.5}>
            <Stack
              direction="row"
              spacing={1}
              alignItems="center"
              justifyContent="space-between"
            >
              <Typography typography="s1" fontWeight="fontWeightMedium">
                {check.name}
              </Typography>
              <GatewayStatusChip
                tone={check.action === "block" ? "error" : "warning"}
                label={check.action || "flagged"}
                showIcon={false}
              />
            </Stack>
            {(check.score != null || check.confidence != null) && (
              <Typography typography="s2" color="text.secondary">
                Score: {check.score ?? check.confidence}
                {check.threshold != null
                  ? ` / Threshold: ${check.threshold}`
                  : ""}
              </Typography>
            )}
            {check.latency_ms != null && (
              <Typography typography="s2" color="text.secondary">
                Latency: {check.latency_ms}ms
              </Typography>
            )}
            {(check.message || check.details) && (
              <Typography typography="s2" color="text.secondary">
                {check.message || check.details}
              </Typography>
            )}
            <FeedbackWidget requestLogId={log.id} checkName={check.name} />
          </Stack>
        </Box>
      ))}
    </Stack>
  );
}

GuardrailsTab.propTypes = { log: PropTypes.object.isRequired };

const NO_HEADERS = "The gateway recorded no headers for this request.";

function HeadersTab({ log }) {
  return (
    <Stack spacing={2.5}>
      <Box>
        <Heading>Request headers</Heading>
        <KeyValueTable
          entries={Object.entries(log.request_headers || {})}
          empty={NO_HEADERS}
        />
      </Box>
      <Box>
        <Heading>Response headers</Heading>
        <KeyValueTable
          entries={Object.entries(log.response_headers || {})}
          empty={NO_HEADERS}
        />
      </Box>
    </Stack>
  );
}

HeadersTab.propTypes = { log: PropTypes.object.isRequired };

function DrawerSkeleton() {
  return (
    <Stack spacing={2} aria-busy="true" aria-label="Loading request">
      <Stack direction="row" spacing={3}>
        {[0, 1, 2, 3].map((i) => (
          <Box key={i}>
            <Skeleton variant="text" width={56} />
            <Skeleton variant="text" width={80} />
          </Box>
        ))}
      </Stack>
      <Skeleton variant="rounded" height={28} />
      {[0, 1, 2, 3].map((i) => (
        <Skeleton key={i} variant="rounded" height={36} />
      ))}
    </Stack>
  );
}

const RequestDetailDrawer = ({
  logId,
  open,
  onClose,
  navigation,
  onNavigate,
}) => {
  const location = useLocation();
  const { data, isLoading, error, refetch } = useRequestDetail(logId);
  const copyRequestLink = () => {
    // A drawer on Runs still links to Logs, without carrying run-page UI state.
    const query = new URLSearchParams(
      location.pathname === paths.dashboard.gateway.logs ? location.search : "",
    );
    query.set("log", logId);
    const url = new URL(logsLink(query), window.location.origin);
    copyText(url.href, "Request link copied");
  };
  const [tab, setTab] = useState("overview");

  useEffect(() => {
    setTab("overview");
  }, [logId]);

  const log = data?.result ?? data ?? null;
  const tabs = log?.guardrail_triggered ? [...TABS, "guardrails"] : TABS;
  const status = statusOf(log);
  const id = log?.request_id || log?.id || "";
  const billingMode = log?.billing?.mode || log?.metadata?.billing_mode;
  const resolved =
    log?.resolved_model && log.resolved_model !== log.model
      ? `${log.model} → ${log.resolved_model}`
      : log?.model;

  return (
    <GatewayDetailDrawer
      open={open}
      onClose={onClose}
      width={720}
      title={
        isLoading || !log ? (
          <Skeleton variant="text" width={240} />
        ) : (
          <Box
            component="span"
            sx={{ fontFamily: GATEWAY_MONO, typography: "s1", fontWeight: 500 }}
          >
            {id}
          </Box>
        )
      }
      subtitle={
        log ? (
          <Box
            component="span"
            sx={{
              display: "inline-flex",
              alignItems: "center",
              gap: 1,
              flexWrap: "wrap",
            }}
          >
            <GatewayStatusChip
              tone={status.tone}
              label={status.blocked ? `${status.label} blocked` : status.label}
              showIcon={false}
            />
            {/* The gateway's error code (agentcc error_code), beside its
                status; the message stays in the error box below. */}
            {log.error_code && (
              <Box
                component="span"
                aria-label="Error code"
                title="The gateway's error code"
                sx={{ fontFamily: GATEWAY_MONO, color: "text.primary" }}
              >
                {log.error_code}
              </Box>
            )}
            <span>{resolved}</span>
            {log.provider && (
              <span>· {providerLabel(log.provider, billingMode)}</span>
            )}
            <span title={exactLogTime(log.started_at) || undefined}>
              · {formatLogTime(log.started_at)}
              {relativeLogTime(log.started_at) &&
                ` (${relativeLogTime(log.started_at)})`}
            </span>
          </Box>
        ) : null
      }
      actions={
        <>
          {logId && (
            <Button size="small" onClick={copyRequestLink}>
              Copy request link
            </Button>
          )}
          {log && (
            <CopyIdsButton requestId={id} generationId={log.generation_id} />
          )}
        </>
      }
    >
      {navigation && onNavigate && (
        <Box
          role="group"
          aria-label="Requests on this page"
          sx={{
            display: "flex",
            alignItems: "center",
            flexWrap: "wrap",
            gap: 1,
            mb: 2,
          }}
        >
          <Button
            size="small"
            variant="outlined"
            aria-label="Previous request"
            disabled={!navigation.ready || !navigation.previousId}
            onClick={() => onNavigate(navigation.previousId)}
          >
            Previous
          </Button>
          <Button
            size="small"
            variant="outlined"
            aria-label="Next request"
            disabled={!navigation.ready || !navigation.nextId}
            onClick={() => onNavigate(navigation.nextId)}
          >
            Next
          </Button>
          <Typography typography="s2" color="text.secondary" role="status">
            {!navigation.ready
              ? "Page results are unavailable or updating. Navigation is paused."
              : navigation.position
                ? `${navigation.position} of ${navigation.count} on this page`
                : "This request is not in the loaded page."}
          </Typography>
        </Box>
      )}
      {error && !log && (
        <GatewayEmptyState
          icon="alertCircle"
          title="This request could not be loaded"
          description={error.message || "The gateway API did not answer."}
          actionLabel="Retry"
          onAction={() => refetch()}
        />
      )}
      {isLoading && <DrawerSkeleton />}
      {!isLoading && log && (
        <Stack spacing={2}>
          <Box
            component="ul"
            aria-label="Summary"
            sx={{
              m: 0,
              p: 1.5,
              listStyle: "none",
              display: "grid",
              gridTemplateColumns: {
                xs: "repeat(2, minmax(0, 1fr))",
                sm: "repeat(4, minmax(0, 1fr))",
              },
              gap: 1.5,
              borderRadius: 1,
              border: "1px solid",
              borderColor: "divider",
            }}
          >
            <SummaryItem label="Duration">
              {formatMs(log.latency_ms)}
            </SummaryItem>
            <SummaryItem label="Tokens">
              {`${log.input_tokens ?? 0} in / ${log.output_tokens ?? 0} out`}
            </SummaryItem>
            <SummaryItem label="Cost">
              {isUnpriced(log) || noChargeComputed(log) ? (
                <>
                  {EMPTY}
                  <Typography
                    component="span"
                    typography="s2"
                    color="text.secondary"
                    sx={{ display: "block" }}
                  >
                    {isUnpriced(log) ? NO_PRICE : NO_CHARGE}
                  </Typography>
                </>
              ) : (
                formatCost(log.cost)
              )}
            </SummaryItem>
            <SummaryItem label={BILLING_MODE_LABEL}>
              {billingMode ? BILLING_MODES[billingMode] || billingMode : EMPTY}
            </SummaryItem>
          </Box>

          <CustomTabs
            value={tab}
            onChange={(_event, value) => setTab(value)}
            aria-label="Request details"
            variant="scrollable"
            scrollButtons="auto"
            sx={{ borderBottom: 1, borderColor: "divider", minHeight: 36 }}
          >
            {tabs.map((value) => (
              <Tab
                key={value}
                value={value}
                label={TAB_LABELS[value]}
                sx={{ minHeight: 36 }}
              />
            ))}
          </CustomTabs>

          <Box>
            {tab === "overview" && <OverviewTab log={log} />}
            {tab === "request" && (
              <CodeBlock data={log.request_body} label="Request body" />
            )}
            {tab === "response" && (
              <CodeBlock data={log.response_body} label="Response body" />
            )}
            {tab === "headers" && <HeadersTab log={log} />}
            {tab === "metadata" && (
              <KeyValueTable
                entries={shownMetadata(log.metadata)}
                keyLabel="Key"
                empty="No metadata on this request."
              />
            )}
            {tab === "guardrails" && <GuardrailsTab log={log} />}
          </Box>
        </Stack>
      )}
    </GatewayDetailDrawer>
  );
};

RequestDetailDrawer.propTypes = {
  logId: PropTypes.string,
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  navigation: PropTypes.shape({
    ready: PropTypes.bool.isRequired,
    position: PropTypes.number,
    count: PropTypes.number.isRequired,
    previousId: PropTypes.string,
    nextId: PropTypes.string,
  }),
  onNavigate: PropTypes.func,
};

export default RequestDetailDrawer;
