import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Link from "@mui/material/Link";
import Skeleton from "@mui/material/Skeleton";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

import { RouterLink } from "src/routes/components";
import { paths } from "src/routes/paths";

import {
  GatewayDetailDrawer,
  GatewayEmptyState,
  GatewayStatusChip,
} from "../components/frame";
import CostTotal from "../components/CostTotal";
import { GatewayIcon } from "../shell/gateway-icons";
import { formatLogTime } from "../logs/logDates";
import { formatMs, statusOf } from "../logs/logStatus";
import { EMPTY, NO_PRICE, formatCost, isUnpriced } from "../utils/formatters";
import { providerLabel } from "../utils/providerLabel";
import { useSessionDetail, useSessionRequests } from "./hooks/useSessions";
import { sessionStartedAt, sessionStatus } from "./sessionStatus";

// One session: its numbers, its metadata and its requests in order, each
// request opening in Logs (?log=<id>, the request drawer), and the whole
// session one click from Logs filtered by its id.

const logsHref = (params) =>
  `${paths.dashboard.gateway.logs}?${new URLSearchParams(params).toString()}`;

function Stat({ label, children }) {
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

Stat.propTypes = {
  label: PropTypes.string.isRequired,
  children: PropTypes.node,
};

function RequestItem({ request, first }) {
  const status = statusOf(request);
  return (
    <Box
      component="li"
      sx={{ borderTop: first ? 0 : "1px solid", borderColor: "divider" }}
    >
      <Link
        component={RouterLink}
        href={logsHref({ log: request.id })}
        underline="none"
        color="inherit"
        sx={{
          display: "grid",
          gridTemplateColumns: "auto minmax(0, 1fr)",
          columnGap: 1.25,
          alignItems: "center",
          px: 1,
          py: 0.75,
          borderRadius: 0.5,
          "&:hover": { bgcolor: "action.hover" },
        }}
      >
        <GatewayStatusChip
          tone={status.tone}
          label={status.label}
          showIcon={false}
        />
        <Box sx={{ minWidth: 0 }}>
          <Typography typography="s2" noWrap>
            {request.model || EMPTY}
            <Box component="span" sx={{ color: "text.secondary" }}>
              {request.provider
                ? ` · ${providerLabel(request.provider, request.billing_mode)}`
                : ""}
            </Box>
          </Typography>
          <Typography typography="s3" color="text.secondary" noWrap>
            {[
              formatLogTime(request.started_at),
              formatMs(request.latency_ms),
              `${Number(request.total_tokens ?? 0).toLocaleString()} tokens`,
              isUnpriced(request) ? NO_PRICE : formatCost(request.cost),
            ].join(" · ")}
          </Typography>
        </Box>
      </Link>
    </Box>
  );
}

RequestItem.propTypes = {
  request: PropTypes.object.isRequired,
  first: PropTypes.bool,
};

export default function SessionDetailDrawer({ sessionId, open, onClose }) {
  const detail = useSessionDetail(sessionId);
  const requests = useSessionRequests(sessionId);
  const session = detail.data;
  const stats = session?.stats || {};
  const metadata =
    session?.metadata && typeof session.metadata === "object"
      ? session.metadata
      : {};
  const rows = Array.isArray(requests.data)
    ? [...requests.data].sort(
        (a, b) => Date.parse(a.started_at || 0) - Date.parse(b.started_at || 0),
      )
    : [];
  const status = sessionStatus(
    session?.status,
    session?.closed_reason,
    session?.idle_timeout_seconds,
  );

  return (
    <GatewayDetailDrawer
      open={open}
      onClose={onClose}
      title={session ? session.name || session.session_id : "Session"}
      subtitle={
        session && session.name && session.name !== session.session_id
          ? session.session_id
          : "Session"
      }
      width={560}
      actions={
        session ? (
          <Button
            component={RouterLink}
            href={logsHref({ session_id: session.session_id })}
            size="small"
            variant="outlined"
            startIcon={<GatewayIcon name="logs" size={16} />}
          >
            Open in Logs
          </Button>
        ) : null
      }
    >
      {detail.isLoading && (
        <Stack spacing={1} aria-busy="true">
          <Skeleton variant="rounded" height={64} />
          <Skeleton variant="rounded" height={160} />
        </Stack>
      )}
      {!detail.isLoading && !session && (
        <GatewayEmptyState
          icon="sessions"
          title="This session could not be loaded"
          description={detail.error?.message || "It may have been deleted."}
        />
      )}
      {session && (
        <Stack spacing={2}>
          <Box
            component="ul"
            aria-label="Session summary"
            sx={{
              m: 0,
              p: 1.5,
              listStyle: "none",
              display: "grid",
              gridTemplateColumns: {
                xs: "repeat(2, minmax(0, 1fr))",
                sm: "repeat(3, minmax(0, 1fr))",
              },
              gap: 1.5,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 1,
            }}
          >
            <Stat label="Status">
              <Box
                component="span"
                title={status.detail || undefined}
                sx={{ display: "inline-flex" }}
              >
                <GatewayStatusChip
                  tone={status.tone}
                  label={status.label}
                  showIcon={false}
                />
              </Box>
            </Stat>
            <Stat label="Requests">
              {Number(stats.request_count ?? 0).toLocaleString()}
            </Stat>
            <Stat label="Tokens">
              {Number(stats.total_tokens ?? 0).toLocaleString()}
            </Stat>
            <Stat label="Cost">
              <CostTotal
                value={stats.total_cost}
                unpriced={stats.unpriced_requests}
                requests={stats.request_count}
              />
            </Stat>
            <Stat label="Avg latency">{formatMs(stats.avg_latency_ms)}</Stat>
            <Stat label="Started">
              {formatLogTime(sessionStartedAt(session))}
            </Stat>
            <Stat label="Last request">
              {session.last_request_at
                ? formatLogTime(session.last_request_at)
                : EMPTY}
            </Stat>
          </Box>

          {Object.keys(metadata).length > 0 && (
            <Box>
              <Typography
                component="h3"
                typography="s1"
                fontWeight="fontWeightMedium"
                sx={{ mb: 0.75 }}
              >
                Metadata
              </Typography>
              <Box
                component="dl"
                sx={{
                  m: 0,
                  display: "grid",
                  gridTemplateColumns: "minmax(0, 1fr) minmax(0, 2fr)",
                  rowGap: 0.5,
                  columnGap: 1.5,
                }}
              >
                {Object.entries(metadata).map(([key, value]) => (
                  <React.Fragment key={key}>
                    <Typography
                      component="dt"
                      typography="s2"
                      color="text.secondary"
                      noWrap
                      title={key}
                    >
                      {key}
                    </Typography>
                    <Typography
                      component="dd"
                      typography="s2"
                      sx={{ m: 0, overflowWrap: "anywhere" }}
                    >
                      {typeof value === "object"
                        ? JSON.stringify(value)
                        : String(value)}
                    </Typography>
                  </React.Fragment>
                ))}
              </Box>
            </Box>
          )}

          <Box>
            <Box
              sx={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "baseline",
                mb: 0.75,
              }}
            >
              <Typography
                component="h3"
                typography="s1"
                fontWeight="fontWeightMedium"
              >
                Requests
              </Typography>
              <Typography typography="s2" color="text.secondary">
                Oldest first; open one in Logs
              </Typography>
            </Box>
            {requests.isLoading ? (
              <Stack spacing={0.5}>
                {[0, 1, 2].map((i) => (
                  <Skeleton key={i} variant="rounded" height={40} />
                ))}
              </Stack>
            ) : rows.length === 0 ? (
              <Typography typography="s2" color="text.secondary">
                {requests.error
                  ? "The requests could not be loaded."
                  : "No requests in this session yet."}
              </Typography>
            ) : (
              <Box
                component="ol"
                aria-label="Requests in this session"
                sx={{
                  listStyle: "none",
                  m: 0,
                  p: 0,
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: 1,
                }}
              >
                {rows.map((request, i) => (
                  <RequestItem
                    key={request.id}
                    request={request}
                    first={i === 0}
                  />
                ))}
              </Box>
            )}
          </Box>
        </Stack>
      )}
    </GatewayDetailDrawer>
  );
}

SessionDetailDrawer.propTypes = {
  sessionId: PropTypes.string,
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
};
