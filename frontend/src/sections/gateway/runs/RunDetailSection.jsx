import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import { useNavigate, useParams } from "react-router-dom";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import ButtonBase from "@mui/material/ButtonBase";
import Skeleton from "@mui/material/Skeleton";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

import { RouterLink } from "src/routes/components";
import { paths } from "src/routes/paths";

import {
  GatewayEmptyState,
  GatewayGrid,
  GatewayPageFrame,
  GatewayStatusChip,
} from "../components/frame";
import { GatewayIcon } from "../shell/gateway-icons";
import RequestDetailDrawer from "../logs/RequestDetailDrawer";
import RunLink from "../logs/RunLink";
import { formatLogTime } from "../logs/logDates";
import { formatMs, statusOf } from "../logs/logStatus";
import CostTotal from "../components/CostTotal";
import { EMPTY, NO_PRICE, formatCost, isUnpriced } from "../utils/formatters";
import { RUNS_WINDOW, useRunRequests } from "./hooks/useRunRequests";
import { runCost, summarizeRun } from "./runModel";

// One agent run: every request that carried its run id, on the run's own
// time axis (each call at its offset from the first, sized by its latency),
// broken down by step and by model. A call opens the request drawer.

const TONE_COLOR = {
  healthy: "success.main",
  warning: "warning.main",
  error: "error.main",
  neutral: "text.disabled",
};

const pct = (value, total) =>
  `${Math.min(100, Math.max(0, (value / total) * 100)).toFixed(2)}%`;

function Heading({ children }) {
  return (
    <Typography
      component="h2"
      typography="s1"
      fontWeight="fontWeightMedium"
      sx={{ mb: 0.75 }}
    >
      {children}
    </Typography>
  );
}

Heading.propTypes = { children: PropTypes.node };

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
        sx={{ fontVariantNumeric: "tabular-nums", overflowWrap: "anywhere" }}
      >
        {children}
      </Typography>
    </Box>
  );
}

SummaryItem.propTypes = { label: PropTypes.string, children: PropTypes.node };

function CallRow({ call, axis, onOpen, first }) {
  const status = statusOf(call);
  const color = TONE_COLOR[status.tone];
  return (
    <Box
      component="li"
      sx={{ borderTop: first ? 0 : "1px solid", borderColor: "divider" }}
    >
      <ButtonBase
        onClick={() => onOpen(call.id)}
        aria-label={`Open request ${call.request_id || call.id}`}
        sx={{
          width: "100%",
          textAlign: "left",
          display: "grid",
          gridTemplateColumns: {
            xs: "64px minmax(0, 1fr)",
            sm: "64px minmax(0, 1.2fr) minmax(140px, 1fr)",
          },
          columnGap: 1.5,
          rowGap: 0.5,
          alignItems: "center",
          px: 1,
          py: 0.75,
          borderRadius: 0.5,
          "&:hover": { bgcolor: "action.hover" },
        }}
      >
        <Typography
          typography="s3"
          color="text.secondary"
          sx={{ textAlign: "right", fontVariantNumeric: "tabular-nums" }}
        >
          {call.offsetMs != null ? `+${formatMs(call.offsetMs)}` : "—"}
        </Typography>
        <Box
          sx={{ minWidth: 0, display: "flex", alignItems: "center", gap: 1 }}
        >
          <GatewayStatusChip
            tone={status.tone}
            label={status.label}
            showIcon={false}
          />
          <Box sx={{ minWidth: 0 }}>
            <Typography typography="s2" noWrap>
              {call.agent_step || "No step"}
              <Box component="span" sx={{ color: "text.secondary" }}>
                {` · ${call.model || EMPTY}`}
              </Box>
            </Typography>
            <Typography typography="s3" color="text.secondary" noWrap>
              {`${formatMs(call.latency_ms)} · `}
              {/* A call with no price reads as Logs does, never $0.00. */}
              {isUnpriced(call) ? (
                <Box component="span" title={NO_PRICE}>
                  {EMPTY}
                </Box>
              ) : (
                formatCost(call.cost)
              )}
            </Typography>
          </Box>
        </Box>
        <Box
          aria-hidden
          sx={{
            gridColumn: { xs: "2", sm: "3" },
            position: "relative",
            height: 8,
            borderRadius: 0.5,
            bgcolor: "action.hover",
          }}
        >
          {call.offsetMs != null && (
            <Box
              style={{
                left: pct(call.offsetMs, axis),
                width: pct(Math.max(call.durationMs ?? 0, axis * 0.006), axis),
              }}
              sx={{
                position: "absolute",
                top: 0,
                bottom: 0,
                borderRadius: 0.5,
                bgcolor: color,
              }}
            />
          )}
        </Box>
      </ButtonBase>
    </Box>
  );
}

CallRow.propTypes = {
  call: PropTypes.object.isRequired,
  axis: PropTypes.number.isRequired,
  onOpen: PropTypes.func.isRequired,
  first: PropTypes.bool,
};

// Each minimum holds its header: the label, 32 px of padding, the 24 px menu
// button and the 24 px sort arrow (runs/__tests__/RunsPages.test.jsx).
const breakdownColumns = (keyHeader) => [
  {
    field: "key",
    headerName: keyHeader,
    minWidth: 140,
    flex: 1.4,
  },
  { field: "requests", headerName: "Requests", minWidth: 144, flex: 0.7 },
  {
    field: "errors",
    headerName: "Errors",
    minWidth: 122,
    flex: 0.6,
    cellRenderer: ({ value }) => (
      <Box
        component="span"
        sx={{ color: value > 0 ? "error.main" : "text.secondary" }}
      >
        {value}
      </Box>
    ),
  },
  {
    field: "latencyMs",
    headerName: "Time in calls",
    minWidth: 168,
    flex: 0.8,
    valueFormatter: ({ value }) => formatMs(value),
  },
  {
    field: "tokens",
    headerName: "Tokens",
    minWidth: 130,
    flex: 0.7,
    valueFormatter: ({ value }) => Number(value).toLocaleString(),
  },
  {
    field: "cost",
    headerName: "Cost",
    minWidth: 120,
    flex: 0.7,
    // Unknown (no call priced) sorts as missing; some unpriced: a floor.
    valueGetter: ({ data }) =>
      runCost(data, { cell: true }).unknown ? null : data.cost,
    valueFormatter: ({ data }) => runCost(data, { cell: true }).text,
    tooltipValueGetter: ({ data }) => runCost(data, { cell: true }).title,
  },
];

const STEP_COLUMNS = breakdownColumns("Step");
const MODEL_COLUMNS = breakdownColumns("Model");

export default function RunDetailSection() {
  const { runId } = useParams();
  const navigate = useNavigate();
  const { data, isLoading, error, refetch } = useRunRequests(runId);
  const [openLog, setOpenLog] = useState(null);

  const run = useMemo(() => summarizeRun(data?.results), [data]);
  const total = data?.count ?? 0;
  const axis = Math.max(run.totalMs, 1);
  const logsHref = `${paths.dashboard.gateway.logs}?${new URLSearchParams({ run_id: runId }).toString()}`;

  const description = [run.agentId, run.workflow, run.env, run.framework]
    .filter(Boolean)
    .join(" · ");

  return (
    <GatewayPageFrame
      title={runId}
      documentTitle={`Run ${runId}`}
      description={description || "Every request that carried this run id."}
      back={{ href: paths.dashboard.gateway.runs, label: "Runs" }}
      actions={
        <Button
          component={RouterLink}
          href={logsHref}
          variant="outlined"
          startIcon={<GatewayIcon name="logs" size={16} />}
        >
          Open in Logs
        </Button>
      }
    >
      {error && (
        <GatewayEmptyState
          icon="alertCircle"
          title="This run could not be loaded"
          description={error.message || "The gateway API did not answer."}
          actionLabel="Retry"
          onAction={() => refetch()}
        />
      )}

      {isLoading && (
        <Stack spacing={1.5} aria-busy="true" aria-label="Loading run">
          <Skeleton variant="rounded" height={64} />
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} variant="rounded" height={36} />
          ))}
        </Stack>
      )}

      {!isLoading && !error && run.requests === 0 && (
        <GatewayEmptyState
          icon="runs"
          title="No requests carry this run id"
          description="Check the id, or the time it ran: the gateway keeps request logs for this organization's retention period."
          actionLabel="All runs"
          onAction={() => navigate(paths.dashboard.gateway.runs)}
        />
      )}

      {!isLoading && !error && run.requests > 0 && (
        <Stack spacing={2.5} sx={{ minWidth: 0 }}>
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
                sm: "repeat(3, minmax(0, 1fr))",
                md: "repeat(6, minmax(0, 1fr))",
              },
              gap: 1.5,
              borderRadius: 1,
              border: "1px solid",
              borderColor: "divider",
            }}
          >
            <SummaryItem label="Agent">{run.agentId || EMPTY}</SummaryItem>
            <SummaryItem label="Requests">{run.requests}</SummaryItem>
            <SummaryItem label="Errors">{run.errors}</SummaryItem>
            <SummaryItem label="Cost">
              <CostTotal
                value={run.cost}
                unpriced={run.unpriced}
                requests={run.requests}
              />
            </SummaryItem>
            <SummaryItem label="Duration">{formatMs(run.totalMs)}</SummaryItem>
            <SummaryItem label="Started">
              {formatLogTime(run.startedAt)}
            </SummaryItem>
          </Box>

          {(run.parentRunId || run.rootRunId) && (
            <Stack direction="row" spacing={3} flexWrap="wrap" useFlexGap>
              {run.parentRunId && (
                <Typography typography="s2" color="text.secondary">
                  Parent run <RunLink runId={run.parentRunId} />
                </Typography>
              )}
              {run.rootRunId && run.rootRunId !== run.parentRunId && (
                <Typography typography="s2" color="text.secondary">
                  Root run <RunLink runId={run.rootRunId} />
                </Typography>
              )}
            </Stack>
          )}

          {total > run.requests && (
            <Typography typography="s2" color="text.secondary">
              {`Showing the first ${Math.min(run.requests, RUNS_WINDOW).toLocaleString()} of ${total.toLocaleString()} requests in this run. Open in Logs for all of them.`}
            </Typography>
          )}

          <Box>
            <Box
              sx={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "baseline",
              }}
            >
              <Heading>Timeline</Heading>
              <Typography typography="s2" color="text.secondary">
                {`${run.requests} calls over ${formatMs(run.totalMs)}`}
              </Typography>
            </Box>
            <Box
              component="ol"
              aria-label="Run timeline"
              sx={{
                listStyle: "none",
                m: 0,
                p: 0,
                border: "1px solid",
                borderColor: "divider",
                borderRadius: 1,
              }}
            >
              {run.calls.map((call, i) => (
                <CallRow
                  key={call.id}
                  call={call}
                  axis={axis}
                  onOpen={setOpenLog}
                  first={i === 0}
                />
              ))}
            </Box>
          </Box>

          <Box
            sx={{
              display: "grid",
              gap: 2.5,
              alignItems: "start",
              gridTemplateColumns: { xs: "1fr", xl: "1fr 1fr" },
            }}
          >
            <Box sx={{ minWidth: 0 }}>
              <Heading>Steps</Heading>
              <GatewayGrid
                rowData={run.bySteps}
                columnDefs={STEP_COLUMNS}
                stateKey="gateway-run-steps"
                getRowId={(params) => params.data.key}
                domLayout="autoHeight"
                height="auto"
                rowHeight={36}
                headerHeight={36}
              />
            </Box>
            <Box sx={{ minWidth: 0 }}>
              <Heading>Models</Heading>
              <GatewayGrid
                rowData={run.byModels}
                columnDefs={MODEL_COLUMNS}
                stateKey="gateway-run-models"
                getRowId={(params) => params.data.key}
                domLayout="autoHeight"
                height="auto"
                rowHeight={36}
                headerHeight={36}
              />
            </Box>
          </Box>
        </Stack>
      )}

      <RequestDetailDrawer
        logId={openLog}
        open={Boolean(openLog)}
        onClose={() => setOpenLog(null)}
      />
    </GatewayPageFrame>
  );
}
