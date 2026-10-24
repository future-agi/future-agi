import React, { useState, useMemo, useCallback } from "react";
import PropTypes from "prop-types";
import { Box, Tabs, Tab } from "@mui/material";
import { useNavigate, useParams } from "react-router-dom";
import { useGatewayContext } from "../context/useGatewayContext";
import TimeRangeSelector from "./TimeRangeSelector";
import OverviewKPIs from "./OverviewKPIs";
import UsageCharts from "./UsageCharts";
import CostAnalytics from "./CostAnalytics";
import LatencyAnalytics from "./LatencyAnalytics";
import ErrorAnalytics from "./ErrorAnalytics";
import ModelComparison from "./ModelComparison";
import SectionHeader from "../components/SectionHeader";
import { GATEWAY_ICONS } from "../constants/gatewayIcons";

// ---------------------------------------------------------------------------
// Time range preset → { start, end } converter
// ---------------------------------------------------------------------------

const TIME_RANGE_OFFSETS = {
  "1h": 1 * 60 * 60 * 1000,
  "6h": 6 * 60 * 60 * 1000,
  "24h": 24 * 60 * 60 * 1000,
  "7d": 7 * 24 * 60 * 60 * 1000,
  "30d": 30 * 24 * 60 * 60 * 1000,
  "90d": 90 * 24 * 60 * 60 * 1000,
};

function computeDateRange(preset) {
  const now = new Date();
  const offsetMs = TIME_RANGE_OFFSETS[preset] || TIME_RANGE_OFFSETS["24h"];
  const start = new Date(now.getTime() - offsetMs);
  return {
    start: start.toISOString(),
    end: now.toISOString(),
  };
}

// ---------------------------------------------------------------------------
// Tab definitions
// ---------------------------------------------------------------------------

const TAB_LABELS = ["Usage", "Cost", "Latency", "Errors", "Models"];
const TAB_SLUGS = ["usage", "cost", "latency", "errors", "models"];

function tabSlugToIndex(slug) {
  const idx = TAB_SLUGS.indexOf(slug);
  return idx >= 0 ? idx : 0;
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

const AnalyticsDashboardSection = ({ gatewayId: gatewayIdProp } = {}) => {
  const { gatewayId: ctxGatewayId } = useGatewayContext();
  const gatewayId = gatewayIdProp || ctxGatewayId;
  const { tab: tabSlug } = useParams();
  const navigate = useNavigate();
  const activeTab = tabSlugToIndex(tabSlug);

  const handleTabChange = useCallback(
    (_event, newIndex) => {
      if (newIndex === 0) {
        navigate("/dashboard/gateway/analytics", { replace: true });
      } else {
        navigate(`/dashboard/gateway/analytics/${TAB_SLUGS[newIndex]}`, {
          replace: true,
        });
      }
    },
    [navigate],
  );

  const [timeRange, setTimeRange] = useState("24h");

  const { start, end } = useMemo(
    () => computeDateRange(timeRange),
    [timeRange],
  );

  // Common props passed to all analytics sub-components
  const analyticsProps = { start, end, gatewayId };

  return (
    <Box p={3}>
      {/* ---- Header: Title + Time Range Selector ---- */}
      <SectionHeader
        sx={{ mb: 1.5 }}
        icon={GATEWAY_ICONS.analytics}
        title="Analytics"
        subtitle="Explore usage, cost, latency, and error trends"
      >
        <TimeRangeSelector value={timeRange} onChange={setTimeRange} />
      </SectionHeader>

      {/* ---- Overview KPIs (always visible) ---- */}
      <Box mb={3}>
        <OverviewKPIs {...analyticsProps} />
      </Box>

      {/* ---- Tab navigation ---- */}
      <Tabs
        value={activeTab}
        onChange={handleTabChange}
        variant="standard"
        sx={{ mb: 1.5, borderBottom: 1, borderColor: "divider" }}
      >
        {TAB_LABELS.map((label, index) => (
          <Tab key={label} label={label} value={index} />
        ))}
      </Tabs>

      {/* ---- Active tab content ---- */}
      <Box>
        {activeTab === 0 && <UsageCharts {...analyticsProps} />}
        {activeTab === 1 && <CostAnalytics {...analyticsProps} />}
        {activeTab === 2 && <LatencyAnalytics {...analyticsProps} />}
        {activeTab === 3 && <ErrorAnalytics {...analyticsProps} />}
        {activeTab === 4 && <ModelComparison {...analyticsProps} />}
      </Box>
    </Box>
  );
};

AnalyticsDashboardSection.propTypes = {
  gatewayId: PropTypes.string,
};

export default AnalyticsDashboardSection;
