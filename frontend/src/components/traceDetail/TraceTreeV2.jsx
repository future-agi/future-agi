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

// ---------------------------------------------------------------------------
// Type config — icon path + color
// ---------------------------------------------------------------------------
import { getTypeConfig } from "./spanTypeConfig";
import {
  collectSubtreeEvals,
  evalPassOf,
  evalStatusWord,
  getOwnEvalScores,
} from "./evalScores";

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
  if (ratio >= 0.75) return "accent.fail";
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
// Span annotations (R4 lane T, additive): small chips after the span
// name, e.g. `▲ First wrong step`, `↻ retry 2`. Absent → nothing renders.
// ---------------------------------------------------------------------------
const ANNOTATION_TONES = ["error", "warning", "info", "success"];

const SpanAnnotationChips = ({ items }) => (
  <Stack
    direction="row"
    spacing={0.5}
    sx={{ flexShrink: 0, ml: 0.75, alignItems: "center" }}
  >
    {items.map((a, i) => {
      const tone = ANNOTATION_TONES.includes(a.tone) ? a.tone : "info";
      return (
        <Box
          key={`${a.label}-${i}`}
          component="span"
          data-span-annotation={a.label}
          sx={(theme) => ({
            display: "inline-flex",
            alignItems: "center",
            gap: "2px",
            px: 0.5,
            borderRadius: "4px",
            fontSize: 10,
            fontWeight: 600,
            lineHeight: "15px",
            whiteSpace: "nowrap",
            color: theme.palette[tone].main,
            bgcolor: alpha(theme.palette[tone].main, 0.12),
          })}
        >
          {a.icon ? <Iconify icon={a.icon} width={10} /> : null}
          {a.label}
        </Box>
      );
    })}
  </Stack>
);

SpanAnnotationChips.propTypes = {
  items: PropTypes.arrayOf(
    PropTypes.shape({
      tone: PropTypes.oneOf(ANNOTATION_TONES),
      label: PropTypes.string.isRequired,
      icon: PropTypes.string,
    }),
  ).isRequired,
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
  neutralLatency = false,
  rootLatency,
  searchQuery,
  annotations,
  highlightSpanIds,
  contextRows,
  rowExtras,
}) => {
  const span = getSpan(entry);
  const rowRef = useRef(null);
  const hasChildren = entry.children?.length > 0;
  const isExpanded = expandedSet.has(span.id);
  const isSelected = selectedSpanId === span.id;
  const chips = annotations?.[span.id];
  const isHighlighted = Boolean(highlightSpanIds?.includes(span.id));

  // Auto-scroll selected row into view
  useEffect(() => {
    if (!isSelected || !rowRef.current) return;
    // IT-R3 (additive): `contextRows` keeps N rows above the selected one
    // in view (the steps that led to it), not just the row at the edge.
    const scroller = contextRows
      ? rowRef.current.closest("[data-tree-scroller]")
      : null;
    if (scroller) {
      const row = rowRef.current.getBoundingClientRect();
      const box = scroller.getBoundingClientRect();
      const rowH = rowRef.current.offsetHeight || 28;
      scroller.scrollTop += row.top - box.top - contextRows * rowH;
      return;
    }
    rowRef.current.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [isSelected]); // eslint-disable-line react-hooks/exhaustive-deps
  const isRoot = depth === 0;
  const type = span.observation_type || "unknown";
  const cfg = getTypeConfig(type);
  const latency = span.latency_ms ?? span.latency ?? 0;
  const tokens = span.total_tokens ?? 0;
  const hasError = span.status === "ERROR";
  const errorCount = isRoot ? countErrors(entry) : hasError ? 1 : 0;
  // Additive (IT-R5 G4 S2): a caller may name the root (`Row T13 · try 3
  // of 3`) instead of the trace's own root name.
  const spanName = (isRoot && rowExtras?.rootLabel) || span.name || "unnamed";
  // _filterMatch: true = this span matched the filter, false/undefined = ancestor kept for hierarchy
  const isDimmed = entry._filterMatch === false;

  // Eval scores — roll up from entire subtree (includes self + all children)
  const subtreeEvals = useMemo(() => collectSubtreeEvals(entry), [entry]);
  const evalPassCount = subtreeEvals.pass;
  const evalFailCount = subtreeEvals.fail;
  const evalTotal = subtreeEvals.total;

  // This span's own evals — check if THIS span has failed evals (for red dot indicator)
  const ownEvals = getOwnEvalScores(entry);
  const hasOwnFailedEval = ownEvals.some((e) => evalPassOf(e) === false);

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
          bgcolor: isSelected
            ? "rgba(120, 87, 252, 0.1)"
            : isHighlighted
              ? (theme) => alpha(theme.palette.warning.main, 0.12)
              : "transparent",
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
          {chips?.length ? (
            <Stack direction="row" alignItems="center" sx={{ minWidth: 0 }}>
              <Typography
                noWrap
                sx={{
                  fontSize: 11.5,
                  fontWeight: isSelected ? 600 : 500,
                  lineHeight: 1.2,
                  color: "text.primary",
                  minWidth: 0,
                }}
              >
                {spanName}
              </Typography>
              <SpanAnnotationChips items={chips} />
            </Stack>
          ) : (
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
          )}
          {showMetrics && (
            <Stack direction="row" spacing={0.75} sx={{ mt: 0, lineHeight: 1 }}>
              {(!visibleMetrics || visibleMetrics.latency) && (
                <Typography
                  component="span"
                  sx={{
                    fontSize: 9.5,
                    // Additive (IT-R5 G4 P3): `neutralLatency` keeps the clock grey;
                    // red is for errors only.
                    color: neutralLatency
                      ? "text.disabled"
                      : getLatencyColor(latency, rootLatency),
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
              {rowExtras?.showModel &&
              span.model &&
              span.model !== spanName &&
              !isRoot ? (
                // Additive (IT-R5 G4 S2): the model of an LLM step.
                <Typography
                  component="span"
                  data-tree-model={span.model}
                  sx={{ fontSize: 9.5, color: "text.secondary" }}
                >
                  {span.model}
                </Typography>
              ) : null}
              {/* Additive (W1-9 B3): a host's per-row extras (step badges). */}
              {rowExtras?.renderExtra
                ? rowExtras.renderExtra(entry, { isRoot })
                : null}
              {errorCount > 0 && (
                <Typography
                  component="span"
                  sx={{
                    fontSize: 9.5,
                    color: "accent.fail",
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
                    color: evalFailCount > 0 ? "accent.fail" : "accent.pass",
                    fontWeight: 500,
                    display: "inline-flex",
                    alignItems: "center",
                    gap: "2px",
                    bgcolor: (t) =>
                      alpha(
                        evalFailCount > 0
                          ? t.palette.accent.fail
                          : t.palette.accent.pass,
                        0.08,
                      ),
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
                ownEvals.length > 0 &&
                (() => {
                  // W1-9: a skipped / N/A / errored score is neither.
                  const ownPass = ownEvals.filter(
                    (e) => evalPassOf(e) === true,
                  ).length;
                  const ownFail = ownEvals.filter(
                    (e) => evalPassOf(e) === false,
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
                            {ownPass}/{ownEvals.length} passed
                          </Typography>
                          {ownEvals.map((e, i) => {
                            const s =
                              e?.score ??
                              (e?.result === true
                                ? 100
                                : e?.result === false
                                  ? 0
                                  : null);
                            const verdict = evalPassOf(e);
                            const pass = verdict === true;
                            // W1-9: no verdict reads its reason word, grey.
                            const tone =
                              verdict === null
                                ? "rgba(255,255,255,0.6)"
                                : pass
                                  ? "#4ade80"
                                  : "#f87171";
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
                                    verdict === null
                                      ? "mdi:minus-circle-outline"
                                      : pass
                                        ? "mdi:check-circle-outline"
                                        : "mdi:alert-circle-outline"
                                  }
                                  width={10}
                                  sx={{ color: tone }}
                                />
                                <Typography
                                  sx={{ fontSize: 10, color: "#fff", flex: 1 }}
                                >
                                  {e.eval_name || "eval"}
                                  {e.run_label ? ` · ${e.run_label}` : ""}
                                </Typography>
                                <Typography
                                  sx={{
                                    fontSize: 10,
                                    color: tone,
                                    fontWeight: 600,
                                  }}
                                >
                                  {verdict === null
                                    ? evalStatusWord(e)
                                    : s != null
                                      ? `${s}%`
                                      : "—"}
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
                          // W1-9: verdicts that never ran are neither.
                          color:
                            ownFail > 0
                              ? "accent.fail"
                              : ownPass > 0
                                ? "accent.pass"
                                : "text.disabled",
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
                              : ownPass > 0
                                ? "mdi:shield-check-outline"
                                : "mdi:shield-outline"
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
          {rowExtras?.durationBar && !isRoot && rootLatency > 0 ? (
            // Additive (IT-R5 G4 S2): the step's share of the run's time.
            <Box
              data-tree-duration-bar=""
              sx={{
                mt: "2px",
                height: 3,
                borderRadius: 1.5,
                width: `${Math.max(2, Math.min(100, (latency / rootLatency) * 100))}%`,
                bgcolor: hasError ? "error.light" : "primary.light",
                opacity: 0.7,
              }}
            />
          ) : null}
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
              contextRows={contextRows}
              onSelect={onSelect}
              expandedSet={expandedSet}
              onToggleExpand={onToggleExpand}
              showMetrics={showMetrics}
              visibleMetrics={visibleMetrics}
              neutralLatency={neutralLatency}
              rootLatency={rootLatency}
              searchQuery={searchQuery}
              annotations={annotations}
              highlightSpanIds={highlightSpanIds}
              rowExtras={rowExtras}
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
  neutralLatency: PropTypes.bool,
  rootLatency: PropTypes.number,
  searchQuery: PropTypes.string,
  annotations: PropTypes.object,
  highlightSpanIds: PropTypes.arrayOf(PropTypes.string),
  contextRows: PropTypes.number,
  rowExtras: PropTypes.object,
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
  neutralLatency = false,
  onToggleMetrics: onToggleMetricsProp,
  onRefresh,
  annotations,
  highlightSpanIds,
  contextRows,
  rowExtras,
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
      <Box sx={{ flex: 1, overflow: "auto" }} data-tree-scroller>
        {spans.map((entry, idx) => (
          <TreeNodeRow
            key={getSpan(entry).id || idx}
            entry={entry}
            depth={0}
            isLast={idx === spans.length - 1}
            selectedSpanId={selectedSpanId}
            contextRows={contextRows}
            onSelect={onSelectSpan}
            expandedSet={expandedSet}
            onToggleExpand={handleToggleExpand}
            showMetrics={showMetrics}
            visibleMetrics={visibleMetrics}
            neutralLatency={neutralLatency}
            rootLatency={rootLatency}
            searchQuery={searchQuery}
            annotations={annotations}
            highlightSpanIds={highlightSpanIds}
            rowExtras={rowExtras}
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
  neutralLatency: PropTypes.bool,
  onToggleMetrics: PropTypes.func,
  onRefresh: PropTypes.func,
  // R4 lane T (additive, inert when absent): chips after a span's name,
  // `{[spanId]: [{tone: "error"|"warning"|"info"|"success", label, icon?}]}`
  annotations: PropTypes.objectOf(
    PropTypes.arrayOf(
      PropTypes.shape({
        tone: PropTypes.oneOf(ANNOTATION_TONES),
        label: PropTypes.string.isRequired,
        icon: PropTypes.string,
      }),
    ),
  ),
  // R4 lane T (additive): rows tinted to draw the eye (e.g. a retry)
  highlightSpanIds: PropTypes.arrayOf(PropTypes.string),
  // IT-R3 (additive): rows of context kept above the selected row
  contextRows: PropTypes.number,
  // IT-R5 G4 S2 (additive): {rootLabel, showModel, durationBar}
  rowExtras: PropTypes.object,
};

export default React.memo(TraceTreeV2);
