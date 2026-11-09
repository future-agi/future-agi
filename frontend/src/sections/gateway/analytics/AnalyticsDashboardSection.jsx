import React, { useCallback, useMemo, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Tab from "@mui/material/Tab";
import { useNavigate, useParams } from "react-router-dom";

import { paths } from "src/routes/paths";
import { CustomTabs } from "src/components/tabs/tabs";

import { useGatewayContext } from "../context/useGatewayContext";
import { GatewayEmptyState, GatewayPageFrame } from "../components/frame";
import AnalyticsKpis from "./AnalyticsKpis";
import UsageCharts from "./UsageCharts";
import RangeToggle from "./RangeToggle";
import CostAnalytics from "./CostAnalytics";
import LatencyAnalytics from "./LatencyAnalytics";
import ErrorAnalytics from "./ErrorAnalytics";
import ModelComparison from "./ModelComparison";
import { useAnalyticsOverview } from "./hooks/useAnalyticsOverview";
import { ANALYTICS_RANGES, DEFAULT_RANGE, rangeWindow } from "./analyticsRange";
import { zoneNote } from "../utils/timeZone";
import { useGatewayConfig } from "../providers/hooks/useGatewayConfig";
import { sectionEnforcement } from "../settings/sectionEnforcement";

// Gateway analytics: one page. The overview strip, then usage, cost,
// latency, errors and models over the chosen range, each tab at its own URL
// (/analytics/<tab>, which Monitoring links to). Every chart asks for the
// range's bucket size (analyticsRange.js).

const TABS = [
  { slug: "usage", label: "Usage" },
  { slug: "cost", label: "Cost" },
  { slug: "latency", label: "Latency" },
  { slug: "errors", label: "Errors" },
  { slug: "models", label: "Models" },
];

const PANELS = {
  usage: UsageCharts,
  cost: CostAnalytics,
  latency: LatencyAnalytics,
  errors: ErrorAnalytics,
  models: ModelComparison,
};

export default function AnalyticsDashboardSection({
  gatewayId: gatewayIdProp,
} = {}) {
  const { gatewayId: ctxGatewayId } = useGatewayContext();
  const gatewayId = gatewayIdProp || ctxGatewayId;
  const { tab } = useParams();
  const navigate = useNavigate();
  const active = TABS.some((t) => t.slug === tab) ? tab : "usage";

  const [range, setRange] = useState(DEFAULT_RANGE);
  const { start, end, granularity } = useMemo(
    () => rangeWindow(range),
    [range],
  );

  const overview = useAnalyticsOverview({ start, end, gatewayId });
  // Keep historical cache figures visible; state current enforcement separately.
  const cacheOff =
    sectionEnforcement(
      useGatewayConfig(gatewayId, { meta: { errorHandled: true } }),
      "cache",
    ) === "not_enforced";

  const onTab = useCallback(
    (_event, slug) => {
      const root = paths.dashboard.gateway.analytics;
      navigate(slug === "usage" ? root : `${root}/${slug}`, { replace: true });
    },
    [navigate],
  );

  const Panel = PANELS[active];

  return (
    <GatewayPageFrame
      title="Analytics"
      description={`Requests, cost, latency and errors through the gateway over time, by model and provider. ${zoneNote()}`}
      actions={
        <RangeToggle
          value={range}
          options={ANALYTICS_RANGES}
          onChange={setRange}
        />
      }
    >
      {overview.error ? (
        <GatewayEmptyState
          icon="alertCircle"
          title="Analytics could not be loaded"
          description={
            overview.error.message || "The gateway API did not answer."
          }
          actionLabel="Retry"
          onAction={() => overview.refetch()}
        />
      ) : (
        <>
          <AnalyticsKpis
            overview={overview.data}
            cacheOff={cacheOff}
            loading={overview.isLoading}
            period={
              ANALYTICS_RANGES.find((r) => r.value === range)?.period ||
              "period"
            }
          />
          <Box sx={{ borderBottom: 1, borderColor: "divider" }}>
            <CustomTabs
              value={active}
              onChange={onTab}
              variant="scrollable"
              allowScrollButtonsMobile
              aria-label="Analytics views"
            >
              {TABS.map((t) => (
                <Tab key={t.slug} value={t.slug} label={t.label} />
              ))}
            </CustomTabs>
          </Box>
          <Box sx={{ minWidth: 0 }}>
            <Panel
              start={start}
              end={end}
              granularity={granularity}
              gatewayId={gatewayId}
              cacheOff={cacheOff}
            />
          </Box>
        </>
      )}
    </GatewayPageFrame>
  );
}

AnalyticsDashboardSection.propTypes = { gatewayId: PropTypes.string };
