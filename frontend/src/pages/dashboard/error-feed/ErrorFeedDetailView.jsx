import React, { useMemo } from "react";
import {
  Box,
  Button,
  Chip,
  IconButton,
  Stack,
  Tab,
  Tabs,
  Tooltip,
  Typography,
  useTheme,
  alpha,
} from "@mui/material";
import PropTypes from "prop-types";
import { useNavigate, useParams } from "react-router-dom";
import Iconify from "src/components/iconify";
import {
  useErrorFeedDetail,
  useUpdateErrorFeedIssue,
} from "src/api/errorFeed/error-feed";
import ErrorStatusChip from "./components/ErrorStatusChip";
import ErrorSeverityBadge from "./components/ErrorSeverityBadge";
import ErrorMetadataPanel from "./components/ErrorMetadataPanel";
import OverviewTab from "./components/OverviewTab";
import TracesTab from "./components/TracesTab";
import StateGraphTab from "./components/StateGraphTab";
import TrendsTab from "./components/TrendsTab";
import { useErrorFeedStore } from "./store";

// ── Detail page skeleton ─────────────────────────────────────────────────────
function DetailSkeleton() {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const pulse = {
    bgcolor: isDark ? alpha("#fff", 0.07) : alpha("#000", 0.07),
    borderRadius: "4px",
    animation: "pulse 1.4s ease-in-out infinite",
    "@keyframes pulse": { "0%,100%": { opacity: 1 }, "50%": { opacity: 0.4 } },
  };
  return (
    <Stack gap={2.5} p={2} flex={1}>
      <Stack gap={0.75}>
        <Box sx={{ ...pulse, height: 22, width: "55%" }} />
        <Box sx={{ ...pulse, height: 14, width: "30%" }} />
      </Stack>
      <Box sx={{ ...pulse, height: 100, width: "100%", borderRadius: 1 }} />
      <Box sx={{ ...pulse, height: 300, width: "100%", borderRadius: 1 }} />
    </Stack>
  );
}

// ── Tab label with optional badge ────────────────────────────────────────────
function TabLabel({ label, count, icon }) {
  return (
    <Stack direction="row" alignItems="center" gap={0.6}>
      {icon && <Iconify icon={icon} width={14} sx={{ opacity: 0.65 }} />}
      {label}
      {count != null && (
        <Chip
          label={count}
          size="small"
          sx={{
            height: 16,
            fontSize: "10px",
            fontWeight: 600,
            borderRadius: "3px",
            bgcolor: "action.hover",
            color: "text.disabled",
            "& .MuiChip-label": { px: "5px" },
          }}
        />
      )}
    </Stack>
  );
}

TabLabel.propTypes = {
  label: PropTypes.string,
  count: PropTypes.number,
  icon: PropTypes.string,
};

// ── Tab definitions ──────────────────────────────────────────────────────────
const TABS = [
  { key: "overview", label: "Overview", icon: "mdi:view-dashboard-outline" },
  { key: "traces", label: "Traces", icon: "mdi:timeline-text-outline" },
  { key: "stategraph", label: "State Graph", icon: "mdi:graph-outline" },
  { key: "trends", label: "Trends", icon: "mdi:chart-line" },
];

// ── Main view ─────────────────────────────────────────────────────────────────
export default function ErrorFeedDetailView() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { activeTab, setActiveTab } = useErrorFeedStore();
  const { data: detail, isLoading } = useErrorFeedDetail(id);
  const updateIssue = useUpdateErrorFeedIssue();

  const currentError = useMemo(() => {
    if (!detail?.row) return null;
    return {
      ...detail.row,
      description: detail.description,
      successTrace: detail.successTrace,
      representativeTrace: detail.representativeTrace,
    };
  }, [detail]);

  if (isLoading || !currentError) {
    return <DetailSkeleton />;
  }

  const tabIndex = TABS.findIndex((t) => t.key === activeTab);
  const safeTabIndex = tabIndex === -1 ? 0 : tabIndex;

  return (
    <Box
      sx={{
        display: "flex",
        flex: 1,
        height: "100%",
        overflow: "hidden",
        bgcolor: "background.paper",
      }}
    >
      {/* ── Main column ── */}
      <Box
        sx={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
          minWidth: 0,
        }}
      >
        {/* ── Header ── */}
        <Box
          sx={{
            px: 2,
            pt: 1.5,
            pb: 1.25,
            borderBottom: "1px solid",
            borderColor: "divider",
            flexShrink: 0,
            bgcolor: "background.paper",
          }}
        >
          {/* Breadcrumb row */}
          <Stack direction="row" alignItems="center" gap={0.5} mb={1}>
            <Button
              size="small"
              variant="text"
              startIcon={<Iconify icon="mdi:arrow-left" width={14} />}
              onClick={() => navigate("/dashboard/error-feed")}
              sx={{
                height: 26,
                fontSize: "12px",
                color: "text.secondary",
                px: 0.75,
                minWidth: 0,
                "&:hover": { color: "text.primary", bgcolor: "action.hover" },
              }}
            >
              Error Feed
            </Button>
            <Iconify
              icon="mdi:chevron-right"
              width={13}
              sx={{ color: "text.secondary" }}
            />
            <Chip
              label={currentError.error.type
                .replace(/Error$/, "")
                .replace(/([A-Z])/g, " $1")
                .trim()}
              size="small"
              sx={{
                height: 20,
                fontSize: "11px",
                borderRadius: "4px",
                bgcolor: "action.hover",
                color: "text.secondary",
                "& .MuiChip-label": { px: "7px" },
              }}
            />
          </Stack>

          {/* Error title */}
          <Stack
            direction="row"
            alignItems="flex-start"
            justifyContent="space-between"
            gap={2}
          >
            <Stack gap={0.5} flex={1} minWidth={0}>
              <Typography
                typography="m3"
                fontWeight="fontWeightSemiBold"
                color="text.primary"
                sx={{ lineHeight: 1.35 }}
              >
                {currentError.error.name}
              </Typography>
              <Stack
                direction="row"
                alignItems="center"
                gap={1.5}
                flexWrap="wrap"
              >
                <Stack direction="row" alignItems="center" gap={0.75}>
                  <Box
                    sx={{
                      width: 6,
                      height: 6,
                      borderRadius: "50%",
                      bgcolor: "orange.500",
                      flexShrink: 0,
                    }}
                  />
                  <Typography typography="s2" color="text.disabled">
                    {currentError.error.type}
                  </Typography>
                </Stack>
                <ErrorStatusChip status={currentError.status} />
                <ErrorSeverityBadge severity={currentError.severity} />
                {/* Cluster badge */}
                <Chip
                  icon={<Iconify icon="mdi:layers-outline" width={12} />}
                  label={`${currentError.traceCount?.toLocaleString()} traces`}
                  size="small"
                  sx={{
                    height: 20,
                    fontSize: "11px",
                    borderRadius: "4px",
                    bgcolor: "action.hover",
                    color: "text.secondary",
                    "& .MuiChip-icon": { ml: "6px", color: "text.disabled" },
                    "& .MuiChip-label": { px: "7px" },
                    "&:hover": { bgcolor: "action.hover" },
                    "&:focus": { bgcolor: "action.hover" },
                  }}
                />
              </Stack>
            </Stack>

            {/* Action buttons */}
            <Stack
              direction="row"
              alignItems="center"
              gap={0.75}
              flexShrink={0}
            >
              <Tooltip title="Copy cluster ID" arrow>
                <IconButton
                  size="small"
                  sx={{
                    borderRadius: 1,
                    border: "1px solid",
                    borderColor: "divider",
                  }}
                >
                  <Iconify icon="mdi:content-copy" width={14} />
                </IconButton>
              </Tooltip>
              <Tooltip title="Share" arrow>
                <IconButton
                  size="small"
                  sx={{
                    borderRadius: 1,
                    border: "1px solid",
                    borderColor: "divider",
                  }}
                >
                  <Iconify icon="mdi:share-variant-outline" width={14} />
                </IconButton>
              </Tooltip>
              <Button
                size="small"
                variant="contained"
                color="primary"
                startIcon={<Iconify icon="mdi:check" width={13} />}
                disabled={updateIssue.isPending}
                onClick={() =>
                  updateIssue.mutate({
                    clusterId: currentError.clusterId,
                    status: "resolved",
                  })
                }
                sx={{
                  height: 30,
                  fontSize: "12px",
                  borderRadius: "6px",
                  textTransform: "none",
                }}
              >
                Resolve
              </Button>
              <Button
                size="small"
                variant="outlined"
                startIcon={
                  <Iconify icon="mdi:check-circle-outline" width={13} />
                }
                disabled={updateIssue.isPending}
                onClick={() =>
                  updateIssue.mutate({
                    clusterId: currentError.clusterId,
                    status: "acknowledged",
                  })
                }
                sx={{
                  height: 30,
                  fontSize: "12px",
                  borderRadius: "6px",
                  textTransform: "none",
                  borderColor: "divider",
                  color: "text.secondary",
                }}
              >
                Acknowledge
              </Button>
              <Button
                size="small"
                variant="outlined"
                startIcon={<Iconify icon="mdi:eye-off-outline" width={13} />}
                disabled={updateIssue.isPending}
                onClick={() =>
                  updateIssue.mutate({
                    clusterId: currentError.clusterId,
                    status: "escalating",
                  })
                }
                sx={{
                  height: 30,
                  fontSize: "12px",
                  borderRadius: "6px",
                  textTransform: "none",
                  borderColor: "divider",
                  color: "text.secondary",
                }}
              >
                Ignore issue
              </Button>
            </Stack>
          </Stack>
        </Box>

        {/* ── Tab bar ── */}
        <Box
          sx={{
            borderBottom: "1px solid",
            borderColor: "divider",
            px: 1.5,
            flexShrink: 0,
            bgcolor: "background.paper",
          }}
        >
          <Tabs
            value={safeTabIndex}
            onChange={(_, v) => setActiveTab(TABS[v].key)}
            sx={{
              minHeight: 34,
              "& .MuiTab-root": {
                fontSize: "12px",
                minHeight: 34,
                py: 0,
                px: 0,
                mr: "20px",
                textTransform: "none",
                fontWeight: 500,
                color: "text.secondary",
                "&.Mui-selected": { color: "text.primary", fontWeight: 600 },
              },
              "& .MuiTabs-indicator": {
                height: 2,
                borderRadius: "2px 2px 0 0",
              },
            }}
          >
            {TABS.map((tab) => (
              <Tab
                key={tab.key}
                label={
                  <TabLabel
                    label={tab.label}
                    icon={tab.icon}
                    count={
                      tab.key === "traces"
                        ? currentError?.traceCount
                        : undefined
                    }
                  />
                }
              />
            ))}
          </Tabs>
        </Box>

        {/* ── Scrollable tab content ── */}
        <Box sx={{ flex: 1, overflowY: "auto" }}>
          <Box sx={{ p: 2 }}>
            {safeTabIndex === 0 && <OverviewTab _error={currentError} />}
            {safeTabIndex === 1 && <TracesTab error={currentError} />}
            {safeTabIndex === 2 && <StateGraphTab error={currentError} />}
            {safeTabIndex === 3 && <TrendsTab error={currentError} />}
          </Box>
        </Box>
      </Box>

      {/* ── Right sidebar ── */}
      <ErrorMetadataPanel error={currentError} />
    </Box>
  );
}
