import React, { useState } from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
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
        sx={{
          fontVariantNumeric: "tabular-nums",
          overflowWrap: "anywhere",
        }}
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

// A cold refetch clears React Query's error while it is pending. Keep this
// control mounted and busy until its own refetch settles, independently of
// the other query. Cached data stays in the query, not a second local cache.
function QueryRecovery({ query, part, hasData }) {
  const [retrying, setRetrying] = useState(false);
  const busy = retrying || Boolean(query.isFetching);
  if (!query.error && !retrying) return null;
  const details = part === "details";
  const status = query.error?.statusCode ?? query.error?.response?.status;
  const notFound = details && !hasData && status === 404;
  const title = hasData
    ? `${details ? "Session details" : "Requests"} may be stale`
    : notFound
      ? "Session not found"
      : `${details ? "Session details" : "Requests"} could not be loaded`;

  const retry = async () => {
    if (busy) return;
    setRetrying(true);
    try {
      await query.refetch();
    } catch {
      // The query owns the error state; a failed retry must not escape the UI.
    } finally {
      setRetrying(false);
    }
  };

  return (
    <Alert severity="warning" sx={{ mb: 1 }}>
      <AlertTitle>{title}</AlertTitle>
      {hasData
        ? "Showing the last loaded response, not a confirmed current result."
        : notFound
          ? "The API could not find this session."
          : "Try this read again without reloading the other part of the session."}
      <Box sx={{ mt: 1 }}>
        <Button
          size="small"
          variant="outlined"
          disabled={busy}
          aria-busy={busy}
          onClick={retry}
        >
          {`Retry ${part}`}
        </Button>
      </Box>
    </Alert>
  );
}

QueryRecovery.propTypes = {
  query: PropTypes.object.isRequired,
  part: PropTypes.oneOf(["details", "requests"]).isRequired,
  hasData: PropTypes.bool.isRequired,
};

export default function SessionDetailDrawer({
  sessionId,
  open,
  onClose,
  onCopyLink,
}) {
  const detail = useSessionDetail(open ? sessionId : null);
  const requests = useSessionRequests(open ? sessionId : null);
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
  const partialRequests =
    rows.length > 0 &&
    (Number(stats.request_count) > rows.length || rows.length >= 100);
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
        <>
          {sessionId && onCopyLink && (
            <Button size="small" variant="outlined" onClick={onCopyLink}>
              Copy session link
            </Button>
          )}
          {session && (
            <Button
              component={RouterLink}
              href={logsHref({ session_id: session.session_id })}
              size="small"
              variant="outlined"
              startIcon={<GatewayIcon name="logs" size={16} />}
            >
              Open in Logs
            </Button>
          )}
        </>
      }
    >
      <Stack spacing={2}>
        <QueryRecovery
          key={`details-${sessionId}`}
          query={detail}
          part="details"
          hasData={Boolean(session)}
        />
        {detail.isLoading && !session && (
          <Stack spacing={1} aria-busy="true">
            <Skeleton variant="rounded" height={64} />
            <Skeleton variant="rounded" height={160} />
          </Stack>
        )}
        {!detail.isLoading && !session && !detail.error && (
          <GatewayEmptyState
            icon="sessions"
            title="Session details are unavailable"
            description="No session details were returned. This does not confirm that the session was deleted."
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
          </Stack>
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
              {partialRequests
                ? "Latest requests"
                : "Oldest first; open one in Logs"}
            </Typography>
          </Box>
          {partialRequests && (
            <Alert severity="info" sx={{ mb: 1 }}>
              Showing latest {rows.length}
              {Number(stats.request_count) >= rows.length
                ? ` of ${Number(stats.request_count).toLocaleString()}`
                : ""}{" "}
              requests, oldest first within this subset.
              {session?.session_id && (
                <Box sx={{ mt: 1 }}>
                  <Button
                    component={RouterLink}
                    href={logsHref({ session_id: session.session_id })}
                    size="small"
                    variant="outlined"
                  >
                    View all in Logs
                  </Button>
                </Box>
              )}
            </Alert>
          )}
          <QueryRecovery
            key={`requests-${sessionId}`}
            query={requests}
            part="requests"
            hasData={Array.isArray(requests.data)}
          />
          {requests.isLoading && !Array.isArray(requests.data) ? (
            <Stack spacing={0.5} aria-busy="true">
              {[0, 1, 2].map((i) => (
                <Skeleton key={i} variant="rounded" height={40} />
              ))}
            </Stack>
          ) : rows.length > 0 ? (
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
          ) : !requests.error && Array.isArray(requests.data) ? (
            <Typography typography="s2" color="text.secondary">
              No requests in this session yet.
            </Typography>
          ) : null}
        </Box>
      </Stack>
    </GatewayDetailDrawer>
  );
}

SessionDetailDrawer.propTypes = {
  sessionId: PropTypes.string,
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  onCopyLink: PropTypes.func,
};
