import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  IconButton,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Skeleton,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from "@mui/material";
import { useTheme } from "@mui/material/styles";
import { useNavigate } from "react-router";
import EnterpriseMark from "src/components/capability-gate/EnterpriseMark";
import Iconify from "src/components/iconify";
import { CAPABILITY, useFeatureDenied } from "src/hooks/useCapabilities";
import {
  askLink,
  insightsReportLink,
} from "src/sections/projects/observeHandoff";
import { UNITS, unitWord } from "src/sections/projects/observePopulation";
import { CitationChip, EvidenceQuote } from "./Citation";
import { UNITS_PAGE, collectRunUnits, useRunUnits } from "./insightsApi";
import { isMutedCategory, sortCategoryTree } from "./insightsDistribution";
import {
  formatCost,
  formatCount,
  formatDay,
  formatLatency,
  formatPercent,
  formatSentiment,
  formatWhen,
  humanizeKey,
  humanizeValue,
} from "./insightsFormat";
import { useCancellable } from "./insightsHooks";
import { unitLink, unitsGridLink } from "./insightsLinks";
import { askSeed } from "./insightsScope";
import {
  notableDirection,
  notableText,
  notableTooltip,
  sortNotable,
} from "./insightsNotable";
import { useReport } from "./reportContext";
import {
  AttributePills,
  ErrorState,
  Swatch,
  ToneChip,
  toneColor,
} from "./parts";

const TREE_WIDTH = 280;

// ─── Tree ───────────────────────────────────────────────────────────────────

function TreeItem({
  category,
  depth,
  selected,
  expandable,
  expanded,
  onToggle,
  onOpen,
}) {
  const report = useReport();
  const muted = isMutedCategory(category);
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={0.5}
      role="treeitem"
      aria-selected={selected}
      aria-expanded={expandable ? expanded : undefined}
      tabIndex={0}
      onClick={() => onOpen(category.id)}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onOpen(category.id);
        }
        if (expandable && event.key === "ArrowRight" && !expanded)
          onToggle(category.id);
        if (expandable && event.key === "ArrowLeft" && expanded)
          onToggle(category.id);
      }}
      sx={{
        pl: depth ? 3.5 : 0.5,
        pr: 1,
        py: 0.625,
        borderRadius: 1,
        cursor: "pointer",
        bgcolor: selected ? "action.selected" : "transparent",
        color: muted ? "text.secondary" : "text.primary",
        "&:hover": { bgcolor: selected ? "action.selected" : "action.hover" },
        "&:focus-visible": {
          outline: "2px solid",
          outlineColor: "primary.main",
          outlineOffset: -2,
        },
      }}
    >
      {!depth && (
        <Box sx={{ width: 20, flexShrink: 0, display: "flex" }}>
          {expandable && (
            <IconButton
              size="small"
              tabIndex={-1}
              aria-label={`${expanded ? "Hide" : "Show"} subcategories of ${category.name}`}
              onClick={(event) => {
                event.stopPropagation();
                onToggle(category.id);
              }}
              sx={{ p: 0.25 }}
            >
              <Iconify
                icon={expanded ? "mdi:chevron-down" : "mdi:chevron-right"}
                width={16}
              />
            </IconButton>
          )}
        </Box>
      )}
      {!depth && <Swatch color={report.colorOf(category.id)} size={8} />}
      <Typography
        variant="body2"
        noWrap
        title={category.name}
        sx={{
          flex: 1,
          minWidth: 0,
          fontSize: 13,
          fontWeight: selected ? 600 : depth ? 400 : 500,
        }}
      >
        {category.name}
      </Typography>
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ fontVariantNumeric: "tabular-nums", flexShrink: 0 }}
      >
        {formatCount(category.stats?.count)}
      </Typography>
    </Stack>
  );
}

TreeItem.propTypes = {
  category: PropTypes.object.isRequired,
  depth: PropTypes.number,
  selected: PropTypes.bool,
  expandable: PropTypes.bool,
  expanded: PropTypes.bool,
  onToggle: PropTypes.func,
  onOpen: PropTypes.func.isRequired,
};

function CategoryTree({ categoryId }) {
  const report = useReport();
  // A folded sub's id opens its top.
  const current = report.categoriesById.get(categoryId);
  const currentId = current?.id || categoryId;
  const currentTop = current?.level === 2 ? current.parent_id : currentId;
  const [expanded, setExpanded] = useState(() => new Set([currentTop]));
  const rows = useMemo(() => sortCategoryTree(report.tree), [report.tree]);
  const toggle = (id) =>
    setExpanded((value) => {
      const next = new Set(value);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  return (
    <Box component="nav" aria-label="Categories">
      <Typography
        variant="overline"
        color="text.secondary"
        sx={{ display: "block", px: 0.5, mb: 0.5 }}
      >
        Categories
      </Typography>
      <Stack role="tree" spacing={0.25}>
        {rows.map((row) => {
          const open = expanded.has(row.id) || row.id === currentTop;
          return (
            <React.Fragment key={row.id}>
              <TreeItem
                category={row}
                depth={0}
                selected={row.id === currentId}
                expandable={row.expandable}
                expanded={open}
                onToggle={toggle}
                onOpen={report.openCategory}
              />
              {row.expandable &&
                open &&
                row.childRows.map((child) => (
                  <TreeItem
                    key={child.id}
                    category={child}
                    depth={1}
                    selected={child.id === currentId}
                    onOpen={report.openCategory}
                  />
                ))}
            </React.Fragment>
          );
        })}
      </Stack>
    </Box>
  );
}

CategoryTree.propTypes = { categoryId: PropTypes.string.isRequired };

// ─── Header ─────────────────────────────────────────────────────────────────

function Kpi({ label, value, compare, tone }) {
  const theme = useTheme();
  return (
    <Box
      sx={{
        px: 1.25,
        py: 0.75,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        minWidth: 0,
      }}
    >
      <Typography
        variant="caption"
        color="text.secondary"
        component="div"
        noWrap
        sx={{ lineHeight: "16px" }}
      >
        {label}
      </Typography>
      <Typography
        variant="body2"
        component="div"
        sx={{
          fontWeight: 600,
          fontVariantNumeric: "tabular-nums",
          color: tone ? toneColor(theme, tone) : "text.primary",
          lineHeight: "20px",
        }}
      >
        {value}
        {compare && (
          <Box
            component="span"
            sx={{
              ml: 0.5,
              fontWeight: 400,
              color: "text.secondary",
              fontSize: 12,
            }}
          >
            {compare}
          </Box>
        )}
      </Typography>
    </Box>
  );
}

Kpi.propTypes = {
  label: PropTypes.node.isRequired,
  value: PropTypes.node,
  compare: PropTypes.node,
  tone: PropTypes.string,
};

// A rate that is clearly worse than the report's overall rate reads as bad.
const rateTone = (value, overall, higherIsWorse = true) => {
  if (!Number.isFinite(value) || !Number.isFinite(overall)) return undefined;
  const gap = value - overall;
  if (Math.abs(gap) < 0.1) return undefined;
  return gap > 0 === higherIsWorse ? "bad" : "good";
};

function kpis(stats, totals, unit, lens) {
  const plural = (UNITS[unit] || UNITS.traces).plural;
  const vs = (value, format) =>
    Number.isFinite(value) ? `vs ${format(value)}` : undefined;
  return [
    {
      key: "count",
      label: humanizeKey(plural),
      value: formatCount(stats.count),
    },
    unit !== "users" && {
      key: "users",
      label: "Users",
      value: formatCount(stats.users),
    },
    lens !== "failures" &&
      Number.isFinite(stats.failure_rate) && {
        key: "failure",
        label: "Agent failure",
        value: formatPercent(stats.failure_rate),
        compare: vs(totals.failure_rate, formatPercent),
        tone: rateTone(stats.failure_rate, totals.failure_rate),
      },
    Number.isFinite(stats.unresolved_rate) && {
      key: "unresolved",
      label: "Unresolved",
      value: formatPercent(stats.unresolved_rate),
      compare: vs(totals.unresolved_rate, formatPercent),
      tone: rateTone(stats.unresolved_rate, totals.unresolved_rate),
    },
    Number.isFinite(stats.sentiment) && {
      key: "sentiment",
      label: "Sentiment",
      value: formatSentiment(stats.sentiment),
      compare: vs(totals.sentiment, formatSentiment),
      tone:
        Number.isFinite(totals.sentiment) &&
        Math.abs(stats.sentiment - totals.sentiment) >= 0.5
          ? stats.sentiment > totals.sentiment
            ? "good"
            : "bad"
          : undefined,
    },
    Number.isFinite(stats.latency_ms) && {
      key: "latency",
      label: "Median latency",
      value: formatLatency(stats.latency_ms),
      compare: vs(totals.latency_ms, formatLatency),
    },
    stats.cost > 0 && {
      key: "cost",
      label: "Avg cost",
      value: formatCost(stats.cost),
    },
    ...Object.entries(stats.attributes || {}).map(([key, summary]) => ({
      key: `attr-${key}`,
      label: humanizeKey(key),
      value:
        summary.type === "boolean"
          ? formatPercent(summary.rate)
          : summary.type === "number"
            ? Number(summary.mean).toFixed(1)
            : summary.top?.[0]?.value || "–",
    })),
  ].filter(Boolean);
}

// Every day the report covers, so quiet days show as gaps.
function dayRange(daily, reportDaily) {
  const keys = Object.keys({ ...(reportDaily || {}), ...(daily || {}) }).sort();
  if (!keys.length) return [];
  const days = [];
  const end = new Date(`${keys[keys.length - 1]}T00:00:00Z`);
  for (
    let day = new Date(`${keys[0]}T00:00:00Z`);
    day <= end && days.length < 120;
    day = new Date(day.getTime() + 86400000)
  ) {
    const key = day.toISOString().slice(0, 10);
    days.push({ key, count: daily?.[key] || 0 });
  }
  return days;
}

function DailyBars({ daily, reportDaily, color }) {
  const days = dayRange(daily, reportDaily);
  if (days.length < 2) return null;
  const max = Math.max(1, ...days.map((d) => d.count));
  return (
    <Box sx={{ width: 200, flexShrink: 0 }}>
      <Typography
        variant="caption"
        color="text.secondary"
        component="div"
        sx={{ mb: 0.5 }}
      >
        By day
      </Typography>
      <Stack
        direction="row"
        alignItems="flex-end"
        sx={{ height: 44, gap: "2px" }}
        role="img"
        aria-label={days.map((d) => `${d.key}: ${d.count}`).join(", ")}
      >
        {days.map((day) => (
          <Tooltip
            key={day.key}
            arrow
            describeChild
            title={`${formatDay(`${day.key}T12:00:00Z`)}: ${formatCount(day.count)}`}
          >
            <Box
              sx={{
                flex: 1,
                maxWidth: 24,
                height: day.count
                  ? `${Math.max(8, (day.count / max) * 100)}%`
                  : "2px",
                bgcolor: day.count ? color || "text.secondary" : "divider",
                borderRadius: "3px 3px 0 0",
              }}
            />
          </Tooltip>
        ))}
      </Stack>
      <Stack direction="row" justifyContent="space-between" sx={{ mt: 0.25 }}>
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ fontSize: 11 }}
        >
          {formatDay(`${days[0].key}T12:00:00Z`)}
        </Typography>
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ fontSize: 11 }}
        >
          {formatDay(`${days[days.length - 1].key}T12:00:00Z`)}
        </Typography>
      </Stack>
    </Box>
  );
}

DailyBars.propTypes = {
  daily: PropTypes.object,
  reportDaily: PropTypes.object,
  color: PropTypes.string,
};

function NotableSentences({ notes }) {
  const theme = useTheme();
  if (!notes.length) return null;
  return (
    <Stack component="ul" spacing={0.5} sx={{ m: 0, p: 0, listStyle: "none" }}>
      {notes.map((note) => {
        const higher = note.value > note.rest;
        return (
          <Tooltip
            key={`${note.metric}-${note.kind}`}
            arrow
            placement="top-start"
            describeChild
            title={notableTooltip(note)}
          >
            <Stack
              component="li"
              direction="row"
              spacing={0.75}
              alignItems="flex-start"
            >
              <Iconify
                icon={higher ? "mdi:arrow-up" : "mdi:arrow-down"}
                width={14}
                sx={{
                  mt: "3px",
                  flexShrink: 0,
                  color: toneColor(theme, notableDirection(note)),
                }}
              />
              <Typography variant="body2" sx={{ fontSize: 13 }}>
                {notableText(note)}
              </Typography>
            </Stack>
          </Tooltip>
        );
      })}
    </Stack>
  );
}

NotableSentences.propTypes = { notes: PropTypes.array.isRequired };

function RootCause({ insight }) {
  if (!insight?.root_cause) return null;
  const quote = insight.evidence?.find((e) => e?.quote);
  return (
    <Box
      sx={{
        p: 1.5,
        borderRadius: 1,
        border: "1px solid",
        borderColor: "divider",
        bgcolor: "background.neutral",
        display: "grid",
        gridTemplateColumns: "88px minmax(0, 1fr)",
        rowGap: 1,
        columnGap: 1.5,
      }}
    >
      <Typography variant="caption" fontWeight={600} sx={{ pt: "2px" }}>
        Root cause
      </Typography>
      <Box>
        <Typography variant="body2" sx={{ fontSize: 13, lineHeight: "20px" }}>
          {insight.root_cause}
        </Typography>
        {quote && (
          <Stack
            direction="row"
            spacing={0.75}
            alignItems="flex-end"
            sx={{ mt: 1 }}
          >
            <EvidenceQuote quote={quote.quote} dense sx={{ flex: 1 }} />
            {quote.unit_id && <CitedUnit unitId={quote.unit_id} />}
          </Stack>
        )}
      </Box>
      {insight.fix && (
        <>
          <Typography variant="caption" fontWeight={600} sx={{ pt: "2px" }}>
            Fix
          </Typography>
          <Typography variant="body2" sx={{ fontSize: 13, lineHeight: "20px" }}>
            {insight.fix}
          </Typography>
        </>
      )}
    </Box>
  );
}

RootCause.propTypes = { insight: PropTypes.object };

// A citation chip for a unit that may or may not have a report number.
function CitedUnit({ unitId }) {
  const report = useReport();
  let n = null;
  report.refIndex.forEach((entry, key) => {
    if (n === null && entry.unitId === unitId) n = key;
  });
  if (n !== null) return <CitationChip n={n} unitId={unitId} />;
  return (
    <Button
      size="small"
      color="inherit"
      onClick={() => report.openExample(unitId)}
      sx={{ fontSize: 12, fontWeight: 500, flexShrink: 0, minWidth: 0 }}
    >
      Open
    </Button>
  );
}

CitedUnit.propTypes = { unitId: PropTypes.string.isRequired };

// ─── Tables ─────────────────────────────────────────────────────────────────

const OUTCOME_TONE = {
  resolved: "good",
  partially_resolved: "neutral",
  escalated: "neutral",
  unresolved: "bad",
  abandoned: "bad",
};

function Pager({ offset, count, total, onOffset }) {
  if (total <= UNITS_PAGE) return null;
  const last = Math.min(total, offset + count);
  return (
    <Stack
      direction="row"
      alignItems="center"
      justifyContent="flex-end"
      spacing={0.5}
      sx={{ px: 1, py: 0.5 }}
    >
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ fontVariantNumeric: "tabular-nums" }}
      >
        {formatCount(offset + 1)}–{formatCount(last)} of {formatCount(total)}
      </Typography>
      <IconButton
        size="small"
        aria-label="Previous page"
        disabled={offset === 0}
        onClick={() => onOffset(Math.max(0, offset - UNITS_PAGE))}
      >
        <Iconify icon="mdi:chevron-left" width={18} />
      </IconButton>
      <IconButton
        size="small"
        aria-label="Next page"
        disabled={last >= total}
        onClick={() => onOffset(offset + UNITS_PAGE)}
      >
        <Iconify icon="mdi:chevron-right" width={18} />
      </IconButton>
    </Stack>
  );
}

Pager.propTypes = {
  offset: PropTypes.number.isRequired,
  count: PropTypes.number.isRequired,
  total: PropTypes.number.isRequired,
  onOffset: PropTypes.func.isRequired,
};

const tableSx = {
  tableLayout: "fixed",
  "& td, & th": { borderColor: "divider" },
  "& thead th": {
    bgcolor: "background.neutral",
    color: "text.secondary",
    fontSize: 12,
    fontWeight: 500,
    py: 0.75,
    whiteSpace: "nowrap",
  },
  "& tbody td": { fontSize: 13, verticalAlign: "top", py: 1 },
  "& tbody tr": { cursor: "pointer" },
};

const clamp = (lines) => ({
  display: "-webkit-box",
  WebkitLineClamp: lines,
  WebkitBoxOrient: "vertical",
  overflow: "hidden",
});

function SummariesTable({ units, onOpen }) {
  const hasAttributes = units.some(
    (u) => Object.keys(u.facets?.attributes || {}).length > 0,
  );
  return (
    <Table size="small" sx={tableSx}>
      <TableHead>
        <TableRow>
          <TableCell>Summary</TableCell>
          <TableCell sx={{ width: 124 }}>Outcome</TableCell>
          <TableCell align="right" sx={{ width: 84 }}>
            Sentiment
          </TableCell>
          <TableCell sx={{ width: 190 }}>Failure</TableCell>
          {hasAttributes && (
            <TableCell sx={{ width: 180 }}>Attributes</TableCell>
          )}
        </TableRow>
      </TableHead>
      <TableBody>
        {units.map((unit) => {
          const facets = unit.facets || {};
          const answer =
            unit.lens_answer && unit.lens_answer !== unit.summary
              ? unit.lens_answer
              : "";
          return (
            <TableRow
              key={unit.unit_id}
              hover
              tabIndex={0}
              onClick={() => onOpen(unit.unit_id)}
              onKeyDown={(event) => {
                if (event.key === "Enter") onOpen(unit.unit_id);
              }}
              sx={{
                "&:focus-visible": {
                  outline: "2px solid",
                  outlineColor: "primary.main",
                  outlineOffset: -2,
                },
              }}
            >
              <TableCell>
                <Tooltip
                  arrow
                  placement="top-start"
                  enterDelay={500}
                  describeChild
                  title={answer ? `Answer: ${answer}` : ""}
                >
                  <Box>
                    <Typography
                      variant="body2"
                      sx={{ fontSize: 13, ...clamp(2) }}
                    >
                      {unit.summary || unit.label || unit.excerpt}
                    </Typography>
                    {answer && (
                      <Typography
                        variant="caption"
                        color="text.secondary"
                        sx={{ display: "block", ...clamp(1) }}
                      >
                        {answer}
                      </Typography>
                    )}
                  </Box>
                </Tooltip>
              </TableCell>
              <TableCell>
                {facets.outcome ? (
                  <ToneChip
                    label={humanizeValue(facets.outcome)}
                    tone={OUTCOME_TONE[facets.outcome] || "neutral"}
                    sx={{ height: 20, fontSize: 11 }}
                  />
                ) : (
                  "–"
                )}
              </TableCell>
              <TableCell
                align="right"
                sx={{ fontVariantNumeric: "tabular-nums" }}
              >
                {Number.isFinite(facets.sentiment)
                  ? `${facets.sentiment}/5`
                  : "–"}
              </TableCell>
              <TableCell>
                <Typography
                  variant="body2"
                  color={facets.failure_mode ? "text.primary" : "text.disabled"}
                  sx={{ fontSize: 12, ...clamp(2) }}
                >
                  {facets.failure_mode
                    ? humanizeValue(facets.failure_mode)
                    : "None"}
                </Typography>
              </TableCell>
              {hasAttributes && (
                <TableCell>
                  <AttributePills attributes={facets.attributes} />
                </TableCell>
              )}
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}

SummariesTable.propTypes = {
  units: PropTypes.array.isRequired,
  onOpen: PropTypes.func.isRequired,
};

function ItemsTable({ units, unit, onOpen }) {
  return (
    <Table size="small" sx={tableSx}>
      <TableHead>
        <TableRow>
          <TableCell sx={{ width: 128 }}>Time</TableCell>
          <TableCell sx={{ width: 140 }}>User</TableCell>
          <TableCell>{unit === "traces" ? "Input" : "First message"}</TableCell>
          <TableCell align="right" sx={{ width: 60 }}>
            Turns
          </TableCell>
          <TableCell align="right" sx={{ width: 80 }}>
            Latency
          </TableCell>
          <TableCell align="right" sx={{ width: 72 }}>
            Cost
          </TableCell>
          <TableCell align="right" sx={{ width: 64 }}>
            Errors
          </TableCell>
          <TableCell sx={{ width: 36 }} />
        </TableRow>
      </TableHead>
      <TableBody>
        {units.map((row) => {
          const metrics = row.metrics || {};
          return (
            <TableRow
              key={row.unit_id}
              hover
              tabIndex={0}
              onClick={() => onOpen(row)}
              onKeyDown={(event) => {
                if (event.key === "Enter") onOpen(row);
              }}
              sx={{
                "&:hover .go": { color: "primary.main" },
                "&:focus-visible": {
                  outline: "2px solid",
                  outlineColor: "primary.main",
                  outlineOffset: -2,
                },
                "& td": { fontVariantNumeric: "tabular-nums" },
              }}
            >
              <TableCell sx={{ whiteSpace: "nowrap", color: "text.secondary" }}>
                {formatWhen(row.time)}
              </TableCell>
              <TableCell>
                <Typography
                  variant="body2"
                  noWrap
                  sx={{ fontSize: 13, maxWidth: 140 }}
                  title={row.user_id}
                >
                  {row.user_id || "–"}
                </Typography>
              </TableCell>
              <TableCell>
                <Typography variant="body2" sx={{ fontSize: 13, ...clamp(1) }}>
                  {row.excerpt || row.label || "–"}
                </Typography>
              </TableCell>
              <TableCell align="right">{formatCount(metrics.turns)}</TableCell>
              <TableCell align="right">
                {formatLatency(metrics.latency_ms)}
              </TableCell>
              <TableCell align="right">
                {metrics.cost > 0 ? formatCost(metrics.cost) : "–"}
              </TableCell>
              <TableCell
                align="right"
                sx={{ color: metrics.errors > 0 ? "error.main" : undefined }}
              >
                {formatCount(metrics.errors)}
              </TableCell>
              <TableCell padding="none">
                <Iconify
                  className="go"
                  icon="mdi:arrow-top-right"
                  width={16}
                  sx={{ color: "text.disabled", mt: "10px" }}
                />
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}

ItemsTable.propTypes = {
  units: PropTypes.array.isRequired,
  unit: PropTypes.string,
  onOpen: PropTypes.func.isRequired,
};

// ─── Explorer ───────────────────────────────────────────────────────────────

function MoreMenu({ disabled, busy, onPatterns, onGrid, canGrid }) {
  const [anchor, setAnchor] = useState(null);
  return (
    <>
      <IconButton
        size="small"
        aria-label="More actions for this category"
        onClick={(event) => setAnchor(event.currentTarget)}
        disabled={disabled}
        sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1 }}
      >
        {busy ? (
          <CircularProgress size={16} thickness={5} />
        ) : (
          <Iconify icon="mdi:dots-horizontal" width={18} />
        )}
      </IconButton>
      <Menu
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
      >
        <MenuItem
          onClick={() => {
            setAnchor(null);
            onPatterns();
          }}
        >
          <ListItemIcon>
            <Iconify icon="mdi:file-tree-outline" width={18} />
          </ListItemIcon>
          <ListItemText
            primary="Find patterns inside"
            secondary="A new report on just this category"
            secondaryTypographyProps={{ variant: "caption" }}
          />
        </MenuItem>
        {canGrid && (
          <MenuItem
            onClick={() => {
              setAnchor(null);
              onGrid();
            }}
          >
            <ListItemIcon>
              <Iconify icon="mdi:table-large" width={18} />
            </ListItemIcon>
            <ListItemText
              primary="Open all in grid"
              secondary="The Traces view filtered to these"
              secondaryTypographyProps={{ variant: "caption" }}
            />
          </MenuItem>
        )}
      </Menu>
    </>
  );
}

MoreMenu.propTypes = {
  disabled: PropTypes.bool,
  busy: PropTypes.bool,
  onPatterns: PropTypes.func.isRequired,
  onGrid: PropTypes.func.isRequired,
  canGrid: PropTypes.bool,
};

/**
 * A category up close, LangSmith-style: the category tree on the left; on
 * the right its numbers, what sets it apart, its root cause and every item
 * in it as summaries or raw rows.
 */
export default function CategoryExplorer({
  categoryId,
  reportName,
  run,
  lens,
  onBack,
}) {
  const navigate = useNavigate();
  const report = useReport();
  const category = report.categoriesById.get(categoryId);
  const parent = category?.parent_id
    ? report.categoriesById.get(category.parent_id)
    : null;
  const [view, setView] = useState("summaries");
  const [offset, setOffset] = useState(0);
  const unit = run.unit;
  const plural = (UNITS[unit] || UNITS.traces).plural;
  const totals = run.result?.totals || {};
  // The same units whichever id opened it: a folded sub opens its top.
  const params =
    category?.level === 2
      ? { sub_category_id: category.id }
      : { category_id: category?.id || categoryId };
  const units = useRunUnits(run.id, { ...params, offset, limit: UNITS_PAGE });
  const collecting = useCancellable("The examples could not be collected.");
  // Instant eval is Enterprise: without it, Measure is marked and opens its
  // gate.
  const askDenied = useFeatureDenied(CAPABILITY.INSTANT_EVAL);

  if (!category)
    return (
      <Box sx={{ px: 3, py: 2.5 }}>
        <Button
          size="small"
          color="inherit"
          onClick={onBack}
          startIcon={<Iconify icon="mdi:arrow-left" width={16} />}
        >
          Back to report
        </Button>
        <Alert severity="info" sx={{ mt: 2 }}>
          This category is not part of this run. It may come from another run of
          the report.
        </Alert>
      </Box>
    );

  const stats = category.stats || {};
  const color = report.colorOf(category.id);
  const notes = sortNotable(category.notable);
  const rows = units.data?.units || [];
  const total = units.data?.total || 0;

  // Over the rows the report read: its filters and date (a preset's window
  // moved to now).
  const measure = () =>
    navigate(
      askLink(report.projectId, {
        ...askSeed(run.scope, unit),
        unit,
        source: `the report “${reportName}”`,
        questions: [
          {
            question:
              `Is this an example of “${category.name}”? ${category.description || ""}`.trim(),
            answerType: "yes_no",
          },
        ],
      }),
    );

  const findPatterns = () =>
    collecting.run(async (signal) => {
      const all = await collectRunUnits(run.id, params, signal);
      navigate(
        insightsReportLink(report.projectId, {
          unit,
          population: all.map((row) => ({
            id: row.unit_id,
            time: row.time || "",
            user_id: row.user_id || "",
          })),
          question: `Within “${category.name}”, what distinct patterns are there and what causes them?`,
          label: `${category.name} · ${reportName}`,
          source: reportName,
        }),
      );
      return all.length;
    });

  const openGrid = () =>
    collecting.run(async (signal) => {
      const all = await collectRunUnits(run.id, params, signal);
      const href = unitsGridLink(report.projectId, unit, all);
      if (href) navigate(href);
      return all.length;
    });

  const openItem = (row) => {
    const link = unitLink(report.projectId, unit, row, {
      window: report.runWindow,
    });
    if (link) navigate(link.href);
    else report.openExample(row.unit_id);
  };

  return (
    <Box sx={{ display: "flex", minHeight: "100%" }}>
      <Box
        sx={{
          width: TREE_WIDTH,
          flexShrink: 0,
          borderRight: "1px solid",
          borderColor: "divider",
          px: 1.5,
          pt: 1.5,
          pb: 3,
        }}
      >
        <Button
          size="small"
          color="inherit"
          onClick={onBack}
          startIcon={<Iconify icon="mdi:arrow-left" width={16} />}
          sx={{ mb: 1.5, fontWeight: 500, color: "text.secondary" }}
        >
          Back to report
        </Button>
        <Box sx={{ position: "sticky", top: 12 }}>
          <CategoryTree key={categoryId} categoryId={categoryId} />
        </Box>
      </Box>

      <Box sx={{ flex: 1, minWidth: 0, px: 3, pt: 2, pb: 4 }}>
        <Typography
          variant="caption"
          color="text.secondary"
          component="div"
          noWrap
          sx={{ mb: 0.5 }}
        >
          {reportName}
          {parent ? ` / ${parent.name}` : ""}
        </Typography>
        <Stack direction="row" alignItems="center" spacing={1.5}>
          <Swatch color={color} size={12} />
          <Typography
            variant="m2"
            component="h2"
            fontWeight={600}
            sx={{ minWidth: 0, flex: 1 }}
          >
            {category.name}{" "}
            <Box
              component="span"
              sx={{
                color: "text.secondary",
                fontWeight: 400,
                fontSize: 15,
                whiteSpace: "nowrap",
              }}
            >
              ({formatCount(stats.count)} {unitWord(unit, stats.count)})
            </Box>
          </Typography>
          <Tooltip
            arrow
            title={
              stats.share_ci
                ? `95% CI ${formatPercent(stats.share_ci[0])}–${formatPercent(stats.share_ci[1])}`
                : ""
            }
          >
            <Stack
              direction="row"
              spacing={1}
              alignItems="center"
              sx={{ flexShrink: 0 }}
            >
              <Box
                sx={{
                  width: 96,
                  height: 8,
                  borderRadius: 4,
                  bgcolor: "action.hover",
                  overflow: "hidden",
                }}
              >
                <Box
                  sx={{
                    width: `${(stats.share || 0) * 100}%`,
                    height: "100%",
                    bgcolor: color,
                    borderRadius: 4,
                  }}
                />
              </Box>
              <Typography
                variant="body2"
                sx={{ fontVariantNumeric: "tabular-nums", fontWeight: 600 }}
              >
                {formatPercent(stats.share)}
              </Typography>
            </Stack>
          </Tooltip>
        </Stack>
        {category.description && (
          <Typography
            variant="body2"
            color="text.secondary"
            sx={{ mt: 0.75, fontSize: 13, maxWidth: 820 }}
          >
            {category.description}
          </Typography>
        )}

        <Box
          sx={{
            mt: 2,
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(118px, 1fr))",
            gap: 1,
          }}
        >
          {kpis(stats, totals, unit, lens).map((kpi) => (
            <Kpi
              key={kpi.key}
              label={kpi.label}
              value={kpi.value}
              compare={kpi.compare}
              tone={kpi.tone}
            />
          ))}
        </Box>

        {(notes.length > 0 || stats.daily) && (
          <Stack
            direction="row"
            spacing={3}
            alignItems="flex-start"
            sx={{ mt: 2 }}
          >
            <Box sx={{ flex: 1, minWidth: 0 }}>
              {notes.length > 0 ? (
                <>
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    component="div"
                    sx={{ mb: 0.5 }}
                  >
                    Compared with the rest of the report
                  </Typography>
                  <NotableSentences notes={notes} />
                </>
              ) : (
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ fontSize: 13 }}
                >
                  Nothing here differs clearly from the rest of the report once
                  every comparison is accounted for.
                </Typography>
              )}
            </Box>
            <DailyBars
              daily={stats.daily}
              reportDaily={totals.daily}
              color={color}
            />
          </Stack>
        )}

        {category.insight?.root_cause && (
          <Box sx={{ mt: 2 }}>
            <RootCause insight={category.insight} />
          </Box>
        )}

        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          sx={{ mt: 3, mb: 1.25 }}
        >
          <ToggleButtonGroup
            exclusive
            size="small"
            value={view}
            onChange={(_, value) => value && setView(value)}
            aria-label="Show"
          >
            <ToggleButton
              value="summaries"
              sx={{ px: 1.5, py: 0.25, fontSize: 13 }}
            >
              Summaries
            </ToggleButton>
            <ToggleButton
              value="items"
              sx={{ px: 1.5, py: 0.25, fontSize: 13 }}
            >
              {humanizeKey(plural)}
            </ToggleButton>
          </ToggleButtonGroup>
          <Typography variant="caption" color="text.secondary" sx={{ flex: 1 }}>
            {view === "summaries"
              ? "Click a row to read it with its evidence"
              : "Click a row to open it in Observe"}
          </Typography>
          <Tooltip
            arrow
            describeChild
            title={
              askDenied
                ? "Instant eval is an Enterprise feature."
                : `Opens Instant eval on ${plural} with this as a yes/no question, to measure it across all your traffic and keep tracking it.`
            }
          >
            <Button
              size="small"
              variant="outlined"
              startIcon={<Iconify icon="mdi:creation-outline" width={16} />}
              endIcon={askDenied ? <EnterpriseMark compact /> : undefined}
              onClick={measure}
              sx={{ fontSize: 13 }}
            >
              Measure with Jev
            </Button>
          </Tooltip>
          <MoreMenu
            disabled={!stats.count || collecting.state.status === "busy"}
            busy={collecting.state.status === "busy"}
            onPatterns={findPatterns}
            onGrid={openGrid}
            canGrid={unit !== "users"}
          />
        </Stack>
        {collecting.state.status === "error" && (
          <Alert severity="error" onClose={collecting.reset} sx={{ mb: 1.25 }}>
            {collecting.state.error}
          </Alert>
        )}

        <Box
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
            overflow: "hidden",
          }}
        >
          {units.isLoading ? (
            <Stack spacing={1} sx={{ p: 1.5 }}>
              {[0, 1, 2, 3, 4].map((i) => (
                <Skeleton key={i} variant="rounded" height={40} />
              ))}
            </Stack>
          ) : units.isError ? (
            <Box sx={{ p: 1.5 }}>
              <ErrorState
                message={`The ${plural} could not be loaded.`}
                onRetry={() => units.refetch()}
              />
            </Box>
          ) : !rows.length ? (
            <Typography variant="body2" color="text.secondary" sx={{ p: 2 }}>
              No {plural} in this category.
            </Typography>
          ) : (
            <Box
              sx={{
                opacity: units.isPlaceholderData ? 0.6 : 1,
                overflowX: "auto",
              }}
            >
              {view === "summaries" ? (
                <SummariesTable units={rows} onOpen={report.openExample} />
              ) : (
                <ItemsTable units={rows} unit={unit} onOpen={openItem} />
              )}
              <Pager
                offset={offset}
                count={rows.length}
                total={total}
                onOffset={setOffset}
              />
            </Box>
          )}
        </Box>
      </Box>
    </Box>
  );
}

CategoryExplorer.propTypes = {
  categoryId: PropTypes.string.isRequired,
  reportName: PropTypes.string,
  run: PropTypes.object.isRequired,
  lens: PropTypes.string,
  onBack: PropTypes.func.isRequired,
};
