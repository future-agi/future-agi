import React, {
  useMemo,
  useState,
  useCallback,
  useEffect,
  useRef,
} from "react";
import PropTypes from "prop-types";
import { Box, Collapse, IconButton, Stack, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import {
  formatLatency,
  formatTokenCount,
  formatCost,
} from "src/sections/projects/LLMTracing/formatters";
import { getTypeConfig } from "./spanTypeConfig";
import { spanOwnEvalRows, collectSubtreeEvals } from "./evalScores";

// ---------------------------------------------------------------------------
// Helpers (same logic as TraceTreeV2)
// ---------------------------------------------------------------------------
function getSpan(entry) {
  return entry?.observation_span || {};
}

function collectAllSpans(nodes) {
  const all = [];
  function walk(list) {
    for (const entry of list) {
      all.push(getSpan(entry));
      if (entry.children?.length) walk(entry.children);
    }
  }
  walk(nodes);
  return all;
}

function collectAllIds(entries) {
  const ids = new Set();
  const walk = (list) => {
    for (const entry of list) {
      const span = getSpan(entry);
      if (span.id) ids.add(span.id);
      if (entry.children?.length) walk(entry.children);
    }
  };
  walk(entries);
  return ids;
}

function countErrors(entry) {
  const span = getSpan(entry);
  let count = span.status === "ERROR" ? 1 : 0;
  if (entry.children?.length) {
    for (const child of entry.children) count += countErrors(child);
  }
  return count;
}

function getRootLatency(spans) {
  if (!spans?.length) return 0;
  const root = getSpan(spans[0]);
  return root?.latency_ms || root?.latency || 0;
}

function getLatencyColor(latency, rootLatency) {
  if (!rootLatency || !latency) return "text.disabled";
  const ratio = latency / rootLatency;
  if (ratio >= 0.75) return "#DC2626";
  if (ratio >= 0.5) return "#D97706";
  return "text.disabled";
}

// Left column defaults (pixels)
const LEFT_DEFAULT = 300;
const LEFT_MIN = 180;
const LEFT_MAX = 600;

// ---------------------------------------------------------------------------
// Toolbar — search, expand/collapse, metrics toggle
// ---------------------------------------------------------------------------
const TimelineToolbar = ({
  searchQuery,
  onSearchChange,
  allCollapsed,
  onCollapseAll,
  onExpandAll,
  showMetrics,
  onToggleMetrics,
}) => (
  <Stack
    spacing={0.25}
    sx={{
      px: 1,
      py: 0.5,
      borderBottom: "1px solid",
      borderColor: "divider",
      flexShrink: 0,
    }}
  >
    <Stack direction="row" alignItems="center" justifyContent="space-between">
      <Typography
        sx={{
          fontSize: 10.5,
          fontWeight: 600,
          color: "text.disabled",
          letterSpacing: "0.03em",
          textTransform: "uppercase",
        }}
      >
        Trace Timeline
      </Typography>
      <Stack direction="row" spacing={0}>
        <CustomTooltip
          show
          title={allCollapsed ? "Expand all" : "Collapse all"}
          placement="top"
          arrow
          size="small"
          type="black"
        >
          <IconButton
            size="small"
            onClick={allCollapsed ? onExpandAll : onCollapseAll}
            sx={{ p: 0.25 }}
          >
            <Iconify
              icon={
                allCollapsed
                  ? "mdi:unfold-more-horizontal"
                  : "mdi:unfold-less-horizontal"
              }
              width={14}
              color="text.disabled"
            />
          </IconButton>
        </CustomTooltip>
        <CustomTooltip
          show
          title={showMetrics ? "Hide metrics" : "Show metrics"}
          placement="top"
          arrow
          size="small"
          type="black"
        >
          <IconButton size="small" onClick={onToggleMetrics} sx={{ p: 0.25 }}>
            <Iconify
              icon={showMetrics ? "mdi:eye-outline" : "mdi:eye-off-outline"}
              width={14}
              color="text.disabled"
            />
          </IconButton>
        </CustomTooltip>
      </Stack>
    </Stack>
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 0.5,
        px: 0.75,
        py: 0.25,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 0.5,
      }}
    >
      <Iconify icon="mdi:magnify" width={11} color="text.disabled" />
      <Box
        component="input"
        placeholder="Search spans"
        value={searchQuery}
        onChange={(e) => onSearchChange(e.target.value)}
        sx={{
          border: "none",
          outline: "none",
          flex: 1,
          fontSize: 11,
          color: "text.primary",
          bgcolor: "transparent",
          fontFamily: "inherit",
          py: 0.25,
          "&::placeholder": { color: "text.disabled" },
        }}
      />
    </Box>
  </Stack>
);

TimelineToolbar.propTypes = {
  searchQuery: PropTypes.string,
  onSearchChange: PropTypes.func.isRequired,
  allCollapsed: PropTypes.bool,
  onCollapseAll: PropTypes.func.isRequired,
  onExpandAll: PropTypes.func.isRequired,
  showMetrics: PropTypes.bool,
  onToggleMetrics: PropTypes.func.isRequired,
};

// ---------------------------------------------------------------------------
// TimelineRow — span name + metrics + Gantt bar
// ---------------------------------------------------------------------------
const TimelineRow = ({
  entry,
  depth,
  selectedSpanId,
  onSelect,
  traceStart,
  traceDuration,
  showMetrics,
  visibleMetrics,
  rootLatency,
  searchQuery,
  expandedSet,
  onToggleExpand,
  leftWidth,
}) => {
  const span = getSpan(entry);
  const hasChildren = entry.children?.length > 0;
  const isExpanded = expandedSet.has(span.id);
  const isSelected = selectedSpanId === span.id;
  const isRoot = depth === 0;
  const type = (span.observation_type || "unknown").toLowerCase();
  const cfg = getTypeConfig(type);
  const spanName = span.name || "unnamed";

  const spanStartTime = span.start_time;
  const startMs = spanStartTime
    ? new Date(spanStartTime).getTime() - traceStart
    : 0;
  const durationMs = span.latency_ms || span.latency || 0;
  const leftPct = traceDuration > 0 ? (startMs / traceDuration) * 100 : 0;
  const widthPct = traceDuration > 0 ? (durationMs / traceDuration) * 100 : 0;
  const tokens = span.total_tokens || 0;
  const hasError = span.status === "ERROR";
  const errorCount = isRoot ? countErrors(entry) : hasError ? 1 : 0;
  const isLlm = type === "llm" || type === "generation";
  const modelName = isLlm ? span.model || span.model_name || "" : "";

  // Eval scores
  const subtreeEvals = useMemo(() => collectSubtreeEvals(entry), [entry]);
  // This span's own eval rows flattened from the task-grouped eval_scores.
  const ownEvalRows = spanOwnEvalRows(entry);

  // Bar label — duration only (metrics are in the left panel)
  const durationLabel = formatLatency(durationMs);

  // Search filtering
  const matchesSearch =
    !searchQuery || spanName.toLowerCase().includes(searchQuery.toLowerCase());
  const childrenMatchSearch = useMemo(() => {
    if (!searchQuery || !entry.children?.length) return false;
    const check = (e) => {
      const s = getSpan(e);
      if ((s.name || "").toLowerCase().includes(searchQuery.toLowerCase()))
        return true;
      return e.children?.some(check) || false;
    };
    return entry.children.some(check);
  }, [searchQuery, entry]);

  if (searchQuery && !matchesSearch && !childrenMatchSearch) return null;

  return (
    <>
      <Box
        onClick={() => onSelect(span.id)}
        sx={{
          display: "flex",
          alignItems: "center",
          height: 28,
          cursor: "pointer",
          bgcolor: isSelected ? "rgba(120, 87, 252, 0.08)" : "transparent",
          borderLeft: isSelected
            ? "2px solid #7C3AED"
            : "2px solid transparent",
          "&:hover": {
            bgcolor: isSelected ? "rgba(120, 87, 252, 0.08)" : "action.hover",
          },
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        {/* Left: span info with indentation — horizontally scrollable */}
        <Box
          sx={{
            width: leftWidth,
            minWidth: leftWidth,
            maxWidth: leftWidth,
            flexShrink: 0,
            overflowX: "auto",
            overflowY: "hidden",
            borderRight: "1px solid",
            borderColor: "divider",
            "&::-webkit-scrollbar": { height: 3 },
            "&::-webkit-scrollbar-thumb": {
              bgcolor: "action.disabled",
              borderRadius: 2,
            },
          }}
        >
          {/* Inner wrapper — expands to fit content, enables horizontal scroll */}
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              width: "max-content",
              minWidth: "100%",
              pl: `${depth * 16 + 4}px`,
              pr: 1,
            }}
          >
            {hasChildren ? (
              <IconButton
                size="small"
                onClick={(e) => {
                  e.stopPropagation();
                  onToggleExpand(span.id);
                }}
                sx={{ p: 0, mr: 0.25, flexShrink: 0 }}
              >
                <Iconify
                  icon="mdi:chevron-down"
                  width={12}
                  color="text.disabled"
                  sx={{
                    transform: isExpanded ? "rotate(0deg)" : "rotate(-90deg)",
                    transition: "transform 150ms",
                  }}
                />
              </IconButton>
            ) : (
              <Box sx={{ width: 16, mr: 0.25, flexShrink: 0 }} />
            )}
            <SvgColor
              src={cfg.icon}
              sx={{
                width: 13,
                height: 13,
                color: cfg.color,
                flexShrink: 0,
                mr: 0.5,
              }}
            />

            {/* All info on one line: name · model · metrics */}
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                gap: "6px",
                whiteSpace: "nowrap",
              }}
            >
              <Typography
                component="span"
                sx={{
                  fontSize: 12,
                  fontWeight: isSelected ? 600 : 500,
                  color: "text.primary",
                  lineHeight: 1.4,
                }}
              >
                {spanName}
              </Typography>
              {modelName && (
                <Typography
                  component="span"
                  sx={{ fontSize: 10, color: "text.disabled" }}
                >
                  {modelName}
                </Typography>
              )}
              {showMetrics && (
                <>
                  {(!visibleMetrics || visibleMetrics.latency) && (
                    <Typography
                      component="span"
                      sx={{
                        fontSize: 10,
                        color: getLatencyColor(durationMs, rootLatency),
                        display: "inline-flex",
                        alignItems: "center",
                        gap: "2px",
                      }}
                    >
                      <Iconify icon="mdi:clock-outline" width={9} />
                      {formatLatency(durationMs)}
                    </Typography>
                  )}
                  {(!visibleMetrics || visibleMetrics.tokens) && tokens > 0 && (
                    <Typography
                      component="span"
                      sx={{
                        fontSize: 10,
                        color: "text.disabled",
                        display: "inline-flex",
                        alignItems: "center",
                        gap: "2px",
                      }}
                    >
                      <Iconify icon="mdi:pound" width={9} />
                      {formatTokenCount(tokens)}
                    </Typography>
                  )}
                  {visibleMetrics?.cost && span.cost > 0 && (
                    <Typography
                      component="span"
                      sx={{
                        fontSize: 10,
                        color: "text.disabled",
                        display: "inline-flex",
                        alignItems: "center",
                        gap: "2px",
                      }}
                    >
                      <Iconify icon="mdi:currency-usd" width={9} />
                      {formatCost(span.cost)}
                    </Typography>
                  )}
                  {errorCount > 0 && (
                    <Typography
                      component="span"
                      sx={{
                        fontSize: 10,
                        color: "#DC2626",
                        fontWeight: 600,
                        display: "inline-flex",
                        alignItems: "center",
                        gap: "2px",
                      }}
                    >
                      <Iconify icon="mdi:alert-outline" width={9} />
                      {errorCount}
                    </Typography>
                  )}
                  {isRoot && subtreeEvals.total > 0 && (
                    <Typography
                      component="span"
                      sx={{
                        fontSize: 10,
                        color: subtreeEvals.fail > 0 ? "#DC2626" : "#16A34A",
                        fontWeight: 500,
                        display: "inline-flex",
                        alignItems: "center",
                        gap: "2px",
                        bgcolor:
                          subtreeEvals.fail > 0
                            ? alpha("#DC2626", 0.08)
                            : alpha("#16A34A", 0.08),
                        px: "3px",
                        borderRadius: "3px",
                      }}
                    >
                      <Iconify
                        icon={
                          subtreeEvals.fail > 0
                            ? "mdi:shield-alert-outline"
                            : "mdi:shield-check-outline"
                        }
                        width={9}
                      />
                      {`${subtreeEvals.pass}/${subtreeEvals.total}`}
                    </Typography>
                  )}
                  {!isRoot &&
                    ownEvalRows.length > 0 &&
                    (() => {
                      const ownPass = ownEvalRows.filter(
                        (r) => r.pass === true,
                      ).length;
                      const ownFail = ownEvalRows.filter(
                        (r) => r.pass === false,
                      ).length;
                      return (
                        <CustomTooltip
                          show
                          type="black"
                          size="small"
                          placement="top"
                          title={
                            <Box sx={{ p: 0.25, minWidth: 120 }}>
                              <Typography
                                sx={{
                                  fontSize: 10,
                                  color: "rgba(255,255,255,0.5)",
                                  mb: 0.25,
                                }}
                              >
                                {ownPass}/{ownEvalRows.length} passed
                              </Typography>
                              {ownEvalRows.map((r, i) => {
                                const p = r.pass === true;
                                return (
                                  <Box
                                    key={i}
                                    sx={{
                                      display: "flex",
                                      alignItems: "center",
                                      gap: 0.5,
                                      py: 0.15,
                                    }}
                                  >
                                    <Iconify
                                      icon={
                                        p
                                          ? "mdi:check-circle-outline"
                                          : "mdi:alert-circle-outline"
                                      }
                                      width={10}
                                      sx={{ color: p ? "#4ade80" : "#f87171" }}
                                    />
                                    <Typography
                                      sx={{
                                        fontSize: 10,
                                        color: "#fff",
                                        flex: 1,
                                      }}
                                    >
                                      {r.evalName || "eval"}
                                    </Typography>
                                    <Typography
                                      sx={{
                                        fontSize: 10,
                                        color: p ? "#4ade80" : "#f87171",
                                        fontWeight: 600,
                                      }}
                                    >
                                      {r.label}
                                    </Typography>
                                  </Box>
                                );
                              })}
                            </Box>
                          }
                        >
                          <Typography
                            component="span"
                            sx={{
                              fontSize: 10,
                              fontWeight: 600,
                              color: ownFail > 0 ? "#dc2626" : "#16a34a",
                              display: "inline-flex",
                              alignItems: "center",
                              gap: "2px",
                              cursor: "default",
                            }}
                          >
                            <Iconify
                              icon={
                                ownFail > 0
                                  ? "mdi:shield-alert-outline"
                                  : "mdi:shield-check-outline"
                              }
                              width={10}
                            />
                            {ownFail > 0 ? ownFail : ""}
                          </Typography>
                        </CustomTooltip>
                      );
                    })()}
                </>
              )}
            </Box>
          </Box>
          {/* close inner wrapper */}
        </Box>
        {/* close left panel scroll container */}

        {/* Right: Gantt bar area — shows duration only */}
        <Box
          sx={{
            flex: 1,
            position: "relative",
            height: "100%",
            display: "flex",
            alignItems: "center",
            overflow: "hidden",
          }}
        >
          <Box
            sx={{
              position: "absolute",
              left: `${Math.max(leftPct, 0)}%`,
              width: `${Math.max(widthPct, 0.3)}%`,
              height: 16,
              bgcolor: cfg.color,
              borderRadius: "2px",
              minWidth: 3,
              opacity: isSelected ? 1 : 0.8,
              "&:hover": { opacity: 1 },
              transition: "opacity 150ms",
              display: "flex",
              alignItems: "center",
              justifyContent: "flex-end",
              overflow: "hidden",
              top: "50%",
              transform: "translateY(-50%)",
              ...(hasError && {
                border: "1.5px solid #DC2626",
                background: `linear-gradient(rgba(220,38,38,0.25), rgba(220,38,38,0.25)), ${cfg.color}`,
              }),
            }}
          >
            {widthPct > 30 && (
              <Typography
                sx={{
                  fontSize: 10,
                  color: "#fff",
                  fontWeight: 600,
                  whiteSpace: "nowrap",
                  pr: 0.5,
                  textShadow: "0 1px 2px rgba(0,0,0,0.3)",
                }}
              >
                {durationLabel}
              </Typography>
            )}
          </Box>
          {widthPct <= 30 && (
            <Typography
              sx={{
                position: "absolute",
                left: `${Math.max(leftPct + widthPct + 0.5, 0)}%`,
                top: "50%",
                transform: "translateY(-50%)",
                fontSize: 10,
                color: "text.secondary",
                whiteSpace: "nowrap",
                fontWeight: 500,
              }}
            >
              {durationLabel}
            </Typography>
          )}
        </Box>
      </Box>

      {/* Children */}
      {hasChildren && (
        <Collapse in={isExpanded} unmountOnExit>
          {entry.children.map((child) => (
            <TimelineRow
              key={getSpan(child).id}
              entry={child}
              depth={depth + 1}
              selectedSpanId={selectedSpanId}
              onSelect={onSelect}
              traceStart={traceStart}
              traceDuration={traceDuration}
              showMetrics={showMetrics}
              visibleMetrics={visibleMetrics}
              rootLatency={rootLatency}
              searchQuery={searchQuery}
              expandedSet={expandedSet}
              onToggleExpand={onToggleExpand}
              leftWidth={leftWidth}
            />
          ))}
        </Collapse>
      )}
    </>
  );
};

TimelineRow.propTypes = {
  entry: PropTypes.object.isRequired,
  depth: PropTypes.number.isRequired,
  selectedSpanId: PropTypes.string,
  onSelect: PropTypes.func.isRequired,
  traceStart: PropTypes.number.isRequired,
  traceDuration: PropTypes.number.isRequired,
  showMetrics: PropTypes.bool,
  visibleMetrics: PropTypes.object,
  rootLatency: PropTypes.number,
  searchQuery: PropTypes.string,
  expandedSet: PropTypes.instanceOf(Set).isRequired,
  onToggleExpand: PropTypes.func.isRequired,
  leftWidth: PropTypes.number.isRequired,
};

// ---------------------------------------------------------------------------
// SpanTreeTimeline
// ---------------------------------------------------------------------------
const SpanTreeTimeline = ({
  spans,
  selectedSpanId,
  onSelectSpan,
  showMetrics: showMetricsProp,
  visibleMetrics: visibleMetricsProp,
  onToggleMetrics: onToggleMetricsProp,
}) => {
  const [searchQuery, setSearchQuery] = useState("");
  const [showMetricsLocal, setShowMetricsLocal] = useState(true);
  const showMetrics =
    showMetricsProp !== undefined ? showMetricsProp : showMetricsLocal;
  const handleToggleMetrics =
    onToggleMetricsProp || (() => setShowMetricsLocal((p) => !p));
  const visibleMetrics = visibleMetricsProp || {
    latency: true,
    tokens: true,
    cost: false,
  };

  // Resizable left panel
  const [leftWidth, setLeftWidth] = useState(LEFT_DEFAULT);
  const containerRef = useRef(null);
  const isDragging = useRef(false);

  const handleDragStart = useCallback(
    (e) => {
      e.preventDefault();
      isDragging.current = true;
      const startX = e.clientX;
      const startWidth = leftWidth;

      const onMove = (moveE) => {
        const diff = moveE.clientX - startX;
        setLeftWidth(Math.min(LEFT_MAX, Math.max(LEFT_MIN, startWidth + diff)));
      };
      const onUp = () => {
        isDragging.current = false;
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
      };
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    },
    [leftWidth],
  );

  const [expandedSet, setExpandedSet] = useState(() =>
    spans ? collectAllIds(spans) : new Set(),
  );
  const allIds = useMemo(
    () => (spans ? collectAllIds(spans) : new Set()),
    [spans],
  );
  const allCollapsed = expandedSet.size === 0;
  const rootLatency = useMemo(() => getRootLatency(spans), [spans]);

  // Re-expand all when a new trace loads
  const spansKey = spans?.length ? getSpan(spans[0])?.id : null;
  useEffect(() => {
    if (spans) {
      setExpandedSet(collectAllIds(spans));
      setSearchQuery("");
    }
  }, [spansKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleCollapseAll = useCallback(() => setExpandedSet(new Set()), []);
  const handleExpandAll = useCallback(
    () => setExpandedSet(new Set(allIds)),
    [allIds],
  );
  const handleToggleExpand = useCallback((id) => {
    setExpandedSet((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const { traceStart, traceDuration, timeLabels } = useMemo(() => {
    if (!spans?.length)
      return { traceStart: 0, traceDuration: 1, timeLabels: [] };
    const allSpans = collectAllSpans(spans);
    const starts = allSpans
      .map((s) => new Date(s.start_time).getTime())
      .filter((t) => !isNaN(t) && isFinite(t));
    const ends = allSpans
      .map((s) => {
        const st = new Date(s.start_time).getTime();
        return st + (s.latency_ms || s.latency || 0);
      })
      .filter((t) => !isNaN(t) && isFinite(t));

    if (!starts.length || !ends.length)
      return { traceStart: 0, traceDuration: 1, timeLabels: [] };
    const minT = Math.min(...starts);
    const maxT = Math.max(...ends);
    const dur = maxT - minT || 1;

    const labels = [0, 0.25, 0.5, 0.75, 1].map((pct) => ({
      pct: pct * 100,
      label: formatLatency(dur * pct),
    }));

    return { traceStart: minT, traceDuration: dur, timeLabels: labels };
  }, [spans]);

  if (!spans?.length) {
    return (
      <Box sx={{ p: 2, textAlign: "center" }}>
        <Typography color="text.disabled" sx={{ fontSize: 12 }}>
          No spans in this trace
        </Typography>
      </Box>
    );
  }

  return (
    <Box
      ref={containerRef}
      sx={{
        display: "flex",
        flexDirection: "column",
        flex: 1,
        overflow: "hidden",
        position: "relative",
      }}
    >
      {/* Toolbar */}
      <TimelineToolbar
        searchQuery={searchQuery}
        onSearchChange={setSearchQuery}
        allCollapsed={allCollapsed}
        onCollapseAll={handleCollapseAll}
        onExpandAll={handleExpandAll}
        showMetrics={showMetrics}
        onToggleMetrics={handleToggleMetrics}
      />

      {/* Content area — header + rows, with full-height drag handle overlay */}
      <Box
        sx={{
          flex: 1,
          position: "relative",
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
        }}
      >
        {/* Full-height drag handle — sits on the divider line */}
        <Box
          onMouseDown={handleDragStart}
          sx={{
            position: "absolute",
            left: leftWidth - 3,
            top: 0,
            bottom: 0,
            width: 7,
            cursor: "col-resize",
            zIndex: 20,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            "&:hover > div, &:active > div": {
              bgcolor: "primary.main",
              opacity: 1,
              width: "3px",
            },
          }}
        >
          <Box
            sx={{
              width: "1px",
              height: "100%",
              bgcolor: "divider",
              transition: "all 150ms",
              opacity: 0.5,
            }}
          />
        </Box>

        {/* Time scale header */}
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            height: 24,
            borderBottom: "1px solid",
            borderColor: "divider",
            flexShrink: 0,
          }}
        >
          <Box
            sx={{
              width: leftWidth,
              minWidth: leftWidth,
              maxWidth: leftWidth,
              flexShrink: 0,
              height: "100%",
            }}
          />
          <Box sx={{ flex: 1, position: "relative", height: "100%" }}>
            {timeLabels.map((tick) => (
              <Typography
                key={tick.pct}
                sx={{
                  position: "absolute",
                  left: `${tick.pct}%`,
                  top: "50%",
                  transform: "translate(-50%, -50%)",
                  fontSize: 9,
                  color: "text.disabled",
                  whiteSpace: "nowrap",
                  fontWeight: 500,
                }}
              >
                {tick.label}
              </Typography>
            ))}
          </Box>
        </Box>

        {/* Span rows with vertical grid lines */}
        <Box sx={{ flex: 1, overflow: "auto", position: "relative" }}>
          {/* Vertical grid lines behind the bars */}
          <Box
            sx={{
              position: "absolute",
              top: 0,
              left: leftWidth,
              right: 0,
              bottom: 0,
              pointerEvents: "none",
              zIndex: 0,
            }}
          >
            {timeLabels.map((tick) => (
              <Box
                key={tick.pct}
                sx={{
                  position: "absolute",
                  left: `${tick.pct}%`,
                  top: 0,
                  bottom: 0,
                  width: "1px",
                  bgcolor: "divider",
                  opacity: 0.4,
                }}
              />
            ))}
          </Box>

          {/* Rows */}
          <Box sx={{ position: "relative", zIndex: 1 }}>
            {spans.map((entry) => (
              <TimelineRow
                key={getSpan(entry).id}
                entry={entry}
                depth={0}
                selectedSpanId={selectedSpanId}
                onSelect={onSelectSpan}
                traceStart={traceStart}
                traceDuration={traceDuration}
                showMetrics={showMetrics}
                visibleMetrics={visibleMetrics}
                rootLatency={rootLatency}
                searchQuery={searchQuery}
                expandedSet={expandedSet}
                onToggleExpand={handleToggleExpand}
                leftWidth={leftWidth}
              />
            ))}
          </Box>
        </Box>
      </Box>
    </Box>
  );
};

SpanTreeTimeline.propTypes = {
  spans: PropTypes.array,
  selectedSpanId: PropTypes.string,
  onSelectSpan: PropTypes.func.isRequired,
  showMetrics: PropTypes.bool,
  visibleMetrics: PropTypes.object,
  onToggleMetrics: PropTypes.func,
};

export default React.memo(SpanTreeTimeline);
