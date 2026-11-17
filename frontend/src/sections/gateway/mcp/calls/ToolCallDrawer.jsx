import { useMCPReleaseFeatures } from "../hooks/useMCPReleaseFeatures";
import React, { lazy, Suspense, useState } from "react";
import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Divider from "@mui/material/Divider";
import Skeleton from "@mui/material/Skeleton";
import Typography from "@mui/material/Typography";

import { GatewayDetailDrawer, GatewayStatusChip } from "../../components/frame";
import { formatLatency } from "../../utils/formatters";
import { useMCPToolCall } from "../hooks/useMCPToolCalls";
import { useMCPToolCallFeatures } from "../hooks/useMCPToolCallFeatures";
import JsonBlock from "../components/JsonBlock";
import KeyValueList from "../components/KeyValueList";
import SectionTitle from "../components/SectionTitle";
import CallOutcomes from "./CallOutcomes";
import {
  billingLabel,
  formatBytes,
  formatCallTime,
  formatMicros,
  outcomeOf,
  toolLabel,
} from "./toolCalls";

const TraceDetailDrawer = lazy(
  () => import("src/components/traceDetail/TraceDetailDrawerV2"),
);

const GUARD_LISTS = [
  ["checked", "Checked"],
  ["blocked", "Blocked"],
  ["masked", "Masked"],
  ["shadow", "Shadow"],
];

function Section({ title, children }) {
  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
      <SectionTitle title={title} component="h3" />
      {children}
    </Box>
  );
}

Section.propTypes = { title: PropTypes.string, children: PropTypes.node };

function Content({
  call,
  detail,
  detailLoading,
  detailUnavailable,
  detailMissing,
  detailFetching,
  retryDetail,
  virtualServerName,
  keyName,
  virtualServers,
}) {
  const outcome = outcomeOf(call.outcome);
  const hasError =
    call.error_class ||
    call.error_code ||
    call.error_message ||
    call.error_origin;
  const guard =
    call.guardrail && typeof call.guardrail === "object" ? call.guardrail : {};
  const guardRows = GUARD_LISTS.filter(
    ([key]) => Array.isArray(guard[key]) && guard[key].length > 0,
  ).map(([key, label]) => ({ label, value: guard[key].join(", ") }));
  const stored = call.content_mode === "full";

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2.5 }}>
      <Box
        sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}
      >
        <GatewayStatusChip tone={outcome.tone} label={outcome.label} />
        <Typography typography="s2" color="text.secondary">
          {call.started_at ? formatCallTime(call.started_at) : ""}
        </Typography>
      </Box>

      <Section title="Call">
        <KeyValueList
          rows={[
            { label: "Tool", value: toolLabel(call) },
            { label: "Server", value: call.server_id || "—" },
            ...(virtualServers
              ? [
                  {
                    label: "Virtual server",
                    // The recorded value, as the list column shows it,
                    // whether or not the switch is on now.
                    value: call.virtual_server_id
                      ? virtualServerName || call.virtual_server_id
                      : "None (the org's default view)",
                  },
                ]
              : []),
            { label: "Key", value: keyName || call.api_key_id || undefined },
            { label: "Caller", value: call.principal_kind || undefined },
            {
              label: "Agent run",
              value: call.agent_run_id || undefined,
              mono: true,
            },
            { label: "Agent step", value: call.agent_step || undefined },
            {
              label: "Protocol",
              value: call.protocol_version || call.era || undefined,
            },
          ]}
        />
      </Section>

      {hasError && (
        <Section title="Error">
          <KeyValueList
            rows={[
              { label: "Class", value: call.error_class || undefined },
              { label: "Origin", value: call.error_origin || undefined },
              {
                label: "Code",
                value: call.error_code || undefined,
                mono: true,
              },
              { label: "Message", value: call.error_message || undefined },
            ]}
          />
        </Section>
      )}

      <CallOutcomes outcomes={call.outcomes} />

      <Section title="Timing and size">
        <KeyValueList
          rows={[
            { label: "Total", value: formatLatency(call.latency_ms) },
            {
              label: "Upstream",
              value: formatLatency(call.upstream_latency_ms),
            },
            {
              label: "Gateway",
              value: formatLatency(call.gateway_overhead_ms),
            },
            { label: "Sent", value: formatBytes(call.bytes_in) },
            { label: "Received", value: formatBytes(call.bytes_out) },
          ]}
        />
      </Section>

      <Section title="Billing">
        <KeyValueList
          rows={[
            { label: "Billing mode", value: billingLabel(call.billing_mode) },
            { label: "Credential", value: call.credential_source || undefined },
            // One amount, as the calls list's Cost: what the org pays on the
            // call's route. Upstream cost and margin are never shown apart.
            { label: "Cost", value: formatMicros(call.billed_cost_micros) },
          ]}
        />
      </Section>

      {guardRows.length > 0 && (
        <Section title="Guardrail result">
          <KeyValueList rows={guardRows} />
        </Section>
      )}

      <Section title="Identifiers">
        <KeyValueList
          rows={[
            {
              label: "Tool call",
              value: call.tool_call_id || undefined,
              mono: true,
            },
            {
              label: "Request",
              value: call.request_id || undefined,
              mono: true,
            },
            {
              label: "Session",
              value: call.session_hash
                ? `${call.session_hash.slice(0, 16)}…`
                : undefined,
              mono: true,
            },
          ]}
        />
      </Section>

      <Divider />

      {!stored ? (
        <Typography typography="s2" color="text.secondary">
          {`Arguments and result are not stored for this call (content mode: ${call.content_mode || "none"}).`}
        </Typography>
      ) : detailLoading ? (
        <Skeleton variant="rounded" height={120} />
      ) : detailUnavailable ? (
        <Alert
          severity="warning"
          action={
            <Button aria-label="Retry call details" disabled={detailFetching} onClick={retryDetail}>
              Retry
            </Button>
          }
        >
          {detailMissing
            ? "This tool-call record was not found. Its details may no longer be available."
            : "Call details are unavailable. This does not mean the arguments or result were not recorded."}
          {" Retrying only reads this call's details; it does not run the tool again."}
        </Alert>
      ) : (
        <>
          <Section title="Arguments">
            {detail?.arguments !== undefined ? (
              <JsonBlock value={detail.arguments} label="Arguments" />
            ) : (
              <Typography typography="s2" color="text.secondary">
                No arguments were recorded.
              </Typography>
            )}
          </Section>
          <Section title="Result">
            {detail?.result !== undefined ? (
              <JsonBlock value={detail.result} label="Result" />
            ) : (
              <Typography typography="s2" color="text.secondary">
                No result was recorded.
              </Typography>
            )}
          </Section>
        </>
      )}
    </Box>
  );
}

Content.propTypes = {
  virtualServers: PropTypes.bool,
  call: PropTypes.object.isRequired,
  detail: PropTypes.object,
  detailLoading: PropTypes.bool,
  detailUnavailable: PropTypes.bool,
  detailMissing: PropTypes.bool,
  detailFetching: PropTypes.bool,
  retryDetail: PropTypes.func,
  virtualServerName: PropTypes.string,
  keyName: PropTypes.string,
};

// One tool call: what was called, by whom, how it ended (its attempts,
// approval and limits), its timing and cost, and (content mode "full" only)
// its arguments and result.
export default function ToolCallDrawer(props) {
  const { explorer } = useMCPToolCallFeatures(props.gatewayId);
  return explorer ? <EnabledToolCallDrawer {...props} /> : null;
}

function EnabledToolCallDrawer({
  gatewayId,
  call,
  onClose,
  virtualServerName,
  keyName,
}) {
  const features = useMCPReleaseFeatures(gatewayId);
  // The list's rule: the recorded virtual server stays readable while the
  // status loads or fails, and after the switch is turned off.
  const virtualServers =
    (features.shown?.virtualServers ?? features.virtualServers) ||
    features.off?.virtualServers === true;
  const [traceId, setTraceId] = useState(null);
  const stored = call?.content_mode === "full";
  const { data: detail, isLoading, isError, error, isFetching, refetch } = useMCPToolCall(
    stored ? call?.id : null,
    gatewayId,
  );
  return (
    <>
      <GatewayDetailDrawer
        open={Boolean(call)}
        onClose={onClose}
        title={call?.tool || ""}
        subtitle="Tool call"
        actions={
          call?.agent_trace_id ? (
            <Button
              size="small"
              onClick={() => setTraceId(call.agent_trace_id)}
            >
              Open trace
            </Button>
          ) : undefined
        }
      >
        {call && (
          <Content
            call={call}
            detail={detail}
            detailLoading={isLoading}
            detailUnavailable={isError || !detail}
            detailMissing={isError && error?.response?.status === 404}
            detailFetching={isFetching}
            retryDetail={() => refetch()}
            virtualServers={virtualServers}
            virtualServerName={virtualServerName}
            keyName={keyName}
          />
        )}
      </GatewayDetailDrawer>
      {traceId && (
        <Suspense
          fallback={<Typography role="status">Loading trace…</Typography>}
        >
          <TraceDetailDrawer
            open
            readOnly
            traceId={traceId}
            onClose={() => setTraceId(null)}
            hasPrev={false}
            hasNext={false}
          />
        </Suspense>
      )}
    </>
  );
}

ToolCallDrawer.propTypes = {
  gatewayId: PropTypes.string,
  call: PropTypes.object,
  onClose: PropTypes.func.isRequired,
  virtualServerName: PropTypes.string,
  keyName: PropTypes.string,
};

EnabledToolCallDrawer.propTypes = ToolCallDrawer.propTypes;
