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
  Tooltip,
  useTheme,
  alpha,
  ToggleButtonGroup,
  ToggleButton,
  IconButton,
  ButtonBase,
  Button,
} from "@mui/material";
import Section from "src/components/section";
import MetricStrip from "src/components/metric-strip";
import Iconify from "src/components/iconify";
import axios, { endpoints } from "src/utils/axios";
import { fCurrency, fUsage } from "src/utils/format-number";
import UsageChart from "./UsageChart";
import WorkspaceBreakdown from "./WorkspaceBreakdown";
import SimulationUsage from "src/sections/simulate-v2/usage/SimulationUsage";
import { useOptionalSimStore } from "src/sections/simulate-v2/store";
import { simMeters } from "src/sections/simulate-v2/_mock/usageLedger";
import {
  SIM_METERS,
  withSimulationUsage,
} from "src/sections/simulate-v2/usage/productUsage";

// ── Config ────────────────────────────────────────────────────────────────

const DIMENSION_CONFIG = {
  storage: {
    icon: "solar:database-linear",
    // eslint-disable-next-line no-restricted-syntax -- ds: data colour
    color: "#3b82f6",
    label: "Storage",
  },
  ai_credits: {
    icon: "solar:cpu-linear",
    // eslint-disable-next-line no-restricted-syntax -- ds: data colour
    color: "#7857FC",
    label: "AI Credits",
  },
  gateway_requests: {
    icon: "solar:transfer-horizontal-linear",
    // eslint-disable-next-line no-restricted-syntax -- ds: data colour
    color: "#06b6d4",
    label: "Gateway Requests",
  },
  gateway_cache_hits: {
    icon: "solar:bolt-linear",
    // eslint-disable-next-line no-restricted-syntax -- ds: data colour
    color: "#14b8a6",
    label: "Gateway Cache Hits",
  },
  text_sim_tokens: {
    icon: "solar:file-search-linear",
    // eslint-disable-next-line no-restricted-syntax -- ds: data colour
    color: "#f59e0b",
    label: "Text Simulation",
  },
  voice_sim_minutes: {
    icon: "solar:microphone-3-linear",
    // eslint-disable-next-line no-restricted-syntax -- ds: data colour
    color: "#ef4444",
    label: "Voice Simulation",
  },
  tracing_events: {
    icon: "solar:chart-2-linear",
    // eslint-disable-next-line no-restricted-syntax -- ds: data colour
    color: "#ec4899",
    label: "Tracing Events",
  },
};

function getSingularUnit(unit) {
  if (!unit) return "unit";
  if (/^[A-Z]+$/.test(unit)) return unit;
  return unit.endsWith("s") ? unit.slice(0, -1) : unit;
}

function formatTierRate(rateStr) {
  if (rateStr == null) return "$0";
  const num = parseFloat(String(rateStr).replace(/[^0-9.-]/g, ""));
  if (!Number.isFinite(num) || num === 0) return "$0";
  return fCurrency(num, true);
}

const DISCRETE_UNITS = new Set(["events", "requests", "hits", "tokens"]);

function isDiscreteUnit(unit) {
  return DISCRETE_UNITS.has(unit);
}

function normalizeUsageValue(value, unit) {
  const numericValue = Number(value || 0);
  if (!Number.isFinite(numericValue)) return 0;
  return isDiscreteUnit(unit) ? Math.round(numericValue) : numericValue;
}

function formatUsageNumber(value, unit, { exact = false } = {}) {
  const normalizedValue = normalizeUsageValue(value, unit);
  if (normalizedValue === 0) return "0";

  if (exact) {
    return normalizedValue.toLocaleString(undefined, {
      maximumFractionDigits: isDiscreteUnit(unit) ? 0 : 4,
    });
  }

  if (normalizedValue >= 1e9) return `${(normalizedValue / 1e9).toFixed(1)}B`;
  if (normalizedValue >= 1e6) return `${(normalizedValue / 1e6).toFixed(1)}M`;
  if (normalizedValue >= 1e3) return `${(normalizedValue / 1e3).toFixed(1)}K`;
  if (Number.isInteger(normalizedValue))
    return normalizedValue.toLocaleString();
  return normalizedValue.toFixed(1);
}

function formatUsage(value, unit, options) {
  return `${formatUsageNumber(value, unit, options)} ${unit}`;
}

function parseTierRate(rate) {
  const rateText = String(rate || "")
    .split("/")[0]
    .replace(/[$,\s]/g, "");
  const parsedRate = Number(rateText);
  return Number.isFinite(parsedRate) ? parsedRate : 0;
}

function getFirstPaidTierRate(tierBreakdown = []) {
  return (
    tierBreakdown
      .map((tier) => parseTierRate(tier.rate))
      .find((rate) => rate > 0) || 0
  );
}

function getPeriodAllowance(freeAllowance, periodMonthCount) {
  return (freeAllowance || 0) * periodMonthCount;
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
          bgcolor: "action.selected",
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

// ── Dimension Product Card ────────────────────────────────────────────────

function DimensionCard({ dim, periodCaption, periodMonthCount }) {
  const theme = useTheme();
  const [expanded, setExpanded] = useState(false);
  const config = DIMENSION_CONFIG[dim.key] || {};
  const color = config.color || theme.palette.primary.main;
  const periodFreeAllowance = getPeriodAllowance(
    dim.free_allowance,
    periodMonthCount,
  );
  const currentUsage = normalizeUsageValue(dim.current_usage, dim.display_unit);
  const projectedUsage = normalizeUsageValue(
    dim.projected_usage,
    dim.display_unit,
  );
  const freeAllowance = normalizeUsageValue(
    periodFreeAllowance,
    dim.display_unit,
  );
  const showExactCurrentUsage = isDiscreteUnit(dim.display_unit);
  const displayedCurrentUsage = showExactCurrentUsage
    ? dim.current_usage_raw ?? dim.current_usage
    : dim.current_usage;
  const billableUsage = Math.max(
    0,
    normalizeUsageValue(displayedCurrentUsage, dim.display_unit) -
      freeAllowance,
  );

  const usagePct = freeAllowance > 0 ? (currentUsage / freeAllowance) * 100 : 0;
  const isOverFree = usagePct > 100;

  return (
    <Section variant="panel" sx={{ p: 0, overflow: "hidden" }}>
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
                borderRadius: "8px",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                bgcolor: "background.neutral",
                flexShrink: 0,
              }}
            >
              <Iconify
                icon={config.icon || "solar:pie-chart-2-linear"}
                width={20}
                sx={{ color }}
              />
            </Box>
            <Box>
              <Typography variant="subtitle1" fontWeight={600}>
                {dim.display_name}
              </Typography>
              <Stack direction="row" spacing={0.5} alignItems="center">
                {isOverFree && (
                  <Chip
                    label="Over free tier"
                    size="small"
                    color="warning"
                    variant="filled"
                  />
                )}
                {dim.free_allowance > 0 && !isOverFree && (
                  <Typography variant="caption" color="text.secondary">
                    {usagePct > 0 && usagePct < 1
                      ? `${usagePct.toFixed(2)}`
                      : usagePct.toFixed(0)}
                    % of free tier used
                  </Typography>
                )}
              </Stack>
            </Box>
          </Stack>

          <Stack alignItems="flex-end" sx={{ textAlign: "right" }}>
            <Typography variant="h6" fontWeight={600}>
              {fCurrency(dim.estimated_cost)}
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {periodCaption}
            </Typography>
          </Stack>
        </Stack>

        {/* Usage gauge */}
        <UsageGauge
          current={currentUsage}
          projected={projectedUsage}
          freeAllowance={freeAllowance}
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
              {formatUsage(displayedCurrentUsage, dim.display_unit, {
                exact: showExactCurrentUsage,
              })}
            </Box>
            {freeAllowance > 0 && billableUsage <= 0 && (
              <>
                {" "}
                of{" "}
                {formatUsage(freeAllowance, dim.display_unit, {
                  exact: showExactCurrentUsage,
                })}{" "}
                free
              </>
            )}
            {freeAllowance > 0 && billableUsage > 0 && (
              <>
                {" "}
                (
                {formatUsageNumber(freeAllowance, dim.display_unit, {
                  exact: true,
                })}{" "}
                free +{" "}
                {formatUsageNumber(billableUsage, dim.display_unit, {
                  exact: true,
                })}{" "}
                billed)
              </>
            )}
          </Typography>
          {projectedUsage > currentUsage && (
            <Stack direction="row" spacing={0.5} alignItems="center">
              <Iconify
                icon="solar:graph-up-linear"
                width={14}
                sx={{ color: "text.disabled" }}
              />
              <Typography variant="caption" color="text.disabled">
                ~{formatUsage(projectedUsage, dim.display_unit)} projected
              </Typography>
            </Stack>
          )}
        </Stack>

        {/* Expandable tier breakdown */}
        {dim.tier_breakdown && dim.tier_breakdown.length > 0 && (
          <Box mt={1.5}>
            <ButtonBase
              component="div"
              onClick={() => setExpanded(!expanded)}
              sx={{
                justifyContent: "flex-start",
                textAlign: "left",
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
                icon="solar:alt-arrow-down-linear"
                sx={{
                  fontSize: 16,
                  color: "primary.main",
                  transform: expanded ? "rotate(180deg)" : "none",
                  transition: "transform 0.2s",
                }}
              />
            </ButtonBase>
            <Collapse in={expanded}>
              <Box
                sx={{
                  mt: 1,
                  p: 1.5,
                  borderRadius: "8px",
                  bgcolor: "background.neutral",
                  border: "1px solid",
                  borderColor: "divider",
                }}
              >
                <Box
                  sx={{
                    display: "grid",
                    gridTemplateColumns: "1fr 120px 140px 100px",
                    columnGap: 2,
                    pb: 0.75,
                    mb: 0.25,
                    borderBottom: "1px solid",
                    borderColor: "divider",
                  }}
                >
                  <Typography variant="overline" color="text.secondary">
                    {`Range${dim.display_unit ? ` (${dim.display_unit})` : ""}`}
                  </Typography>
                  <Typography
                    variant="overline"
                    color="text.secondary"
                    textAlign="right"
                  >
                    Used
                  </Typography>
                  <Typography
                    variant="overline"
                    color="text.secondary"
                    textAlign="right"
                  >
                    Rate
                  </Typography>
                  <Typography
                    variant="overline"
                    color="text.secondary"
                    textAlign="right"
                  >
                    Cost
                  </Typography>
                </Box>
                {dim.tier_breakdown.map((tier, i) => (
                  <Box
                    key={i}
                    sx={{
                      display: "grid",
                      gridTemplateColumns: "1fr 120px 140px 100px",
                      columnGap: 2,
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
                    <Typography
                      variant="caption"
                      color="text.secondary"
                      sx={{ textAlign: "right" }}
                    >
                      {fUsage(tier.usage, dim.display_unit)}
                    </Typography>
                    <Typography
                      variant="caption"
                      color="text.secondary"
                      sx={{ textAlign: "right" }}
                    >
                      {formatTierRate(tier.rate)}/
                      {getSingularUnit(dim.display_unit)}
                    </Typography>
                    <Typography
                      variant="caption"
                      fontWeight={600}
                      sx={{ textAlign: "right" }}
                    >
                      {fCurrency(tier.cost, true)}
                    </Typography>
                  </Box>
                ))}
              </Box>
            </Collapse>
          </Box>
        )}
      </Box>
    </Section>
  );
}

const dimensionPropType = PropTypes.shape({
  key: PropTypes.string,
  display_name: PropTypes.string,
  current_usage: PropTypes.number,
  current_usage_raw: PropTypes.number,
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
  periodCaption: PropTypes.string.isRequired,
  periodMonthCount: PropTypes.number.isRequired,
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

function getPeriodCaption(rangeValue) {
  if (rangeValue === "current") return "month to date";
  return `last ${rangeValue} months`;
}

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

function getPeriodMonthCount(rangeValue) {
  if (rangeValue === "current") return 1;
  const months = parseInt(rangeValue, 10);
  return Number.isFinite(months) ? months : 1;
}

// ── Main Component ────────────────────────────────────────────────────────

export default function UsageSummaryV2() {
  const theme = useTheme();
  const queryClient = useQueryClient();
  const [timeRange, setTimeRange] = useState("current");
  const [isRefreshing, setIsRefreshing] = useState(false);

  const periodParams = useMemo(() => getPeriodRange(timeRange), [timeRange]);
  const periodMonthCount = useMemo(
    () => getPeriodMonthCount(timeRange),
    [timeRange],
  );

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

  const { data: apiOverview, isLoading } = useQuery({
    queryKey: ["v2-usage-overview", periodParams],
    queryFn: () =>
      axios.get(endpoints.settings.v2.usageOverview, {
        params: periodParams,
      }),
    select: (res) => res.data?.result,
    refetchInterval: 60000,
  });

  /* The simulation meters (Text Simulation, Voice Simulation, AI Credits)
     also count the prototype's runs, from the same ledger as the Simulation
     section and each run's Cost tab. Every other card is the backend's. */
  const simStore = useOptionalSimStore();
  const meters = useMemo(() => {
    const now = new Date();
    const months = periodMonthCount;
    return simMeters(simStore?.state, {
      since: new Date(
        now.getFullYear(),
        now.getMonth() - months + 1,
        1,
      ).getTime(),
    });
  }, [simStore?.state, periodMonthCount]);
  const overview = useMemo(() => {
    const now = new Date();
    const daysInMonth = new Date(
      now.getFullYear(),
      now.getMonth() + 1,
      0,
    ).getDate();
    return withSimulationUsage(apiOverview, meters, {
      months: periodMonthCount,
      projectBy: timeRange === "current" ? daysInMonth / now.getDate() : 1,
    });
  }, [apiOverview, meters, periodMonthCount, timeRange]);

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
    return overview.dimensions.reduce((sum, dim) => {
      const paidTierRate = getFirstPaidTierRate(dim.tier_breakdown);
      if (!paidTierRate) return sum;

      const freeUsed = Math.min(
        dim.current_usage || 0,
        getPeriodAllowance(dim.free_allowance, periodMonthCount),
      );
      return freeUsed > 0 ? sum + freeUsed * paidTierRate : sum;
    }, 0);
  }, [overview, periodMonthCount]);

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
        <Alert severity="warning" sx={{ mb: 2 }}>
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
          sx={{ mb: 2 }}
          action={
            banner.action && (
              <Button size="small" color="inherit" href={banner.action.url}>
                {banner.action.label}
              </Button>
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
          <Typography variant="h4" fontWeight={600}>
            Usage & Billing
          </Typography>
          <Stack direction="row" spacing={1.5} alignItems="center" mt={1}>
            <Chip
              label={planLabel}
              size="small"
              sx={{
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
            fontWeight={600}
            sx={{
              lineHeight: 1,
              mt: 0.5,
              color: "text.primary",
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
            <ToggleButton key={tr.value} value={tr.value}>
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
                icon="solar:refresh-linear"
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
              <Iconify icon="solar:download-minimalistic-linear" width={20} />
            </IconButton>
          </Tooltip>
        </Stack>
      </Stack>

      {/* ── Score Cards Row ── */}
      <MetricStrip
        sx={{ mb: 4 }}
        items={[
          {
            label: "Usage charges",
            value: fCurrency(overview?.total_estimated_cost || 0),
            hint: "this billing period",
            tooltip: "Total charges from metered usage across all dimensions",
          },
          {
            label: "Free tier savings",
            value: freeTierSavings > 0 ? fCurrency(freeTierSavings) : "$0",
            hint: "included with your plan",
            tooltip: "How much you're saving from free tier allowances",
          },
          {
            label: "Active products",
            value: `${activeDimensions}`,
            hint: `of ${overview?.dimensions?.length || 0} available`,
            tooltip: "Products with usage this billing period",
          },
          {
            label: "Days remaining",
            value: daysRemaining !== null ? `${daysRemaining}` : "—",
            hint: "in billing period",
            tooltip: "Days left in the current billing cycle",
          },
        ]}
      />

      {/* ── Products Section ── */}
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        mb={1.5}
      >
        <Typography variant="h6" fontWeight={600}>
          Products
        </Typography>
        <GaugeLegend />
      </Stack>

      <Stack spacing={2}>
        {overview?.dimensions?.map((dim) => (
          <DimensionCard
            key={dim.key}
            dim={dim}
            periodCaption={getPeriodCaption(timeRange)}
            periodMonthCount={periodMonthCount}
          />
        ))}
      </Stack>

      {/* Empty state */}
      {(!overview?.dimensions || overview.dimensions.length === 0) && (
        <Section variant="panel" sx={{ p: 5, textAlign: "center" }}>
          <Iconify
            icon="solar:pie-chart-2-linear"
            width={56}
            sx={{ color: "text.disabled", mb: 1.5 }}
          />
          <Typography variant="h6" color="text.secondary" gutterBottom>
            No usage data yet
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Start using FutureAGI features and your usage will appear here.
          </Typography>
        </Section>
      )}

      {/* ── Simulation: runs priced as their Cost tabs, and limits ── */}
      <SimulationUsage />

      {/* ── Dimension Detail (chart + workspace breakdown) ── */}
      {overview?.dimensions?.length > 0 && (
        <DimensionDetail
          dimensions={overview.dimensions}
          period={periodParams.period}
          periodEnd={periodParams.period_end}
          simDaily={meters.daily}
        />
      )}
    </Box>
  );
}

// ── Dimension Detail (chart + workspace table) ────────────────────────────

DimensionDetail.propTypes = {
  dimensions: PropTypes.arrayOf(dimensionPropType).isRequired,
  period: PropTypes.string,
  periodEnd: PropTypes.string,
  /* Per-day usage of the simulation meters from the ledger, by key. */
  simDaily: PropTypes.object,
};

function DimensionDetail({ dimensions, period, periodEnd, simDaily = {} }) {
  const theme = useTheme();
  const [selectedDim, setSelectedDim] = useState(dimensions[0]?.key || "");

  const selected = dimensions.find((d) => d.key === selectedDim);
  if (!selected) return null;

  const config = DIMENSION_CONFIG[selected.key] || {};

  return (
    <Box mt={5}>
      <Typography variant="h6" fontWeight={600} mb={2}>
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
                  icon={dc.icon || "solar:pie-chart-2-linear"}
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
      <Section variant="panel" sx={{ p: 2.5, mb: 3 }}>
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
              {fUsage(selected.current_usage, selected.display_unit)} total
            </Typography>
          </Stack>
        </Stack>
        <UsageChart
          dimension={selectedDim}
          period={period}
          periodEnd={periodEnd}
          freeAllowance={selected.free_allowance}
          displayUnit={selected.display_unit}
          addSeries={
            SIM_METERS.includes(selectedDim) ? simDaily[selectedDim] : null
          }
        />
      </Section>

      {/* Workspace breakdown */}
      <Section variant="panel" sx={{ p: 2.5 }}>
        <Typography variant="subtitle2" fontWeight={600} mb={2}>
          Usage by workspace — {selected.display_name}
        </Typography>
        <WorkspaceBreakdown
          dimension={selectedDim}
          period={period}
          periodEnd={periodEnd}
          displayUnit={selected.display_unit}
          addUsage={
            SIM_METERS.includes(selectedDim)
              ? (simDaily[selectedDim] || []).reduce((a, d) => a + d.usage, 0)
              : 0
          }
        />
      </Section>
    </Box>
  );
}
