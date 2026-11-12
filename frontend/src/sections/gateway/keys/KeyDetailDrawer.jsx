import React, { useMemo } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Chip,
  Divider,
  IconButton,
  Link,
  Skeleton,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import Chart from "react-apexcharts";
import { useTheme } from "@mui/material/styles";

import { paths } from "src/routes/paths";
import { RouterLink } from "src/routes/components";

import { GatewayDetailDrawer, GatewayStatusChip } from "../components/frame";
import GatewayAdminOnly from "../components/GatewayAdminOnly";
import { useGatewayAdmin } from "../hooks/useGatewayAdmin";
import { useApiKeyDetail } from "./hooks/useApiKeys";
import { useAnalyticsOverview } from "../analytics/hooks/useAnalyticsOverview";
import { useAnalyticsUsage } from "../analytics/hooks/useAnalyticsUsage";
import CostTotal from "../components/CostTotal";
import { overviewCost, perRequest, val } from "../utils/analyticsHelpers";
import { formatPercent } from "../utils/formatters";
import {
  DetailFigure,
  DetailRow,
  DetailSection,
} from "../providers/ui/DetailRows";
import FaIcon from "../providers/ui/FaIcon";
import { copyText } from "../models/copyText";
import {
  KEY_STATUS,
  exactTime,
  keyPrefixLabel,
  lastUsedDetail,
} from "./key-format";
import { useKeyRevocation } from "./useKeyRevocation";
import { canRotate } from "./rotate";
import { GATEWAY_MONO } from "../utils/fonts";
import { localTimeTooltip } from "../utils/timeZone";

function ChipList({ items, emptyText }) {
  if (!items?.length) {
    return (
      <Typography typography="s1" color="text.secondary">
        {emptyText}
      </Typography>
    );
  }
  return (
    <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.5 }}>
      {items.map((item) => (
        <Chip key={item} label={item} size="small" variant="outlined" />
      ))}
    </Box>
  );
}

ChipList.propTypes = {
  items: PropTypes.arrayOf(PropTypes.string),
  emptyText: PropTypes.string.isRequired,
};

// One API key: its details and access, its last 7 days of use from the
// request logs, and (on a live key) Edit, Rotate and Revoke, which the page
// carries out. Rotate also requires ownership; an active member may revoke their
// own key, identified by the server's user ID rather than its contact label.
const KeyDetailDrawer = ({
  keyId,
  open,
  onClose,
  gatewayId,
  onEdit,
  onRotate,
  onRevoke,
  revokeWarning,
  paperRef,
}) => {
  const theme = useTheme();
  const { canEdit: canWrite } = useGatewayAdmin();
  const { canRevoke, userId } = useKeyRevocation();
  const {
    data, isLoading, isError, error, refetch, isFetching,
  } = useApiKeyDetail(open ? keyId : null);
  const readFailed = isError || Boolean(error);
  const notFound = Number(error?.response?.status ?? error?.statusCode ?? error?.status) === 404;
  // A cached row does not turn a failed detail refetch into an authoritative
  // record. Keep the same key selected for Retry, with no stale write actions.
  const keyData = readFailed ? null : data;

  // Per-key analytics: last 7 days, once the key (and its gateway id) is known.
  const resolvedKeyId = keyData?.gatewayKeyId;
  const now = useMemo(() => new Date(), []);
  const weekAgo = useMemo(() => {
    const d = new Date(now);
    d.setDate(d.getDate() - 7);
    return d;
  }, [now]);

  const analyticsParams = useMemo(
    () => ({
      start: weekAgo.toISOString(),
      end: now.toISOString(),
      gatewayId,
      apiKeyId: resolvedKeyId,
    }),
    [weekAgo, now, gatewayId, resolvedKeyId],
  );

  // These are ambient reads of this drawer's Usage panel, not the result of
  // anything the user just submitted. `errorHandled` keeps the app-wide query
  // error toast off them, so a failed refetch cannot land next to a save and
  // read as "your save failed"; the panel reports it where it happened.
  const analyticsOptions = {
    enabled: open && Boolean(resolvedKeyId),
    // Shared analytics retains the previous filter's data; this entity drawer
    // must never relabel the previous key's figures as the newly selected key.
    placeholderData: undefined,
    meta: { errorHandled: true },
  };

  const overviewQuery = useAnalyticsOverview(
    resolvedKeyId ? analyticsParams : {},
    analyticsOptions,
  );

  const usageQuery = useAnalyticsUsage(
    resolvedKeyId ? { ...analyticsParams, granularity: "1h" } : {},
    analyticsOptions,
  );

  const { data: overviewData, isError: overviewFailed } = overviewQuery;
  const { isError: usageFailed } = usageQuery;
  const overviewPending = overviewQuery.isLoading || overviewQuery.isPending ||
    overviewQuery.isPlaceholderData || !overviewData;
  const usagePending = usageQuery.isLoading || usageQuery.isPending ||
    usageQuery.isPlaceholderData || !usageQuery.data;
  const usageData = resolvedKeyId && !usageFailed && !usagePending ? usageQuery.data : null;
  const analyticsFailed = overviewFailed || usageFailed;
  // Unknown and pending reads are not successful zero-traffic measurements.
  const figure = (read) => {
    if (!resolvedKeyId || overviewFailed) return "\u2014";
    return overviewPending ? "Loading\u2026" : read();
  };

  const sparklineSeries = useMemo(() => {
    const series = usageData?.series;
    if (!series?.length) return [];
    return [
      {
        name: "Requests",
        data: series.map((p) => ({
          x: new Date(p.bucket).getTime(),
          y: p.request_count ?? 0,
        })),
      },
    ];
  }, [usageData]);

  const sparklineOptions = useMemo(
    () => ({
      chart: {
        type: "area",
        sparkline: { enabled: true },
        toolbar: { show: false },
      },
      colors: [theme.palette.primary.main],
      stroke: { curve: "smooth", width: 2 },
      fill: {
        type: "gradient",
        gradient: {
          shadeIntensity: 1,
          opacityFrom: 0.3,
          opacityTo: 0.05,
          stops: [0, 100],
        },
      },
      tooltip: {
        theme: theme.palette.mode,
        x: localTimeTooltip(),
        y: { formatter: (v) => (v != null ? v.toLocaleString() : "0") },
      },
    }),
    [theme],
  );

  const revokedLocally =
    keyData?.status === "revoked" || Boolean(revokeWarning);
  const status =
    KEY_STATUS[revokedLocally ? "revoked" : keyData?.status] || null;
  const live = keyData?.status === "active" && !revokedLocally;
  const revocable =
    ["active", "pending", "failed"].includes(keyData?.status) &&
    !revokedLocally;
  const logsHref = `${paths.dashboard.gateway.logs}?api_key_id=${encodeURIComponent(
    keyData?.gatewayKeyId || keyId || "",
  )}`;

  return (
    <GatewayDetailDrawer
      open={open}
      paperRef={paperRef}
      onClose={onClose}
      title={keyData?.name || (isLoading ? "Loading key" : "API key")}
      subtitle={keyData ? keyPrefixLabel(keyData.keyPrefix) : undefined}
      actions={
        keyData && revocable ? (
          <Box sx={{ display: "flex", gap: 1, flexWrap: "wrap" }}>
            {live && (
              <GatewayAdminOnly allowed={canWrite}>
                <Button
                  size="small"
                  variant="outlined"
                  startIcon={<FaIcon name="edit" />}
                  onClick={() => onEdit?.(keyData)}
                  disabled={!canWrite}
                >
                  Edit
                </Button>
              </GatewayAdminOnly>
            )}
            {canRotate(keyData, userId) && canRevoke(keyData) && (
              <GatewayAdminOnly allowed={canWrite}>
                <Button
                  size="small"
                  variant="outlined"
                  startIcon={<FaIcon name="reload" />}
                  onClick={() => canWrite && onRotate?.(keyData)}
                  disabled={!canWrite}
                >
                  Rotate
                </Button>
              </GatewayAdminOnly>
            )}
            <GatewayAdminOnly allowed={canRevoke(keyData)}>
              <Button
                size="small"
                variant="outlined"
                color="error"
                onClick={() => canRevoke(keyData) && onRevoke?.(keyData)}
                disabled={!canRevoke(keyData)}
              >
                Revoke
              </Button>
            </GatewayAdminOnly>
          </Box>
        ) : null
      }
    >
      {isLoading ? (
        <Stack spacing={2}>
          <Skeleton width="60%" height={28} />
          <Skeleton width="40%" />
          <Skeleton variant="rounded" height={160} />
        </Stack>
      ) : readFailed && !notFound ? (
        <Alert
          severity="error"
          action={
            <Button color="inherit" disabled={isFetching} onClick={() => refetch()}>
              {isFetching ? "Retrying…" : "Retry"}
            </Button>
          }
        >
          <Typography typography="s1">Could not load this key</Typography>
          <Typography typography="s2">Try again to load its details.</Typography>
        </Alert>
      ) : keyData ? (
        <Stack spacing={2.5}>
          {revokedLocally && (
            <Alert severity="warning">
              {revokeWarning && (
                <Typography sx={{ mb: 1 }}>{revokeWarning}</Typography>
              )}
              Gateway enforcement is not confirmed by this local status.
              Requests may still be accepted until the gateway applies the
              revocation.
            </Alert>
          )}
          <DetailSection title="Details">
            <Box>
              <DetailRow label="Status">
                {status ? (
                  <GatewayStatusChip tone={status.tone} label={status.label} />
                ) : (
                  keyData.status || "—"
                )}
              </DetailRow>
              <DetailRow label="Key" mono>
                {keyPrefixLabel(keyData.keyPrefix)}
              </DetailRow>
              {/* The id the gateway and Logs know the key by (api_key_id). */}
              <DetailRow label="Key ID" mono>
                {keyData.gatewayKeyId ? (
                  <Box
                    component="span"
                    sx={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 0.5,
                    }}
                  >
                    {keyData.gatewayKeyId}
                    <Tooltip title="Copy key ID">
                      <IconButton
                        size="small"
                        aria-label="Copy key ID"
                        onClick={() =>
                          copyText(keyData.gatewayKeyId, "Key ID copied")
                        }
                      >
                        <FaIcon name="copy" />
                      </IconButton>
                    </Tooltip>
                  </Box>
                ) : (
                  "—"
                )}
              </DetailRow>
              <DetailRow label="Owner">{keyData.owner || "—"}</DetailRow>
              <DetailRow label="Created">
                {exactTime(keyData.created_at) || "—"}
              </DetailRow>
              <DetailRow label="Updated">
                {exactTime(keyData.updated_at) || "—"}
              </DetailRow>
              <DetailRow label="Last used">
                {lastUsedDetail(keyData.last_used_at)}
              </DetailRow>
              <DetailRow label="Expires">
                {exactTime(keyData.expires_at) || "Never"}
              </DetailRow>
            </Box>
          </DetailSection>

          <Divider />

          <DetailSection title="Allowed models">
            <ChipList items={keyData.allowedModels} emptyText="All models" />
          </DetailSection>
          <DetailSection title="Allowed providers">
            <ChipList
              items={keyData.allowedProviders}
              emptyText="All providers"
            />
          </DetailSection>

          {keyData.metadata &&
            Object.keys(keyData.metadata).filter((k) => k !== "org_id").length >
              0 && (
              <DetailSection title="Metadata">
                <Box
                  component="pre"
                  sx={{
                    m: 0,
                    bgcolor: "background.neutral",
                    border: "1px solid",
                    borderColor: "divider",
                    borderRadius: 1,
                    p: 1.5,
                    fontFamily: GATEWAY_MONO,
                    typography: "s2",
                    whiteSpace: "pre-wrap",
                    overflowX: "auto",
                    color: "text.primary",
                  }}
                >
                  {JSON.stringify(
                    Object.fromEntries(
                      Object.entries(keyData.metadata).filter(
                        ([k]) => k !== "org_id",
                      ),
                    ),
                    null,
                    2,
                  )}
                </Box>
              </DetailSection>
            )}

          <Divider />

          <DetailSection
            title="Usage (last 7 days)"
            action={
              <Link
                component={RouterLink}
                href={logsHref}
                underline="hover"
                typography="s2"
                onClick={onClose}
                sx={{ display: "inline-flex", alignItems: "center", gap: 0.5 }}
              >
                View logs
                <FaIcon name="open" size={14} />
              </Link>
            }
          >
            {analyticsFailed && (
              <Alert severity="warning">
                Usage for this key could not be loaded.
              </Alert>
            )}
            <Box
              sx={{
                display: "grid",
                gridTemplateColumns: {
                  xs: "repeat(2, minmax(0, 1fr))",
                  sm: "repeat(4, minmax(0, 1fr))",
                },
                gap: 2,
              }}
            >
              <DetailFigure
                label="Requests"
                value={figure(() =>
                  Number(
                    val(overviewData?.total_requests) ?? 0,
                  ).toLocaleString(),
                )}
              />
              <DetailFigure
                label="Cost"
                value={figure(() => (
                  <CostTotal {...overviewCost(overviewData)} />
                ))}
              />
              <DetailFigure
                label="Tokens"
                value={figure(() =>
                  Number(val(overviewData?.total_tokens) ?? 0).toLocaleString(),
                )}
              />
              <DetailFigure
                label="Error rate"
                value={figure(() =>
                  formatPercent(
                    perRequest(
                      overviewData?.total_requests,
                      val(overviewData?.error_rate),
                    ),
                  ),
                )}
              />
            </Box>
            {resolvedKeyId && !usageFailed && usagePending && (
              <Typography typography="s2" color="text.secondary" role="status">
                Loading request history…
              </Typography>
            )}
            {sparklineSeries.length > 0 && (
              <Chart
                type="area"
                height={100}
                options={sparklineOptions}
                series={sparklineSeries}
              />
            )}
          </DetailSection>
        </Stack>
      ) : (
        <Typography typography="s1" color="text.secondary">
          This key was not found. It may have been deleted.
        </Typography>
      )}
    </GatewayDetailDrawer>
  );
};

KeyDetailDrawer.propTypes = {
  keyId: PropTypes.string,
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  gatewayId: PropTypes.string,
  onEdit: PropTypes.func,
  onRotate: PropTypes.func,
  onRevoke: PropTypes.func,
  revokeWarning: PropTypes.string,
  paperRef: PropTypes.oneOfType([PropTypes.func, PropTypes.object]),
};

export default KeyDetailDrawer;
