import React, { useState, useMemo } from "react";
import PropTypes from "prop-types";
import {
  Drawer,
  Box,
  Typography,
  Stack,
  Chip,
  Divider,
  Button,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogContentText,
  DialogActions,
  Skeleton,
  IconButton,
} from "@mui/material";
import Iconify from "src/components/iconify";
import Chart from "react-apexcharts";
import { useTheme } from "@mui/material/styles";
import { useNavigate } from "react-router-dom";
import { useApiKeyDetail, useRevokeApiKey } from "./hooks/useApiKeys";
import EditKeyDialog from "./EditKeyDialog";
import { GatewayMetadataJson } from "../components/GatewayMetadata";
import { hasGatewayMetadata } from "../utils/metadataDisplay";
import { useAnalyticsOverview } from "../analytics/hooks/useAnalyticsOverview";
import { useAnalyticsUsage } from "../analytics/hooks/useAnalyticsUsage";
import { val } from "../utils/analyticsHelpers";
import { formatCost, formatDateTime as formatDate } from "../utils/formatters";

const STATUS_COLORS = {
  active: "success",
  revoked: "error",
  expired: "warning",
};

const InfoRow = ({ label, children }) => (
  <Stack
    direction="row"
    justifyContent="space-between"
    alignItems="center"
    py={0.75}
  >
    <Typography variant="body2" color="text.secondary" sx={{ minWidth: 120 }}>
      {label}
    </Typography>
    <Box sx={{ textAlign: "right" }}>{children}</Box>
  </Stack>
);

InfoRow.propTypes = {
  label: PropTypes.string.isRequired,
  children: PropTypes.node.isRequired,
};

const KeyDetailDrawer = ({ keyId, open, onClose, gatewayId }) => {
  const theme = useTheme();
  const navigate = useNavigate();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);

  const { data: keyData, isLoading } = useApiKeyDetail(open ? keyId : null);
  const revokeMutation = useRevokeApiKey();

  // Per-key analytics: last 7 days — only fetch once keyData is loaded
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

  const { data: overviewData } = useAnalyticsOverview(
    resolvedKeyId ? analyticsParams : {},
    { enabled: Boolean(resolvedKeyId) },
  );

  const { data: usageData } = useAnalyticsUsage(
    resolvedKeyId ? { ...analyticsParams, granularity: "1h" } : {},
    { enabled: Boolean(resolvedKeyId) },
  );

  const handleRevoke = () => {
    revokeMutation.mutate(keyId, {
      onSuccess: () => {
        setConfirmOpen(false);
        onClose();
      },
    });
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
        x: { format: "MMM dd, HH:mm" },
        y: { formatter: (val) => (val != null ? val.toLocaleString() : "0") },
      },
    }),
    [theme],
  );

  const isRevoked = keyData?.status === "revoked";
  const isExpired = keyData?.status === "expired";

  return (
    <>
      <Drawer
        anchor="right"
        open={open}
        onClose={onClose}
        PaperProps={{ sx: { width: { xs: "100%", sm: 420 }, p: 3 } }}
      >
        {isLoading ? (
          <Stack spacing={2}>
            <Skeleton width="60%" height={32} />
            <Skeleton width="40%" />
            <Skeleton variant="rectangular" height={200} />
          </Stack>
        ) : keyData ? (
          <Stack spacing={2.5}>
            {/* Header */}
            <Stack
              direction="row"
              justifyContent="space-between"
              alignItems="flex-start"
            >
              <Box>
                <Typography variant="h6">{keyData.name}</Typography>
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ fontFamily: "monospace" }}
                >
                  {keyData.keyPrefix ? `${keyData.keyPrefix}****` : "\u2014"}
                </Typography>
              </Box>
              <Stack direction="row" spacing={0.5} alignItems="center">
                <Chip
                  label={keyData.status}
                  color={STATUS_COLORS[keyData.status] || "default"}
                  size="small"
                  variant="outlined"
                />
                {keyData.status === "active" && (
                  <IconButton
                    onClick={() => setEditOpen(true)}
                    size="small"
                    title="Edit"
                  >
                    <Iconify icon="mdi:pencil-outline" width={18} />
                  </IconButton>
                )}
                <IconButton onClick={onClose} size="small">
                  <Iconify icon="mdi:close" width={18} />
                </IconButton>
              </Stack>
            </Stack>

            <Divider />

            {/* Details */}
            <Box>
              <InfoRow label="Owner">
                <Typography variant="body2">
                  {keyData.owner || "\u2014"}
                </Typography>
              </InfoRow>
              <InfoRow label="Created">
                <Typography variant="body2">
                  {formatDate(keyData.created_at)}
                </Typography>
              </InfoRow>
              <InfoRow label="Last Used">
                <Typography variant="body2">
                  {formatDate(keyData.last_used_at)}
                </Typography>
              </InfoRow>
              <InfoRow label="Expires">
                <Typography variant="body2">
                  {formatDate(keyData.expires_at)}
                </Typography>
              </InfoRow>
            </Box>

            <Divider />

            {/* Models */}
            <Box>
              <Typography variant="subtitle2" mb={1}>
                Allowed Models
              </Typography>
              {keyData.allowedModels?.length > 0 ? (
                <Stack direction="row" flexWrap="wrap" gap={0.5}>
                  {keyData.allowedModels.map((m) => (
                    <Chip
                      key={m}
                      label={m}
                      size="small"
                      variant="outlined"
                      sx={{
                        borderColor: "divider",
                        color: "text.secondary",
                        fontSize: "0.75rem",
                      }}
                    />
                  ))}
                </Stack>
              ) : (
                <Typography variant="body2" color="text.secondary">
                  All models
                </Typography>
              )}
            </Box>

            {/* Providers */}
            <Box>
              <Typography variant="subtitle2" mb={1}>
                Allowed Providers
              </Typography>
              {keyData.allowedProviders?.length > 0 ? (
                <Stack direction="row" flexWrap="wrap" gap={0.5}>
                  {keyData.allowedProviders.map((p) => (
                    <Chip
                      key={p}
                      label={p}
                      size="small"
                      variant="outlined"
                      sx={{
                        borderColor: "divider",
                        color: "text.secondary",
                        fontSize: "0.75rem",
                      }}
                    />
                  ))}
                </Stack>
              ) : (
                <Typography variant="body2" color="text.secondary">
                  All providers
                </Typography>
              )}
            </Box>

            {/* Metadata */}
            {hasGatewayMetadata(keyData.metadata) && (
              <Box>
                <Typography variant="subtitle2" mb={1}>
                  Metadata
                </Typography>
                <Box
                  sx={{
                    bgcolor: "action.hover",
                    borderRadius: 1,
                    p: 1.5,
                    fontFamily: "monospace",
                    fontSize: "0.75rem",
                    whiteSpace: "pre-wrap",
                    overflowX: "auto",
                  }}
                >
                  <GatewayMetadataJson metadata={keyData.metadata} />
                </Box>
              </Box>
            )}

            <Divider />

            {/* Usage analytics */}
            <Box>
              <Typography variant="subtitle2" mb={1.5}>
                Usage (Last 7 days)
              </Typography>
              <Stack direction="row" spacing={2} mb={1.5}>
                <Box sx={{ flex: 1, textAlign: "center" }}>
                  <Typography variant="caption" color="text.secondary">
                    Requests
                  </Typography>
                  <Typography variant="h6" fontSize="1rem">
                    {Number(
                      val(overviewData?.total_requests) ?? 0,
                    ).toLocaleString()}
                  </Typography>
                </Box>
                <Box sx={{ flex: 1, textAlign: "center" }}>
                  <Typography variant="caption" color="text.secondary">
                    Cost
                  </Typography>
                  <Typography variant="h6" fontSize="1rem">
                    {formatCost(val(overviewData?.total_cost))}
                  </Typography>
                </Box>
                <Box sx={{ flex: 1, textAlign: "center" }}>
                  <Typography variant="caption" color="text.secondary">
                    Tokens
                  </Typography>
                  <Typography variant="h6" fontSize="1rem">
                    {Number(
                      val(overviewData?.total_tokens) ?? 0,
                    ).toLocaleString()}
                  </Typography>
                </Box>
                <Box sx={{ flex: 1, textAlign: "center" }}>
                  <Typography variant="caption" color="text.secondary">
                    Error Rate
                  </Typography>
                  <Typography variant="h6" fontSize="1rem">
                    {val(overviewData?.error_rate) != null
                      ? `${Number(val(overviewData.error_rate)).toFixed(1)}%`
                      : "0%"}
                  </Typography>
                </Box>
              </Stack>

              {sparklineSeries.length > 0 && (
                <Chart
                  type="area"
                  height={100}
                  options={sparklineOptions}
                  series={sparklineSeries}
                />
              )}

              <Button
                size="small"
                variant="outlined"
                startIcon={<Iconify icon="mdi:text-box-outline" width={16} />}
                onClick={() => {
                  onClose();
                  navigate(
                    `/dashboard/gateway/logs?api_key=${encodeURIComponent(keyData?.gatewayKeyId || keyId)}`,
                  );
                }}
                sx={{ mt: 1 }}
              >
                View Logs
              </Button>
            </Box>

            {/* Revoke */}
            {!isRevoked && !isExpired && (
              <>
                <Divider />
                <Button
                  color="error"
                  variant="outlined"
                  fullWidth
                  onClick={() => setConfirmOpen(true)}
                >
                  Revoke Key
                </Button>
              </>
            )}
          </Stack>
        ) : (
          <Typography color="text.secondary">Key not found.</Typography>
        )}
      </Drawer>

      {/* Revoke confirmation */}
      <Dialog open={confirmOpen} onClose={() => setConfirmOpen(false)}>
        <DialogTitle>Revoke API Key?</DialogTitle>
        <DialogContent>
          <DialogContentText>
            This will immediately disable the key &quot;{keyData?.name}&quot;.
            Any requests using this key will be rejected. This action cannot be
            undone.
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setConfirmOpen(false)}>Cancel</Button>
          <Button
            color="error"
            variant="contained"
            onClick={handleRevoke}
            disabled={revokeMutation.isPending}
          >
            {revokeMutation.isPending ? "Revoking..." : "Revoke"}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Edit dialog */}
      <EditKeyDialog
        open={editOpen}
        onClose={() => setEditOpen(false)}
        keyData={keyData}
      />
    </>
  );
};

KeyDetailDrawer.propTypes = {
  keyId: PropTypes.string,
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  gatewayId: PropTypes.string,
};

export default KeyDetailDrawer;
