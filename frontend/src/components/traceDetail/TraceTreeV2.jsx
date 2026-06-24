import React, {
  useState,
  useMemo,
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
import { spanOwnEvalRows, collectSubtreeEvals } from "./evalScores";

// ---------------------------------------------------------------------------
// Type config — icon path + color
// ---------------------------------------------------------------------------
import { getTypeConfig } from "./spanTypeConfig";

function getSpan(entry) {
  return entry?.observation_span || {};
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

// ---------------------------------------------------------------------------
// Compact toolbar
// ---------------------------------------------------------------------------
const TreeToolbar = ({
  searchQuery,
  onSearchChange,
  onRefresh,
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
        Trace Tree
      </Typography>
      <Stack direction="row" spacing={0}>
        {onRefresh && (
          <CustomTooltip
            show
            title="Refresh"
            placement="top"
            arrow
            size="small"
            type="black"
          >
            <IconButton size="small" onClick={onRefresh} sx={{ p: 0.25 }}>
              <Iconify icon="mdi:refresh" width={14} color="text.disabled" />
            </IconButton>
          </CustomTooltip>
        )}
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
        placeholder="Search"
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

TreeToolbar.propTypes = {
  searchQuery: PropTypes.string,
  onSearchChange: PropTypes.func.isRequired,
  onRefresh: PropTypes.func,
  allCollapsed: PropTypes.bool,
  onCollapseAll: PropTypes.func.isRequired,
  onExpandAll: PropTypes.func.isRequired,
  showMetrics: PropTypes.bool,
  onToggleMetrics: PropTypes.func.isRequired,
};

// ---------------------------------------------------------------------------
// Compact tree row
// ---------------------------------------------------------------------------
const INDENT = 20;

const TreeNodeRow = ({
  entry,
  depth,
  isLast,
  selectedSpanId,
  onSelect,
  expandedSet,
  onToggleExpand,
  showMetrics,
  visibleMetrics,
  rootLatency,
  searchQuery,
}) => {
  const span = getSpan(entry);
  const rowRef = useRef(null);
  const hasChildren = entry.children?.length > 0;
  const isExpanded = expandedSet.has(span.id);
  const isSelected = selectedSpanId === span.id;

  // Auto-scroll selected row into view
  useEffect(() => {
    if (isSelected && rowRef.current) {
      rowRef.current.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }, [isSelected]);
  const isRoot = depth === 0;
  const type = span.observation_type || "unknown";
  const cfg = getTypeConfig(type);
  const latency = span.latency_ms ?? span.latency ?? 0;
  const tokens = span.total_tokens ?? 0;
  const hasError = span.status === "ERROR";
  const errorCount = isRoot ? countErrors(entry) : hasError ? 1 : 0;
  const spanName = span.name || "unnamed";
  // _filterMatch: true = this span matched the filter, false/undefined = ancestor kept for hierarchy
  const isDimmed = entry._filterMatch === false;

  // Eval scores — roll up from entire subtree (includes self + all children)
  const subtreeEvals = useMemo(() => collectSubtreeEvals(entry), [entry]);
  const evalPassCount = subtreeEvals.pass;
  const evalFailCount = subtreeEvals.fail;
  const evalTotal = subtreeEvals.total;

  // This span's own eval rows for the chip + tooltip; red-dot if any failed.
  const ownEvalRows = spanOwnEvalRows(entry);

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
        ref={rowRef}
        onClick={() => onSelect(span.id)}
        sx={{
          display: "flex",
          alignItems: "flex-start",
          cursor: "pointer",
          bgcolor: isSelected ? "rgba(120, 87, 252, 0.1)" : "transparent",
          borderLeft: isSelected
            ? "2px solid #7C3AED"
            : "2px solid transparent",
          opacity: isDimmed ? 0.4 : 1,
          "&:hover": {
            bgcolor: isSelected ? "rgba(120, 87, 252, 0.1)" : "action.hover",
            opacity: 1,
          },
          pl: `${depth * INDENT + 6}px`,
          pr: 0.75,
          py: "3px",
          position: "relative",
          minHeight: 0,
        }}
      >
        {/* Dashed L-shaped connector line */}
        {depth > 0 && (
          <Box
            sx={{
              position: "absolute",
              left: `${(depth - 1) * INDENT + 6 + 7}px`,
              top: 0,
              width: INDENT - 6,
              height: isLast ? 14 : "100%",
              borderLeft: "1.5px dashed",
              borderBottom: isLast ? "1.5px dashed" : "none",
              borderColor: hasError ? "error.light" : "divider",
              borderBottomLeftRadius: isLast ? 6 : 0,
              pointerEvents: "none",
            }}
          />
        )}
        {/* Horizontal connector for non-last */}
        {depth > 0 && !isLast && (
          <Box
            sx={{
              position: "absolute",
              left: `${(depth - 1) * INDENT + 6 + 7}px`,
              top: 14,
              width: INDENT - 6,
              height: 0,
              borderBottom: "1.5px dashed",
              borderColor: "divider",
              pointerEvents: "none",
            }}
          />
        )}

        {/* Continuing vertical dashed line for ancestors */}
        {depth > 0 && !isLast && (
          <Box
            sx={{
              position: "absolute",
              left: `${(depth - 1) * INDENT + 6 + 7}px`,
              top: 14,
              height: "calc(100% - 14px)",
              borderLeft: "1.5px dashed",
              borderColor: "divider",
              pointerEvents: "none",
            }}
          />
        )}

        {/* Type icon */}
        <SvgColor
          src={cfg.icon}
          sx={{
            width: 13,
            height: 13,
            color: cfg.color,
            flexShrink: 0,
            mt: "2px",
          }}
        />

        {/* Name + metrics */}
        <Box sx={{ flex: 1, minWidth: 0, ml: 0.5 }}>
          <Typography
            noWrap
            sx={{
              fontSize: 11.5,
              fontWeight: isSelected ? 600 : 500,
              lineHeight: 1.2,
              color: "text.primary",
            }}
          >
            {spanName}
          </Typography>
          {showMetrics && (
            <Stack direction="row" spacing={0.75} sx={{ mt: 0, lineHeight: 1 }}>
              {(!visibleMetrics || visibleMetrics.latency) && (
                <Typography
                  component="span"
                  sx={{
                    fontSize: 9.5,
                    color: getLatencyColor(latency, rootLatency),
                    display: "inline-flex",
                    alignItems: "center",
                    gap: "2px",
                  }}
                >
                  <Iconify icon="mdi:clock-outline" width={9} />
                  {formatLatency(latency)}
                </Typography>
              )}
              {(!visibleMetrics || visibleMetrics.tokens) && tokens > 0 && (
                <Typography
                  component="span"
                  sx={{
                    fontSize: 9.5,
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
                    fontSize: 9.5,
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
                    fontSize: 9.5,
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
              {/* Root: show total rollup badge */}
              {isRoot && evalTotal > 0 && (
                <Typography
                  component="span"
                  sx={{
                    fontSize: 9.5,
                    color: evalFailCount > 0 ? "#DC2626" : "#16A34A",
                    fontWeight: 500,
                    display: "inline-flex",
                    alignItems: "center",
                    gap: "2px",
                    bgcolor:
                      evalFailCount > 0
                        ? alpha("#DC2626", 0.08)
                        : alpha("#16A34A", 0.08),
                    px: "3px",
                    py: "1px",
                    borderRadius: "3px",
                  }}
                >
                  <Iconify
                    icon={
                      evalFailCount > 0
                        ? "mdi:shield-alert-outline"
                        : "mdi:shield-check-outline"
                    }
                    width={9}
                  />
                  {`${evalPassCount}/${evalTotal}`}
                </Typography>
              )}
              {/* Non-root: single eval status chip with hover breakdown */}
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
                            const pass = r.pass === true;
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
                                    pass
                                      ? "mdi:check-circle-outline"
                                      : "mdi:alert-circle-outline"
                                  }
                                  width={10}
                                  sx={{ color: pass ? "#4ade80" : "#f87171" }}
                                />
                                <Typography
                                  sx={{ fontSize: 10, color: "#fff", flex: 1 }}
                                >
                                  {r.evalName || "eval"}
                                </Typography>
                                <Typography
                                  sx={{
                                    fontSize: 10,
                                    color: pass ? "#4ade80" : "#f87171",
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
                          fontSize: 9,
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
                          width={11}
                        />
                        {ownFail > 0 ? ownFail : ""}
                      </Typography>
                    </CustomTooltip>
                  );
                })()}
            </Stack>
          )}
        </Box>

        {/* Expand/collapse chevron */}
        {hasChildren && (
          <IconButton
            size="small"
            onClick={(e) => {
              e.stopPropagation();
              onToggleExpand(span.id);
            }}
            sx={{ p: 0, mt: 0.25, flexShrink: 0 }}
          >
            <Iconify
              icon="mdi:chevron-down"
              width={14}
              color="text.disabled"
              sx={{
                transform: isExpanded ? "rotate(0deg)" : "rotate(-90deg)",
                transition: "transform 150ms",
              }}
            />
          </IconButton>
        )}
      </Box>

      {/* Children */}
      {hasChildren && (
        <Collapse in={isExpanded} unmountOnExit>
          {entry.children.map((child, idx) => (
            <TreeNodeRow
              key={getSpan(child).id || idx}
              entry={child}
              depth={depth + 1}
              isLast={idx === entry.children.length - 1}
              selectedSpanId={selectedSpanId}
              onSelect={onSelect}
              expandedSet={expandedSet}
              onToggleExpand={onToggleExpand}
              showMetrics={showMetrics}
              visibleMetrics={visibleMetrics}
              rootLatency={rootLatency}
              searchQuery={searchQuery}
            />
          ))}
        </Collapse>
      )}
    </>
  );
};

TreeNodeRow.propTypes = {
  entry: PropTypes.object.isRequired,
  depth: PropTypes.number.isRequired,
  isLast: PropTypes.bool,
  selectedSpanId: PropTypes.string,
  onSelect: PropTypes.func.isRequired,
  expandedSet: PropTypes.instanceOf(Set).isRequired,
  onToggleExpand: PropTypes.func.isRequired,
  showMetrics: PropTypes.bool,
  visibleMetrics: PropTypes.object,
  rootLatency: PropTypes.number,
  searchQuery: PropTypes.string,
};

// ---------------------------------------------------------------------------
// TraceTreeV2
// ---------------------------------------------------------------------------
const TraceTreeV2 = ({
  spans,
  selectedSpanId,
  onSelectSpan,
  showMetrics: showMetricsProp,
  visibleMetrics,
  onToggleMetrics: onToggleMetricsProp,
  onRefresh,
}) => {
  const [searchQuery, setSearchQuery] = useState("");
  const [showMetricsLocal, setShowMetricsLocal] = useState(true);
  // Use prop if provided, otherwise use local state
  const showMetrics =
    showMetricsProp !== undefined ? showMetricsProp : showMetricsLocal;
  const handleToggleMetrics =
    onToggleMetricsProp || (() => setShowMetricsLocal((p) => !p));
  const [expandedSet, setExpandedSet] = useState(() =>
    spans ? collectAllIds(spans) : new Set(),
  );

  const rootLatency = useMemo(() => getRootLatency(spans), [spans]);
  const allIds = useMemo(
    () => (spans ? collectAllIds(spans) : new Set()),
    [spans],
  );
  const allCollapsed = expandedSet.size === 0;

  // Re-expand all when a new trace loads
  const spansKey = spans?.length ? getSpan(spans[0])?.id : null;
  React.useEffect(() => {
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
      sx={{
        display: "flex",
        flexDirection: "column",
        flex: 1,
        overflow: "hidden",
      }}
    >
      <TreeToolbar
        searchQuery={searchQuery}
        onSearchChange={setSearchQuery}
        onRefresh={onRefresh}
        allCollapsed={allCollapsed}
        onCollapseAll={handleCollapseAll}
        onExpandAll={handleExpandAll}
        showMetrics={showMetrics}
        onToggleMetrics={handleToggleMetrics}
      />
      <Box sx={{ flex: 1, overflow: "auto" }}>
        {spans.map((entry, idx) => (
          <TreeNodeRow
            key={getSpan(entry).id || idx}
            entry={entry}
            depth={0}
            isLast={idx === spans.length - 1}
            selectedSpanId={selectedSpanId}
            onSelect={onSelectSpan}
            expandedSet={expandedSet}
            onToggleExpand={handleToggleExpand}
            showMetrics={showMetrics}
            visibleMetrics={visibleMetrics}
            rootLatency={rootLatency}
            searchQuery={searchQuery}
          />
        ))}
      </Box>
    </Box>
  );
};

TraceTreeV2.propTypes = {
  spans: PropTypes.array,
  selectedSpanId: PropTypes.string,
  onSelectSpan: PropTypes.func.isRequired,
  showMetrics: PropTypes.bool,
  visibleMetrics: PropTypes.object,
  onToggleMetrics: PropTypes.func,
  onRefresh: PropTypes.func,
};

export default React.memo(TraceTreeV2);
