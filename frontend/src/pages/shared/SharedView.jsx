import React, { useState, useCallback, useMemo } from "react";
import { useParams } from "react-router";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Divider,
  Stack,
  Typography,
} from "@mui/material";
import { Helmet } from "react-helmet-async";
import { useResolveSharedLink } from "src/api/shared-links";
import TraceTreeV2 from "src/components/traceDetail/TraceTreeV2";
import SpanDetailPane from "src/components/traceDetail/SpanDetailPane";
import {
  formatLatency,
  formatTokenCount,
  formatCost,
} from "src/sections/projects/LLMTracing/formatters";
import Iconify from "src/components/iconify";
import SharedVoiceView from "./SharedVoiceView";
import { isVoiceCall } from "./sharedViewHelpers";

function getSpan(entry) {
  return entry?.observation_span || entry?.observationSpan || {};
}

export default function SharedView() {
  const { token } = useParams();
  const [selectedSpanId, setSelectedSpanId] = useState(null);
  const [leftPanelWidth, setLeftPanelWidth] = useState(35);

  // Resolve token → resource metadata
  const {
    data: shared,
    isLoading: resolving,
    isError,
    error,
  } = useResolveSharedLink(token);

  const resourceType = shared?.resourceType || shared?.resource_type;
  const resourceId = shared?.resourceId || shared?.resource_id;
  const resourceData = shared?.data;

  const isTrace = resourceType === "trace";
  // Voice calls are stored server-side as traces, so the resource_type is
  // still "trace" — we dispatch on the actual payload shape (presence of a
  // conversation-type span or top-level voice fields).
  const isVoice = useMemo(
    () => isTrace && isVoiceCall(resourceData),
    [isTrace, resourceData],
  );

  // For traces, the resolve endpoint returns full span tree in data
  const spans =
    resourceData?.observationSpans || resourceData?.observation_spans;
  const summary = resourceData?.summary;

  const selectedSpanData = useMemo(() => {
    if (!selectedSpanId || !spans) return null;
    function find(entries) {
      for (const entry of entries) {
        const span = getSpan(entry);
        if (span?.id === selectedSpanId) return entry;
        if (entry.children?.length) {
          const found = find(entry.children);
          if (found) return found;
        }
      }
      return null;
    }
    return find(spans);
  }, [selectedSpanId, spans]);

  const handleSelectSpan = useCallback((spanId) => {
    setSelectedSpanId((prev) => (prev === spanId ? null : spanId));
  }, []);

  const handleDragStart = useCallback(
    (e) => {
      e.preventDefault();
      const startX = e.clientX;
      const startWidth = leftPanelWidth;
      const container = e.target.closest("[data-shared-content]");
      if (!container) return;
      const containerWidth = container.offsetWidth;
      const onMouseMove = (moveEvent) => {
        const diff = moveEvent.clientX - startX;
        setLeftPanelWidth(
          Math.min(
            70,
            Math.max(20, startWidth + (diff / containerWidth) * 100),
          ),
        );
      };
      const onMouseUp = () => {
        document.removeEventListener("mousemove", onMouseMove);
        document.removeEventListener("mouseup", onMouseUp);
      };
      document.addEventListener("mousemove", onMouseMove);
      document.addEventListener("mouseup", onMouseUp);
    },
    [leftPanelWidth],
  );

  const isLoading = resolving;

  // Error states
  if (isError) {
    const status = error?.response?.status;
    const msg = error?.response?.data?.error;
    return (
      <>
        <Helmet>
          <title>Shared Link</title>
        </Helmet>
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            height: "100vh",
            bgcolor: "background.default",
          }}
        >
          <Box sx={{ textAlign: "center", maxWidth: 420, p: 4 }}>
            <Iconify
              icon={
                status === 401
                  ? "mdi:lock-outline"
                  : status === 403
                    ? "mdi:shield-lock-outline"
                    : "mdi:link-off"
              }
              width={48}
              sx={{ color: "text.disabled", mb: 2 }}
            />
            <Typography variant="h6" sx={{ mb: 1, color: "text.primary" }}>
              {status === 401 && "Sign in required"}
              {status === 403 && "Access denied"}
              {status === 410 && "Link expired"}
              {status === 404 && "Link not found"}
              {![401, 403, 404, 410].includes(status) && "Something went wrong"}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {msg ||
                "This shared link may have been revoked or is no longer available."}
            </Typography>
            {status === 401 && (
              <Typography
                component="a"
                href={`/auth/jwt/login?returnTo=${encodeURIComponent(window.location.pathname)}`}
                sx={{
                  display: "inline-block",
                  mt: 2,
                  color: "primary.main",
                  fontSize: 14,
                  fontWeight: 500,
                }}
              >
                Sign in to continue
              </Typography>
            )}
          </Box>
        </Box>
      </>
    );
  }

  return (
    <>
      <Helmet>
        <title>
          {isVoice
            ? `Shared Voice Call — ${resourceId?.substring(0, 8) || "..."}`
            : isTrace
              ? `Shared Trace — ${resourceId?.substring(0, 8) || "..."}`
              : resourceType === "dashboard"
                ? `Shared Dashboard — ${resourceData?.name || resourceId?.substring(0, 8) || "..."}`
                : resourceType === "project"
                  ? `Shared Project — ${resourceData?.name || resourceId?.substring(0, 8) || "..."}`
                  : "Shared Resource"}
        </title>
      </Helmet>

      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          height: "100vh",
          bgcolor: "background.paper",
        }}
      >
        {/* Header bar */}
        <Box
          sx={{
            px: 2,
            py: 1.5,
            bgcolor: "background.default",
            borderBottom: "1px solid",
            borderColor: "divider",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            flexShrink: 0,
          }}
        >
          <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
            <Iconify
              icon={
                isVoice
                  ? "mdi:phone-outline"
                  : resourceType === "dashboard"
                    ? "mdi:view-dashboard-outline"
                    : resourceType === "project"
                      ? "mdi:folder-outline"
                      : "mdi:share-variant-outline"
              }
              width={20}
              sx={{ color: "primary.main" }}
            />
            <Typography
              sx={{ fontSize: 14, fontWeight: 600, color: "text.primary" }}
            >
              {isVoice
                ? "Shared voice call"
                : resourceType === "dashboard"
                  ? "Shared dashboard"
                  : resourceType === "project"
                    ? "Shared project"
                    : `Shared ${resourceType || "resource"}`}
            </Typography>
            {resourceId && (
              <Typography
                sx={{
                  fontSize: 12,
                  fontFamily: "monospace",
                  color: "text.disabled",
                }}
              >
                {resourceId.substring(0, 12)}...
              </Typography>
            )}
          </Box>
          <Typography sx={{ fontSize: 11, color: "text.disabled" }}>
            View only
          </Typography>
        </Box>

        {/* Content */}
        {isLoading ? (
          <Box
            sx={{
              flex: 1,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <CircularProgress size={32} />
          </Box>
        ) : isVoice ? (
          /* Voice call view — transcript + audio player, read-only */
          <SharedVoiceView resourceData={resourceData} />
        ) : isTrace ? (
          /* Trace view */
          <Box
            data-shared-content
            sx={{ flex: 1, display: "flex", overflow: "hidden" }}
          >
            {/* Left: Tree */}
            <Box
              sx={{
                width: `${leftPanelWidth}%`,
                display: "flex",
                flexDirection: "column",
                overflow: "hidden",
                borderRight: "1px solid",
                borderColor: "divider",
                flexShrink: 0,
              }}
            >
              <Box sx={{ flex: 1, overflow: "auto" }}>
                <TraceTreeV2
                  spans={spans}
                  selectedSpanId={selectedSpanId}
                  onSelectSpan={handleSelectSpan}
                />
              </Box>
            </Box>

            {/* Divider */}
            <Box
              onMouseDown={handleDragStart}
              sx={{
                width: 8,
                cursor: "col-resize",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                flexShrink: 0,
                "&:hover .dots": { opacity: 1 },
              }}
            >
              <Box
                className="dots"
                sx={{
                  display: "flex",
                  flexDirection: "column",
                  gap: "3px",
                  opacity: 0.4,
                  transition: "opacity 150ms",
                }}
              >
                {[0, 1, 2, 3, 4, 5].map((i) => (
                  <Box
                    key={i}
                    sx={{
                      width: 3,
                      height: 3,
                      borderRadius: "50%",
                      bgcolor: "text.disabled",
                    }}
                  />
                ))}
              </Box>
            </Box>

            {/* Right: Span detail */}
            <Box
              sx={{
                flex: 1,
                overflow: "auto",
                display: "flex",
                flexDirection: "column",
              }}
            >
              {selectedSpanData ? (
                <SpanDetailPane
                  entry={selectedSpanData}
                  onClose={() => setSelectedSpanId(null)}
                />
              ) : (
                <Box
                  sx={{
                    p: 3,
                    textAlign: "center",
                    color: "text.secondary",
                    mt: 8,
                  }}
                >
                  <Iconify
                    icon="mdi:cursor-default-click-outline"
                    width={40}
                    sx={{ mb: 1, opacity: 0.5 }}
                  />
                  <Typography variant="body2" fontSize={13}>
                    Select a span to view details
                  </Typography>
                  {summary && (
                    <Box
                      sx={{
                        mt: 2,
                        display: "flex",
                        justifyContent: "center",
                        gap: 3,
                      }}
                    >
                      <Typography variant="caption">
                        {summary.total_spans || summary.totalSpans} spans
                      </Typography>
                      <Typography variant="caption">
                        {formatLatency(
                          summary.total_duration_ms || summary.totalDurationMs,
                        )}
                      </Typography>
                      <Typography variant="caption">
                        {formatTokenCount(
                          summary.total_tokens || summary.totalTokens,
                        )}{" "}
                        tokens
                      </Typography>
                      <Typography variant="caption">
                        {formatCost(summary.total_cost || summary.totalCost)}
                      </Typography>
                    </Box>
                  )}
                </Box>
              )}
            </Box>
          </Box>
        ) : resourceType === "dashboard" ? (
          <SharedDashboardView dashboard={resourceData} />
        ) : resourceType === "project" ? (
          <SharedProjectView project={resourceData} />
        ) : (
          /* Unsupported resource type — keep a bounded debug payload visible. */
          <Box sx={{ flex: 1, p: 3, overflow: "auto" }}>
            <Alert severity="info" sx={{ mb: 2 }}>
              Viewing shared {resourceType}
            </Alert>
            <pre
              style={{
                fontSize: 12,
                fontFamily: "monospace",
                whiteSpace: "pre-wrap",
              }}
            >
              {JSON.stringify(shared?.data, null, 2)}
            </pre>
          </Box>
        )}
      </Box>
    </>
  );
}

function SharedProjectView({ project }) {
  const projectUrl = project?.url_path || null;
  const traceType = project?.trace_type || "project";
  const modelType = project?.model_type || null;

  return (
    <Box
      sx={{
        flex: 1,
        overflow: "auto",
        bgcolor: "background.default",
      }}
    >
      <Box sx={{ maxWidth: 900, mx: "auto", px: { xs: 2, md: 4 }, py: 4 }}>
        <Stack
          direction={{ xs: "column", sm: "row" }}
          spacing={2}
          alignItems={{ xs: "flex-start", sm: "center" }}
          justifyContent="space-between"
          sx={{ mb: 3 }}
        >
          <Box sx={{ minWidth: 0 }}>
            <Typography
              component="h1"
              sx={{
                fontSize: { xs: 22, md: 28 },
                fontWeight: 700,
                color: "text.primary",
                wordBreak: "break-word",
              }}
            >
              {project?.name || "Untitled project"}
            </Typography>
            <Stack
              direction="row"
              spacing={1}
              useFlexGap
              flexWrap="wrap"
              sx={{ mt: 1 }}
            >
              <Chip size="small" label={traceType} />
              {modelType && (
                <Chip size="small" variant="outlined" label={modelType} />
              )}
            </Stack>
          </Box>
          {projectUrl && (
            <Button
              href={projectUrl}
              variant="contained"
              startIcon={<Iconify icon="mdi:open-in-new" width={16} />}
              sx={{ flexShrink: 0 }}
            >
              Open project
            </Button>
          )}
        </Stack>

        <Box
          sx={{
            bgcolor: "background.paper",
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
            overflow: "hidden",
          }}
        >
          <Box sx={{ p: 2.5 }}>
            <Typography
              sx={{ fontSize: 13, fontWeight: 700, color: "text.primary" }}
            >
              Project details
            </Typography>
          </Box>
          <Divider />
          <Box
            sx={{
              p: 2.5,
              display: "grid",
              gridTemplateColumns: {
                xs: "1fr",
                sm: "repeat(2, minmax(0, 1fr))",
              },
              gap: 2,
            }}
          >
            <WidgetStat label="Project ID" value={project?.id || "—"} />
            <WidgetStat label="Workspace" value={project?.workspace || "—"} />
            <WidgetStat label="Created" value={project?.created_at || "—"} />
            <WidgetStat label="Updated" value={project?.updated_at || "—"} />
          </Box>
        </Box>
      </Box>
    </Box>
  );
}

function SharedDashboardView({ dashboard }) {
  const widgets = dashboard?.widgets || [];
  const widgetCount = dashboard?.widget_count ?? widgets.length;

  return (
    <Box
      sx={{
        flex: 1,
        overflow: "auto",
        bgcolor: "background.default",
      }}
    >
      <Box sx={{ maxWidth: 1200, mx: "auto", px: { xs: 2, md: 4 }, py: 3 }}>
        <Stack
          direction={{ xs: "column", sm: "row" }}
          spacing={2}
          alignItems={{ xs: "flex-start", sm: "center" }}
          justifyContent="space-between"
          sx={{ mb: 3 }}
        >
          <Box sx={{ minWidth: 0 }}>
            <Typography
              component="h1"
              sx={{
                fontSize: { xs: 22, md: 28 },
                fontWeight: 700,
                color: "text.primary",
                wordBreak: "break-word",
              }}
            >
              {dashboard?.name || "Untitled dashboard"}
            </Typography>
            {dashboard?.description && (
              <Typography
                sx={{
                  mt: 0.75,
                  fontSize: 14,
                  color: "text.secondary",
                  maxWidth: 760,
                }}
              >
                {dashboard.description}
              </Typography>
            )}
          </Box>
          <Chip
            size="small"
            variant="outlined"
            icon={<Iconify icon="mdi:view-grid-outline" width={16} />}
            label={`${widgetCount} ${widgetCount === 1 ? "widget" : "widgets"}`}
            sx={{ flexShrink: 0 }}
          />
        </Stack>

        {widgets.length ? (
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: {
                xs: "1fr",
                md: "repeat(2, minmax(0, 1fr))",
              },
              gap: 2,
            }}
          >
            {widgets.map((widget) => (
              <DashboardWidgetPreview key={widget.id} widget={widget} />
            ))}
          </Box>
        ) : (
          <Box
            sx={{
              py: 8,
              px: 2,
              textAlign: "center",
              bgcolor: "background.paper",
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 1,
            }}
          >
            <Iconify
              icon="mdi:view-dashboard-outline"
              width={40}
              sx={{ color: "text.disabled", mb: 1 }}
            />
            <Typography sx={{ fontSize: 14, color: "text.secondary" }}>
              No widgets in this dashboard
            </Typography>
          </Box>
        )}
      </Box>
    </Box>
  );
}

function DashboardWidgetPreview({ widget }) {
  const chartType = widget?.chart_config?.chart_type || "chart";
  const metrics = Array.isArray(widget?.query_config?.metrics)
    ? widget.query_config.metrics
    : [];
  const timeRange =
    widget?.query_config?.time_range?.preset ||
    widget?.query_config?.timeRange?.preset ||
    null;

  return (
    <Box
      sx={{
        bgcolor: "background.paper",
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        minHeight: 220,
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
      }}
    >
      <Box sx={{ p: 2 }}>
        <Stack
          direction="row"
          spacing={1.5}
          alignItems="flex-start"
          justifyContent="space-between"
        >
          <Box sx={{ minWidth: 0 }}>
            <Typography
              sx={{
                fontSize: 15,
                fontWeight: 700,
                color: "text.primary",
                wordBreak: "break-word",
              }}
            >
              {widget?.name || "Untitled widget"}
            </Typography>
            {widget?.description && (
              <Typography
                sx={{
                  mt: 0.5,
                  fontSize: 12,
                  color: "text.secondary",
                  wordBreak: "break-word",
                }}
              >
                {widget.description}
              </Typography>
            )}
          </Box>
          <Chip size="small" label={chartTypeLabel(chartType)} />
        </Stack>
      </Box>

      <Divider />

      <Box
        sx={{
          p: 2,
          display: "grid",
          gridTemplateColumns: "repeat(3, minmax(0, 1fr))",
          gap: 1,
        }}
      >
        <WidgetStat label="Width" value={widget?.width ?? "—"} />
        <WidgetStat label="Height" value={widget?.height ?? "—"} />
        <WidgetStat label="Range" value={timeRange || "—"} />
      </Box>

      <Box sx={{ px: 2, pb: 2, flex: 1 }}>
        <Typography
          sx={{
            mb: 1,
            fontSize: 11,
            fontWeight: 700,
            color: "text.secondary",
            textTransform: "uppercase",
          }}
        >
          Metrics
        </Typography>
        {metrics.length ? (
          <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap">
            {metrics.map((metric, index) => (
              <Chip
                key={`${metric.name || metric.id || "metric"}-${index}`}
                size="small"
                variant="outlined"
                label={metricLabel(metric)}
              />
            ))}
          </Stack>
        ) : (
          <Typography sx={{ fontSize: 13, color: "text.disabled" }}>
            No metrics configured
          </Typography>
        )}
      </Box>
    </Box>
  );
}

function WidgetStat({ label, value }) {
  return (
    <Box>
      <Typography
        sx={{
          fontSize: 11,
          fontWeight: 700,
          color: "text.disabled",
          textTransform: "uppercase",
        }}
      >
        {label}
      </Typography>
      <Typography
        sx={{
          fontSize: 13,
          fontWeight: 600,
          color: "text.primary",
          wordBreak: "break-word",
        }}
      >
        {value}
      </Typography>
    </Box>
  );
}

function chartTypeLabel(value) {
  return String(value || "chart").replaceAll("_", " ");
}

function metricLabel(metric) {
  const name =
    metric.display_name || metric.displayName || metric.name || metric.id;
  const aggregation = metric.aggregation;
  return aggregation ? `${aggregation} ${name}` : name;
}

const dashboardWidgetShape = PropTypes.shape({
  id: PropTypes.string,
  name: PropTypes.string,
  description: PropTypes.string,
  width: PropTypes.number,
  height: PropTypes.number,
  query_config: PropTypes.shape({
    metrics: PropTypes.arrayOf(
      PropTypes.shape({
        id: PropTypes.string,
        name: PropTypes.string,
        display_name: PropTypes.string,
        displayName: PropTypes.string,
        aggregation: PropTypes.string,
      }),
    ),
    time_range: PropTypes.shape({
      preset: PropTypes.string,
    }),
    timeRange: PropTypes.shape({
      preset: PropTypes.string,
    }),
  }),
  chart_config: PropTypes.shape({
    chart_type: PropTypes.string,
  }),
});

SharedProjectView.propTypes = {
  project: PropTypes.shape({
    id: PropTypes.string,
    name: PropTypes.string,
    trace_type: PropTypes.string,
    model_type: PropTypes.string,
    workspace: PropTypes.string,
    created_at: PropTypes.string,
    updated_at: PropTypes.string,
    url_path: PropTypes.string,
  }),
};

SharedDashboardView.propTypes = {
  dashboard: PropTypes.shape({
    name: PropTypes.string,
    description: PropTypes.string,
    widget_count: PropTypes.number,
    widgets: PropTypes.arrayOf(dashboardWidgetShape),
  }),
};

DashboardWidgetPreview.propTypes = {
  widget: dashboardWidgetShape,
};

WidgetStat.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
};
