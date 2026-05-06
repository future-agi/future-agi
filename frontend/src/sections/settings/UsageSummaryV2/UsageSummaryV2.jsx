/**
 * Usage Summary V2 — World-class usage dashboard
 *
 * - Hero: large current bill, projected cost, days remaining
 * - Score cards: key metrics at a glance
 * - Per-product usage gauge (multi-segment bar)
 * - Daily time-series charts + workspace drill-down
 */

import { useState, useMemo, useCallback } from "react";
import PropTypes from "prop-types";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Box,
  Typography,
  Stack,
  Skeleton,
  Chip,
  Alert,
  Collapse,
  Paper,
  Tooltip,
  useTheme,
  alpha,
  ToggleButtonGroup,
  ToggleButton,
  IconButton,
} from "@mui/material";
import Iconify from "src/components/iconify";
import axios, { endpoints } from "src/utils/axios";
import { fCurrency } from "src/utils/format-number";
import UsageChart from "./UsageChart";
import WorkspaceBreakdown from "./WorkspaceBreakdown";

// ── Config ────────────────────────────────────────────────────────────────

const DIMENSION_CONFIG = {
  storage: { icon: "mdi:database", color: "#3b82f6", label: "Storage" },
  ai_credits: { icon: "mdi:chip", color: "#8b5cf6", label: "AI Credits" },
  gateway_requests: {
    icon: "mdi:swap-horizontal",
    color: "#06b6d4",
    label: "Gateway Requests",
  },
  gateway_cache_hits: {
    icon: "mdi:lightning-bolt",
    color: "#14b8a6",
    label: "Gateway Cache Hits",
  },
  text_sim_tokens: {
    icon: "mdi:text-box-search-outline",
    color: "#f59e0b",
    label: "Text Simulation",
  },
  voice_sim_minutes: {
    icon: "mdi:microphone-outline",
    color: "#ef4444",
    label: "Voice Simulation",
  },
  tracing_events: {
    icon: "mdi:chart-timeline-variant",
    color: "#ec4899",
    label: "Tracing Events",
  },
};

function formatUsage(value, unit) {
  if (value == null) return `0 ${unit}`;
  if (value === 0) return `0 ${unit}`;
  if (value >= 1e9) return `${(value / 1e9).toFixed(1)}B ${unit}`;
  if (value >= 1e6) return `${(value / 1e6).toFixed(1)}M ${unit}`;
  if (value >= 1e3) return `${(value / 1e3).toFixed(1)}K ${unit}`;
  if (Number.isInteger(value)) return `${value.toLocaleString()} ${unit}`;
  return `${value.toFixed(1)} ${unit}`;
}

// ── Usage Gauge (multi-segment bar) ───────────────────────────────────────

function UsageGauge({ current, projected, freeAllowance, color }) {
  const theme = useTheme();

  // Calculate segments as percentages of total capacity
  const maxVal = Math.max(current, projected, freeAllowance) * 1.3 || 1;
  const currentPct = (current / maxVal) * 100;
  const projectedPct = Math.max(0, ((projected - current) / maxVal) * 100);
  const freePct = freeAllowance > 0 ? (freeAllowance / maxVal) * 100 : 0;
  const isOverFree = freeAllowance > 0 && current > freeAllowance;
  const barColor = isOverFree ? theme.palette.error.main : color;

  return (
    <Box sx={{ position: "relative", width: "100%", mt: 1.5, mb: 0.5 }}>
      {/* Background track */}
      <Box
        sx={{
          height: 8,
          borderRadius: 4,
          bgcolor: alpha(theme.palette.text.primary, 0.06),
          position: "relative",
          overflow: "hidden",
        }}
      >
        {/* Current usage segment */}
        <Box
          sx={{
            position: "absolute",
            left: 0,
            top: 0,
            height: "100%",
            width: `${Math.min(currentPct, 100)}%`,
            bgcolor: barColor,
            borderRadius: 4,
            transition: "width 0.8s cubic-bezier(0.4, 0, 0.2, 1)",
          }}
        />
        {/* Projected usage segment (striped) */}
        {projected > current && (
          <Box
            sx={{
              position: "absolute",
              left: `${Math.min(currentPct, 100)}%`,
              top: 0,
              height: "100%",
              width: `${Math.min(projectedPct, 100 - currentPct)}%`,
              opacity: 0.5,
              borderRadius: "0 4px 4px 0",
              transition: "width 0.8s cubic-bezier(0.4, 0, 0.2, 1)",
              background: `repeating-linear-gradient(
                -45deg,
                ${barColor},
                ${barColor} 2px,
                transparent 2px,
                transparent 5px
              )`,
            }}
          />
        )}
      </Box>
      {/* Free tier marker */}
      {freeAllowance > 0 && freePct < 100 && (
        <Tooltip title={`Free tier limit`} arrow placement="top">
          <Box
            sx={{
              position: "absolute",
              left: `${Math.min(freePct, 99)}%`,
              top: -2,
              width: 2,
              height: 12,
              bgcolor: alpha(theme.palette.text.secondary, 0.4),
              borderRadius: 1,
            }}
          />
        </Tooltip>
      )}
    </Box>
  );
}

UsageGauge.propTypes = {
  current: PropTypes.number,
  projected: PropTypes.number,
  freeAllowance: PropTypes.number,
  color: PropTypes.string,
};

// ── Stat Card ─────────────────────────────────────────────────────────────

function StatCard({ icon, label, value, subtitle, color, tooltip }) {
  const theme = useTheme();
  const content = (
    <Paper
      variant="outlined"
      sx={{
        p: 2.5,
        borderRadius: 2,
        flex: 1,
        minWidth: 160,
        position: "relative",
        overflow: "hidden",
        transition: "border-color 0.2s, box-shadow 0.2s",
        "&:hover": {
          borderColor: color || theme.palette.primary.main,
          boxShadow: `0 0 0 1px ${alpha(color || theme.palette.primary.main, 0.2)}`,
        },
      }}
    >
      <Stack direction="row" spacing={1.5} alignItems="flex-start">
        <Box
          sx={{
            width: 40,
            height: 40,
            borderRadius: 1.5,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            bgcolor: alpha(color || theme.palette.primary.main, 0.1),
            flexShrink: 0,
          }}
        >
          <Iconify
            icon={icon}
            width={22}
            sx={{ color: color || theme.palette.primary.main }}
          />
        </Box>
        <Box sx={{ minWidth: 0 }}>
          <Typography variant="h5" fontWeight={700} noWrap>
            {value}
          </Typography>
          <Typography variant="caption" color="text.secondary" noWrap>
            {label}
          </Typography>
          {subtitle && (
            <Typography
              variant="caption"
              display="block"
              color="text.disabled"
              noWrap
            >
              {subtitle}
            </Typography>
          )}
        </Box>
      </Stack>
    </Paper>
  );

  if (tooltip) {
    return (
      <Tooltip title={tooltip} arrow placement="top">
        {content}
      </Tooltip>
    );
  }
  return content;
}

StatCard.propTypes = {
  icon: PropTypes.string.isRequired,
  label: PropTypes.string.isRequired,
  value: PropTypes.oneOfType([PropTypes.string, PropTypes.number]).isRequired,
  subtitle: PropTypes.string,
  color: PropTypes.string,
  tooltip: PropTypes.string,
};

// ── Dimension Product Card ────────────────────────────────────────────────

function DimensionCard({ dim }) {
  const theme = useTheme();
  const [expanded, setExpanded] = useState(false);
  const config = DIMENSION_CONFIG[dim.key] || {};
  const color = config.color || theme.palette.primary.main;

  const usagePct =
    dim.free_allowance > 0 ? (dim.current_usage / dim.free_allowance) * 100 : 0;
  const isOverFree = usagePct > 100;

  return (
    <Paper
      variant="outlined"
      sx={{
        p: 0,
        borderRadius: 2,
        overflow: "hidden",
        transition: "border-color 0.2s, box-shadow 0.2s",
        "&:hover": {
          borderColor: alpha(color, 0.4),
          boxShadow: `0 0 0 1px ${alpha(color, 0.1)}`,
        },
      }}
    >
      <Box sx={{ p: 2.5 }}>
        {/* Row 1: Product name + spend */}
        <Stack
          direction="row"
          alignItems="flex-start"
          justifyContent="space-between"
        >
          <Stack direction="row" alignItems="center" spacing={1.5}>
            <Box
              sx={{
                width: 40,
                height: 40,
                borderRadius: 1.5,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                bgcolor: alpha(color, 0.1),
                flexShrink: 0,
              }}
            >
              <Iconify
                icon={config.icon || "mdi:chart-donut"}
                width={22}
                sx={{ color }}
              />
            </Box>
            <Box>
              <Typography variant="subtitle1" fontWeight={700}>
                {dim.display_name}
              </Typography>
              <Stack direction="row" spacing={0.5} alignItems="center">
                {isOverFree && (
                  <Chip
                    label="Over free tier"
                    size="small"
                    color="warning"
                    variant="filled"
                    sx={{ height: 20, fontSize: "0.675rem" }}
                  />
                )}
                {dim.free_allowance > 0 && !isOverFree && (
                  <Typography variant="caption" color="text.secondary">
                    {usagePct.toFixed(0)}% of free tier used
                  </Typography>
                )}
              </Stack>
            </Box>
          </Stack>

          <Stack alignItems="flex-end" sx={{ textAlign: "right" }}>
            <Typography variant="h6" fontWeight={700}>
              {fCurrency(dim.estimated_cost)}
            </Typography>
            <Typography variant="caption" color="text.secondary">
              month to date
            </Typography>
          </Stack>
        </Stack>

        {/* Usage gauge */}
        <UsageGauge
          current={dim.current_usage || 0}
          projected={dim.projected_usage || 0}
          freeAllowance={dim.free_allowance || 0}
          color={color}
        />

        {/* Row 2: Usage numbers */}
        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="center"
          mt={0.5}
        >
          <Typography variant="caption" color="text.secondary">
            <Box
              component="span"
              sx={{ fontWeight: 600, color: "text.primary" }}
            >
              {formatUsage(dim.current_usage, dim.display_unit)}
            </Box>
            {dim.free_allowance > 0 && (
              <> of {formatUsage(dim.free_allowance, dim.display_unit)} free</>
            )}
          </Typography>
          {dim.projected_usage > dim.current_usage && (
            <Stack direction="row" spacing={0.5} alignItems="center">
              <Iconify
                icon="mdi:trending-up"
                width={14}
                sx={{ color: "text.disabled" }}
              />
              <Typography variant="caption" color="text.disabled">
                ~{formatUsage(dim.projected_usage, dim.display_unit)} projected
              </Typography>
            </Stack>
          )}
        </Stack>

        {/* Expandable tier breakdown */}
        {dim.tier_breakdown && dim.tier_breakdown.length > 0 && (
          <Box mt={1.5}>
            <Box
              onClick={() => setExpanded(!expanded)}
              sx={{
                cursor: "pointer",
                display: "inline-flex",
                alignItems: "center",
                gap: 0.5,
                py: 0.5,
                "&:hover": { opacity: 0.8 },
              }}
            >
              <Typography variant="caption" color="primary" fontWeight={500}>
                {expanded ? "Hide" : "View"} pricing tiers
              </Typography>
              <Iconify
                icon="mdi:chevron-down"
                sx={{
                  fontSize: 16,
                  color: "primary.main",
                  transform: expanded ? "rotate(180deg)" : "none",
                  transition: "transform 0.2s",
                }}
              />
            </Box>
            <Collapse in={expanded}>
              <Box
                sx={{
                  mt: 1,
                  p: 1.5,
                  borderRadius: 1.5,
                  bgcolor: alpha(theme.palette.text.primary, 0.02),
                  border: "1px solid",
                  borderColor: "divider",
                }}
              >
                {dim.tier_breakdown.map((tier, i) => (
                  <Stack
                    key={i}
                    direction="row"
                    justifyContent="space-between"
                    sx={{
                      py: 0.75,
                      borderBottom:
                        i < dim.tier_breakdown.length - 1
                          ? "1px solid"
                          : "none",
                      borderColor: "divider",
                    }}
                  >
                    <Typography variant="caption" color="text.secondary">
                      {tier.range}
                    </Typography>
                    <Stack direction="row" spacing={2}>
                      <Typography variant="caption" color="text.secondary">
                        {tier.rate}/unit
                      </Typography>
                      <Typography variant="caption" fontWeight={600}>
                        {fCurrency(tier.cost)}
                      </Typography>
                    </Stack>
                  </Stack>
                ))}
              </Box>
            </Collapse>
          </Box>
        )}
      </Box>
    </Paper>
  );
}

const dimensionPropType = PropTypes.shape({
  key: PropTypes.string,
  display_name: PropTypes.string,
  current_usage: PropTypes.number,
  projected_usage: PropTypes.number,
  free_allowance: PropTypes.number,
  estimated_cost: PropTypes.number,
  display_unit: PropTypes.string,
  tier_breakdown: PropTypes.arrayOf(
    PropTypes.shape({
      range: PropTypes.string,
      rate: PropTypes.string,
      cost: PropTypes.number,
    }),
  ),
});

DimensionCard.propTypes = {
  dim: dimensionPropType.isRequired,
};

// ── Legend ─────────────────────────────────────────────────────────────────

function GaugeLegend() {
  const theme = useTheme();
  const items = [
    { label: "Current usage", color: theme.palette.primary.main },
    { label: "Projected", striped: true, color: theme.palette.primary.main },
    { label: "Free tier limit", marker: true },
  ];

  return (
    <Stack direction="row" spacing={2.5} alignItems="center" sx={{ mb: 2 }}>
      {items.map((item) => (
        <Stack
          key={item.label}
          direction="row"
          spacing={0.75}
          alignItems="center"
        >
          {item.marker ? (
            <Box
              sx={{
                width: 2,
                height: 12,
                bgcolor: alpha(theme.palette.text.secondary, 0.4),
                borderRadius: 1,
              }}
            />
          ) : (
            <Box
              sx={{
                width: 16,
                height: 8,
                borderRadius: 4,
                ...(item.striped
                  ? {
                      opacity: 0.5,
                      background: `repeating-linear-gradient(
                        -45deg,
                        ${item.color},
                        ${item.color} 2px,
                        transparent 2px,
                        transparent 5px
                      )`,
                    }
                  : { bgcolor: item.color }),
              }}
            />
          )}
          <Typography variant="caption" color="text.disabled">
            {item.label}
          </Typography>
        </Stack>
      ))}
    </Stack>
  );
}

// ── CSV Export helper ─────────────────────────────────────────────────────

function exportUsageCSV(dimensions, period) {
  if (!dimensions?.length) return;
  const headers = [
    "Dimension",
    "Current Usage",
    "Unit",
    "Free Allowance",
    "Projected",
    "Estimated Cost",
  ];
  const rows = dimensions.map((d) => [
    d.display_name,
    d.current_usage,
    d.display_unit,
    d.free_allowance,
    d.projected_usage,
    d.estimated_cost,
  ]);
  const csv = [headers, ...rows]
    .map((r) =>
      r.map((v) => `"${String(v ?? "").replace(/"/g, '""')}"`).join(","),
    )
    .join("\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `usage-${period || "current"}.csv`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// ── Time Range Options ───────────────────────────────────────────────────

const TIME_RANGES = [
  { value: "current", label: "MTD" },
  { value: "3", label: "3M" },
  { value: "6", label: "6M" },
  { value: "12", label: "12M" },
];

function getPeriodRange(rangeValue) {
  const now = new Date();
  const end = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
  if (rangeValue === "current") return { period: end };
  const months = parseInt(rangeValue, 10);
  const start = new Date(now.getFullYear(), now.getMonth() - months + 1, 1);
  return {
    period: `${start.getFullYear()}-${String(start.getMonth() + 1).padStart(2, "0")}`,
    period_end: end,
  };
}

// ── Main Component ────────────────────────────────────────────────────────

export default function UsageSummaryV2() {
  const theme = useTheme();
  const queryClient = useQueryClient();
  const [timeRange, setTimeRange] = useState("current");
  const [isRefreshing, setIsRefreshing] = useState(false);

  const periodParams = useMemo(() => getPeriodRange(timeRange), [timeRange]);

  const handleRefresh = useCallback(async () => {
    setIsRefreshing(true);
    await queryClient.invalidateQueries({ queryKey: ["v2-usage-overview"] });
    await queryClient.invalidateQueries({ queryKey: ["v2-notifications"] });
    await queryClient.invalidateQueries({ queryKey: ["v2-usage-time-series"] });
    await queryClient.invalidateQueries({
      queryKey: ["v2-workspace-breakdown"],
    });
    setIsRefreshing(false);
  }, [queryClient]);

  const { data: overview, isLoading } = useQuery({
    queryKey: ["v2-usage-overview", periodParams],
    queryFn: () =>
      axios.get(endpoints.settings.v2.usageOverview, {
        params: periodParams,
      }),
    select: (res) => res.data?.result,
    refetchInterval: 60000,
  });

  const { data: banners } = useQuery({
    queryKey: ["v2-notifications"],
    queryFn: () => axios.get(endpoints.settings.v2.notifications),
    select: (res) => res.data?.result?.banners || [],
  });

  const handleExportCSV = useCallback(() => {
    exportUsageCSV(overview?.dimensions, overview?.period);
  }, [overview]);

  // Derived values
  const planLabel = useMemo(() => {
    if (!overview) return "";
    if (overview.plan === "free") return "Free";
    if (overview.plan === "payg") return "Pay-as-you-go";
    if (overview.plan_display_name)
      return `Pay-as-you-go + ${overview.plan_display_name}`;
    return overview.plan;
  }, [overview]);

  const daysRemaining = useMemo(() => {
    if (overview?.plan === "free") return null;
    if (!overview?.billing_period_end) return null;
    const end = new Date(overview.billing_period_end);
    const now = new Date();
    return Math.max(0, Math.ceil((end - now) / (1000 * 60 * 60 * 24)));
  }, [overview]);

  const freeTierSavings = useMemo(() => {
    if (!overview?.dimensions) return 0;
    // Sum what free usage would have cost at tier-1 rates
    return overview.dimensions.reduce((sum, dim) => {
      const freeUsed = Math.min(
        dim.current_usage || 0,
        dim.free_allowance || 0,
      );
      if (freeUsed > 0 && dim.tier_breakdown?.[0]?.rate) {
        const rateStr = dim.tier_breakdown[0].rate.replace(/[^0-9.]/g, "");
        return sum + freeUsed * parseFloat(rateStr || "0");
      }
      return sum;
    }, 0);
  }, [overview]);

  const activeDimensions = useMemo(() => {
    if (!overview?.dimensions) return 0;
    return overview.dimensions.filter((d) => (d.current_usage || 0) > 0).length;
  }, [overview]);

  if (isLoading) {
    return (
      <Box>
        <Stack direction="row" justifyContent="space-between" mb={4}>
          <Box>
            <Skeleton variant="text" width={200} height={32} />
            <Skeleton variant="text" width={280} height={20} />
          </Box>
          <Box sx={{ textAlign: "right" }}>
            <Skeleton variant="text" width={80} height={56} />
            <Skeleton variant="text" width={120} height={20} />
          </Box>
        </Stack>
        <Stack direction="row" spacing={2} mb={4}>
          {[1, 2, 3, 4].map((i) => (
            <Skeleton key={i} variant="rounded" height={90} sx={{ flex: 1 }} />
          ))}
        </Stack>
        {[1, 2, 3].map((i) => (
          <Skeleton key={i} variant="rounded" height={110} sx={{ mb: 2 }} />
        ))}
      </Box>
    );
  }

  return (
    <Box>
      {/* Pending add-on cancellation notice */}
      {overview?.pending_cancel && (
        <Alert severity="warning" sx={{ mb: 2, borderRadius: 2 }}>
          You&apos;ve requested to cancel your add-on. Your plan will become
          inactive
          {overview.cancel_at
            ? ` on ${new Date(overview.cancel_at).toLocaleDateString()}`
            : " at the end of the current billing cycle"}
          .
        </Alert>
      )}

      {/* ── Banners ── */}
      {banners?.map((banner) => (
        <Alert
          key={banner.id}
          severity={banner.type === "error" ? "error" : "warning"}
          sx={{ mb: 2, borderRadius: 2 }}
          action={
            banner.action && (
              <Typography
                variant="caption"
                component="a"
                href={banner.action.url}
                sx={{
                  color: "inherit",
                  textDecoration: "underline",
                  cursor: "pointer",
                }}
              >
                {banner.action.label}
              </Typography>
            )
          }
        >
          {banner.message}
        </Alert>
      ))}

      {/* ── Hero Section ── */}
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="flex-end"
        sx={{ mb: 4 }}
      >
        <Box>
          <Typography variant="h4" fontWeight={800}>
            Usage & Billing
          </Typography>
          <Stack direction="row" spacing={1.5} alignItems="center" mt={1}>
            <Chip
              label={planLabel}
              size="small"
              sx={{
                fontWeight: 600,
                bgcolor: alpha(theme.palette.primary.main, 0.1),
                color: theme.palette.primary.main,
                border: "none",
              }}
            />
            {overview?.billing_period_start && (
              <Typography variant="body2" color="text.secondary">
                {overview.billing_period_start} — {overview.billing_period_end}
              </Typography>
            )}
            {daysRemaining !== null && (
              <Chip
                label={`${daysRemaining} days left`}
                size="small"
                variant="outlined"
                sx={{ fontWeight: 500, fontSize: "0.7rem" }}
              />
            )}
          </Stack>
        </Box>

        <Box sx={{ textAlign: "right" }}>
          <Typography variant="caption" color="text.secondary" fontWeight={500}>
            Current period total
          </Typography>
          <Typography
            variant="h3"
            fontWeight={800}
            sx={{
              lineHeight: 1,
              mt: 0.5,
              background:
                theme.palette.mode === "dark"
                  ? "linear-gradient(135deg, #fff 0%, #94a3b8 100%)"
                  : "linear-gradient(135deg, #1e293b 0%, #475569 100%)",
              WebkitBackgroundClip: "text",
              WebkitTextFillColor: "transparent",
            }}
          >
            {fCurrency(overview?.total_with_platform || 0)}
          </Typography>
          {overview?.platform_fee > 0 && (
            <Typography
              variant="caption"
              color="text.disabled"
              display="block"
              mt={0.5}
            >
              Includes {fCurrency(overview.platform_fee)} platform fee
            </Typography>
          )}
        </Box>
      </Stack>

      {/* ── Time Range + Export ── */}
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        sx={{ mb: 3 }}
      >
        <ToggleButtonGroup
          value={timeRange}
          exclusive
          onChange={(_, val) => val && setTimeRange(val)}
          size="small"
        >
          {TIME_RANGES.map((tr) => (
            <ToggleButton
              key={tr.value}
              value={tr.value}
              sx={{ px: 2, py: 0.5, fontSize: "0.75rem" }}
            >
              {tr.label}
            </ToggleButton>
          ))}
        </ToggleButtonGroup>
        <Stack direction="row" spacing={0.5}>
          <Tooltip title="Refresh usage data" arrow>
            <IconButton
              size="small"
              onClick={handleRefresh}
              disabled={isRefreshing}
            >
              <Iconify
                icon="mdi:refresh"
                width={20}
                sx={
                  isRefreshing
                    ? {
                        animation: "spin 1s linear infinite",
                        "@keyframes spin": {
                          "0%": { transform: "rotate(0deg)" },
                          "100%": { transform: "rotate(360deg)" },
                        },
                      }
                    : undefined
                }
              />
            </IconButton>
          </Tooltip>
          <Tooltip title="Export usage data as CSV" arrow>
            <IconButton size="small" onClick={handleExportCSV}>
              <Iconify icon="mdi:download" width={20} />
            </IconButton>
          </Tooltip>
        </Stack>
      </Stack>

      {/* ── Score Cards Row ── */}
      <Stack
        direction="row"
        spacing={2}
        sx={{ mb: 4, flexWrap: "wrap" }}
        useFlexGap
      >
        <StatCard
          icon="mdi:receipt-text-outline"
          label="Usage charges"
          value={fCurrency(overview?.total_estimated_cost || 0)}
          subtitle="this billing period"
          color="#3b82f6"
          tooltip="Total charges from metered usage across all dimensions"
        />
        <StatCard
          icon="mdi:piggy-bank-outline"
          label="Free tier savings"
          value={freeTierSavings > 0 ? fCurrency(freeTierSavings) : "$0"}
          subtitle="included with your plan"
          color="#22c55e"
          tooltip="How much you're saving from free tier allowances"
        />
        <StatCard
          icon="mdi:pulse"
          label="Active products"
          value={`${activeDimensions}`}
          subtitle={`of ${overview?.dimensions?.length || 0} available`}
          color="#8b5cf6"
          tooltip="Products with usage this billing period"
        />
        <StatCard
          icon="mdi:calendar-clock-outline"
          label="Days remaining"
          value={daysRemaining !== null ? `${daysRemaining}` : "—"}
          subtitle="in billing period"
          color="#f59e0b"
          tooltip="Days left in the current billing cycle"
        />
      </Stack>

      {/* ── Products Section ── */}
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        mb={1.5}
      >
        <Typography variant="h6" fontWeight={700}>
          Products
        </Typography>
        <GaugeLegend />
      </Stack>

      <Stack spacing={2}>
        {overview?.dimensions?.map((dim) => (
          <DimensionCard key={dim.key} dim={dim} />
        ))}
      </Stack>

      {/* Empty state */}
      {(!overview?.dimensions || overview.dimensions.length === 0) && (
        <Paper
          variant="outlined"
          sx={{
            p: 5,
            textAlign: "center",
            borderStyle: "dashed",
            borderRadius: 2,
          }}
        >
          <Iconify
            icon="mdi:chart-donut"
            width={56}
            sx={{ color: "text.disabled", mb: 1.5 }}
          />
          <Typography variant="h6" color="text.secondary" gutterBottom>
            No usage data yet
          </Typography>
          <Typography variant="body2" color="text.disabled">
            Start using FutureAGI features and your usage will appear here.
          </Typography>
        </Paper>
      )}

      {/* ── Dimension Detail (chart + workspace breakdown) ── */}
      {overview?.dimensions?.length > 0 && (
        <DimensionDetail
          dimensions={overview.dimensions}
          period={overview.period}
        />
      )}
    </Box>
  );
}

// ── Dimension Detail (chart + workspace table) ────────────────────────────

DimensionDetail.propTypes = {
  dimensions: PropTypes.arrayOf(dimensionPropType).isRequired,
  period: PropTypes.string,
};

function DimensionDetail({ dimensions, period }) {
  const theme = useTheme();
  const [selectedDim, setSelectedDim] = useState(dimensions[0]?.key || "");

  const selected = dimensions.find((d) => d.key === selectedDim);
  if (!selected) return null;

  const config = DIMENSION_CONFIG[selected.key] || {};

  return (
    <Box mt={5}>
      <Typography variant="h6" fontWeight={700} mb={2}>
        Usage details
      </Typography>

      {/* Dimension selector tabs */}
      <Stack direction="row" spacing={1} mb={3} flexWrap="wrap" useFlexGap>
        {dimensions.map((dim) => {
          const dc = DIMENSION_CONFIG[dim.key] || {};
          const isActive = dim.key === selectedDim;
          return (
            <Chip
              key={dim.key}
              icon={
                <Iconify
                  icon={dc.icon || "mdi:chart-donut"}
                  width={16}
                  sx={{ color: isActive ? "common.white" : dc.color }}
                />
              }
              label={dim.display_name}
              variant={isActive ? "filled" : "outlined"}
              onClick={() => setSelectedDim(dim.key)}
              size="small"
              sx={{
                cursor: "pointer",
                fontWeight: isActive ? 600 : 400,
                ...(isActive && {
                  bgcolor: config.color || theme.palette.primary.main,
                  color: "common.white",
                  "& .MuiChip-icon": { color: "common.white" },
                }),
              }}
            />
          );
        })}
      </Stack>

      {/* Chart */}
      <Paper variant="outlined" sx={{ p: 2.5, borderRadius: 2, mb: 3 }}>
        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="center"
          mb={2}
        >
          <Typography variant="subtitle2" fontWeight={600}>
            Daily usage — {selected.display_name}
          </Typography>
          <Stack direction="row" spacing={1} alignItems="center">
            <Typography variant="caption" color="text.disabled">
              {formatUsage(selected.current_usage, selected.display_unit)} total
            </Typography>
          </Stack>
        </Stack>
        <UsageChart
          dimension={selectedDim}
          period={period}
          freeAllowance={selected.free_allowance}
          displayUnit={selected.display_unit}
        />
      </Paper>

      {/* Workspace breakdown */}
      <Paper variant="outlined" sx={{ p: 2.5, borderRadius: 2 }}>
        <Typography variant="subtitle2" fontWeight={600} mb={2}>
          Usage by workspace — {selected.display_name}
        </Typography>
        <WorkspaceBreakdown
          dimension={selectedDim}
          period={period}
          displayUnit={selected.display_unit}
        />
      </Paper>
    </Box>
  );
}
