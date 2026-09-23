import PropTypes from "prop-types";
import { memo, useContext, useEffect, useMemo, useState } from "react";
import { DrilldownContext } from "./drilldownContext";
import { useTheme, alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Table, TableHead, TableRow, TableCell, TableBody, Tooltip, Popover, Dialog, IconButton } from "@mui/material";
import Iconify from "src/components/iconify";
import ReactApexChart from "../components/SafeApexChart";
import { attribute, isMeasured, DOMAINS } from "../_mock/failures";
import { deriveUseCaseLabel, csatOf, latencyOf as agentLatencyOf } from "./TraceTable";
import { attachPersonaDims } from "./personaDimensions";
import { deriveToolCalls } from "./toolCalls.js";
import { CustomWidgetBody } from "./layout/customWidgetRenderer";
import { GoldenSetManager, DropoffFunnel, DivergenceTimeline, PitchBreakThemes, useGoldenSet } from "./goldenSet.jsx";
import { useRunLayout, readViewFromUrl, writeViewToUrl } from "./layout/useRunLayout";
import { SECTIONS, getPanelMeta, panelSection, snapshotAsCustom } from "./layout/panelRegistry";
import HiddenWidgetsPopover from "./layout/HiddenWidgetsPopover";
import ViewTabBar from "./layout/ViewTabBar";
import SortablePanel from "./layout/SortablePanel";
import SortableSection from "./layout/SortableSection";
import WidgetEditor from "./layout/WidgetEditor";
import {
  DndContext, DragOverlay, closestCenter, PointerSensor, KeyboardSensor,
  useSensor, useSensors,
} from "@dnd-kit/core";
import { SortableContext, arrayMove, sortableKeyboardCoordinates, rectSortingStrategy } from "@dnd-kit/sortable";

/**
 * Analytics — enterprise density. Prioritises numbers and small
 * charts over ornamentation. Reads like a monitoring console: a KPI
 * strip on top, a distribution chart beside sortable eval / attribution
 * tables, then a scatter of the run's tail. No decorative icons, no
 * per-row progress bars, no colored callout cards. Colour is reserved
 * for state (pass/fail/warn) and applied to text only.
 */

const RED   = "#DC2626";
const GREEN = "#16A34A";
const AMBER = "#CA8A04";
/* Softened variants for chart fills — the KPI/table colors stay the
   full-strength state hues, but chart bars/dots use a slightly less
   saturated anchor plus a top-down gradient wash so the visual noise
   drops but the state (pass/fail) is still unmistakable. */
const CHART_GREEN = "#34D399";
const CHART_RED   = "#F87171";

const numFmt = new Intl.NumberFormat();

/**
 * Tiny CSV emitter — no dep, no server round-trip. Takes an array
 * of homogenous objects, unions the keys (row 0 wins ties), quotes
 * anything with a comma / quote / newline, prompts the browser to
 * save it. Used by every Panel's export button.
 */
function downloadCsv(filename, rows) {
  if (!Array.isArray(rows) || rows.length === 0) return;
  const cols = Array.from(rows.reduce((set, r) => {
    Object.keys(r || {}).forEach((k) => set.add(k));
    return set;
  }, new Set()));
  const escape = (v) => {
    if (v == null) return "";
    const s = String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const body = [
    cols.join(","),
    ...rows.map((r) => cols.map((c) => escape(r[c])).join(",")),
  ].join("\n");
  const blob = new Blob([body], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

/* Some mock tasks carry `latencyMs`, others only `durationMs`. Read one,
   fall back to the other so the latency KPI / histogram / task-latency
   line render for every task shape. */
const latencyOf = (t) => Number(t?.latencyMs || t?.durationMs || 0);

/*
  Derive sentiment / disconnection-reason categories from task metadata.
  Real product would read these from evaluator output; the mocks don't
  carry them, so we derive stable pseudo-values from the task id +
  status so donuts stay legible across renders. Matches the shape
  competitors (Retell, Cekura) put on their dashboards.
*/
function sentimentOf(t) {
  if (t?.sentiment) return t.sentiment;
  if (t?.status === "passed") {
    return hashId(t.id || "") % 5 === 0 ? "neutral" : "positive";
  }
  if (t?.status === "error") return "negative";
  return hashId(t.id || "") % 3 === 0 ? "negative" : "neutral";
}
function endReasonOf(t) {
  if (t?.endReason) return t.endReason;
  if (t?.status === "passed")  return "complete";
  if (t?.status === "error")   return "error";
  if (t?.escalated)            return "escalated";
  const h = hashId(t.id || "") % 4;
  if (h === 0) return "timeout";
  if (h === 1) return "escalated";
  return "incomplete";
}

/* ── derivations ───────────────────────────────────────────────────── */

/* Deterministic pseudo-random per task id so mock voice metrics stay
   stable across renders instead of shimmering. */
function hashId(id) {
  const s = String(id || "");
  let h = 0;
  for (let i = 0; i < s.length; i += 1) h = ((h << 5) - h + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}
const jitter = (id, salt, base, spread) => {
  const h = hashId(`${id}·${salt}`);
  return base + (h % spread);
};

const pct = (a, b) => (b ? Math.round((a / b) * 100) : 0);

/* Percentiles from a sorted numeric array. Empty arrays return 0 so
   the KPI never renders NaN or blank while data is warming up. */
function percentile(arr, p) {
  if (!arr || arr.length === 0) return 0;
  const idx = Math.min(arr.length - 1, Math.floor((p / 100) * arr.length));
  return arr[idx];
}
const sortedNums = (tasks, sel) => tasks.map(sel).filter((v) => v > 0).sort((a, b) => a - b);

/* Business outcomes the CEO reads, not the QA lens. */
function deriveBusiness(tasks) {
  const measured = tasks.filter(isMeasured);
  const passed = measured.filter((t) => t.status === "passed");
  const failed = measured.filter((t) => t.status !== "passed");
  /* Escalations: any task whose trace touched escalate_to_human or
     whose fault attribution landed on agent behaviour requiring a
     human hand-off. Falls back to a deterministic 12% share on
     measured failures so the tile never reads empty in demos. */
  const escalated = tasks.filter((t) => {
    const toolCalls = (t.steps || []).flatMap((s) => s.tools || []);
    if (toolCalls.some((c) => /escalate_to_human/.test(c.name || ""))) return true;
    if (t.status !== "passed" && attribute(t)?.domain === "agent") {
      return hashId(t.id || "") % 100 < 45;
    }
    return false;
  }).length;
  const compliancePassed = measured.filter((t) => {
    const r = (t.evalResults || []).find((e) => /compliance|policy|pii|hallucination/i.test(e.name || e.evalId || ""));
    if (!r) return t.status === "passed";
    return r.passed;
  }).length;
  const totalCost = tasks.reduce((a, t) => a + (t.cost || 0), 0);
  const passedCount = passed.length;
  const durations = sortedNums(tasks, (t) => t.durationMs || 0);
  return {
    total: tasks.length,
    measured: measured.length,
    passed: passedCount,
    failed: failed.length,
    escalated,
    resolutionRate: pct(passedCount, measured.length),
    escalationRate: pct(escalated, tasks.length),
    containmentRate: 100 - pct(escalated, tasks.length),
    complianceRate: pct(compliancePassed, measured.length),
    totalCost,
    costPerPass: passedCount ? totalCost / passedCount : 0,
    costPerResolution: passedCount ? totalCost / passedCount : 0,
    medHandleTimeS: percentile(durations, 50) / 1000,
  };
}

/* Voice-only latency shape. If a task carries `latencyBreakdown` we
   use that; otherwise we derive a deterministic split from latencyMs
   so the panel demos cleanly. */
function deriveVoiceLatency(tasks) {
  const withLat = tasks.filter((t) => latencyOf(t) > 0);
  if (withLat.length === 0) return null;
  const ttfw = [], llm = [], tts = [], asr = [];
  const interrupts = []; const wer = [];
  withLat.forEach((t) => {
    const total = latencyOf(t);
    const b = t.latencyBreakdown || {};
    ttfw.push(b.ttfw || Math.round(total * 0.35 + jitter(t.id, "ttfw", 0, 120)));
    llm.push(b.llm || Math.round(total * 0.45 + jitter(t.id, "llm", 0, 180)));
    tts.push(b.tts || Math.round(total * 0.15 + jitter(t.id, "tts", 0, 60)));
    asr.push(b.asr || Math.round(total * 0.05 + jitter(t.id, "asr", 0, 40)));
    interrupts.push(jitter(t.id, "int", 0, 4));
    wer.push(jitter(t.id, "wer", 3, 9) / 100);
  });
  const sort = (a) => [...a].sort((x, y) => x - y);
  const s = sort(ttfw); const sl = sort(llm); const st = sort(tts); const sa = sort(asr);
  const avgWer = wer.reduce((a, x) => a + x, 0) / wer.length;
  const totalInterrupts = interrupts.reduce((a, x) => a + x, 0);
  return {
    ttfw: { p50: percentile(s, 50), p90: percentile(s, 90), p99: percentile(s, 99) },
    llm:  { p50: percentile(sl, 50), p90: percentile(sl, 90), p99: percentile(sl, 99) },
    tts:  { p50: percentile(st, 50), p90: percentile(st, 90), p99: percentile(st, 99) },
    asr:  { p50: percentile(sa, 50), p90: percentile(sa, 90), p99: percentile(sa, 99) },
    werPct: avgWer * 100,
    interrupts: totalInterrupts,
    interruptsPerTask: totalInterrupts / withLat.length,
  };
}

/* Failure pattern clustering: group failing tasks by (domain, use
   case) so the top clusters read as concrete themes rather than
   opaque bucket ids. */
function deriveFailureClusters(tasks, limit = 4) {
  const buckets = new Map();
  tasks.forEach((t) => {
    if (t.status === "passed" || !isMeasured(t)) return;
    const dom = attribute(t)?.domain || "agent";
    const uc = deriveUseCaseLabel(t) || "Uncategorised";
    const key = `${dom}::${uc}`;
    const cur = buckets.get(key) || { domain: dom, useCase: uc, count: 0, tasks: [] };
    cur.count += 1; cur.tasks.push(t);
    buckets.set(key, cur);
  });
  return [...buckets.values()].sort((a, b) => b.count - a.count).slice(0, limit);
}

/* ── kpi strip ─────────────────────────────────────────────────────── */

/* HeroKpi lives below KpiStrip — a Vercel Analytics-style card with
   caption label, big value, colored delta chip and a gradient area
   sparkline. Kept as a function declaration so both KpiStrip (which
   uses it) and any future callers can reference it before its own
   line in file order. */

/* ── dashboard header ─────────────────────────────────────────────── */

/* Bland/Cekura-style toolbar above the analytics grid. Non-functional
   controls in the prototype — but the shape is what makes the page
   feel like a real analytics product: dashboard name on the left, a
   date-range pill + time-bucket toggle + Filters + Add Panel + Edit
   on the right. */
/**
 * Quieter dashboard header — the earlier toolbar (Add panel / Edit /
 * bucket toggle / filters) played "look at me, I'm Datadog" but none
 * of those controls did anything. Dropped for the prototype so the
 * KPI row can carry the top of the page. A single Filters pill on
 * the right stays because it plausibly reads as "scope the view".
 */
/*
  Retell-parity filter + breakdown vocabulary. Grouped into the same
  four tabs Retell uses. Each field carries `values` (options users can
  pick from) and `test` (a predicate applied to a task). Fields without
  `test` are the UI-only vocab surfacing — the real product would drive
  them from server-side scoping.
*/
const FILTER_TABS = [
  {
    id: "base", label: "Base",
    fields: [
      { key: "Successful",           values: ["Yes", "No"],
        test: (t, v) => (v === "Yes" ? t.status === "passed" : t.status !== "passed") },
      { key: "Status",               values: ["Passed", "Failed", "Errored", "Escalated"],
        test: (t, v) => (v === "Escalated" ? !!t.escalated : t.status === v.toLowerCase()) },
      { key: "User sentiment",       values: ["Positive", "Neutral", "Negative"],
        test: (t, v) => sentimentOf(t) === v.toLowerCase() },
      { key: "Disconnection reason", values: ["Task complete", "Escalated", "Incomplete", "Timeout", "Error"],
        test: (t, v) => {
          const map = { "Task complete": "complete", Escalated: "escalated", Incomplete: "incomplete", Timeout: "timeout", Error: "error" };
          return endReasonOf(t) === map[v];
        } },
      { key: "Duration",             values: ["< 5s", "5–15s", "> 15s"],
        test: (t, v) => {
          const s = (t.durationMs || 0) / 1000;
          if (v === "< 5s") return s < 5;
          if (v === "5–15s") return s >= 5 && s <= 15;
          return s > 15;
        } },
      { key: "End-to-end latency",   values: ["< 1s", "1–5s", "> 5s"],
        test: (t, v) => {
          const s = latencyOf(t) / 1000;
          if (v === "< 1s") return s < 1;
          if (v === "1–5s") return s >= 1 && s <= 5;
          return s > 5;
        } },
      { key: "Combined cost",        values: ["< $0.05", "$0.05–$0.20", "> $0.20"],
        test: (t, v) => {
          const c = t.cost || 0;
          if (v === "< $0.05") return c < 0.05;
          if (v === "$0.05–$0.20") return c >= 0.05 && c <= 0.20;
          return c > 0.20;
        } },
      { key: "Agent",                values: ["Any"] },
      { key: "Task ID",              values: ["Any"] },
      { key: "Batch ID",             values: ["Any"] },
      { key: "Type",                 values: ["Any"] },
    ],
  },
  {
    id: "post-call", label: "Post-call",
    fields: [
      { key: "Policy adherence", values: ["Pass", "Fail"] },
      { key: "Task success",     values: ["Pass", "Fail"] },
      { key: "Summary keywords", values: ["Any"] },
      { key: "Extracted entities", values: ["Any"] },
    ],
  },
  {
    id: "metadata", label: "Metadata",
    fields: [
      { key: "Environment",    values: ["Any"] },
      { key: "Agent version",  values: ["Any"] },
      { key: "Run trigger",    values: ["Any"] },
      { key: "Author",         values: ["Any"] },
    ],
  },
  {
    id: "dynamic", label: "Dynamic",
    fields: [
      { key: "persona.role",       values: ["Any"] },
      { key: "persona.age",        values: ["Any"] },
      { key: "scenario.critical",  values: ["true", "false"] },
      { key: "scenario.mode",      values: ["Any"] },
    ],
  },
];

const BREAKDOWN_FIELDS = [
  "Agent", "Agent version", "Disconnection reason",
  "Status", "Successful", "Type", "Sentiment",
];

/** Lookup a filter field's definition by its `key`. */
function findFilterField(key) {
  for (const tab of FILTER_TABS) {
    const f = tab.fields.find((x) => x.key === key);
    if (f) return f;
  }
  return null;
}

/** Apply the currently-active filters to a task list. Filters combine
 *  with AND across fields, OR within a field's multiple values. */
export function applyFilters(tasks, activeFilters) {
  if (!activeFilters?.length) return tasks;
  return tasks.filter((t) =>
    activeFilters.every(({ field, value }) => {
      const def = findFilterField(field);
      if (!def || !def.test) return true; // vocab-only fields don't filter
      return def.test(t, value);
    })
  );
}

function DashboardHeader({
  dashboardName = "Run analytics",
  activeFilters = [], onFiltersChange,
  activeBreakdowns = [], onBreakdownsChange,
}) {
  const [filterAnchor, setFilterAnchor] = useState(null);
  const [breakdownAnchor, setBreakdownAnchor] = useState(null);
  const [filterTab, setFilterTab] = useState("base");
  /* Field being value-picked. When set, the popover shows the value list
     for that field instead of the field list. */
  const [pendingField, setPendingField] = useState(null);

  const setFilters = (next) => onFiltersChange?.(next);
  const setBreakdowns = (next) => onBreakdownsChange?.(next);

  const addFilter = (field, value) => {
    if (activeFilters.some((f) => f.field === field && f.value === value)) return;
    setFilters([...activeFilters, { field, value }]);
  };
  const removeFilter = (field, value) => {
    setFilters(activeFilters.filter((f) => !(f.field === field && f.value === value)));
  };

  const toggleBreakdown = (label) => {
    setBreakdowns(
      activeBreakdowns.includes(label)
        ? activeBreakdowns.filter((b) => b !== label)
        : [...activeBreakdowns, label]
    );
  };

  const currentTab = FILTER_TABS.find((t) => t.id === filterTab) || FILTER_TABS[0];
  const closeFilter = () => { setFilterAnchor(null); setPendingField(null); };

  return (
    <Stack spacing={1} sx={{ mb: 0.25 }}>
      <Stack direction="row" alignItems="center" spacing={1}>
        <Typography sx={{
          fontSize: 13, fontWeight: 600, color: "text.subtitle",
          textTransform: "uppercase", letterSpacing: 0.6,
        }}>
          {dashboardName}
        </Typography>
        <Box sx={{ flex: 1 }} />
        <HeaderPill
          icon="solar:filter-linear"
          label={activeFilters.length ? `Filter · ${activeFilters.length}` : "Filter"}
          primary={activeFilters.length > 0}
          onClick={(e) => setFilterAnchor(e.currentTarget)}
        />
        <HeaderPill
          icon="solar:widget-2-linear"
          label={activeBreakdowns.length ? `Breakdown · ${activeBreakdowns.length}` : "Breakdown"}
          primary={activeBreakdowns.length > 0}
          onClick={(e) => setBreakdownAnchor(e.currentTarget)}
        />
      </Stack>

      {/* Chip strip — active filters + breakdowns, each with × to remove. */}
      {(activeFilters.length > 0 || activeBreakdowns.length > 0) && (
        <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
          {activeFilters.map((f) => (
            <FilterChip
              key={`f-${f.field}-${f.value}`}
              label={`${f.field}: ${f.value}`}
              onRemove={() => removeFilter(f.field, f.value)}
            />
          ))}
          {activeBreakdowns.map((b) => (
            <FilterChip key={`b-${b}`} label={`Break by: ${b}`} tone="accent" onRemove={() => toggleBreakdown(b)} />
          ))}
        </Stack>
      )}

      {/* Filter popover — wider so all four tabs fit on one line. */}
      <Popover
        open={!!filterAnchor}
        anchorEl={filterAnchor}
        onClose={closeFilter}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{ paper: { sx: { width: 380, maxHeight: 440, mt: 0.5 } } }}
      >
        {pendingField ? (
          /* Value picker for the field the user just clicked. */
          <Box>
            <Stack direction="row" alignItems="center" spacing={1} sx={{
              px: 1.5, py: 1, borderBottom: "1px solid", borderColor: "divider",
            }}>
              <Box
                component="button"
                type="button"
                onClick={() => setPendingField(null)}
                sx={{
                  display: "inline-flex", alignItems: "center",
                  bgcolor: "transparent", border: "none", cursor: "pointer",
                  color: "text.subtitle", p: 0.25, borderRadius: 0.5,
                  "&:hover": { color: "text.primary", bgcolor: "action.hover" },
                }}
              >
                <Iconify icon="solar:arrow-left-linear" width={14} />
              </Box>
              <Typography sx={{ fontSize: 12, fontWeight: 600 }}>
                {pendingField.key}
              </Typography>
              <Box sx={{ flex: 1 }} />
              <Typography sx={{ fontSize: 10.5, color: "text.subtitle" }}>Choose a value</Typography>
            </Stack>
            <Box sx={{ py: 0.5 }}>
              {(pendingField.values || []).map((v) => (
                <Box
                  key={v}
                  component="button"
                  type="button"
                  onClick={() => { addFilter(pendingField.key, v); closeFilter(); }}
                  sx={{
                    display: "block", width: "100%", textAlign: "left",
                    px: 1.5, py: 0.875, cursor: "pointer",
                    bgcolor: "transparent", border: "none", color: "text.primary",
                    font: "inherit", fontSize: 13,
                    "&:hover": { bgcolor: "action.hover" },
                  }}
                >
                  {v}
                </Box>
              ))}
            </Box>
          </Box>
        ) : (
          <>
            <Stack direction="row" sx={{
              borderBottom: "1px solid", borderColor: "divider",
              px: 0.5,
            }}>
              {FILTER_TABS.map((t) => {
                const active = t.id === filterTab;
                return (
                  <Box
                    key={t.id}
                    component="button"
                    type="button"
                    onClick={() => setFilterTab(t.id)}
                    sx={{
                      px: 1.25, py: 1.25, cursor: "pointer",
                      fontSize: 12, fontWeight: active ? 700 : 500,
                      color: active ? "text.primary" : "text.subtitle",
                      bgcolor: "transparent", border: "none", font: "inherit",
                      borderBottom: "2px solid",
                      borderColor: active ? "text.primary" : "transparent",
                      whiteSpace: "nowrap",
                      "&:hover": { color: "text.primary" },
                    }}
                  >
                    {t.label}
                  </Box>
                );
              })}
            </Stack>
            <Box sx={{ py: 0.5 }}>
              {currentTab.fields.map((f) => {
                const activeCount = activeFilters.filter((af) => af.field === f.key).length;
                return (
                  <Box
                    key={f.key}
                    component="button"
                    type="button"
                    onClick={() => setPendingField(f)}
                    sx={{
                      display: "flex", alignItems: "center", gap: 1, width: "100%",
                      px: 1.5, py: 0.875, cursor: "pointer",
                      bgcolor: "transparent", border: "none", color: "text.primary",
                      font: "inherit", fontSize: 13, textAlign: "left",
                      "&:hover": { bgcolor: "action.hover" },
                    }}
                  >
                    <Iconify icon="solar:add-circle-linear" width={14} sx={{ color: "text.subtitle" }} />
                    <Typography component="span" sx={{ fontSize: 13, flex: 1 }}>{f.key}</Typography>
                    {activeCount > 0 && (
                      <Typography component="span" sx={{
                        fontSize: 10, fontWeight: 700, color: "primary.main",
                        px: 0.75, py: 0.125, borderRadius: 999, bgcolor: (t) => alpha(t.palette.primary.main, 0.12),
                      }}>
                        {activeCount}
                      </Typography>
                    )}
                    <Iconify icon="solar:alt-arrow-right-linear" width={12} sx={{ color: "text.subtitle" }} />
                  </Box>
                );
              })}
            </Box>
          </>
        )}
      </Popover>

      {/* Breakdown popover */}
      <Popover
        open={!!breakdownAnchor}
        anchorEl={breakdownAnchor}
        onClose={() => setBreakdownAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{ paper: { sx: { width: 260, mt: 0.5 } } }}
      >
        <Box sx={{ py: 0.5 }}>
          {BREAKDOWN_FIELDS.map((f) => {
            const isActive = activeBreakdowns.includes(f);
            return (
              <Stack
                key={f}
                direction="row" alignItems="center" spacing={1}
                onClick={() => toggleBreakdown(f)}
                sx={{
                  px: 1.5, py: 0.875, cursor: "pointer",
                  color: isActive ? "text.primary" : "text.primary",
                  bgcolor: isActive ? "action.selected" : "transparent",
                  "&:hover": { bgcolor: "action.hover" },
                }}
              >
                <Iconify
                  icon={isActive ? "eva:checkmark-fill" : "solar:add-circle-linear"}
                  width={14}
                  sx={{ color: isActive ? "primary.main" : "text.subtitle" }}
                />
                <Typography sx={{ fontSize: 13, flex: 1, fontWeight: isActive ? 600 : 400 }}>
                  {f}
                </Typography>
              </Stack>
            );
          })}
        </Box>
      </Popover>
    </Stack>
  );
}
DashboardHeader.propTypes = {
  dashboardName: PropTypes.string,
  activeFilters: PropTypes.array,
  onFiltersChange: PropTypes.func,
  activeBreakdowns: PropTypes.array,
  onBreakdownsChange: PropTypes.func,
};

function FilterChip({ label, tone, onRemove }) {
  const accent = tone === "accent" ? "#7857FC" : null;
  return (
    <Stack
      direction="row" alignItems="center" spacing={0.5}
      sx={{
        pl: 1, pr: 0.5, py: 0.375, borderRadius: 999,
        border: "1px solid",
        borderColor: accent ? alpha(accent, 0.4) : "divider",
        bgcolor: accent ? (t) => alpha(accent, t.palette.mode === "dark" ? 0.14 : 0.06) : "transparent",
      }}
    >
      <Typography sx={{ fontSize: 11.5, color: accent || "text.primary", fontWeight: 600 }}>
        {label}
      </Typography>
      <Box
        onClick={onRemove}
        sx={{
          width: 16, height: 16, borderRadius: 999, display: "grid", placeItems: "center", cursor: "pointer",
          color: "text.subtitle",
          "&:hover": { bgcolor: "action.hover", color: "text.primary" },
        }}
      >
        <Iconify icon="eva:close-fill" width={11} />
      </Box>
    </Stack>
  );
}
FilterChip.propTypes = { label: PropTypes.node, tone: PropTypes.string, onRemove: PropTypes.func };

/**
 * Small toolbar button — used for Export PDF / other tab-level
 * actions. Rendered as a real <button> for accessibility.
 */
function ToolbarButton({ icon, label, onClick }) {
  return (
    <Box
      component="button"
      type="button"
      onClick={onClick}
      sx={{
        display: "inline-flex", alignItems: "center", gap: 0.75,
        px: 1.5, py: 0.75, borderRadius: 1,
        border: "1px solid", borderColor: "divider",
        bgcolor: "background.paper", color: "text.primary",
        cursor: "pointer", font: "inherit",
        transition: "border-color 120ms, background-color 120ms",
        "&:hover": {
          borderColor: (t) => alpha(t.palette.text.primary, 0.4),
          bgcolor: "action.hover",
        },
      }}
    >
      <Iconify icon={icon} width={14} sx={{ color: "text.subtitle" }} />
      <Typography component="span" sx={{ fontSize: 12.5, fontWeight: 600 }}>
        {label}
      </Typography>
    </Box>
  );
}
ToolbarButton.propTypes = { icon: PropTypes.string, label: PropTypes.node, onClick: PropTypes.func };

function HeaderPill({ icon, label, primary, onClick }) {
  /* Rendered as an actual <button> — Stack-on-a-div sometimes swallows
     clicks under nested pointer-events rules; a real button always
     fires onClick and gets keyboard focus for free. currentTarget
     stays valid because React 17+ no longer pools synthetic events. */
  return (
    <Box
      component="button"
      type="button"
      onClick={onClick}
      sx={{
        display: "inline-flex", alignItems: "center", gap: 0.75,
        px: 1.25, py: 0.5, borderRadius: 0.75,
        border: "1px solid", borderColor: primary ? "text.primary" : "divider",
        bgcolor: (t) => primary ? (t.palette.mode === "dark" ? alpha(t.palette.text.primary, 0.08) : "background.paper") : "transparent",
        color: primary ? "text.primary" : "text.subtitle",
        cursor: "pointer", font: "inherit",
        transition: "border-color 120ms, background-color 120ms",
        "&:hover": { borderColor: (t) => alpha(t.palette.text.primary, 0.4) },
        "&:focus-visible": { outline: "2px solid", outlineColor: "text.primary", outlineOffset: 1 },
      }}
    >
      <Iconify icon={icon} width={13} sx={{ color: "inherit" }} />
      <Typography component="span" sx={{ typography: "s2", fontSize: 11.5, fontWeight: 600, color: "inherit" }}>
        {label}
      </Typography>
    </Box>
  );
}
HeaderPill.propTypes = { icon: PropTypes.string, label: PropTypes.node, primary: PropTypes.bool, onClick: PropTypes.func };

function BucketToggle() {
  return (
    <Stack
      direction="row"
      sx={{
        p: 0.25, borderRadius: 0.75,
        border: "1px solid", borderColor: "divider",
        bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.03 : 0.02),
      }}
    >
      {["Hour", "Day", "Week"].map((b, i) => (
        <Box key={b} sx={{
          px: 1, py: 0.375, borderRadius: 0.5, cursor: "pointer",
          typography: "s3", fontSize: 11, fontWeight: 600,
          color: i === 1 ? "text.primary" : "text.subtitle",
          bgcolor: (t) => i === 1 ? (t.palette.mode === "dark" ? alpha(t.palette.text.primary, 0.1) : "#fff") : "transparent",
        }}>
          {b}
        </Box>
      ))}
    </Stack>
  );
}

/* ── big KPI tile grid (Cekura/Bland shape) ────────────────────────── */

/* Larger, spacious tiles with a big value, coloured delta arrow, and
   an explicit "X% vs previous period" caption — matches the shape
   Cekura ships and Bland's Total Calls / Total Cost row. */
function KpiTileGrid({ tasks, biz, trend }) {
  const critical = tasks.filter((t) => t.critical && (t.status === "failed" || t.status === "error")).length;
  const latencies = sortedNums(tasks, (t) => latencyOf(t));
  const p90Lat = percentile(latencies, 90);
  const totalTokens = tasks.reduce((a, t) => a + (t.tokens || 0), 0);

  const deltaPct = (curr, prev) => {
    if (typeof prev !== "number" || prev === 0) return null;
    return Math.round(((curr - prev) / prev) * 100);
  };

  const tiles = [
    { label: "Pass rate",
      value: `${biz.resolutionRate}%`,
      deltaPct: deltaPct(biz.resolutionRate, trend?.prevPassRate),
      goodUp: true,
      tone: biz.resolutionRate >= 80 ? GREEN : biz.resolutionRate >= 50 ? AMBER : RED },
    { label: "Total runs",
      value: numFmt.format(biz.total),
      deltaPct: null },
    { label: "Escalation",
      value: `${biz.escalationRate}%`,
      deltaPct: deltaPct(biz.escalationRate, trend?.prevEscalationRate),
      goodUp: false,
      tone: biz.escalationRate <= 10 ? GREEN : biz.escalationRate <= 25 ? AMBER : RED },
    { label: "Avg duration",
      value: `${biz.medHandleTimeS.toFixed(1)}s`,
      deltaPct: null },
    { label: "Critical issues",
      value: numFmt.format(critical),
      tone: critical ? RED : GREEN,
      deltaPct: null },
    { label: "Total cost",
      value: `$${biz.totalCost.toFixed(2)}`,
      deltaPct: null },
    { label: "Cost / pass",
      value: biz.costPerPass ? `$${biz.costPerPass.toFixed(3)}` : "—",
      deltaPct: null },
    { label: "Latency p90",
      value: `${Math.round(p90Lat)}ms`,
      tone: p90Lat < 1500 ? GREEN : p90Lat < 3000 ? AMBER : RED,
      deltaPct: null },
    { label: "Compliance",
      value: `${biz.complianceRate}%`,
      tone: biz.complianceRate >= 95 ? GREEN : biz.complianceRate >= 80 ? AMBER : RED,
      deltaPct: null },
    { label: "Tokens",
      value: numFmt.format(totalTokens),
      deltaPct: null },
  ];

  return (
    <Box sx={{
      display: "grid", gap: 1.5,
      gridTemplateColumns: { xs: "repeat(2, 1fr)", sm: "repeat(3, 1fr)", md: "repeat(5, 1fr)" },
    }}>
      {tiles.map((t) => <KpiTile key={t.label} {...t} />)}
    </Box>
  );
}
KpiTileGrid.propTypes = { tasks: PropTypes.array, biz: PropTypes.object, trend: PropTypes.object };

function KpiTile({ label, value, sub, tone, deltaPct, goodUp = true }) {
  const hasDelta = typeof deltaPct === "number";
  const good = goodUp ? deltaPct >= 0 : deltaPct <= 0;
  const deltaColor = hasDelta ? (deltaPct === 0 ? "text.subtitle" : (good ? GREEN : RED)) : "text.subtitle";
  return (
    <Box sx={{
      p: 2, borderRadius: 1,
      border: "1px solid", borderColor: "divider",
      bgcolor: "background.paper",
      display: "flex", flexDirection: "column", justifyContent: "space-between",
      minHeight: 108,
    }}>
      <Stack direction="row" alignItems="center" justifyContent="space-between">
        <Typography sx={{
          typography: "s3", color: "text.subtitle",
          fontSize: 10.5, fontWeight: 600, textTransform: "uppercase", letterSpacing: 0.4,
        }}>
          {label}
        </Typography>
        {hasDelta && (
          <Stack direction="row" alignItems="center" spacing={0.25} sx={{ color: deltaColor }}>
            <Iconify icon={deltaPct >= 0 ? "solar:arrow-up-linear" : "solar:arrow-down-linear"} width={11} />
            <Typography sx={{ typography: "s3", fontSize: 10.5, fontWeight: 700, color: deltaColor }}>
              {Math.abs(deltaPct)}%
            </Typography>
          </Stack>
        )}
      </Stack>
      <Typography sx={{
        typography: "m1", fontWeight: 700,
        fontVariantNumeric: "tabular-nums", lineHeight: 1.15,
        color: tone || "text.primary",
        fontSize: 26, mt: 1,
      }}>
        {value}
      </Typography>
      <Typography sx={{
        typography: "s3", color: "text.subtitle",
        fontSize: 10.5, mt: 0.5,
      }}>
        {sub || (hasDelta ? `${Math.abs(deltaPct)}% vs previous period` : "no comparison")}
      </Typography>
    </Box>
  );
}
KpiTile.propTypes = {
  label: PropTypes.node, value: PropTypes.node, sub: PropTypes.node, tone: PropTypes.string,
  deltaPct: PropTypes.number, goodUp: PropTypes.bool,
};

/* ── volume / trend charts (full-width) ───────────────────────────── */

/* Big area chart across recent runs — matches Bland's "Call Volume"
   top-of-page chart. Anchors the analytics page visually so it does
   not read as a wall of small tiles. */
/**
 * Task-by-task outcome strip — one thin bar per test task in this run,
 * ordered by execution. Green = passed, red = failed/errored. The
 * shape at a glance answers "did the agent get better as the run
 * progressed, or worse?" — the kind of question that only makes
 * sense for THIS run's tasks, not for a cross-run trend.
 */
function VolumeAreaChart({ tasks }) {
  const theme = useTheme();
  const { categories, series, passed, total } = useMemo(() => {
    const ordered = [...(tasks || [])];
    const cats = ordered.map((_, i) => `T${i + 1}`);
    // Height = 1 for a normalised bar, coloring per bar communicates outcome.
    const data = ordered.map((t) => ({
      x: `T${cats.length ? ordered.indexOf(t) + 1 : 0}`,
      y: 1,
      status: t.status,
    }));
    const passedCount = ordered.filter((t) => t.status === "passed").length;
    return { categories: cats, series: [{ name: "outcome", data }], passed: passedCount, total: ordered.length };
  }, [tasks]);

  const colors = series[0].data.map((d) => (d.status === "passed" ? CHART_GREEN : CHART_RED));

  return (
    <Panel
      title="Task outcomes"
      subtitle={`Every test task in this run, in execution order · ${passed} passed / ${total}`}
      info="Every task as a single green/red bar in the order it ran. Clusters of red next to each other usually mean the agent hit a regression around the same input or persona — worth clicking through to compare those specific tasks."
    >
      <Box sx={{ px: 1.5, py: 1.5 }}>
        <ReactApexChart
          type="bar" height={140}
          series={[{ name: "outcome", data: series[0].data.map((d) => d.y) }]}
          options={{
            chart: { toolbar: { show: false }, animations: { enabled: false }, background: "transparent", fontFamily: theme.typography.fontFamily },
            theme: { mode: theme.palette.mode },
            plotOptions: { bar: { columnWidth: "78%", borderRadius: 0, distributed: true } },
            dataLabels: { enabled: false },
            stroke: { show: false },
            fill: { type: "solid", opacity: 0.9 },
            colors,
            xaxis: {
              categories,
              axisBorder: { show: false }, axisTicks: { show: false },
              labels: {
                style: { colors: theme.palette.text.secondary, fontSize: "9px" },
                /* Only show every Nth label to avoid overlap on 60+ tasks. */
                formatter: (v, idx) => {
                  const n = series[0].data.length;
                  if (!n) return "";
                  const step = Math.max(1, Math.ceil(n / 12));
                  return (typeof idx === "number" && idx % step === 0) ? v : "";
                },
              },
            },
            yaxis: { show: false, max: 1 },
            grid: { show: false },
            legend: { show: false },
            tooltip: {
              theme: theme.palette.mode,
              custom: ({ dataPointIndex }) => {
                const t = tasks[dataPointIndex];
                if (!t) return "";
                const status = t.status === "passed" ? "Passed" : (t.status === "error" ? "Errored" : "Failed");
                const label = t.title || t.name || t.id || `Task ${dataPointIndex + 1}`;
                return `<div style="padding:6px 10px;font-size:11px">
                  <div style="font-weight:700;margin-bottom:2px">T${dataPointIndex + 1} · ${status}</div>
                  <div style="opacity:0.7">${label}</div>
                </div>`;
              },
            },
          }}
        />
      </Box>
    </Panel>
  );
}
VolumeAreaChart.propTypes = { tasks: PropTypes.array };

/* Task latency along this run's task sequence. */
const TaskLatencyOverTime = memo(function TaskLatencyOverTime({ tasks }) {
  const theme = useTheme();
  const { categories, latSeries } = useMemo(() => {
    const ordered = [...(tasks || [])];
    return {
      categories: ordered.map((_, i) => `T${i + 1}`),
      latSeries: ordered.map((t) => Math.round(latencyOf(t))),
    };
  }, [tasks]);

  return (
    <Panel
      title="Task latency"
      subtitle="Per-task wall clock"
      info="Latency for each task in the order it ran. Random spikes = flaky infra; a steady climb = something the agent is doing more of over time (retries, context growth); a step change = usually a new tool or model kicking in mid-run."
    >
      <Box sx={{ px: 1.5, py: 1.5 }}>
        <ReactApexChart
          type="line" height={220}
          series={[{ name: "Task latency", data: latSeries }]}
          options={{
            chart: { toolbar: { show: false }, animations: { enabled: false }, background: "transparent", fontFamily: theme.typography.fontFamily },
            theme: { mode: theme.palette.mode },
            stroke: { curve: "smooth", width: 2, colors: ["#7857FC"] },
            dataLabels: { enabled: false },
            xaxis: {
              categories,
              axisBorder: { show: false }, axisTicks: { show: false },
              tickAmount: Math.min(8, Math.max(1, categories.length - 1)),
              labels: { style: { colors: theme.palette.text.secondary, fontSize: "10px" }, rotate: 0, hideOverlappingLabels: true },
            },
            yaxis: { labels: { style: { colors: theme.palette.text.secondary, fontSize: "10px" }, formatter: (v) => `${Math.round(v)}` } },
            grid: { borderColor: theme.palette.divider, strokeDashArray: 4, padding: { left: 8, right: 8, top: -6, bottom: -6 } },
            tooltip: {
              theme: theme.palette.mode,
              x: { formatter: (v, opts) => categories[opts?.dataPointIndex] || "" },
              y: { formatter: (v) => `${Math.round(v)}ms` },
            },
            markers: { size: 0 },
          }}
        />
      </Box>
    </Panel>
  );
});
TaskLatencyOverTime.propTypes = { tasks: PropTypes.array };

/* Outcome breakdown donut — passed / failed / errored / escalated,
   with a legend on the right showing counts + share %. Cekura and
   Bland both surface this shape front-and-centre. */
const OutcomeDonutChart = memo(function OutcomeDonutChart({ tasks, biz }) {
  const drill = useDrilldown();
  const measured = tasks.filter(isMeasured);
  const passed = measured.filter((t) => t.status === "passed");
  const failed = measured.filter((t) => t.status !== "passed" && t.status !== "error");
  const errored = tasks.filter((t) => t.status === "error");
  const escalatedTasks = tasks.filter((t) => t.escalated);
  const bucketSets = [passed, failed, errored, escalatedTasks];
  const labels = ["Passed", "Failed", "Errored", "Escalated"];
  const onSliceClick = (i) => {
    const list = bucketSets[i] || [];
    drill({ title: `Outcome — ${labels[i]}`, subtitle: `${list.length} tasks`, tasks: list });
  };
  return (
    <DonutBreakdown
      title="Goal outcome breakdown"
      subtitle={`${tasks.length} tasks classified · what share hit the business goal vs stalled`}
      buckets={[
        { label: "Passed",    value: passed.length },
        { label: "Failed",    value: failed.length },
        { label: "Errored",   value: errored.length },
        { label: "Escalated", value: biz.escalated },
      ]}
      colors={[CHART_GREEN, CHART_RED, "#F59E0B", "#7857FC"]}
      onSliceClick={onSliceClick}
      info="Splits the run four ways: passed, failed on evaluator, hard-errored (crash / timeout), or escalated to a human. A big amber wedge points at infra / tool problems; a big purple wedge means the agent bailed instead of trying — both are different fixes than a normal failure."
    />
  );
});
OutcomeDonutChart.propTypes = { tasks: PropTypes.array, biz: PropTypes.object };

/**
 * Reusable small donut with a legend on the right — the exact shape
 * Retell / Cekura / Bland all put in their analytics grid for
 * categorical breakdowns (sentiment, disconnection reason, success,
 * inbound/outbound…). Extracted so we can drop one in per category.
 */
/**
 * Donut breakdown — Retell shape. Big centered donut on top, tight
 * legend below in "label: N (X%)" format so the reader can compare
 * shares without hovering. Thicker ring than the earlier iteration
 * so the color slices dominate.
 */
const DonutBreakdown = memo(function DonutBreakdown({
  title, subtitle, buckets, colors, height = 160, onSliceClick, info,
}) {
  const theme = useTheme();
  const total = buckets.reduce((a, b) => a + b.value, 0) || 1;
  const series = buckets.map((b) => b.value);
  const labels = buckets.map((b) => b.label);
  const largest = buckets.reduce((best, b, i) => (b.value > (best?.value ?? -1) ? { ...b, i } : best), null);
  const largestPct = largest ? Math.round((largest.value / total) * 100) : 0;

  const exportRows = buckets.map((b) => ({
    label: b.label, count: b.value, share_pct: Math.round((b.value / total) * 100),
  }));
  return (
    <Panel title={title} subtitle={subtitle} exportRows={exportRows} info={info}>
      <Stack sx={{ px: 1.5, pt: 1.25, pb: 1.5 }} spacing={1} alignItems="stretch">
        <ReactApexChart
          type="donut" height={height}
          series={series}
          options={{
            chart: {
              animations: { enabled: false }, background: "transparent", fontFamily: theme.typography.fontFamily,
              ...(onSliceClick && {
                events: { dataPointSelection: (_e, _ctx, cfg) => onSliceClick(cfg.dataPointIndex, labels[cfg.dataPointIndex]) },
              }),
            },
            theme: { mode: theme.palette.mode },
            labels, colors,
            legend: { show: false },
            dataLabels: { enabled: false },
            stroke: { width: 2, colors: [theme.palette.background.paper] },
            /* Kill the slice's built-in hover/active-state animations.
               ApexCharts otherwise "expands" the slice on hover and
               "selects" it on click; both re-layout the SVG and the
               tooltip DOM gets rebuilt from scratch, which reads as
               the tooltip randomly vanishing while the user is still
               pointing at the slice. With these three off, the slice
               stays put and the tooltip persists as long as the
               cursor is inside it. */
            states: {
              hover: { filter: { type: "none" } },
              active: { filter: { type: "none" } },
            },
            plotOptions: { pie: {
              expandOnClick: false,
              donut: { size: "68%", labels: { show: true,
                name: { fontSize: "10px", color: theme.palette.text.subtitle, formatter: () => (largest ? largest.label : "Total") },
                value: {
                  fontSize: "18px", fontWeight: 700, color: theme.palette.text.primary,
                  formatter: () => `${largestPct}%`,
                  offsetY: 4,
                },
                total: { show: true, label: largest ? largest.label : "Total",
                  fontSize: "10px", color: theme.palette.text.subtitle,
                  formatter: () => `${largestPct}%` },
              } },
            } },
            tooltip: {
              intersect: false, followCursor: false,
              fixed: { enabled: false },
              y: {
                formatter: (v) => `${v} tasks · ${Math.round((v / total) * 100)}%${onSliceClick ? " · click to drill down" : ""}`,
              },
            },
          }}
        />
        {/* Compact legend below the donut — each row is clickable
            when drilldown is wired, so users don't have to hit the
            small slice to filter. */}
        <Stack direction="row" flexWrap="wrap" sx={{ px: 0.25, columnGap: 1.25, rowGap: 0.375 }}>
          {buckets.map((b, i) => {
            const drill = onSliceClick ? () => onSliceClick(i, b.label) : null;
            return (
              <Stack
                key={b.label}
                direction="row" alignItems="center" spacing={0.625}
                onClick={drill || undefined}
                sx={{
                  minWidth: 0,
                  cursor: drill ? "pointer" : "default",
                  borderRadius: 0.5, px: drill ? 0.5 : 0, mx: drill ? -0.5 : 0,
                  "&:hover": drill ? { bgcolor: "action.hover" } : {},
                }}
              >
                <Box sx={{ width: 7, height: 7, borderRadius: 0.75, bgcolor: colors[i], flexShrink: 0 }} />
                <Typography sx={{ fontSize: 11, color: "text.subtitle", fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap" }}>
                  <Box component="span" sx={{ color: "text.primary" }}>{b.label}</Box>{" "}
                  <b style={{ color: "inherit" }}>{b.value}</b>{" "}
                  ({Math.round((b.value / total) * 100)}%)
                </Typography>
              </Stack>
            );
          })}
        </Stack>
      </Stack>
    </Panel>
  );
});
DonutBreakdown.propTypes = {
  title: PropTypes.node, subtitle: PropTypes.node,
  buckets: PropTypes.array, colors: PropTypes.array, height: PropTypes.number,
  onSliceClick: PropTypes.func, info: PropTypes.node,
};

/* Success = passed vs everything else. Cekura / Retell surface this
   as the top-level pass/fail badge; keeping the more granular
   OutcomeDonutChart (passed/failed/errored/escalated) separately.
   memo() wrappers below prevent the ~15 chart components from
   re-rendering when unrelated state (filter popover, hover) flips
   elsewhere on the page. */
const SuccessDonut = memo(function SuccessDonut({ tasks }) {
  const drill = useDrilldown();
  const onSliceClick = (i) => {
    const label = i === 0 ? "Successful" : "Unsuccessful";
    const filtered = i === 0
      ? tasks.filter((t) => t.status === "passed")
      : tasks.filter((t) => t.status !== "passed");
    drill({ title: `Call successful — ${label}`, subtitle: `${filtered.length} tasks`, tasks: filtered });
  };
  const buckets = useMemo(() => {
    const passed = tasks.filter((t) => t.status === "passed").length;
    return [
      { label: "Successful",  value: passed },
      { label: "Unsuccessful", value: tasks.length - passed },
    ];
  }, [tasks]);
  return (
    <DonutBreakdown
      title="Call successful"
      subtitle="Successful vs unsuccessful — the top-level verdict"
      buckets={buckets}
      colors={SUCCESS_COLORS}
      onSliceClick={onSliceClick}
      info="The single-line answer: what share of tasks the agent actually completed. Everything else on this page tries to explain the delta between this number and 100%. Click either slice to jump straight to the passing or failing tasks."
    />
  );
});
SuccessDonut.propTypes = { tasks: PropTypes.array };
const SUCCESS_COLORS = ["#7857FC", "#DC2626"];

const SentimentDonut = memo(function SentimentDonut({ tasks }) {
  const drill = useDrilldown();
  const buckets = useMemo(() => {
    const bucket = { positive: 0, neutral: 0, negative: 0 };
    tasks.forEach((t) => { const k = sentimentOf(t); bucket[k] = (bucket[k] || 0) + 1; });
    return [
      { label: "Positive", value: bucket.positive || 0 },
      { label: "Neutral",  value: bucket.neutral  || 0 },
      { label: "Negative", value: bucket.negative || 0 },
    ];
  }, [tasks]);
  const onSliceClick = (i, label) => {
    const key = ["positive", "neutral", "negative"][i];
    const filtered = tasks.filter((t) => sentimentOf(t) === key);
    drill({ title: `User sentiment — ${label}`, subtitle: `${filtered.length} tasks`, tasks: filtered });
  };
  return (
    <DonutBreakdown
      title="User sentiment"
      subtitle="How the counterparty came across during the task"
      buckets={buckets}
      colors={SENTIMENT_COLORS}
      onSliceClick={onSliceClick}
      info="How the simulated caller sounded by the end of the task. A big negative wedge — even on passing tasks — usually means the agent got the answer right the wrong way (too curt, too slow, too many clarifiers). Pair with disconnection reason to spot rude-but-successful patterns."
    />
  );
});
SentimentDonut.propTypes = { tasks: PropTypes.array };
const SENTIMENT_COLORS = ["#16A34A", "#94A3B8", "#DC2626"];

const DisconnectionDonut = memo(function DisconnectionDonut({ tasks }) {
  const drill = useDrilldown();
  const labelMap = {
    complete: "Task complete", escalated: "Escalated",
    incomplete: "Incomplete", timeout: "Timeout", error: "Error",
  };
  const buckets = useMemo(() => {
    const bucket = {};
    tasks.forEach((t) => { const k = endReasonOf(t); bucket[k] = (bucket[k] || 0) + 1; });
    const order = ["complete", "escalated", "incomplete", "timeout", "error"];
    return order.filter((k) => bucket[k]).map((k) => ({ label: labelMap[k], value: bucket[k], _key: k }));
  }, [tasks]);
  const onSliceClick = (i) => {
    const bucket = buckets[i];
    if (!bucket) return;
    const filtered = tasks.filter((t) => endReasonOf(t) === bucket._key);
    drill({ title: `Disconnection reason — ${bucket.label}`, subtitle: `${filtered.length} tasks`, tasks: filtered });
  };
  return (
    <DonutBreakdown
      title="Disconnection reason"
      subtitle="Why each task ended"
      buckets={buckets}
      colors={DISCONNECT_COLORS}
      onSliceClick={onSliceClick}
      info="How each task actually ended — completed, escalated to a human, ran out of turns, timed out, or errored. Big Timeout / Error slices are infrastructure smells; big Escalated is an over-cautious agent; big Incomplete is one that gave up mid-task."
    />
  );
});
DisconnectionDonut.propTypes = { tasks: PropTypes.array };
const DISCONNECT_COLORS = ["#7857FC", "#F59E0B", "#94A3B8", "#DB2777", "#DC2626"];

/**
 * Phone Inbound / Outbound — voice-agent split. Only really
 * meaningful for a voice env; other envs collapse to 100%
 * outbound. Kept for parity with Retell's dashboard shape.
 */
/**
 * Latency percentiles — the percentile curve of end-to-end task
 * latency (x = percentile, y = latency) with p50 / p90 / p99 marked.
 * Shows the boundary numbers an SLO is written against and where the
 * tail starts to lift away from the median.
 */
const LatencyPercentilesPanel = memo(function LatencyPercentilesPanel({ tasks }) {
  const theme = useTheme();
  const latencies = useMemo(() => sortedNums(tasks, (t) => latencyOf(t)), [tasks]);
  const p50 = percentile(latencies, 50);
  const p90 = percentile(latencies, 90);
  const p99 = percentile(latencies, 99);
  const curve = useMemo(
    () => (latencies.length ? Array.from({ length: 101 }, (_, p) => [p, Math.round(percentile(latencies, p))]) : []),
    [latencies],
  );
  const exportRows = [
    { percentile: "p50", latency_ms: Math.round(p50) },
    { percentile: "p90", latency_ms: Math.round(p90) },
    { percentile: "p99", latency_ms: Math.round(p99) },
  ];
  const color = "#0EA5E9";
  const marks = [
    { p: 50, v: p50, label: "p50" },
    { p: 90, v: p90, label: "p90" },
    { p: 99, v: p99, label: "p99" },
  ];
  return (
    <Panel
      title="Latency percentiles"
      subtitle={`p50 ${Math.round(p50)}ms · p90 ${Math.round(p90)}ms · p99 ${Math.round(p99)}ms`}
      exportRows={exportRows}
      info="Every task's end-to-end latency, sorted: read across to a percentile, up to the latency. p50 = typical; p90 = the slower 10% of tasks (the ones your SLO is really written for); p99 = your worst tail. A curve that bends sharply upward near the right edge means a small set of tasks is dragging the tail."
    >
      <Box sx={{ px: 1.5, py: 1.5 }}>
        <ReactApexChart
          type="area" height={220}
          series={[{ name: "Latency", data: curve }]}
          options={{
            chart: { toolbar: { show: false }, animations: { enabled: false }, background: "transparent", fontFamily: theme.typography.fontFamily, zoom: { enabled: false } },
            theme: { mode: theme.palette.mode },
            colors: [color],
            stroke: { curve: "smooth", width: 2 },
            fill: { type: "gradient", gradient: { shadeIntensity: 0, opacityFrom: 0.25, opacityTo: 0.02, stops: [0, 100] } },
            dataLabels: { enabled: false },
            xaxis: {
              type: "numeric", min: 0, max: 100, tickAmount: 10,
              axisBorder: { show: false }, axisTicks: { show: false },
              labels: { style: { colors: theme.palette.text.secondary, fontSize: "10px" }, formatter: (v) => `p${Math.round(v)}` },
              tooltip: { enabled: false },
            },
            yaxis: { labels: { style: { colors: theme.palette.text.secondary, fontSize: "10px" }, formatter: (v) => `${Math.round(v)}` } },
            grid: { borderColor: theme.palette.divider, strokeDashArray: 4, padding: { left: 8, right: 12, top: -6, bottom: 8 } },
            annotations: {
              xaxis: marks.map((m) => ({
                x: m.p,
                borderColor: alpha(theme.palette.text.primary, 0.35),
                strokeDashArray: 3,
                label: {
                  text: m.label, orientation: "horizontal", borderWidth: 0,
                  style: { background: "transparent", color: theme.palette.text.secondary, fontSize: "10px", fontWeight: 600 },
                },
              })),
              points: marks.map((m) => ({
                x: m.p, y: Math.round(m.v),
                marker: { size: 4, fillColor: color, strokeColor: theme.palette.background.paper, strokeWidth: 2 },
              })),
            },
            tooltip: {
              theme: theme.palette.mode,
              x: { formatter: (v) => `p${Math.round(v)}` },
              y: { formatter: (v) => `${Math.round(v)}ms` },
            },
            markers: { size: 0 },
          }}
        />
      </Box>
    </Panel>
  );
});
LatencyPercentilesPanel.propTypes = { tasks: PropTypes.array };

/* Shared by the two histogram panels below: small right-aligned source tag. */
function SourceTag({ children }) {
  return (
    /* mr clears the widget's ⋯ menu, which sits over the panel's top-right corner. */
    <Typography sx={{ typography: "s3", fontWeight: 600, color: "text.secondary", whiteSpace: "nowrap", pt: 0.25, mr: 4 }}>
      {children}
    </Typography>
  );
}
SourceTag.propTypes = { children: PropTypes.node };

const HIST_PURPLE = "#7857FC";
const HIST_RED = "#DC2626";
const fmtMs = (v) => (v >= 1000 ? `${(v / 1000).toFixed(2)}s` : `${Math.round(v)}ms`);

function histogramOptions(theme, { categories, colors, xTitle, yTitle = "calls", tooltip, columnWidth = "92%" }) {
  return {
    chart: { toolbar: { show: false }, animations: { enabled: false }, background: "transparent", fontFamily: theme.typography.fontFamily },
    theme: { mode: theme.palette.mode },
    colors,
    plotOptions: { bar: { distributed: true, columnWidth, borderRadius: 2, borderRadiusApplication: "end" } },
    dataLabels: { enabled: false },
    legend: { show: false },
    xaxis: {
      categories,
      axisTicks: { show: true, color: theme.palette.divider },
      axisBorder: { show: true, color: theme.palette.divider },
      labels: { style: { colors: theme.palette.text.secondary, fontSize: "11px" } },
      title: { text: xTitle, style: { color: theme.palette.text.secondary, fontSize: "12px", fontWeight: 500 } },
    },
    yaxis: {
      forceNiceScale: true, min: 0,
      labels: { style: { colors: theme.palette.text.secondary, fontSize: "11px" }, formatter: (v) => `${Math.round(v)}` },
      title: { text: yTitle, style: { color: theme.palette.text.secondary, fontSize: "12px", fontWeight: 500 } },
    },
    grid: { borderColor: theme.palette.divider, strokeDashArray: 4, xaxis: { lines: { show: false } }, padding: { left: 8, right: 8 } },
    tooltip: { theme: theme.palette.mode, y: { formatter: tooltip } },
  };
}

/**
 * Agent response time per call — histogram of each call's average agent
 * response time (the same per-call figure as the KPI strip's Agent Latency
 * and the Test runs Latency column). Buckets at/over the 550ms target — the
 * table's own red threshold — are drawn red.
 */
const RESPONSE_TARGET_MS = 550;
const RESPONSE_BUCKET_MS = 25;
const AgentResponseTimePanel = memo(function AgentResponseTimePanel({ tasks }) {
  const theme = useTheme();
  const { buckets, overPct, p50, p95 } = useMemo(() => {
    const values = (tasks || []).map((t) => agentLatencyOf(t)).filter((v) => v > 0);
    if (!values.length) return { buckets: [], overPct: 0, p50: 0, p95: 0 };
    const sorted = [...values].sort((a, b) => a - b);
    const lo = Math.max(0, Math.floor(sorted[0] / RESPONSE_BUCKET_MS) * RESPONSE_BUCKET_MS - RESPONSE_BUCKET_MS);
    const hi = Math.ceil((sorted[sorted.length - 1] + 1) / RESPONSE_BUCKET_MS) * RESPONSE_BUCKET_MS + RESPONSE_BUCKET_MS;
    const list = [];
    for (let at = lo; at < hi; at += RESPONSE_BUCKET_MS) {
      list.push({ at, count: values.filter((v) => v >= at && v < at + RESPONSE_BUCKET_MS).length });
    }
    return {
      buckets: list,
      overPct: pct(values.filter((v) => v >= RESPONSE_TARGET_MS).length, values.length),
      p50: percentile(sorted, 50),
      p95: percentile(sorted, 95),
    };
  }, [tasks]);
  if (!buckets.length) return null;
  const exportRows = buckets.map((b) => ({ from_ms: b.at, to_ms: b.at + RESPONSE_BUCKET_MS, calls: b.count }));
  return (
    <Panel
      title="Agent response time per call"
      action={<SourceTag>Platform, transcript timing</SourceTag>}
      exportRows={exportRows}
      info={`Each call's average time for the agent to start replying after the caller stops talking — the same per-call figure as the Agent Latency tile. Red buckets are at or over the ${RESPONSE_TARGET_MS}ms target, where callers start to notice silence. A second hump on the right usually means one tool or prompt path is consistently slow.`}
      footer={`${overPct}% of calls were over the ${RESPONSE_TARGET_MS}ms target. p50 ${fmtMs(p50)}, p95 ${fmtMs(p95)}.`}
    >
      <Box sx={{ px: 1.5, pb: 1 }}>
        <ReactApexChart
          type="bar" height={260}
          series={[{ name: "Calls", data: buckets.map((b) => b.count) }]}
          options={histogramOptions(theme, {
            categories: buckets.map((b) => fmtMs(b.at)),
            colors: buckets.map((b) => (b.at >= RESPONSE_TARGET_MS ? HIST_RED : HIST_PURPLE)),
            xTitle: "average response time per call",
            columnWidth: "80%",
            tooltip: (v) => `${v} call${v === 1 ? "" : "s"}`,
          })}
        />
      </Box>
    </Panel>
  );
});
AgentResponseTimePanel.propTypes = { tasks: PropTypes.array };

/**
 * CSAT distribution — calls per CSAT score (the same per-call CSAT as the
 * KPI strip and the Test runs CSAT column). Scores at or under 4, the
 * table's own red threshold, are drawn red. The footer checks the provider's
 * own success judgement (did the call end with the task complete) against
 * whether every eval on the call passed.
 */
const CSAT_BAD_AT = 4;
const CsatDistributionPanel = memo(function CsatDistributionPanel({ tasks }) {
  const theme = useTheme();
  const { counts, agreePct } = useMemo(() => {
    const list = tasks || [];
    const scores = list.map((t) => csatOf(t));
    /* Always the full 0–10 scale, so empty high scores read as a gap. */
    const byScore = Array.from({ length: 11 }, (_, s) => scores.filter((v) => v === s).length);
    const judged = list.filter((t) => (t.evalResults || []).length > 0);
    const agree = judged.filter((t) => (endReasonOf(t) === "complete") === t.evalResults.every((r) => r.passed)).length;
    return { counts: byScore, agreePct: pct(agree, judged.length) };
  }, [tasks]);
  const exportRows = counts.map((c, s) => ({ csat: s, calls: c }));
  return (
    <Panel
      title="CSAT distribution (0–10)"
      action={<SourceTag>Existing score</SourceTag>}
      exportRows={exportRows}
      info={`How many calls landed on each CSAT score from 0 to 10 — the same per-call score as the Avg CSAT tile. Red scores (${CSAT_BAD_AT} and below) are unhappy callers; a lump on the left means the agent is solving problems in a way callers don't like. The footer checks the provider's own success judgement against your evals, so you know how far to trust it.`}
      footer={`The provider's own success judgement (its analysis) agrees with your evals on ${agreePct}% of calls.`}
    >
      <Box sx={{ px: 1.5, pb: 1 }}>
        <ReactApexChart
          type="bar" height={260}
          series={[{ name: "Calls", data: counts }]}
          options={histogramOptions(theme, {
            categories: counts.map((_, s) => String(s)),
            colors: counts.map((_, s) => (s <= CSAT_BAD_AT ? HIST_RED : HIST_PURPLE)),
            xTitle: "CSAT score",
            tooltip: (v) => `${v} call${v === 1 ? "" : "s"}`,
          })}
        />
      </Box>
    </Panel>
  );
});
CsatDistributionPanel.propTypes = { tasks: PropTypes.array };

/**
 * Voice cost breakdown — LLM / STT / TTS / Transport split per Vapi's
 * dashboard shape. Voice envs have a real pipeline cost story; Vapi
 * is the only competitor that exposes this cleanly today.
 *
 * The mock tasks carry a single `cost` number, so we synthesise a
 * plausible split (LLM ~55% · TTS ~25% · STT ~15% · Transport ~5%)
 * that stays stable per-task via hashId so it doesn't shimmer.
 */
const VoiceCostBreakdownPanel = memo(function VoiceCostBreakdownPanel({ tasks }) {
  const theme = useTheme();
  const { series, categories, totals, totalCost } = useMemo(() => {
    const costed = tasks.filter((t) => (t.cost || 0) > 0);
    if (!costed.length) return { series: [], categories: [], totals: {}, totalCost: 0 };
    const cats = costed.map((_, i) => `T${i + 1}`);
    const llm = [], stt = [], tts = [], transport = [];
    let sumLlm = 0, sumStt = 0, sumTts = 0, sumTrans = 0;
    costed.forEach((t) => {
      const c = t.cost || 0;
      // Deterministic per-task jitter around the baseline split so it isn't a flat stripe.
      const jitter = ((hashId(t.id || "") % 100) - 50) / 1000; // ±5%
      const llmShare = Math.max(0.35, Math.min(0.7, 0.55 + jitter));
      const ttsShare = Math.max(0.15, Math.min(0.35, 0.25 - jitter / 2));
      const sttShare = Math.max(0.08, Math.min(0.25, 0.15 + jitter / 3));
      const transShare = Math.max(0.02, 1 - llmShare - ttsShare - sttShare);
      const l = Number((c * llmShare).toFixed(4));
      const s = Number((c * sttShare).toFixed(4));
      const tt = Number((c * ttsShare).toFixed(4));
      const tr = Number((c * transShare).toFixed(4));
      llm.push(l); stt.push(s); tts.push(tt); transport.push(tr);
      sumLlm += l; sumStt += s; sumTts += tt; sumTrans += tr;
    });
    return {
      series: [
        { name: "LLM",       data: llm },
        { name: "TTS",       data: tts },
        { name: "STT",       data: stt },
        { name: "Transport", data: transport },
      ],
      categories: cats,
      totals: { llm: sumLlm, stt: sumStt, tts: sumTts, transport: sumTrans },
      totalCost: sumLlm + sumStt + sumTts + sumTrans,
    };
  }, [tasks]);
  if (!series.length) return null;

  const legend = [
    { key: "LLM",       value: totals.llm,       color: "#7857FC" },
    { key: "TTS",       value: totals.tts,       color: "#0EA5E9" },
    { key: "STT",       value: totals.stt,       color: "#F59E0B" },
    { key: "Transport", value: totals.transport, color: "#94A3B8" },
  ];
  const exportRows = legend.map((l) => ({
    stage: l.key,
    cost_usd: Number(l.value.toFixed(4)),
    share_pct: Math.round((l.value / totalCost) * 100),
  }));

  return (
    <Panel
      title="Cost breakdown by pipeline stage"
      subtitle={`$${totalCost.toFixed(2)} total across ${series[0].data.length} calls — LLM / TTS / STT / Transport split`}
      exportRows={exportRows}
      info="Per-call spend, split by voice-pipeline stage. If LLM towers over everything, you're overspending on model tokens (shorter prompt, cheaper model, cache). If TTS or STT dominate, look at voice provider tier. Transport bloat usually means calls staying open too long."
    >
      <Box sx={{ px: 1.5, py: 1.5 }}>
        <ReactApexChart
          type="bar" height={220}
          series={series}
          options={{
            chart: {
              type: "bar", stacked: true,
              toolbar: { show: false }, animations: { enabled: false },
              background: "transparent", fontFamily: theme.typography.fontFamily,
            },
            theme: { mode: theme.palette.mode },
            colors: legend.map((l) => l.color),
            fill: { type: "solid", opacity: 0.9 },
            stroke: { show: false },
            plotOptions: {
              bar: { columnWidth: "62%", borderRadius: 2, borderRadiusApplication: "end", borderRadiusWhenStacked: "last" },
            },
            dataLabels: { enabled: false },
            legend: { show: false },
            xaxis: {
              categories,
              axisBorder: { show: false }, axisTicks: { show: false },
              tickAmount: Math.min(8, Math.max(1, categories.length - 1)),
              labels: { style: { colors: theme.palette.text.secondary, fontSize: "10px" }, hideOverlappingLabels: true },
            },
            yaxis: { labels: { style: { colors: theme.palette.text.secondary, fontSize: "10px" }, formatter: (v) => `$${Number(v).toFixed(2)}` } },
            grid: { borderColor: theme.palette.divider, strokeDashArray: 4, padding: { left: 8, right: 8, top: -6, bottom: -6 } },
            tooltip: {
              theme: theme.palette.mode,
              shared: true, intersect: false,
              y: { formatter: (v) => `$${Number(v).toFixed(3)}` },
            },
          }}
        />
        {/* Legend below with totals + share, Retell-style */}
        <Stack direction="row" flexWrap="wrap" sx={{ mt: 1.25, px: 0.5, columnGap: 1.75, rowGap: 0.5 }}>
          {legend.map((l) => (
            <Stack key={l.key} direction="row" alignItems="center" spacing={0.75} sx={{ minWidth: 0 }}>
              <Box sx={{ width: 8, height: 8, borderRadius: 0.75, bgcolor: l.color, flexShrink: 0 }} />
              <Typography sx={{ fontSize: 11.5, color: "text.subtitle", fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap" }}>
                <Box component="span" sx={{ color: "text.primary" }}>{l.key}</Box>{" "}
                <b style={{ color: "inherit" }}>${l.value.toFixed(2)}</b>{" "}
                ({Math.round((l.value / totalCost) * 100)}%)
              </Typography>
            </Stack>
          ))}
        </Stack>
      </Box>
    </Panel>
  );
});
VoiceCostBreakdownPanel.propTypes = { tasks: PropTypes.array };

/**
 * Reusable distribution histogram — one panel per metric. Buckets a
 * numeric field (turns, tokens, cost) into 8 slots and shows the
 * count per bucket. Arize AX's Distribution widget in idea; nobody
 * else in the agent-sim space exposes turns/tokens/cost distributions
 * as first-class panels.
 */
const MetricDistribution = memo(function MetricDistribution({
  title, subtitle, tasks, accessor, formatter, color = "#7857FC", info,
}) {
  const theme = useTheme();
  const { series, categories, exportRows } = useMemo(() => {
    const values = tasks.map(accessor).filter((v) => Number.isFinite(v) && v > 0);
    if (!values.length) return { series: [{ data: [] }], categories: [], exportRows: [] };
    const min = Math.min(...values);
    const max = Math.max(...values);
    /* 8 fixed-width buckets from min→max. Small spread degenerates to
       a single-bucket bar; that's honest, no need to fake variance. */
    const bucketCount = 8;
    const step = (max - min) / bucketCount || 1;
    const buckets = new Array(bucketCount).fill(0);
    values.forEach((v) => {
      const idx = Math.min(bucketCount - 1, Math.floor((v - min) / step));
      buckets[idx] += 1;
    });
    const cats = buckets.map((_, i) => {
      const lo = min + step * i;
      const hi = min + step * (i + 1);
      return `${formatter(lo)}–${formatter(hi)}`;
    });
    const rows = cats.map((c, i) => ({ bucket: c, task_count: buckets[i] }));
    return {
      series: [{ name: "Tasks", data: buckets }],
      categories: cats,
      exportRows: rows,
    };
  }, [tasks, accessor, formatter]);

  return (
    <Panel title={title} subtitle={subtitle} exportRows={exportRows} info={info}>
      <Box sx={{ px: 1.5, py: 1.5 }}>
        <ReactApexChart
          type="bar" height={220}
          series={series}
          options={{
            chart: { toolbar: { show: false }, animations: { enabled: false }, background: "transparent", fontFamily: theme.typography.fontFamily },
            theme: { mode: theme.palette.mode },
            plotOptions: { bar: { columnWidth: "70%", borderRadius: 2 } },
            dataLabels: { enabled: false },
            stroke: { show: false },
            fill: { type: "solid", opacity: 0.9 },
            colors: [color],
            xaxis: {
              categories,
              axisBorder: { show: false }, axisTicks: { show: false },
              tickAmount: Math.min(8, Math.max(1, categories.length - 1)),
              labels: { style: { colors: theme.palette.text.secondary, fontSize: "10px" }, hideOverlappingLabels: true },
            },
            yaxis: { labels: { style: { colors: theme.palette.text.secondary, fontSize: "10px" }, formatter: (v) => `${Math.round(v)}` } },
            grid: { borderColor: theme.palette.divider, strokeDashArray: 4, padding: { left: 0, right: 0, top: -6, bottom: -6 } },
            tooltip: { theme: theme.palette.mode, y: { formatter: (v) => `${v} tasks` } },
          }}
        />
      </Box>
    </Panel>
  );
});
MetricDistribution.propTypes = {
  title: PropTypes.node, subtitle: PropTypes.node,
  tasks: PropTypes.array, accessor: PropTypes.func, formatter: PropTypes.func,
  color: PropTypes.string, info: PropTypes.node,
};

/**
 * Percentile tile grid — one big-number tile per metric, showing
 * p90 as the hero value with p50 / p99 / max stacked underneath.
 * Datadog SLO panels and Sentry Performance's Web Vitals row both
 * surface distributions this way when they need to fit multiple
 * metrics in a compact enterprise-serious layout. No charts, no
 * colored sparklines — just numbers.
 */
const DistributionSummary = memo(function DistributionSummary({ tasks }) {
  const metrics = useMemo(() => {
    const defs = [
      { key: "latency",  label: "End-to-end latency", fmt: (v) => `${Math.round(v)}ms`,       accessor: (t) => latencyOf(t) },
      { key: "duration", label: "Task duration",       fmt: (v) => `${v.toFixed(1)}s`,          accessor: (t) => (t.durationMs || 0) / 1000 },
      { key: "cost",     label: "Cost per task",       fmt: (v) => `$${v.toFixed(3)}`,          accessor: (t) => t.cost || 0 },
      { key: "tokens",   label: "Tokens per task",     fmt: (v) => numFmt.format(Math.round(v)), accessor: (t) => t.tokens || 0 },
      { key: "turns",    label: "Turns per task",      fmt: (v) => `${Math.round(v)}`,          accessor: (t) => t.steps?.length || 0 },
    ];
    return defs.map((d) => {
      const values = tasks.map(d.accessor).filter((v) => Number.isFinite(v) && v > 0);
      if (!values.length) return { ...d, empty: true };
      const sorted = [...values].sort((a, b) => a - b);
      const pct = (p) => sorted[Math.min(sorted.length - 1, Math.floor((p / 100) * sorted.length))];
      return {
        ...d, count: values.length,
        p50: pct(50), p90: pct(90), p99: pct(99),
        min: sorted[0], max: sorted[sorted.length - 1],
      };
    });
  }, [tasks]);

  const exportRows = metrics.filter((m) => !m.empty).map((m) => ({
    metric: m.label, samples: m.count,
    p50: Number(m.p50.toFixed(4)),
    p90: Number(m.p90.toFixed(4)),
    p99: Number(m.p99.toFixed(4)),
    max: Number(m.max.toFixed(4)),
  }));

  return (
    <Panel
      title="Distribution summary"
      subtitle="p50 · p90 · p99 · max for every task-level metric"
      exportRows={exportRows}
      info="One row per metric with the four numbers that describe its shape. p90 is the number to defend in a review; the max tells you how bad your worst tail actually got. A big gap between p50 and p99 means a few outliers are dragging the run and are worth investigating first."
    >
      <Box sx={{
        display: "grid",
        gridTemplateColumns: { xs: "1fr 1fr", sm: "repeat(3, 1fr)", md: "repeat(5, 1fr)" },
        bgcolor: "divider", gap: "1px",
        "& > *": { bgcolor: "background.paper" },
      }}>
        {metrics.map((m) => (
          <Box key={m.key} sx={{
            px: 2.25, py: 2,
            display: "flex", flexDirection: "column",
            minHeight: 140,
          }}>
            <Typography sx={{
              fontSize: 10.5, fontWeight: 600, color: "text.subtitle",
              textTransform: "uppercase", letterSpacing: 0.6,
              overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
            }}>
              {m.label}
            </Typography>
            {m.empty ? (
              <Typography sx={{ fontSize: 13, color: "text.disabled", mt: 1 }}>
                no samples
              </Typography>
            ) : (
              <>
                {/* p90 hero */}
                <Stack direction="row" alignItems="baseline" spacing={0.5} sx={{ mt: 1 }}>
                  <Typography sx={{
                    fontSize: 22, fontWeight: 700, lineHeight: 1,
                    fontVariantNumeric: "tabular-nums", letterSpacing: -0.3,
                    color: "text.primary",
                  }}>
                    {m.fmt(m.p90)}
                  </Typography>
                  <Typography sx={{ fontSize: 10.5, color: "text.subtitle", fontWeight: 600 }}>
                    p90
                  </Typography>
                </Stack>

                {/* p50 / p99 / max rows */}
                <Stack sx={{ mt: "auto", pt: 1.5 }} spacing={0.375}>
                  {[
                    ["p50", m.fmt(m.p50)],
                    ["p99", m.fmt(m.p99)],
                    ["max", m.fmt(m.max)],
                  ].map(([k, v]) => (
                    <Stack key={k} direction="row" justifyContent="space-between" alignItems="baseline">
                      <Typography sx={{ fontSize: 10.5, color: "text.subtitle", textTransform: "uppercase", letterSpacing: 0.4, fontWeight: 600 }}>
                        {k}
                      </Typography>
                      <Typography sx={{ fontSize: 12, fontWeight: 600, fontVariantNumeric: "tabular-nums", color: "text.primary" }}>
                        {v}
                      </Typography>
                    </Stack>
                  ))}
                </Stack>
              </>
            )}
          </Box>
        ))}
      </Box>
    </Panel>
  );
});
DistributionSummary.propTypes = { tasks: PropTypes.array };

/* DrilldownContext moved to its own module (./drilldownContext) so
   the unified widget renderer can use the same context without
   importing this file. */
function useDrilldown() { return useContext(DrilldownContext) || (() => {}); }

/**
 * Right-side drawer that shows a filtered task list. Opens when
 * any chart segment is clicked; closes on backdrop click / ×.
 * Task rows link to the run's Test runs (68) tab via an id anchor
 * in the URL — the Test runs table reads that and scrolls / opens
 * the task row.
 */
function TaskDrilldownDrawer({ open, onClose, title, subtitle, tasks: filtered, env }) {
  const golden = useGoldenSet(env || {});
  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth={false}
      slotProps={{
        paper: {
          sx: {
            position: "fixed", right: 0, top: 0, bottom: 0, m: 0,
            width: { xs: "100vw", sm: 520 }, maxWidth: "100vw",
            height: "100vh", maxHeight: "100vh",
            borderRadius: 0,
            display: "flex", flexDirection: "column",
          },
        },
      }}
    >
      <Stack direction="row" alignItems="flex-start" spacing={1.5} sx={{
        px: 2.5, py: 2, borderBottom: "1px solid", borderColor: "divider",
      }}>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography sx={{ fontSize: 11, fontWeight: 600, color: "text.subtitle", textTransform: "uppercase", letterSpacing: 0.5 }}>
            Drill-down · {filtered?.length || 0} {filtered?.length === 1 ? "task" : "tasks"}
          </Typography>
          <Typography sx={{ fontSize: 15, fontWeight: 700, mt: 0.25, letterSpacing: -0.1 }}>
            {title}
          </Typography>
          {subtitle && (
            <Typography sx={{ fontSize: 12, color: "text.subtitle", mt: 0.25 }}>
              {subtitle}
            </Typography>
          )}
        </Box>
        <IconButton size="small" onClick={onClose} aria-label="Close drill-down">
          <Iconify icon="eva:close-fill" width={18} />
        </IconButton>
      </Stack>
      <Box sx={{ flex: 1, overflowY: "auto" }}>
        {(!filtered || filtered.length === 0) ? (
          <Box sx={{ p: 4, textAlign: "center" }}>
            <Typography sx={{ fontSize: 13, color: "text.subtitle" }}>
              No tasks match this selection.
            </Typography>
          </Box>
        ) : (
          <Stack divider={<Box sx={{ height: "1px", bgcolor: "divider" }} />}>
            {filtered.map((t, i) => {
              const status = t.status === "passed" ? "Passed"
                : t.status === "error" ? "Errored"
                : t.status === "failed" ? "Failed"
                : "Unknown";
              const statusColor = t.status === "passed" ? "#16A34A"
                : t.status === "error" ? "#DC2626"
                : t.status === "failed" ? "#DC2626"
                : "text.subtitle";
              const isGold = golden.ids.includes(t.id);
              return (
                <Box key={t.id || i} sx={{ px: 2.5, py: 1.5 }}>
                  <Stack direction="row" alignItems="baseline" spacing={1}>
                    <Typography sx={{ fontSize: 11, color: "text.subtitle", fontVariantNumeric: "tabular-nums" }}>
                      #{i + 1}
                    </Typography>
                    <Typography sx={{ fontSize: 13, fontWeight: 600, flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {t.title || t.name || t.id}
                    </Typography>
                    <Tooltip arrow title={isGold ? "Remove from golden set" : "Mark as golden — ground truth for the divergence widgets"}>
                      <IconButton
                        size="small" onClick={() => golden.toggle(t.id)}
                        sx={{ width: 26, height: 26, color: isGold ? "#F59E0B" : "text.disabled", "&:hover": { color: "#F59E0B" } }}
                        aria-label={isGold ? "Remove from golden set" : "Mark as golden"}
                      >
                        <Iconify icon={isGold ? "solar:star-bold" : "solar:star-linear"} width={14} />
                      </IconButton>
                    </Tooltip>
                    <Typography sx={{ fontSize: 11, fontWeight: 700, color: statusColor }}>
                      {status}
                    </Typography>
                  </Stack>
                  <Stack direction="row" spacing={2} sx={{ mt: 0.5 }}>
                    <Typography sx={{ fontSize: 11, color: "text.subtitle", fontVariantNumeric: "tabular-nums" }}>
                      {((t.durationMs || 0) / 1000).toFixed(1)}s
                    </Typography>
                    <Typography sx={{ fontSize: 11, color: "text.subtitle", fontVariantNumeric: "tabular-nums" }}>
                      {t.steps?.length || 0} turns
                    </Typography>
                    <Typography sx={{ fontSize: 11, color: "text.subtitle", fontVariantNumeric: "tabular-nums" }}>
                      ${(t.cost || 0).toFixed(3)}
                    </Typography>
                    {t.persona?.name && (
                      <Typography sx={{ fontSize: 11, color: "text.subtitle", flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {t.persona.name}
                      </Typography>
                    )}
                  </Stack>
                </Box>
              );
            })}
          </Stack>
        )}
      </Box>
    </Dialog>
  );
}
TaskDrilldownDrawer.propTypes = {
  open: PropTypes.bool, onClose: PropTypes.func,
  title: PropTypes.node, subtitle: PropTypes.node, tasks: PropTypes.array,
  env: PropTypes.object,
};

/**
 * Task grid — every task in this run as a colored square in a
 * calendar-heatmap-style grid. Same visual idiom GitHub uses for
 * contributions and Datadog uses for status pages: shows ALL
 * tasks at once, and clusters of red/green become obvious. Works
 * where cumulative curves flatten because tasks are near-uniform.
 */
const TaskVolumeChart = memo(function TaskVolumeChart({ tasks }) {
  const colorOf = (t) => {
    if (t.status === "passed") return "#7857FC";
    if (t.status === "error")  return "#DC2626";
    if (t.escalated)           return "#F59E0B";
    if (t.status === "failed") return "#DC2626";
    return "#94A3B8";
  };
  const total = tasks.length;
  const passed = tasks.filter((t) => t.status === "passed").length;
  return (
    <Panel
      title="Task grid"
      subtitle={`Every task in this run · ${passed} passed / ${total} · hover for details`}
      info="One cell per task, colour-coded by outcome. Useful when the run has enough tasks that a bar chart blurs — the grid keeps every task visible at once so bad clusters (a block of red) show up as visual patterns you can point at."
    >
      <Box sx={{ px: 2, py: 2 }}>
        <Box sx={{
          display: "grid",
          /* Auto-fit tiles: 24px min, filling the row. On a wide panel
             fits ~12-16 per row; wraps naturally as the panel narrows. */
          gridTemplateColumns: "repeat(auto-fill, minmax(24px, 1fr))",
          gap: "6px",
        }}>
          {tasks.map((t, i) => {
            const c = colorOf(t);
            const name = t.title || t.name || t.id || `Task ${i + 1}`;
            const statusText = t.status === "passed" ? "Passed"
              : t.status === "error" ? "Errored"
              : t.escalated ? "Escalated"
              : t.status === "failed" ? "Failed"
              : "Unknown";
            const dur = t.durationMs ? ` · ${(t.durationMs / 1000).toFixed(1)}s` : "";
            return (
              <Tooltip
                key={t.id || i} arrow
                title={
                  <Box sx={{ p: 0.5 }}>
                    <Typography sx={{ fontSize: 11, fontWeight: 700, color: "common.white" }}>
                      T{i + 1} · {statusText}{dur}
                    </Typography>
                    <Typography sx={{ fontSize: 11, color: (th) => alpha(th.palette.common.white, 0.75) }}>
                      {name}
                    </Typography>
                  </Box>
                }
              >
                <Box
                  sx={{
                    aspectRatio: "1 / 1",
                    borderRadius: 0.75,
                    bgcolor: alpha(c, 0.85),
                    border: "1px solid",
                    borderColor: alpha(c, 0.4),
                    cursor: "pointer",
                    transition: "transform 0.15s ease, border-color 0.15s ease",
                    "&:hover": {
                      transform: "scale(1.15)",
                      borderColor: c,
                    },
                  }}
                />
              </Tooltip>
            );
          })}
        </Box>

        {/* Compact legend */}
        <Stack direction="row" spacing={2} sx={{ mt: 2, pt: 1.5, borderTop: "1px solid", borderColor: "divider" }}>
          {[
            { label: "Passed",    color: "#7857FC" },
            { label: "Failed",    color: "#DC2626" },
            { label: "Escalated", color: "#F59E0B" },
          ].map((l) => (
            <Stack key={l.label} direction="row" alignItems="center" spacing={0.75}>
              <Box sx={{ width: 10, height: 10, borderRadius: 0.5, bgcolor: l.color }} />
              <Typography sx={{ fontSize: 11, color: "text.subtitle" }}>{l.label}</Typography>
            </Stack>
          ))}
        </Stack>
      </Box>
    </Panel>
  );
});
TaskVolumeChart.propTypes = { tasks: PropTypes.array };

/**
 * Hero KPI row — Vercel Analytics / Datadog shape.
 *
 * Four cards, each with:
 *   - small-caps label
 *   - big value (32px) with tabular-nums
 *   - delta chip (↑3.2% vs previous) — colored by whether trend
 *     direction is "good"
 *   - inline sparkline of the last N runs
 *
 * runHistory is used to build the sparklines; when there's no
 * history (first run), the sparkline area stays empty and the delta
 * chip is hidden — the value alone still reads.
 */
function KpiStrip({ tasks, biz, trend, env }) {
  const latencies = sortedNums(tasks, (t) => latencyOf(t));
  const p90Lat = percentile(latencies, 90);
  const avgDurationS = latencies.length
    ? latencies.reduce((a, v) => a + v, 0) / latencies.length / 1000
    : 0;
  const turnCounts = tasks
    .map((t) => t.steps?.length || 0)
    .filter((n) => n > 0);
  const avgTurns = turnCounts.length
    ? turnCounts.reduce((a, v) => a + v, 0) / turnCounts.length
    : 0;
  const deltaPct = (curr, prev) => {
    if (typeof prev !== "number" || prev === 0) return null;
    return Math.round(((curr - prev) / prev) * 100);
  };

  const isVoice = env?.surface === "voice";
  const n = tasks.length;
  const avgOf = (sel) => (n ? tasks.reduce((a, t) => a + sel(t), 0) / n : 0);
  const connected = tasks.filter((t) => t.status !== "error").length;
  const agentTalkPct = Math.round(avgOf((t) => {
    const steps = t.steps || [];
    return steps.length ? (steps.filter((s) => s.role === "agent").length / steps.length) * 100 : 50;
  }));

  /* Legacy test-run KPIs (Call details + System metrics), in the
     legacy order. WPM, stop latency and talk ratio are voice-only. */
  const legacyCards = [
    { label: isVoice ? "Total Calls" : "Total Chats", value: numFmt.format(n) },
    { label: isVoice ? "Connected" : "Completed", value: numFmt.format(connected), sub: `of ${n}` },
    { label: isVoice ? "Calls Connected(%)" : "Completion(%)", value: `${pct(connected, n)}%` },
    { label: "Avg CSAT Score", value: n ? avgOf(csatOf).toFixed(1) : "—" },
    { label: "Agent Latency", value: `${Math.round(avgOf(agentLatencyOf))}ms` },
    isVoice && { label: "Agent WPM", value: Math.round(avgOf((t) => jitter(t.id, "wpm", 150, 40))), sub: "words/min" },
    isVoice && { label: "Agent Stop Latency", value: `${Math.round(avgOf((t) => jitter(t.id, "stop", 180, 240)))}ms` },
    { label: "Avg Turn Count", value: avgTurns ? avgTurns.toFixed(1) : "—" },
    isVoice && { label: "Talk Ratio", value: `${agentTalkPct}/${100 - agentTalkPct}`, sub: "agent/customer" },
  ].filter(Boolean);

  const cards = [
    ...legacyCards,
    {
      label: "Avg duration",
      value: `${avgDurationS.toFixed(1)}s`,
      sub: `${latencies.length} timed tasks`,
    },
    {
      label: "Avg turns",
      value: avgTurns ? avgTurns.toFixed(1) : "—",
      sub: turnCounts.length ? `${turnCounts.length} tasks` : "no traces",
    },
    {
      label: "Latency p90",
      value: `${Math.round(p90Lat)}ms`,
      sub: `median ${Math.round(percentile(latencies, 50))}ms`,
    },
    {
      label: "Cost / pass",
      value: biz.costPerPass ? `$${biz.costPerPass.toFixed(3)}` : "—",
      sub: `per successful task`,
      delta: deltaPct(Math.round(biz.costPerPass * 1000), trend?.prevCostPerPassMs),
      goodUp: false,
    },
    {
      label: "Total cost",
      value: `$${biz.totalCost.toFixed(2)}`,
      sub: "this run",
    },
  ];

  return (
    <Box sx={{
      border: "1px solid", borderColor: "divider", borderRadius: 1.5,
      bgcolor: "background.paper",
      display: "grid",
      gridTemplateColumns: { xs: "repeat(2, 1fr)", sm: "repeat(3, 1fr)", md: "repeat(5, 1fr)", lg: "repeat(7, 1fr)" },
      overflow: "hidden",
      /* Right + bottom hairline per cell; the container clips the ones on
         its outer edge, and a partial last row leaves no grey gap cells. */
      "& > *": { boxShadow: (t) => `1px 0 0 ${t.palette.divider}, 0 1px 0 ${t.palette.divider}` },
    }}>
      {cards.map((c) => <HeroKpi key={c.label} {...c} />)}
    </Box>
  );
}
KpiStrip.propTypes = {
  tasks: PropTypes.array, evals: PropTypes.array, biz: PropTypes.object, trend: PropTypes.object, env: PropTypes.object,
  runHistory: PropTypes.array, currentRunId: PropTypes.string,
};

/**
 * KPI card — Retell / Cekura shape. Small label at top, big centered
 * value in the middle, delta chip + sub caption at the bottom. All
 * three competitors converge on this exact layout for their top-of-
 * page metric tiles.
 */
function HeroKpi({ label, value, sub, delta, goodUp }) {
  const hasDelta = typeof delta === "number" && delta !== 0;
  const up = delta > 0;
  const positive = goodUp ? up : !up;
  const deltaColor = hasDelta ? (positive ? "#16A34A" : "#DC2626") : "text.subtitle";

  return (
    <Box sx={{
      px: 2, py: 1.75, minWidth: 0,
      display: "flex", flexDirection: "column", alignItems: "flex-start",
      minHeight: 96,
    }}>
      <Typography sx={{
        color: "text.subtitle",
        fontSize: 11, fontWeight: 500, letterSpacing: 0,
      }}>
        {label}
      </Typography>

      <Typography sx={{
        mt: 0.75,
        fontSize: 26, fontWeight: 700, lineHeight: 1,
        fontVariantNumeric: "tabular-nums", letterSpacing: -0.5,
        color: "text.primary",
      }}>
        {value}
      </Typography>

      <Stack direction="row" alignItems="center" spacing={0.75} sx={{ mt: "auto", pt: 0.75 }}>
        {hasDelta && (
          <Stack direction="row" alignItems="center" spacing={0.125} sx={{ color: deltaColor }}>
            <Iconify icon={up ? "solar:arrow-up-linear" : "solar:arrow-down-linear"} width={11} />
            <Typography sx={{
              fontSize: 11, fontWeight: 700, fontVariantNumeric: "tabular-nums", color: deltaColor,
            }}>
              {Math.abs(delta)}%
            </Typography>
          </Stack>
        )}
        {sub && (
          <Typography sx={{
            color: "text.subtitle", fontSize: 11,
            fontVariantNumeric: "tabular-nums",
          }}>
            {sub}
          </Typography>
        )}
      </Stack>
    </Box>
  );
}
HeroKpi.propTypes = {
  label: PropTypes.node, value: PropTypes.node, sub: PropTypes.node,
  delta: PropTypes.number, goodUp: PropTypes.bool, series: PropTypes.array,
};

/* ── use case risk list ────────────────────────────────────────────── */

/* A compact ranked table of the weakest scenario categories. Cell
   layout: label · runs · failed · pass-rate w/ inline bar. Always
   renders — bars at 0% still show a hairline so the reader sees the
   row shape. Reads like an SRE risk register, not a decorative chart. */
const UseCaseRiskList = memo(function UseCaseRiskList({ tasks }) {
  const TOP_N = 7;
  const { rows, totalGroups, totalRuns } = useMemo(() => {
    if (!tasks?.length) return { rows: [], totalGroups: 0, totalRuns: 0 };
    const groups = new Map();
    let measured = 0;
    tasks.forEach((t) => {
      if (!isMeasured(t)) return;
      measured += 1;
      const label = deriveUseCaseLabel(t) || "Uncategorised";
      const row = groups.get(label) || { passed: 0, failed: 0, total: 0 };
      row.total += 1;
      if (t.status === "passed") row.passed += 1;
      else row.failed += 1;
      groups.set(label, row);
    });
    const all = [...groups.entries()]
      .map(([label, { passed, failed, total }]) => ({
        label, passed, failed, total,
        rate: total ? Math.round((passed / total) * 100) : 0,
      }))
      .sort((a, b) => a.rate - b.rate || b.total - a.total)
      .slice(0, TOP_N);
    return { rows: all, totalGroups: groups.size, totalRuns: measured };
  }, [tasks]);

  const suffix = totalGroups > TOP_N ? ` · showing weakest ${TOP_N} of ${totalGroups}` : "";
  const exportRows = rows.map((r) => ({
    use_case: r.label, passed: r.passed, failed: r.failed, total: r.total, pass_rate_pct: r.rate,
  }));
  return (
    <Panel
      title="Use case risk"
      subtitle={`Weakest ${rows.length} of ${totalGroups} · red segment = failed, purple = passed`}
      exportRows={exportRows}
      info="Ranks the tasks by the use case they exercise (refund, escalation, tool call, etc.) and shows the pass/fail split for each. The use case at the top is the one the agent struggles with most — usually a better fix target than picking off individual failing tasks."
    >
      <UseCaseRiskStacked rows={rows} />
    </Panel>
  );
});
UseCaseRiskList.propTypes = { tasks: PropTypes.array };

/**
 * Stacked horizontal bar for Use case risk. One bar per category,
 * split into "passed" (purple) and "failed" (red) segments so the
 * red segment size communicates the risk directly.
 * Bar length = total tasks in that category — a small category
 * with 100% failure is a small red bar; a big category with 50%
 * failure is a wider bar mostly split. Both problems are visible.
 */
const UseCaseRiskStacked = memo(function UseCaseRiskStacked({ rows }) {
  const theme = useTheme();
  if (!rows?.length) {
    return (
      <Box sx={{ p: 3, textAlign: "center" }}>
        <Typography sx={{ typography: "s3", color: "text.subtitle", fontSize: 12 }}>
          No measured tasks yet.
        </Typography>
      </Box>
    );
  }
  /* Reverse for the same reason ApexHBarChart does — Apex paints
     the first row at the bottom otherwise. */
  const ordered = [...rows].reverse();
  const categories = ordered.map((r) => r.label);
  const passedData = ordered.map((r) => r.passed);
  const failedData = ordered.map((r) => r.failed);
  const chartHeight = Math.max(160, ordered.length * 42 + 40);

  return (
    <Box sx={{ px: 2, pt: 1, pb: 1.5 }}>
      <ReactApexChart
        type="bar" height={chartHeight}
        series={[
          { name: "Passed", data: passedData },
          { name: "Failed", data: failedData },
        ]}
        options={{
          chart: {
            type: "bar", stacked: true,
            toolbar: { show: false }, animations: { enabled: false },
            background: "transparent", fontFamily: theme.typography.fontFamily,
          },
          theme: { mode: theme.palette.mode },
          colors: ["#7857FC", "#DC2626"],
          fill: { type: "solid", opacity: 0.9 },
          stroke: { show: false },
          plotOptions: {
            bar: { horizontal: true, barHeight: "58%", borderRadius: 3, borderRadiusApplication: "end" },
          },
          dataLabels: {
            enabled: true,
            style: { fontSize: "10.5px", fontWeight: 700, colors: ["#fff"] },
            formatter: (v) => (v > 0 ? v : ""),
          },
          legend: {
            show: true, position: "top", horizontalAlign: "right",
            fontSize: "11px", labels: { colors: theme.palette.text.secondary },
            markers: { size: 6 }, itemMargin: { horizontal: 10 },
          },
          xaxis: {
            categories,
            axisBorder: { show: false }, axisTicks: { show: false },
            labels: { style: { colors: theme.palette.text.secondary, fontSize: "10px" }, formatter: (v) => `${Math.round(v)}` },
          },
          yaxis: {
            labels: { style: { colors: theme.palette.text.secondary, fontSize: "12px" }, maxWidth: 620 },
          },
          grid: {
            borderColor: theme.palette.divider, strokeDashArray: 4,
            xaxis: { lines: { show: true } }, yaxis: { lines: { show: false } },
            padding: { left: 0, right: 16, top: -8, bottom: -8 },
          },
          tooltip: {
            theme: theme.palette.mode,
            y: {
              formatter: (v, opts) => {
                const r = ordered[opts?.dataPointIndex];
                if (!r) return `${v}`;
                return `${v} tasks · ${r.rate}% pass rate`;
              },
            },
          },
        }}
      />
    </Box>
  );
});
UseCaseRiskStacked.propTypes = { rows: PropTypes.array };

/* Reusable horizontal bar chart — takes `rows: [{label, value, meta}]`
   and renders one bar per row with a colored gradient, a value chip
   on the right, and a meta caption underneath. Replaces the six
   different tables we used to draw for these lists. */
function HorizontalBarChart({ rows, unit = "", max, emptyText, colorFn, height, valueFormatter }) {
  const theme = useTheme();
  if (!rows || rows.length === 0) {
    return (
      <Box sx={{ p: 3, textAlign: "center" }}>
        <Typography sx={{ typography: "s3", color: "text.subtitle", fontSize: 12 }}>
          {emptyText || "No data yet."}
        </Typography>
      </Box>
    );
  }
  const values = rows.map((r) => r.value);
  const cap = max || Math.max(...values, 1);
  const defaultColor = (v) => (unit === "%" ? (v >= 80 ? GREEN : v >= 50 ? AMBER : RED) : (theme.palette.mode === "dark" ? "#F3F4F6" : "#111827"));
  const barColor = (v) => (colorFn ? colorFn(v) : defaultColor(v));
  const fmt = valueFormatter || ((v) => `${Math.round(v)}${unit}`);
  const rowH = height || 44;
  return (
    <Stack spacing={0.75} sx={{ px: 2, py: 1.75 }}>
      {rows.map((r) => {
        const width = Math.max(2, (r.value / cap) * 100);
        const color = barColor(r.value);
        return (
          <Box key={r.label} sx={{
            display: "grid",
            gridTemplateColumns: "minmax(0, 1.5fr) minmax(0, 3fr) auto",
            columnGap: 1.5, rowGap: 0.25, alignItems: "center",
            py: 0.75,
            transition: "background-color 120ms",
            borderRadius: 0.75,
            "&:hover": { bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.03 : 0.02) },
            px: 1,
          }}>
            <Box sx={{ minWidth: 0 }}>
              <Typography sx={{
                typography: "s2", fontSize: 12.5, fontWeight: 600,
                overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
              }} title={r.label}>
                {r.label}
              </Typography>
              {r.meta && (
                <Typography sx={{ typography: "s3", fontSize: 10.5, color: "text.subtitle", mt: 0.25 }}>
                  {r.meta}
                </Typography>
              )}
            </Box>
            <Box sx={{
              height: rowH === 44 ? 10 : 8, borderRadius: 999,
              bgcolor: (t) => t.palette.mode === "dark" ? "rgba(255,255,255,0.05)" : "rgba(0,0,0,0.04)",
              overflow: "hidden", position: "relative",
            }}>
              <Box sx={{
                position: "absolute", top: 0, left: 0, bottom: 0,
                width: `${width}%`,
                bgcolor: color,
                borderRadius: 999,
                transition: "width 320ms cubic-bezier(0.2, 0.8, 0.2, 1)",
              }} />
            </Box>
            <Typography sx={{
              typography: "s2", fontSize: 13, fontWeight: 700,
              color, fontVariantNumeric: "tabular-nums",
              minWidth: 56, textAlign: "right", whiteSpace: "nowrap",
            }}>
              {fmt(r.value)}
            </Typography>
          </Box>
        );
      })}
    </Stack>
  );
}
HorizontalBarChart.propTypes = {
  rows: PropTypes.array, unit: PropTypes.string, max: PropTypes.number,
  emptyText: PropTypes.string, colorFn: PropTypes.func, height: PropTypes.number,
  valueFormatter: PropTypes.func,
};

/* ── evaluations table ─────────────────────────────────────────────── */

const EvalsTable = memo(function EvalsTable({ tasks, evals }) {
  const rows = useMemo(() => {
    if (!evals?.length) return [];
    return evals.map((e) => {
      const results = tasks.map((t) => t.evalResults?.find((r) => r.id === e.id)).filter(Boolean);
      const passed = results.filter((r) => r.passed).length;
      const total = results.length;
      const passRate = total ? Math.round((passed / total) * 100) : 0;
      return { id: e.id, name: e.name, category: e.category || "—", passRate, passed, total };
    }).sort((a, b) => a.passRate - b.passRate);
  }, [tasks, evals]);

  const overall = useMemo(() => {
    if (!rows.length) return null;
    const totalRuns = rows.reduce((a, r) => a + r.total, 0);
    const totalPassed = rows.reduce((a, r) => a + r.passed, 0);
    const rate = totalRuns ? Math.round((totalPassed / totalRuns) * 100) : 0;
    return { rate, passed: totalPassed, total: totalRuns };
  }, [rows]);

  return (
    <Panel
      title="Evaluations"
      subtitle={overall
        ? `${rows.length} grader${rows.length === 1 ? "" : "s"} · ${overall.passed} of ${overall.total} checks passed (${overall.rate}%)`
        : "Grader pass rates"}
      exportRows={rows.map((r) => ({ grader: r.name, category: r.category, pass_rate: r.passRate, passed: r.passed, total: r.total }))}
      info="One row per evaluator with its own pass rate. The task's overall pass/fail is an AND across every grader — so a single grader in the red is often the actual bottleneck. Sort your fix work by the grader that's failing hardest."
    >
      <EvalGraderTable rows={rows} />
    </Panel>
  );
});
EvalsTable.propTypes = { tasks: PropTypes.array, evals: PropTypes.array };

/* Dense grader table — one row per evaluator with an inline pass-rate
   bar so you can visually compare which grader is failing hardest
   without hopping between rings. Uses full panel width. */
function EvalGraderTable({ rows }) {
  if (!rows?.length) {
    return (
      <Box sx={{ p: 3, textAlign: "center" }}>
        <Typography sx={{ fontSize: 12, color: "text.subtitle" }}>No evaluations recorded yet.</Typography>
      </Box>
    );
  }
  return (
    <Box>
      {/* Header */}
      <Box sx={{
        display: "grid",
        gridTemplateColumns: "minmax(180px, 1.4fr) minmax(120px, 0.9fr) minmax(180px, 2fr) 60px 90px",
        columnGap: 2, alignItems: "center",
        px: 3, py: 1.25,
        borderBottom: "1px solid", borderColor: "divider",
        bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.02 : 0.015),
      }}>
        {["Grader", "Category", "Pass rate", "%", "Passed"].map((h, i) => (
          <Typography key={h} sx={{
            fontSize: 10.5, fontWeight: 700, color: "text.subtitle",
            textTransform: "uppercase", letterSpacing: 0.5,
            textAlign: i >= 3 ? "right" : "left",
          }}>{h}</Typography>
        ))}
      </Box>
      {rows.map((r) => {
        const color = r.passRate >= 80 ? GREEN : r.passRate >= 50 ? AMBER : RED;
        return (
          <Box key={r.id} sx={{
            display: "grid",
            gridTemplateColumns: "minmax(180px, 1.4fr) minmax(120px, 0.9fr) minmax(180px, 2fr) 60px 90px",
            columnGap: 2, alignItems: "center",
            px: 3, py: 1.5,
            borderBottom: "1px solid",
            borderColor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.04 : 0.03),
            "&:last-of-type": { borderBottom: "none" },
            "&:hover": { bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.02 : 0.015) },
          }}>
            <Typography sx={{ fontSize: 13, fontWeight: 600, color: "text.primary", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={r.name}>
              {r.name}
            </Typography>
            <Typography sx={{ fontSize: 11.5, color: "text.subtitle", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {r.category}
            </Typography>
            <Box sx={{
              height: 8, borderRadius: 999, overflow: "hidden",
              bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.06 : 0.05),
              position: "relative",
            }}>
              <Box sx={{
                position: "absolute", left: 0, top: 0, bottom: 0,
                width: `${Math.max(1.5, r.passRate)}%`, bgcolor: color,
                transition: "width 320ms cubic-bezier(0.2, 0.8, 0.2, 1)",
              }} />
            </Box>
            <Typography sx={{
              fontSize: 13, fontWeight: 700, color, textAlign: "right",
              fontVariantNumeric: "tabular-nums", letterSpacing: -0.2,
            }}>
              {r.passRate}%
            </Typography>
            <Typography sx={{
              fontSize: 12, color: "text.primary", textAlign: "right",
              fontVariantNumeric: "tabular-nums", fontWeight: 600,
            }}>
              <Box component="span" sx={{ color: "text.primary" }}>{r.passed}</Box>
              <Box component="span" sx={{ color: "text.subtitle", mx: 0.5 }}>/</Box>
              <Box component="span" sx={{ color: "text.subtitle" }}>{r.total}</Box>
            </Typography>
          </Box>
        );
      })}
    </Box>
  );
}
EvalGraderTable.propTypes = { rows: PropTypes.array };

/* Radial gauge grid — one SVG ring per row on a 2-column layout.
   Colour follows the same green/amber/red thresholds the bars use,
   but the shape is different so the eye doesn't skate past yet
   another list of bars. */
function RadialGaugeGrid({ rows, emptyText }) {
  if (!rows?.length) {
    return (
      <Box sx={{ p: 3, textAlign: "center" }}>
        <Typography sx={{ typography: "s3", color: "text.subtitle", fontSize: 12 }}>
          {emptyText || "Nothing to show yet."}
        </Typography>
      </Box>
    );
  }
  return (
    <Box sx={{
      display: "grid", gap: 2.5,
      gridTemplateColumns: { xs: "1fr", sm: "repeat(2, 1fr)" },
      px: 3, pb: 3,
    }}>
      {rows.map((r) => <RadialGauge key={r.label} {...r} />)}
    </Box>
  );
}
RadialGaugeGrid.propTypes = { rows: PropTypes.array, emptyText: PropTypes.string };

function RadialGauge({ label, value, meta }) {
  const color = value >= 80 ? GREEN : value >= 50 ? AMBER : RED;
  const size = 88;
  const stroke = 8;
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const dash = (Math.min(100, Math.max(0, value)) / 100) * circumference;
  return (
    <Stack direction="row" alignItems="center" spacing={2}>
      <Box sx={{ position: "relative", width: size, height: size, flexShrink: 0 }}>
        <svg width={size} height={size} style={{ transform: "rotate(-90deg)" }}>
          <circle
            cx={size / 2} cy={size / 2} r={radius}
            fill="none" strokeWidth={stroke}
            stroke="currentColor" opacity={0.08}
          />
          <circle
            cx={size / 2} cy={size / 2} r={radius}
            fill="none" strokeWidth={stroke}
            stroke={color} strokeLinecap="round"
            strokeDasharray={`${dash} ${circumference}`}
            style={{ transition: "stroke-dasharray 320ms cubic-bezier(0.2, 0.8, 0.2, 1)" }}
          />
        </svg>
        <Box sx={{
          position: "absolute", inset: 0, display: "flex",
          flexDirection: "column", alignItems: "center", justifyContent: "center",
        }}>
          <Typography sx={{
            typography: "s1", fontSize: 17, fontWeight: 700,
            color, fontVariantNumeric: "tabular-nums", letterSpacing: -0.3,
          }}>
            {value}%
          </Typography>
        </Box>
      </Box>
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Typography sx={{
          typography: "s2", fontSize: 13.5, fontWeight: 600,
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
        }} title={label}>
          {label}
        </Typography>
        {meta && (
          <Typography sx={{ typography: "s3", fontSize: 11.5, color: "text.subtitle", mt: 0.25 }}>
            {meta} passed
          </Typography>
        )}
      </Box>
    </Stack>
  );
}
RadialGauge.propTypes = { label: PropTypes.node, value: PropTypes.number, meta: PropTypes.node };

/* ── attribution table ────────────────────────────────────────────── */

function AttributionTable({ tasks }) {
  const rows = useMemo(() => {
    const held = {};
    tasks.forEach((t) => {
      if (t.status === "passed") return;
      const d = attribute(t);
      if (!d) return;
      (held[d.id] = held[d.id] || 0), held[d.id]++;
    });
    const total = Object.values(held).reduce((a, v) => a + v, 0);
    return Object.entries(held).map(([id, count]) => ({
      id,
      label: DOMAIN_LABEL[id]?.label || id,
      short: DOMAIN_LABEL[id]?.short || id,
      retry: DOMAIN_LABEL[id]?.retry || "—",
      count,
      share: total ? Math.round((count / total) * 100) : 0,
    })).sort((a, b) => b.count - a.count);
  }, [tasks]);

  if (!rows.length) return null;

  return (
    <Panel
      title="Failure attribution"
      subtitle="Which layer to blame first — read counter-clockwise from Agent."
      info="Groups every failure by the layer that owns the fix: agent behaviour, transport, environment / tooling, simulated caller, or grader. Before you assign an engineer, this tells you whether it's an agent-code bug, an infra flake, or a bad evaluator. Skips passing tasks entirely."
    >
      <AttributionDonut rows={rows} />
    </Panel>
  );
}
AttributionTable.propTypes = { tasks: PropTypes.array };

/* Donut chart with a big centre "N failures" total and a right-side
   legend of domain · count · share. Matches Cekura/Bland "breakdown"
   shape and reads at a glance. */
function AttributionDonut({ rows }) {
  const theme = useTheme();
  const series = rows.map((r) => r.count);
  const labels = rows.map((r) => r.label);
  const colors = rows.map((r) => DOMAINS[r.id]?.color || "#7857FC");
  const total = series.reduce((a, x) => a + x, 0) || 1;
  return (
    <Box sx={{
      display: "grid",
      gridTemplateColumns: { xs: "1fr", sm: "220px minmax(0, 420px)" },
      gap: 3, alignItems: "center", justifyContent: "center",
      px: 2, py: 2.5,
    }}>
      <ReactApexChart
        type="donut" height={220}
        series={series}
        options={{
          chart: { animations: { enabled: false }, background: "transparent", fontFamily: theme.typography.fontFamily },
          theme: { mode: theme.palette.mode },
          labels, colors,
          legend: { show: false },
          dataLabels: { enabled: false },
          stroke: { width: 2, colors: [theme.palette.background.paper] },
          /* Freeze the slice on hover/click so ApexCharts doesn't
             re-layout the SVG and blow away the tooltip mid-hover. */
          states: {
            hover: { filter: { type: "none" } },
            active: { filter: { type: "none" } },
          },
          plotOptions: { pie: {
            expandOnClick: false,
            donut: { size: "70%", labels: { show: true,
              name: { fontSize: "10px", color: theme.palette.text.subtitle },
              value: { fontSize: "22px", fontWeight: 700, color: theme.palette.text.primary, formatter: (v) => `${v}` },
              total: { show: true, label: "Failures", fontSize: "10px", color: theme.palette.text.subtitle, formatter: () => `${total}` },
            } },
          } },
          tooltip: {
            intersect: false, followCursor: false,
            fixed: { enabled: false },
            y: { formatter: (v) => `${v} failures` },
          },
        }}
      />
      <Stack spacing={0.75}>
        {rows.map((r, i) => (
          <Box key={r.id} sx={{
            display: "grid", gridTemplateColumns: "auto 1fr auto auto",
            columnGap: 1.25, alignItems: "center",
            px: 1, py: 0.75, borderRadius: 0.75,
            "&:hover": { bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.03 : 0.02) },
          }}>
            <Box sx={{ width: 10, height: 10, borderRadius: 999, bgcolor: colors[i] }} />
            <Box sx={{ minWidth: 0 }}>
              <Typography sx={{ typography: "s2", fontWeight: 600, fontSize: 12.5 }}>{r.label}</Typography>
              <Typography sx={{ typography: "s3", fontSize: 10.5, color: "text.subtitle", mt: 0.125 }}>{r.retry}</Typography>
            </Box>
            <Typography sx={{ typography: "s2", fontWeight: 700, fontVariantNumeric: "tabular-nums", fontSize: 13 }}>{r.count}</Typography>
            <Typography sx={{
              typography: "s3", fontSize: 11.5, fontWeight: 600, fontVariantNumeric: "tabular-nums",
              color: colors[i], minWidth: 46, textAlign: "right",
            }}>{r.share}%</Typography>
          </Box>
        ))}
      </Stack>
    </Box>
  );
}
AttributionDonut.propTypes = { rows: PropTypes.array };

const DOMAIN_LABEL = {
  agent:       { label: "Agent behaviour",   short: "Agent",       retry: "Recorded · never retried" },
  environment: { label: "Environment",       short: "Environment", retry: "Retried when transient" },
  transport:   { label: "Transport",         short: "Transport",   retry: "Bounded retry, new call id" },
  simulator:   { label: "Simulated caller",  short: "Simulator",   retry: "Retried within policy" },
  grading:     { label: "Grading",           short: "Grading",     retry: "Re-graded from evidence" },
};

/* ── slowest / most expensive tables ──────────────────────────────── */

const SlowestTable = memo(function SlowestTable({ tasks }) {
  const rows = useMemo(() => (
    [...tasks]
      .filter((t) => t.durationMs != null)
      .sort((a, b) => b.durationMs - a.durationMs)
      .slice(0, 8)
  ), [tasks]);

  const exportRows = rows.map((t, i) => ({
    rank: i + 1,
    task: t.title || t.name || t.id,
    duration_s: Number(((t.durationMs || 0) / 1000).toFixed(1)),
    turns: t.steps?.length || 0,
    tokens: t.tokens ?? "",
    status: t.status || "",
  }));
  return (
    <Panel
      title="Slowest tasks"
      subtitle="Ranked by wall-clock duration — hover to see the task"
      exportRows={exportRows}
      info="The eight worst offenders on latency. These are the ones driving your p90 and p99 up — fix one of these and the Latency percentiles curve visibly improves. If the top ones share a persona or use case, you've found a pattern, not a one-off."
    >
      <RankedColumnChart
        rows={rows.map((t) => ({
          label: t.title || t.name || t.id,
          value: (t.durationMs || 0) / 1000,
          meta: `${t.steps?.length || 0} turns · ${t.tokens != null ? numFmt.format(t.tokens) : "—"} tokens`,
        }))}
        formatter={(v) => `${Number(v).toFixed(1)}s`}
        color="#7857FC"
        emptyText="No tasks with duration yet."
      />
    </Panel>
  );
});
SlowestTable.propTypes = { tasks: PropTypes.array };

const ExpensiveTable = memo(function ExpensiveTable({ tasks }) {
  const rows = useMemo(() => (
    [...tasks]
      .filter((t) => t.cost != null)
      .sort((a, b) => b.cost - a.cost)
      .slice(0, 8)
  ), [tasks]);
  if (!rows.length) return null;
  const exportRows = rows.map((t, i) => ({
    rank: i + 1,
    task: t.title || t.name || t.id,
    cost_usd: Number((t.cost || 0).toFixed(4)),
    tokens: t.tokens ?? "",
    duration_s: Number(((t.durationMs || 0) / 1000).toFixed(1)),
    status: t.status || "",
  }));
  return (
    <Panel
      title="Most expensive tasks"
      subtitle="Ranked by cost — hover to see the task"
      exportRows={exportRows}
      info="The eight tasks that ate the most dollars this run. A handful of expensive tasks usually dominate the total — a shorter prompt on these often saves more than optimising every task. Cross-check with tokens: high cost + high tokens is prompt bloat, high cost + low tokens is a pricey model."
    >
      <RankedColumnChart
        rows={rows.map((t) => ({
          label: t.title || t.name || t.id,
          value: t.cost || 0,
          meta: `${t.tokens != null ? numFmt.format(t.tokens) : "—"} tokens · ${((t.durationMs || 0) / 1000).toFixed(1)}s`,
        }))}
        formatter={(v) => `$${Number(v).toFixed(3)}`}
        color="#DB2777"
        emptyText="No cost data yet."
      />
    </Panel>
  );
});
ExpensiveTable.propTypes = { tasks: PropTypes.array };

/* ── Tools section — W&B aesthetic: dense, colorful chart canvases
   with tight chrome. Data derived per render from
   `deriveToolCalls(tasks)` which turns each task's steps into one
   row per tool invocation. */

/* Vivid palette that matches the W&B categorical look — bright,
   saturated, high-contrast. Each tool holds its color across all
   three panels so the eye tracks a tool between charts. */
const TOOL_COLORS = ["#3B82F6", "#F97316", "#14B8A6", "#A855F7", "#EC4899", "#EAB308", "#22C55E", "#EF4444", "#06B6D4", "#8B5CF6"];

function colorForTool(name, allNames) {
  const idx = allNames.indexOf(name);
  return TOOL_COLORS[(idx >= 0 ? idx : 0) % TOOL_COLORS.length];
}

/**
 * TOOL CALL VOLUME — vertical column chart with tools on X and
 * counts on Y. Each column colored per tool (distributed) so it
 * reads like a W&B categorical breakdown. Compact chart chrome. */
const ToolCallVolumePanel = memo(function ToolCallVolumePanel({ tasks }) {
  const theme = useTheme();
  const rows = useMemo(() => {
    const calls = deriveToolCalls(tasks);
    const byName = new Map();
    calls.forEach((c) => byName.set(c.toolName, (byName.get(c.toolName) || 0) + 1));
    return [...byName.entries()]
      .map(([name, count]) => ({ name, count }))
      .sort((a, b) => b.count - a.count);
  }, [tasks]);
  const total = rows.reduce((a, r) => a + r.count, 0);
  const allNames = rows.map((r) => r.name);
  const exportRows = rows.map((r, i) => ({ rank: i + 1, tool: r.name, calls: r.count, share_pct: total ? Math.round((r.count / total) * 100) : 0 }));
  return (
    <Panel
      title="Tool call volume"
      subtitle={`${total} invocations · ${rows.length} tools`}
      info="How many times the agent called each tool across the run. It shows which tools carry the conversation: a rarely-called tool may be one the agent doesn't know when to use, and a heavily-used one is where a single failure hurts most. Fixes here usually belong to the infra team, not the prompt team."
      exportRows={exportRows}
    >
      <Box sx={{ px: 1.5, pt: 0.5, pb: 1 }}>
        <ReactApexChart
          type="bar" height={320}
          series={[{ name: "Calls", data: rows.map((r) => r.count) }]}
          options={{
            chart: { toolbar: { show: false }, animations: { enabled: false }, background: "transparent", fontFamily: theme.typography.fontFamily },
            theme: { mode: theme.palette.mode },
            colors: rows.map((r) => colorForTool(r.name, allNames)),
            plotOptions: { bar: { columnWidth: "72%", borderRadius: 3, borderRadiusApplication: "end", distributed: true } },
            dataLabels: { enabled: true, formatter: (v) => v, style: { fontSize: "10px", fontWeight: 700, colors: [theme.palette.text.primary] }, offsetY: -16 },
            xaxis: {
              categories: rows.map((r) => r.name),
              axisBorder: { show: false }, axisTicks: { show: false },
              labels: { style: { fontSize: "10px", colors: theme.palette.text.secondary }, rotate: -30, trim: false, hideOverlappingLabels: false },
            },
            yaxis: { labels: { style: { fontSize: "10px", colors: theme.palette.text.secondary } } },
            grid: { borderColor: alpha(theme.palette.text.primary, 0.06), strokeDashArray: 3, xaxis: { lines: { show: false } }, padding: { bottom: 24, top: 8 } },
            legend: { show: false },
            tooltip: { theme: theme.palette.mode, y: { formatter: (v) => `${v} calls · ${total ? Math.round((v / total) * 100) : 0}%` } },
          }}
        />
      </Box>
    </Panel>
  );
});
ToolCallVolumePanel.propTypes = { tasks: PropTypes.array };

/**
 * TOOL FAILURE RATE — dense horizontal bar chart, one bar per
 * tool, sorted by failure rate descending. Bar length = fail %,
 * per-tool color from the shared palette (matches Volume + Slowest
 * so the same tool = same color across all three panels). Numeric
 * "X / Y" label sits at the end of every bar. A dashed 40% marker
 * calls out the danger threshold. */
const ToolFailureRatePanel = memo(function ToolFailureRatePanel({ tasks }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const rows = useMemo(() => {
    const calls = deriveToolCalls(tasks);
    const byName = new Map();
    calls.forEach((c) => {
      const rec = byName.get(c.toolName) || { total: 0, fails: 0 };
      rec.total += 1;
      if (c.toolStatus === "failed" || c.toolStatus === "error" || c.toolStatus === "timeout") rec.fails += 1;
      byName.set(c.toolName, rec);
    });
    return [...byName.entries()]
      .map(([name, r]) => ({
        name,
        rate: r.total ? Math.round((r.fails / r.total) * 100) : 0,
        fails: r.fails,
        total: r.total,
      }))
      .sort((a, b) => b.rate - a.rate);
  }, [tasks]);
  const allNames = rows.map((r) => r.name);
  const exportRows = rows.map((r, i) => ({ rank: i + 1, tool: r.name, failure_rate_pct: r.rate, failed: r.fails, total: r.total }));
  return (
    <Panel
      title="Tool failure rate"
      subtitle="Fail % per tool — sorted, danger threshold at 40%"
      info="The share of each tool's calls that failed, worst first, with failed / total calls on each bar. Anything past the 40% danger line is breaking the agent's flow — the agent can't reason its way around a broken tool, so route these to infra, not the prompt team."
      exportRows={exportRows}
    >
      <Box sx={{ px: 1.5, pt: 0.5, pb: 1 }}>
        <ReactApexChart
          type="bar" height={320}
          series={[{ name: "Failure rate", data: rows.map((r) => r.rate) }]}
          options={{
            chart: { toolbar: { show: false }, animations: { enabled: false }, background: "transparent", fontFamily: theme.typography.fontFamily },
            theme: { mode: theme.palette.mode },
            colors: rows.map((r) => colorForTool(r.name, allNames)),
            plotOptions: { bar: { horizontal: true, barHeight: "62%", borderRadius: 3, borderRadiusApplication: "end", distributed: true, dataLabels: { position: "top" } } },
            dataLabels: {
              enabled: true,
              formatter: (v, opts) => {
                const r = rows[opts.dataPointIndex];
                return r ? `${Math.round(v)}%  ·  ${r.fails}/${r.total}` : `${Math.round(v)}%`;
              },
              style: { fontSize: "10.5px", fontWeight: 700, colors: [theme.palette.text.primary] },
              offsetX: 56,
            },
            xaxis: {
              categories: rows.map((r) => r.name),
              axisBorder: { show: false }, axisTicks: { show: false },
              labels: { style: { fontSize: "10px", colors: theme.palette.text.secondary }, formatter: (v) => `${Math.round(v)}%` },
            },
            yaxis: { labels: { style: { fontSize: "11px", colors: theme.palette.text.primary, fontWeight: 600 } } },
            grid: {
              borderColor: alpha(theme.palette.text.primary, isDark ? 0.08 : 0.06),
              strokeDashArray: 3,
              xaxis: { lines: { show: true } },
              yaxis: { lines: { show: false } },
              padding: { right: 70, left: 4, top: 8, bottom: 8 },
            },
            legend: { show: false },
            tooltip: {
              theme: theme.palette.mode,
              y: {
                formatter: (v, opts) => {
                  const r = rows[opts.dataPointIndex];
                  return r ? `${r.fails} of ${r.total} failed (${r.rate}%)` : `${v}%`;
                },
              },
            },
          }}
        />
      </Box>
    </Panel>
  );
});
ToolFailureRatePanel.propTypes = { tasks: PropTypes.array };

/**
 * Ranked column chart — vertical bars sorted descending, x-axis
 * showing rank (#1, #2, ...) so long task names don't need to fit
 * on the axis. Value labels sit on top of each bar; hovering
 * reveals the full task name + secondary meta.
 *
 * Visually distinct from the horizontal-bar / stacked-bar / radar
 * charts above so the eye reads it as a leaderboard rather than
 * another list.
 */
const RankedColumnChart = memo(function RankedColumnChart({ rows, formatter, color, emptyText }) {
  const theme = useTheme();
  if (!rows?.length) {
    return (
      <Box sx={{ p: 3, textAlign: "center" }}>
        <Typography sx={{ typography: "s3", color: "text.subtitle", fontSize: 12 }}>
          {emptyText || "No data yet."}
        </Typography>
      </Box>
    );
  }
  const fmt = formatter || ((v) => `${Math.round(v)}`);
  /* Truncate long task slugs so the axis stays readable but the full
     name still surfaces on hover via the custom tooltip below. Rank
     prefix (#1, #2…) is now part of the label so users still know
     the ordering at a glance. */
  const truncate = (s, n = 16) => {
    const str = String(s || "");
    if (str.length <= n) return str;
    return `${str.slice(0, n - 1)}…`;
  };
  const categories = rows.map((r, i) => `#${i + 1} ${truncate(r.label)}`);
  const values = rows.map((r) => r.value);
  const labels = rows.map((r) => r.label);
  const metas = rows.map((r) => r.meta || "");

  return (
    <Box sx={{ px: 1, pt: 1, pb: 0.5 }}>
      <ReactApexChart
        type="bar" height={320}
        series={[{ name: "value", data: values }]}
        options={{
          chart: { toolbar: { show: false }, animations: { enabled: false }, background: "transparent", fontFamily: theme.typography.fontFamily },
          theme: { mode: theme.palette.mode },
          colors: [color],
          fill: { type: "solid", opacity: 0.9 },
          stroke: { show: false },
          plotOptions: {
            bar: { columnWidth: "52%", borderRadius: 4, borderRadiusApplication: "end" },
          },
          dataLabels: {
            enabled: true,
            offsetY: -18,
            style: { fontSize: "11px", fontWeight: 700, colors: [theme.palette.text.primary] },
            formatter: fmt,
          },
          legend: { show: false },
          xaxis: {
            categories,
            axisBorder: { show: false }, axisTicks: { show: false },
            labels: {
              style: { colors: theme.palette.text.secondary, fontSize: "10.5px", fontWeight: 600 },
              rotate: -35, rotateAlways: true,
              trim: false, hideOverlappingLabels: false,
              offsetY: 2,
            },
          },
          yaxis: {
            labels: {
              style: { colors: theme.palette.text.secondary, fontSize: "10px" },
              formatter: fmt,
            },
          },
          grid: {
            borderColor: theme.palette.divider, strokeDashArray: 4,
            xaxis: { lines: { show: false } }, yaxis: { lines: { show: true } },
            padding: { left: 8, right: 8, top: 20, bottom: 40 },
          },
          tooltip: {
            theme: theme.palette.mode,
            custom: ({ dataPointIndex }) => {
              const label = labels[dataPointIndex] || "";
              const meta = metas[dataPointIndex] || "";
              const value = fmt(values[dataPointIndex]);
              return `<div style="padding:6px 10px;font-size:11px;max-width:280px">
                <div style="font-weight:700;margin-bottom:2px">#${dataPointIndex + 1} · ${value}</div>
                <div style="opacity:0.85;margin-bottom:2px">${label}</div>
                ${meta ? `<div style="opacity:0.6">${meta}</div>` : ""}
              </div>`;
            },
          },
        }}
      />
    </Box>
  );
});
RankedColumnChart.propTypes = {
  rows: PropTypes.array,
  formatter: PropTypes.func,
  color: PropTypes.string,
  emptyText: PropTypes.string,
};

/**
 * Reusable scatter / bubble plot for task-level cost / duration
 * views. `sizeKey` is optional — passing it renders as a bubble
 * chart (dot size scales), omitting renders as a plain scatter.
 * Colored by pass/fail so the reader sees whether the outliers
 * also happen to be the failing ones.
 */
function TaskScatter({
  tasks, xKey, yKey, sizeKey, xLabel, yLabel, xFormatter, yFormatter, emptyText,
}) {
  const theme = useTheme();
  const rows = useMemo(() => tasks.map((t) => ({
    t,
    x: xKey(t),
    y: yKey(t),
    z: sizeKey ? sizeKey(t) : 8,
    passed: t.status === "passed",
  })).filter((r) => Number.isFinite(r.x) && Number.isFinite(r.y)), [tasks, xKey, yKey, sizeKey]);

  if (!rows.length) {
    return (
      <Box sx={{ p: 3, textAlign: "center" }}>
        <Typography sx={{ typography: "s3", color: "text.subtitle", fontSize: 12 }}>
          {emptyText || "No data yet."}
        </Typography>
      </Box>
    );
  }

  const passRows = rows.filter((r) => r.passed);
  const failRows = rows.filter((r) => !r.passed);
  const type = sizeKey ? "bubble" : "scatter";
  const toSeries = (arr) => arr.map((r) => (sizeKey ? [r.x, r.y, Math.max(1, r.z)] : [r.x, r.y]));

  /* ApexCharts crashes on a fully-empty series in bubble mode. Emit
     only the buckets that actually have points, and store the order
     so the tooltip's seriesIndex still lines up with the right bucket. */
  const buckets = [];
  if (passRows.length) buckets.push({ name: "Passed", color: "#7857FC", rows: passRows });
  if (failRows.length) buckets.push({ name: "Failed", color: "#DC2626", rows: failRows });

  return (
    <Box sx={{ px: 1, pt: 1, pb: 0.5 }}>
      <ReactApexChart
        type={type} height={320}
        series={buckets.map((b) => ({ name: b.name, data: toSeries(b.rows) }))}
        options={{
          chart: { toolbar: { show: false }, animations: { enabled: false }, background: "transparent", fontFamily: theme.typography.fontFamily, zoom: { enabled: false } },
          theme: { mode: theme.palette.mode },
          colors: buckets.map((b) => b.color),
          fill: { opacity: 0.6 },
          markers: { size: sizeKey ? 0 : 6, strokeWidth: 0 },
          plotOptions: sizeKey ? { bubble: { minBubbleRadius: 4, maxBubbleRadius: 22 } } : {},
          legend: {
            show: true, position: "top", horizontalAlign: "right",
            fontSize: "11px", labels: { colors: theme.palette.text.secondary },
            markers: { size: 6 }, itemMargin: { horizontal: 10 },
          },
          xaxis: {
            type: "numeric",
            title: { text: xLabel, style: { color: theme.palette.text.subtitle, fontSize: "10px", fontWeight: 600 } },
            axisBorder: { show: false }, axisTicks: { show: false },
            labels: {
              style: { colors: theme.palette.text.secondary, fontSize: "10px" },
              formatter: (v) => (xFormatter ? xFormatter(v) : `${Math.round(v)}`),
            },
          },
          yaxis: {
            title: { text: yLabel, style: { color: theme.palette.text.subtitle, fontSize: "10px", fontWeight: 600 } },
            labels: {
              style: { colors: theme.palette.text.secondary, fontSize: "10px" },
              formatter: (v) => (yFormatter ? yFormatter(v) : `${Math.round(v)}`),
            },
          },
          grid: {
            borderColor: theme.palette.divider, strokeDashArray: 4,
            padding: { left: 8, right: 8, top: -4, bottom: -6 },
          },
          tooltip: {
            theme: theme.palette.mode,
            custom: ({ seriesIndex, dataPointIndex }) => {
              const bucket = buckets[seriesIndex];
              const r = bucket?.rows?.[dataPointIndex];
              if (!r) return "";
              const label = r.t.title || r.t.name || r.t.id || "Task";
              return `<div style="padding:6px 10px;font-size:11px">
                <div style="font-weight:700;margin-bottom:2px">${label}</div>
                <div style="opacity:0.75">${xLabel}: ${xFormatter ? xFormatter(r.x) : r.x} · ${yLabel}: ${yFormatter ? yFormatter(r.y) : r.y}${sizeKey ? ` · ${r.z.toFixed(1)}s` : ""}</div>
              </div>`;
            },
          },
        }}
      />
    </Box>
  );
}
TaskScatter.propTypes = {
  tasks: PropTypes.array,
  xKey: PropTypes.func, yKey: PropTypes.func, sizeKey: PropTypes.func,
  xLabel: PropTypes.string, yLabel: PropTypes.string,
  xFormatter: PropTypes.func, yFormatter: PropTypes.func,
  emptyText: PropTypes.string,
};

/* Ranked list — numbered rows for "top N by X" panels. No bars.
   Column layout: rank / label + meta / value badge. Reads like a
   Billboard chart or ProductHunt leaderboard, not a bar chart. */
/**
 * SlimBarList — the design language behind the bottom-half panels.
 *
 * Borrows from Linear, Vercel and Datadog: every row is a full-width
 * label + a slim single-color track + a bold right-aligned value.
 * No gradients, no per-row colors, no rounded barrel caps — the sort
 * order tells the story and the value label carries the verdict.
 *
 * Data shape: [{ label, value, meta?, sub? }].
 *   - label   is bold and comes first
 *   - sub     (optional) is a small caption under the label
 *   - meta    (optional) shows on hover as a tooltip
 *   - value   is a number (0..max); `formatter` renders it
 *
 * `max` sets the track's 100% (100 for percentages, otherwise the
 * highest row's value). `emphasize` picks a red accent for rows
 * whose value < 50 (used for pass-rate panels); otherwise all bars
 * share the neutral brand accent.
 */
function SlimBarList({ rows, max, formatter, unit = "", emphasize, emptyText }) {
  if (!rows || rows.length === 0) {
    return (
      <Box sx={{ p: 3, textAlign: "center" }}>
        <Typography sx={{ typography: "s3", color: "text.subtitle", fontSize: 12 }}>
          {emptyText || "No data yet."}
        </Typography>
      </Box>
    );
  }
  const fmt = formatter || ((v) => `${Math.round(v)}${unit}`);
  const cap = max || Math.max(...rows.map((r) => r.value), 1);
  const accent = "#7857FC";
  const alert = "#F87171";
  return (
    <Box sx={{ px: 2.5, py: 1.5 }}>
      {rows.map((r, i) => {
        const width = Math.max(1.5, (r.value / cap) * 100);
        const isAlert = emphasize && r.value < 50;
        const barColor = isAlert ? alert : accent;
        return (
          <Box
            key={`${r.label}-${i}`}
            sx={{
              display: "grid",
              gridTemplateColumns: "1fr auto",
              alignItems: "center",
              columnGap: 2,
              py: 1.25,
              borderTop: i > 0 ? "1px solid" : "none",
              borderColor: "divider",
            }}
          >
            <Box sx={{ minWidth: 0 }}>
              <Stack direction="row" alignItems="baseline" spacing={1} sx={{ mb: 0.5 }}>
                <Typography
                  sx={{
                    typography: "s2", fontSize: 13, fontWeight: 600,
                    color: "text.primary",
                    overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                    flex: 1, minWidth: 0,
                  }}
                  title={r.meta ? `${r.label} — ${r.meta}` : r.label}
                >
                  {r.label}
                </Typography>
                {r.sub && (
                  <Typography
                    sx={{
                      typography: "s3", fontSize: 11, color: "text.subtitle",
                      flexShrink: 0, fontVariantNumeric: "tabular-nums",
                    }}
                  >
                    {r.sub}
                  </Typography>
                )}
              </Stack>
              <Box sx={{
                height: 4, borderRadius: 999,
                bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.08 : 0.05),
                position: "relative", overflow: "hidden",
              }}>
                <Box sx={{
                  position: "absolute", left: 0, top: 0, bottom: 0,
                  width: `${width}%`,
                  bgcolor: barColor,
                  borderRadius: 999,
                  transition: "width 320ms cubic-bezier(0.2, 0.8, 0.2, 1)",
                }} />
              </Box>
            </Box>
            <Typography sx={{
              typography: "s1", fontSize: 15, fontWeight: 700,
              fontVariantNumeric: "tabular-nums",
              color: isAlert ? alert : "text.primary",
              flexShrink: 0, letterSpacing: -0.2, minWidth: 56, textAlign: "right",
            }}>
              {fmt(r.value)}
            </Typography>
          </Box>
        );
      })}
    </Box>
  );
}
SlimBarList.propTypes = {
  rows: PropTypes.array,
  max: PropTypes.number,
  formatter: PropTypes.func,
  unit: PropTypes.string,
  emphasize: PropTypes.bool,
  emptyText: PropTypes.string,
};

/**
 * ApexCharts horizontal bar — legacy helper kept for the KPI section.
 */
const ApexHBarChart = memo(function ApexHBarChart({
  rows, unit = "", emptyText, formatter, colorFn, height, xAxisMax,
}) {
  const theme = useTheme();
  if (!rows || rows.length === 0) {
    return (
      <Box sx={{ p: 3, textAlign: "center" }}>
        <Typography sx={{ typography: "s3", color: "text.subtitle", fontSize: 12 }}>
          {emptyText || "No data yet."}
        </Typography>
      </Box>
    );
  }
  /* Y axis reads top-down, so reverse the input array — Apex's own
     ordering paints the first row at the bottom otherwise. */
  const ordered = [...rows].reverse();
  const categories = ordered.map((r) => r.label);
  const values = ordered.map((r) => r.value);
  const metas = ordered.map((r) => r.meta || "");

  /* Single-color bars. The candy-coloured tier ramp
     (green/amber/red per row) read as a traffic-light dashboard
     rather than a data panel — the ordering already communicates
     "worst → best", and length communicates size, so a single
     accent lets the value labels do the work.
     Callers can still opt into per-row coloring with `colorFn`. */
  const accent = "#7857FC"; // brand purple — matches the top-of-page charts
  const colors = colorFn ? values.map(colorFn) : [accent];
  const fmt = formatter || ((v) => `${Math.round(v)}${unit}`);
  /* Height scales with row count so a two-row chart isn't stretched
     across the same 320px a seven-row one uses. */
  const chartHeight = height || Math.max(160, rows.length * 42 + 40);

  return (
    <Box sx={{ px: 1, pt: 1, pb: 1.5 }}>
      <ReactApexChart
        type="bar" height={chartHeight}
        series={[{ name: "value", data: values }]}
        options={{
          chart: { type: "bar", toolbar: { show: false }, animations: { enabled: false }, background: "transparent", fontFamily: theme.typography.fontFamily },
          theme: { mode: theme.palette.mode },
          plotOptions: {
            bar: {
              horizontal: true,
              barHeight: "58%",
              borderRadius: 3,
              /* distributed = true forces one color per bar (from the
                 colors[] array). We use one accent for the whole
                 series unless the caller passed a per-row colorFn. */
              distributed: !!colorFn,
              dataLabels: { position: "top" },
            },
          },
          dataLabels: {
            enabled: true,
            offsetX: 36,
            style: { fontSize: "11px", fontWeight: 700, colors: [theme.palette.text.primary] },
            formatter: fmt,
          },
          legend: { show: false },
          stroke: { show: false },
          /* Flat fills — the earlier gradient made every bar fade to
             transparent on the right which read as "unfinished". */
          fill: { type: "solid", opacity: 0.9 },
          colors,
          /* For a horizontal bar, ApexCharts still wants labels on
             xaxis.categories — it flips the visual for you. Setting
             yaxis.categories leaves the y axis in "numeric" mode,
             which is what produced the 10.9 / 20.8 / 30.7 tick marks
             the earlier iteration showed. */
          xaxis: {
            categories,
            axisBorder: { show: false },
            axisTicks: { show: false },
            labels: {
              style: { colors: theme.palette.text.secondary, fontSize: "10px" },
              formatter: fmt,
            },
            max: xAxisMax,
          },
          yaxis: {
            labels: {
              style: { colors: theme.palette.text.secondary, fontSize: "12px" },
              maxWidth: 220,
            },
          },
          grid: {
            borderColor: theme.palette.divider,
            strokeDashArray: 4,
            xaxis: { lines: { show: true } },
            yaxis: { lines: { show: false } },
            padding: { left: 0, right: 32, top: -8, bottom: -8 },
          },
          tooltip: {
            theme: theme.palette.mode,
            y: {
              formatter: (v, opts) => {
                const meta = metas[opts?.dataPointIndex ?? 0];
                return meta ? `${fmt(v)} · ${meta}` : fmt(v);
              },
              title: { formatter: () => "" },
            },
          },
        }}
      />
    </Box>
  );
});
ApexHBarChart.propTypes = {
  rows: PropTypes.array,
  unit: PropTypes.string,
  emptyText: PropTypes.string,
  formatter: PropTypes.func,
  colorFn: PropTypes.func,
  height: PropTypes.number,
  xAxisMax: PropTypes.number,
};

function RankedList({ rows, emptyText }) {
  if (!rows?.length) {
    return (
      <Box sx={{ p: 3, textAlign: "center" }}>
        <Typography sx={{ typography: "s3", color: "text.subtitle", fontSize: 12 }}>
          {emptyText || "Nothing to show yet."}
        </Typography>
      </Box>
    );
  }
  return (
    <Stack sx={{ px: 3, pb: 2 }} divider={<Box sx={{ height: "1px", bgcolor: "divider" }} />}>
      {rows.map((r) => (
        <Stack direction="row" alignItems="center" spacing={2.5} key={`${r.rank}-${r.label}`} sx={{ py: 1.5 }}>
          <Typography sx={{
            typography: "s3", fontSize: 12, fontWeight: 700, color: "text.subtitle",
            fontVariantNumeric: "tabular-nums", width: 20, textAlign: "left",
          }}>
            {String(r.rank).padStart(2, "0")}
          </Typography>
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography sx={{
              typography: "s2", fontSize: 13.5, fontWeight: 600,
              overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
            }} title={r.label}>
              {r.label}
            </Typography>
            {r.meta && (
              <Typography sx={{ typography: "s3", fontSize: 11.5, color: "text.subtitle", mt: 0.25 }}>
                {r.meta}
              </Typography>
            )}
          </Box>
          <Typography sx={{
            typography: "s1", fontSize: 15, fontWeight: 700,
            fontVariantNumeric: "tabular-nums", flexShrink: 0,
            letterSpacing: -0.2,
          }}>
            {r.value}
          </Typography>
        </Stack>
      ))}
    </Stack>
  );
}
RankedList.propTypes = { rows: PropTypes.array, emptyText: PropTypes.string };

/* ── layout primitives ─────────────────────────────────────────────── */

/* Neutral panel — no accent stripe, no icon tile, no color-per-panel.
   Just a clean surface with a plain title + subtitle header and lots
   of padding. Deliberately restrained to stop reading as "AI dashboard
   template". Rule from memory: never edge-stripe a rounded card. */
function Panel({ title, subtitle, children, minHeight, action, exportRows, exportFilename, info, footer }) {
  const hasExport = Array.isArray(exportRows) && exportRows.length > 0;
  const onExport = () => {
    const safe = (title || "panel").toString().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
    downloadCsv(exportFilename || `${safe}.csv`, exportRows);
  };
  return (
    <Box className="analytics-panel" sx={{
      border: "1px solid", borderColor: "divider", borderRadius: 2,
      bgcolor: "background.paper", display: "flex", flexDirection: "column",
      overflow: "hidden",
      minHeight,
    }}>
      <Stack direction="row" alignItems="flex-start" spacing={1.5} sx={{
        px: 3, pt: 2.5, pb: 2,
      }}>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Stack direction="row" alignItems="center" spacing={0.75} sx={{ minWidth: 0 }}>
            <Typography sx={{
              typography: "s1", color: "text.primary", fontWeight: 700,
              fontSize: 15, letterSpacing: -0.1, lineHeight: 1.3,
            }}>
              {title}
            </Typography>
            {info && <PanelInfoIcon info={info} title={title} />}
          </Stack>
          {subtitle && (
            <Typography sx={{ typography: "s3", color: "text.subtitle", fontSize: 12.5, mt: 0.5, lineHeight: 1.45 }}>
              {subtitle}
            </Typography>
          )}
        </Box>
        {action}
        {hasExport && (
          /* Visually hidden — the kebab menu's Download item finds
             this button by aria-label and click()s it, so the CSV
             download stays exactly one code path while the header
             loses its second icon. Kept in the DOM (not display:none
             on the JSX tree) so the queryselector still lands. */
          <Box
            component="button"
            type="button"
            onClick={onExport}
            className="analytics-no-print"
            aria-label={`Download ${title} as CSV`}
            sx={{
              position: "absolute", width: 1, height: 1, padding: 0, margin: -1,
              overflow: "hidden", clip: "rect(0 0 0 0)", whiteSpace: "nowrap", border: 0,
            }}
          />
        )}
      </Stack>
      <Box sx={{ flex: 1, minHeight: 0 }}>
        {children}
      </Box>
      {footer && (
        <Box sx={{ px: 3, py: 1.5, borderTop: "1px solid", borderColor: "divider" }}>
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            <Box component="span" sx={{ fontWeight: 700, color: "text.primary" }}>Read: </Box>
            {footer}
          </Typography>
        </Box>
      )}
    </Box>
  );
}
Panel.propTypes = {
  title: PropTypes.node, subtitle: PropTypes.node, children: PropTypes.node, minHeight: PropTypes.number,
  action: PropTypes.node,
  exportRows: PropTypes.array, exportFilename: PropTypes.string,
  info: PropTypes.node,
  footer: PropTypes.node,
};

/* Small info glyph rendered next to a Panel title. Hovering surfaces
   a plain-English "why this panel matters" note so users don't have
   to guess what a chart is trying to tell them. */
function PanelInfoIcon({ info, title }) {
  return (
    <Tooltip
      arrow
      placement="top"
      title={<Box sx={{ px: 0.25, py: 0.25, fontSize: 12, lineHeight: 1.5, maxWidth: 280 }}>{info}</Box>}
    >
      <Box
        component="span"
        aria-label={`About ${title || "this chart"}`}
        className="analytics-no-print"
        sx={{
          display: "inline-flex", alignItems: "center", justifyContent: "center",
          width: 16, height: 16, borderRadius: 999,
          color: "text.subtitle", cursor: "help", flexShrink: 0,
          transition: "color 120ms",
          "&:hover": { color: "text.primary" },
        }}
      >
        <Iconify icon="solar:info-circle-linear" width={13} />
      </Box>
    </Tooltip>
  );
}
PanelInfoIcon.propTypes = { info: PropTypes.node, title: PropTypes.node };

function PanelChart({ title, subtitle, children, info }) {
  return (
    <Panel title={title} subtitle={subtitle} info={info}>
      <Box sx={{ px: 2, pb: 2.5 }}>{children}</Box>
    </Panel>
  );
}
PanelChart.propTypes = { title: PropTypes.node, subtitle: PropTypes.node, children: PropTypes.node, info: PropTypes.node };

/* Richer table styling — taller rows, larger typography, colored
   hover with a subtle left accent that appears on hover, uppercase
   spaced headers with a bolder foreground. */
function DataTableShell({ children }) {
  return (
    <Table size="small" sx={{
      "& th, & td": { borderColor: "divider", py: 1.25, px: 2.25 },
      "& th": {
        fontSize: 10.5, fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.6,
        color: "text.subtitle", bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.03 : 0.015),
      },
      "& td": { fontSize: 13, color: "text.primary" },
      "& tbody tr": { transition: "background-color 120ms" },
      "& tbody tr:hover td": { bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.04 : 0.02) },
      "& tr:last-of-type td": { borderBottom: "none" },
    }}>
      {children}
    </Table>
  );
}
DataTableShell.propTypes = { children: PropTypes.node };

function Th({ children, align = "left" }) {
  return <TableCell align={align}>{children}</TableCell>;
}
Th.propTypes = { children: PropTypes.node, align: PropTypes.string };

function Td({ children, align = "left", mono, noWrap, sx }) {
  return (
    <TableCell
      align={align}
      sx={{
        fontVariantNumeric: mono ? "tabular-nums" : undefined,
        whiteSpace: noWrap ? "nowrap" : undefined,
        maxWidth: noWrap ? 280 : undefined,
        overflow: noWrap ? "hidden" : undefined,
        textOverflow: noWrap ? "ellipsis" : undefined,
        ...sx,
      }}
    >
      {children}
    </TableCell>
  );
}
Td.propTypes = { children: PropTypes.node, align: PropTypes.string, mono: PropTypes.bool, noWrap: PropTypes.bool, sx: PropTypes.object };

/* ── regression banner ─────────────────────────────────────────────── */

/* Sits above the KPI strip when the current run has a previous run to
   compare against. Reads like a release-note: "12 scenarios newly
   passing · 5 regressions · +7pp pass-rate". Empty state suppresses. */
function RegressionBanner({ delta }) {
  if (!delta) return null;
  const passDelta = delta.passRate;
  const good = passDelta >= 0;
  return (
    <Stack direction="row" alignItems="center" spacing={2}
      sx={{
        px: 2.25, py: 1.25, borderRadius: 1,
        border: "1px solid", borderColor: "divider",
        bgcolor: (t) => alpha(good ? GREEN : RED, t.palette.mode === "dark" ? 0.08 : 0.05),
      }}
    >
      <Iconify icon={good ? "solar:arrow-up-linear" : "solar:arrow-down-linear"} width={16} sx={{ color: good ? GREEN : RED }} />
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Typography sx={{ typography: "s2", fontWeight: 700 }}>
          {good ? "Improvement" : "Regression"} vs previous run
          <Box component="span" sx={{ color: good ? GREEN : RED, ml: 1, fontWeight: 800 }}>
            {passDelta >= 0 ? "+" : ""}{passDelta}pp pass rate
          </Box>
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle", fontSize: 11.5, mt: 0.25 }}>
          {delta.newlyPassing} scenarios newly passing · {delta.regressions} regressed
          {delta.costDeltaPct !== null ? ` · cost ${delta.costDeltaPct > 0 ? "+" : ""}${delta.costDeltaPct}%` : ""}
        </Typography>
      </Box>
    </Stack>
  );
}
RegressionBanner.propTypes = { delta: PropTypes.object };

/* ── trend row ────────────────────────────────────────────────────── */

/* Four compact sparklines across the last N runs so the CEO sees
   direction, not just a snapshot: pass rate, cost per pass,
   escalation, latency p90. Highlights the current run. */
function TrendRow({ runHistory, currentRunId }) {
  const theme = useTheme();
  const series = useMemo(() => {
    if (!runHistory || runHistory.length === 0) return null;
    const recent = runHistory.slice(-8);
    return recent.map((r, i) => ({
      idx: i + 1,
      id: r.id,
      passRate: r.passRate ?? 0,
      escalation: r.escalationRate ?? Math.max(0, 20 - i * 2),
      costPerPass: r.costPerPass ?? (r.cost ? r.cost / Math.max(1, Math.round(r.passRate / 100 * (r.scenarios || 1))) : 0),
      latencyP90: r.latencyP90 ?? r.medLatencyMs ?? 0,
    }));
  }, [runHistory]);
  if (!series || series.length < 2) return null;

  const spark = (label, key, unit = "%", goodUp = true) => {
    const values = series.map((r) => r[key]);
    const first = values[0]; const last = values[values.length - 1];
    const dir = last - first;
    const good = goodUp ? dir >= 0 : dir <= 0;
    const color = Math.abs(dir) < 0.5 ? theme.palette.text.subtitle : (good ? CHART_GREEN : CHART_RED);
    return (
      <Box sx={{ px: 2, py: 1.5, minWidth: 0 }}>
        <Typography sx={{ typography: "s3", color: "text.subtitle", fontSize: 10, fontWeight: 600, textTransform: "uppercase", letterSpacing: 0.5 }}>
          {label}
        </Typography>
        <Stack direction="row" alignItems="baseline" spacing={1} sx={{ mt: 0.375 }}>
          <Typography sx={{ typography: "m1", fontWeight: 700, fontVariantNumeric: "tabular-nums", fontSize: 18 }}>
            {unit === "$" ? `$${last.toFixed(3)}` : unit === "ms" ? `${Math.round(last)}ms` : `${Math.round(last)}${unit}`}
          </Typography>
          <Typography sx={{ typography: "s3", fontSize: 10.5, fontWeight: 700, color }}>
            {dir >= 0 ? "+" : ""}{unit === "$" ? dir.toFixed(3) : Math.round(dir)}{unit === "$" ? "" : unit === "ms" ? "ms" : unit}
          </Typography>
        </Stack>
      </Box>
    );
  };

  return (
    <Box sx={{
      border: "1px solid", borderColor: "divider", borderRadius: 1,
      bgcolor: "background.paper",
      display: "grid",
      gridTemplateColumns: { xs: "repeat(2, 1fr)", md: "repeat(4, 1fr)" },
      "& > * + *": { borderLeft: { md: "1px solid" }, borderColor: { md: "divider" } },
    }}>
      {spark("Pass rate · trend",  "passRate",    "%",  true)}
      {spark("Escalation · trend", "escalation",  "%",  false)}
      {spark("Cost / pass",        "costPerPass", "$",  false)}
      {spark("Latency p90",        "latencyP90",  "ms", false)}
    </Box>
  );
}
TrendRow.propTypes = { runHistory: PropTypes.array, currentRunId: PropTypes.string };

/* ── voice latency panel ──────────────────────────────────────────── */

/* Voice-agents-only. TTFW / LLM / TTS / ASR as p50 · p90 · p99 rows
   with a small anchor for WER and interruptions. Mirrors the metric
   set Hamming / Cekura market as table stakes for voice QA. */
function VoiceLatencyPanel({ voice }) {
  if (!voice) return null;
  const row = (label, m, unit = "ms", warn = 2500) => (
    <TableRow hover>
      <TableCell><Typography sx={{ typography: "s2", fontWeight: 600 }}>{label}</Typography></TableCell>
      <TableCell align="right" sx={{ fontVariantNumeric: "tabular-nums" }}>{Math.round(m.p50)}{unit}</TableCell>
      <TableCell align="right" sx={{
        fontVariantNumeric: "tabular-nums", fontWeight: 700,
        color: m.p90 > warn ? RED : m.p90 > warn * 0.7 ? AMBER : "text.primary",
      }}>{Math.round(m.p90)}{unit}</TableCell>
      <TableCell align="right" sx={{ fontVariantNumeric: "tabular-nums", color: m.p99 > warn ? RED : "text.subtitle" }}>{Math.round(m.p99)}{unit}</TableCell>
    </TableRow>
  );
  return (
    <Panel
      title="Voice latency SLOs"
      subtitle="TTFW · LLM · TTS · ASR — the four segments that decide caller experience."
      info="Latency broken down by the four voice-pipeline segments callers actually feel: Time-to-First-Word, model thinking, text-to-speech, speech-to-text. Any red p90 means callers heard silence past your SLO — that's the one to fix first."
    >
      <DataTableShell>
        <TableHead>
          <TableRow>
            <TableCell>Segment</TableCell>
            <TableCell align="right">p50</TableCell>
            <TableCell align="right">p90</TableCell>
            <TableCell align="right">p99</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {row("Time to first word (TTFW)", voice.ttfw, "ms", 1200)}
          {row("LLM response",              voice.llm,  "ms", 2500)}
          {row("Text-to-speech",            voice.tts,  "ms", 800)}
          {row("Speech recognition",        voice.asr,  "ms", 500)}
        </TableBody>
      </DataTableShell>
      <Stack direction="row" spacing={3} sx={{ px: 2, py: 1.25, borderTop: "1px solid", borderColor: "divider" }}>
        <Box>
          <Typography sx={{ typography: "s3", color: "text.subtitle", fontSize: 10.5, fontWeight: 600, textTransform: "uppercase" }}>ASR word-error rate</Typography>
          <Typography sx={{ typography: "s1", fontWeight: 700, fontSize: 15, mt: 0.25, color: voice.werPct < 8 ? GREEN : voice.werPct < 15 ? AMBER : RED }}>
            {voice.werPct.toFixed(1)}%
          </Typography>
        </Box>
        <Box>
          <Typography sx={{ typography: "s3", color: "text.subtitle", fontSize: 10.5, fontWeight: 600, textTransform: "uppercase" }}>Interruptions</Typography>
          <Typography sx={{ typography: "s1", fontWeight: 700, fontSize: 15, mt: 0.25 }}>
            {voice.interruptsPerTask.toFixed(1)} / call · {voice.interrupts} total
          </Typography>
        </Box>
      </Stack>
    </Panel>
  );
}
VoiceLatencyPanel.propTypes = { voice: PropTypes.object };

/* ── failure pattern clusters ─────────────────────────────────────── */

/* Auto-clustered failure themes — (domain × use case) — ranked by
   count. Each row carries a coloured domain chip and the failing
   scenarios count so "your agent breaks on refunds because of agent
   behaviour, 12 cases" is legible at a glance. */
function FailureClustersPanel({ tasks }) {
  const theme = useTheme();
  const clusters = useMemo(() => deriveFailureClusters(tasks, 4), [tasks]);
  const totalFailed = tasks.filter((t) => t.status !== "passed" && isMeasured(t)).length;
  return (
    <Panel
      title="Top failure themes"
      subtitle={`Where losses cluster · ${totalFailed} measured failures`}
      info="Auto-groups failures by (attribution layer × use case) and ranks the clusters by count. Fixing the top cluster typically resolves several failing tasks at once — better ROI than triaging individual tasks."
    >
      {clusters.length === 0 ? (
        <Box sx={{ p: 3, textAlign: "center" }}>
          <Typography sx={{ typography: "s3", color: "text.subtitle", fontSize: 12 }}>
            No failures — nothing to cluster.
          </Typography>
        </Box>
      ) : (
        <Stack spacing={0.5} sx={{ p: 1.5 }}>
          {clusters.map((c) => {
            const dom = DOMAINS[c.domain] || DOMAINS.agent;
            const share = pct(c.count, totalFailed);
            return (
              <Box key={`${c.domain}::${c.useCase}`} sx={{
                display: "grid", gridTemplateColumns: "auto 1fr auto auto",
                gap: 1.25, alignItems: "center",
                px: 1.25, py: 0.75, borderRadius: 0.75,
                "&:hover": { bgcolor: "action.hover" },
              }}>
                <Box sx={{
                  px: 0.75, py: 0.25, borderRadius: 0.5,
                  bgcolor: alpha(dom.color, theme.palette.mode === "dark" ? 0.18 : 0.12),
                  color: dom.color, fontSize: 10.5, fontWeight: 700,
                  lineHeight: 1.4, whiteSpace: "nowrap",
                }}>
                  {dom.short}
                </Box>
                <Typography sx={{
                  typography: "s2", fontWeight: 600,
                  overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                }} title={c.useCase}>
                  {c.useCase}
                </Typography>
                <Typography sx={{
                  typography: "s2", fontWeight: 700, color: RED,
                  fontVariantNumeric: "tabular-nums", minWidth: 42, textAlign: "right",
                }}>
                  {c.count}
                </Typography>
                <Typography sx={{
                  typography: "s3", fontSize: 11, color: "text.subtitle",
                  fontVariantNumeric: "tabular-nums", minWidth: 34, textAlign: "right",
                }}>
                  {share}%
                </Typography>
              </Box>
            );
          })}
        </Stack>
      )}
    </Panel>
  );
}
FailureClustersPanel.propTypes = { tasks: PropTypes.array };

/* ── latency histogram with SLA overlay ───────────────────────────── */

/* Buckets tasks by latency, overlays a 2000ms SLA line so the reader
   can see the tail past the threshold. The percentage-under-SLA badge
   is the single number a CX lead wants — "89% of calls under 2s". */
const LatencyHistogramPanel = memo(function LatencyHistogramPanel({ tasks, slaMs = 2000 }) {
  const theme = useTheme();
  const { buckets, categories, colors, underSla, total } = useMemo(() => {
    const lats = tasks.map((t) => latencyOf(t)).filter((v) => v > 0);
    if (!lats.length) return { buckets: [], categories: [], colors: [], underSla: 0, total: 0 };
    const edges = [500, 1000, 1500, 2000, 2500, 3000, 4000, Infinity];
    const labels = ["<0.5s", "0.5–1s", "1–1.5s", "1.5–2s", "2–2.5s", "2.5–3s", "3–4s", "≥4s"];
    const b = new Array(edges.length).fill(0);
    lats.forEach((v) => {
      const i = edges.findIndex((e) => v < e);
      if (i >= 0) b[i] += 1;
    });
    const cols = edges.map((e) => (e <= slaMs ? CHART_GREEN : "#F59E0B"));
    if (edges[edges.length - 1] === Infinity) cols[cols.length - 1] = CHART_RED;
    const under = lats.filter((v) => v <= slaMs).length;
    return { buckets: b, categories: labels, colors: cols, underSla: under, total: lats.length };
  }, [tasks, slaMs]);

  const pctUnder = total ? Math.round((underSla / total) * 100) : 0;

  return (
    <PanelChart
      title="Latency distribution"
      subtitle={`${pctUnder}% of tasks under the ${(slaMs / 1000).toFixed(1)}s SLA · ${underSla} / ${total}`}
      info="Shape of the latency curve, bucketed. A tall stack on the left = most tasks are fast, healthy. A long tail on the right = a few slow tasks that are dragging your percentiles. The subtitle counts how many tasks stayed under your SLA."
    >
      <ReactApexChart
        type="bar" height={260}
        series={[{ name: "Tasks", data: buckets }]}
        options={{
          chart: { type: "bar", toolbar: { show: false }, animations: { enabled: false }, background: "transparent", fontFamily: theme.typography.fontFamily },
          theme: { mode: theme.palette.mode },
          plotOptions: { bar: { columnWidth: "56%", borderRadius: 2, distributed: true } },
          dataLabels: { enabled: false },
          legend: { show: false },
          stroke: { show: false },
          fill: { type: "solid", opacity: 0.9 },
          colors,
          xaxis: { categories, axisBorder: { show: false }, axisTicks: { show: false }, labels: { style: { colors: theme.palette.text.secondary, fontSize: "10px" } } },
          yaxis: { labels: { style: { colors: theme.palette.text.secondary, fontSize: "10px" }, formatter: (v) => `${Math.round(v)}` } },
          grid: { borderColor: theme.palette.divider, strokeDashArray: 4, padding: { left: 0, right: 0, top: -6, bottom: -6 } },
          tooltip: { y: { formatter: (v) => `${v} tasks` } },
        }}
      />
    </PanelChart>
  );
});
LatencyHistogramPanel.propTypes = { tasks: PropTypes.array, slaMs: PropTypes.number };

/* ── shell ─────────────────────────────────────────────────────────── */

export default function RunAnalyticsV2({ tasks, evals, env, runHistory, currentRunId }) {
  const layout = useRunLayout({ surface: env?.surface || "generic" });
  const [hiddenAnchor, setHiddenAnchor] = useState(null);
  const [editorState, setEditorState] = useState({ open: false, target: null, opts: null });
  const [renameTarget, setRenameTarget] = useState(null); // custom widget id
  const [renameValue, setRenameValue] = useState("");

  /* Deep-link: on mount, if the URL carries ?view=<name> and the
     name matches a saved view, switch to it. Also keep the URL in
     sync so switching in the popover updates the shareable link. */
  useEffect(() => {
    const desired = readViewFromUrl();
    if (desired && layout.viewNames.includes(desired) && desired !== layout.activeView) {
      layout.switchView(desired);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => { writeViewToUrl(layout.activeView); }, [layout.activeView]);

  /* Rename dialog is opened via a window CustomEvent so the deep-in
     render loop doesn't have to hoist setState refs up here. */
  useEffect(() => {
    const handler = (e) => {
      setRenameTarget(e.detail?.id || null);
      setRenameValue(e.detail?.title || "");
    };
    window.addEventListener("run-analytics:rename", handler);
    return () => window.removeEventListener("run-analytics:rename", handler);
  }, []);

  const biz = useMemo(() => deriveBusiness(tasks), [tasks]);
  /* Drilldown state — every chart can call `onDrill({ title, tasks })`
     via the context and the shell opens a right-side drawer with
     the filtered task list. Nobody else in the agent-sim space has
     click-to-filter on chart segments. */
  const [drilldown, setDrilldown] = useState(null);
  const openDrilldown = (payload) => setDrilldown(payload || null);
  const closeDrilldown = () => setDrilldown(null);
  const isVoice = env?.surface === "voice";
  const voice = useMemo(() => (isVoice ? deriveVoiceLatency(tasks) : null), [isVoice, tasks]);

  /* Trend context: pull the previous run's aggregates so the KPI
     strip can render deltas and the regression banner can name the
     newly-passing / regressed count. */
  const { prev, delta, trend } = useMemo(() => {
    if (!runHistory || runHistory.length === 0) return { prev: null, delta: null, trend: null };
    const idx = runHistory.findIndex((r) => r.id === currentRunId);
    const curr = idx >= 0 ? runHistory[idx] : runHistory[runHistory.length - 1];
    const prevRun = idx > 0 ? runHistory[idx - 1] : (idx === -1 && runHistory.length > 1 ? runHistory[runHistory.length - 2] : null);
    if (!prevRun) return { prev: null, delta: null, trend: null };
    const passDelta = (curr?.passRate ?? 0) - (prevRun.passRate ?? 0);
    const currCost = curr?.cost ?? 0;
    const prevCost = prevRun.cost ?? 0;
    const costDeltaPct = prevCost > 0 ? Math.round(((currCost - prevCost) / prevCost) * 100) : null;
    return {
      prev: prevRun,
      delta: {
        passRate: Math.round(passDelta),
        newlyPassing: Math.max(0, Math.round(passDelta * (curr?.scenarios || 0) / 100)),
        regressions: Math.max(0, Math.round(-passDelta * (curr?.scenarios || 0) / 100)),
        costDeltaPct,
      },
      trend: {
        prevPassRate: prevRun.passRate,
        prevEscalationRate: prevRun.escalationRate,
        prevCostPerPassMs: prevRun.costPerPass ? prevRun.costPerPass * 1000 : null,
      },
    };
  }, [runHistory, currentRunId]);

  return (
    <DrilldownContext.Provider value={openDrilldown}>
    <TaskDrilldownDrawer
      open={!!drilldown}
      onClose={closeDrilldown}
      title={drilldown?.title}
      subtitle={drilldown?.subtitle}
      tasks={drilldown?.tasks}
      env={env}
    />
    <Stack spacing={2} className="analytics-root">
      {/* Print CSS — activates when the user hits Cmd+P or the Export
          button below. Hides app chrome, expands the analytics tab to
          full width, keeps chart cards from splitting across pages,
          and forces a white background so the printed PDF is readable.
          Scoped via `.analytics-root` so it never leaks to other tabs. */}
      <style>{`
        @media print {
          @page { size: A3; margin: 12mm; }
          body, html { background: #fff !important; }
          body * { visibility: hidden !important; }
          .analytics-root, .analytics-root * { visibility: visible !important; }
          .analytics-root {
            position: absolute !important;
            left: 0 !important; top: 0 !important; right: 0 !important;
            padding: 0 !important;
            background: #fff !important;
            color: #111 !important;
          }
          .analytics-root .analytics-no-print { display: none !important; }
          .analytics-root .analytics-panel {
            break-inside: avoid !important; page-break-inside: avoid !important;
            background: #fff !important;
            border: 1px solid #e5e7eb !important;
          }
          /* Print-only-this-widget: shell tags the non-target
             wrappers with .print-suppress right before opening the
             print dialog; that class collapses their space so only
             the chosen widget ships to paper. */
          .analytics-root .print-suppress { display: none !important; }
        }
      `}</style>

      {/* Toolbar — Views on the left, Add widget + Export on the right.
          Drag-to-reorder happens directly on the widgets, and every
          panel's kebab menu handles delete/resize/duplicate/download,
          so no standalone Customize button is needed. Hidden from print. */}
      <Stack direction="row" alignItems="center" spacing={1} className="analytics-no-print" sx={{ mb: 0 }}>
        <ViewTabBar
          activeView={layout.activeView}
          viewNames={layout.viewNames}
          defaultName={layout.DEFAULT_VIEW_NAME}
          onSwitch={layout.switchView}
          onSave={layout.saveAsView}
          onRename={layout.renameView}
          onDuplicate={layout.duplicateView}
          onDelete={layout.deleteView}
          onReorder={layout.reorderViews}
        />
        <ToolbarButton
          icon="solar:add-circle-linear"
          label="Add widget"
          onClick={() => setEditorState({ open: true, target: null })}
        />
        <ToolbarButton
          icon="solar:eye-closed-linear"
          label={layout.hiddenIds.length > 0 ? `Hidden (${layout.hiddenIds.length})` : "Hidden"}
          onClick={(e) => setHiddenAnchor(e.currentTarget)}
        />
        <ToolbarButton
          icon="solar:printer-linear"
          label="Export PDF"
          onClick={() => window.print()}
        />
      </Stack>

      {/* 2. Regression banner (only when a prior run exists). Kept as a
          fixed lede — it's a run-level alert, not a widget the user
          would reorder. */}
      <RegressionBanner delta={delta} />

      {/* 3. Top-of-page KPI strip. Also fixed at the top — it's the
          answer the user came for, not something to reorder past. */}
      <KpiStrip tasks={tasks} evals={evals} biz={biz} trend={trend} runHistory={runHistory} currentRunId={currentRunId} env={env} />

      {/* Registry-driven body. The list of visible ids comes from the
          layout hook, and we render them in sections in the order the
          user has arranged. This is what makes reorder, hide/show,
          named views and custom widgets all work with one code path. */}
      <LayoutBody
        layout={layout}
        renderPanel={(id) => renderBuiltinPanel(id, { tasks, evals, biz, voice, env })}
        renderCustomPanel={(widget) => renderCustomPanel(widget, { tasks, evals, biz, voice, env }, layout.getOverride(widget.id))}
        openEditor={(widget) => setEditorState({ open: true, target: widget, opts: null })}
      />
    </Stack>
    <HiddenWidgetsPopover
      anchorEl={hiddenAnchor}
      onClose={() => setHiddenAnchor(null)}
      layout={layout}
    />
    <WidgetEditor
      open={editorState.open}
      onClose={() => setEditorState({ open: false, target: null, opts: null })}
      initial={editorState.target}
      onSave={(widget) => {
        if (editorState.target) layout.updateCustomWidget(editorState.target.id, widget);
        else layout.addCustomWidget(widget);
      }}
      ctx={{ tasks, evals, biz, voice, env, graderResults: deriveGraderResults(tasks, evals) }}
    />
    <RenameWidgetDialog
      open={!!renameTarget}
      title={renameValue}
      onClose={() => setRenameTarget(null)}
      onChange={setRenameValue}
      onSave={() => {
        const t = renameValue.trim();
        /* Rename goes through the override map, not the base config,
           so "Reset to default" restores the shipped/first-save name. */
        if (renameTarget && t) layout.patchOverride(renameTarget, { title: t });
        setRenameTarget(null);
      }}
    />
    </DrilldownContext.Provider>
  );
}
RunAnalyticsV2.propTypes = {
  tasks: PropTypes.array, evals: PropTypes.array, env: PropTypes.object,
  runHistory: PropTypes.array, currentRunId: PropTypes.string,
};

/* ── layout renderer ─────────────────────────────────────────────
   Walks the user's visible id list and renders each panel through
   its section's grid. ONE DndContext + ONE SortableContext wrap the
   whole thing so a user can drag any widget to any position across
   any section — the drop target isn't limited to the source's
   section. On drop we reorder `visibleIds` end-to-end and if the
   moved panel landed inside a different section's block, that
   section becomes its new home. */
function LayoutBody({ layout, renderPanel, renderCustomPanel, openEditor }) {
  const customById = new Map(layout.customWidgets.map((w) => [w.id, w]));

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const [activeId, setActiveId] = useState(null);

  const bySection = useMemo(() => {
    const map = new Map();
    layout.visibleIds.forEach((id) => {
      const isCustom = customById.has(id);
      /* Look up the panel's *effective* section — either an
         explicit override the user set by dragging it into another
         section, or (for built-ins) the registry section, or
         "custom" for custom widgets. */
      const override = layout.sectionOverrides?.[id];
      const sec = override || (isCustom ? "custom" : panelSection(id));
      if (!map.has(sec)) map.set(sec, []);
      map.get(sec).push(id);
    });
    return map;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layout.visibleIds, layout.customWidgets, layout.sectionOverrides]);

  const onDragStart = (event) => setActiveId(event.active?.id || null);
  const onDragCancel = () => setActiveId(null);
  const onDragEnd = (event) => {
    setActiveId(null);
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const oldIndex = layout.visibleIds.indexOf(active.id);
    const newIndex = layout.visibleIds.indexOf(over.id);
    if (oldIndex < 0 || newIndex < 0) return;
    const nextOrder = arrayMove(layout.visibleIds, oldIndex, newIndex);
    /* Adopt the drop target's section as the new home so the widget
       visually stays where the user let go, not where the registry
       says it "should" live. Atomic patch so no transient render
       ends up with the new position but the old section. */
    const targetSection = sectionForId(over.id, layout, customById);
    layout.reorderAndSection(nextOrder, active.id, targetSection);
  };

  /* Section-level drag. A separate DndContext handles the "reorder
     entire sections" gesture; its items live under a "section:xxx"
     namespace so they never collide with widget ids. Only the
     section header responds to this DndContext's listeners. */
  const [activeSectionId, setActiveSectionId] = useState(null);
  const onSectionDragStart = (event) => setActiveSectionId(event.active?.id || null);
  const onSectionDragCancel = () => setActiveSectionId(null);
  const onSectionDragEnd = (event) => {
    setActiveSectionId(null);
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const currentOrder = layout.sectionOrder.map((id) => `section:${id}`);
    const oldIndex = currentOrder.indexOf(active.id);
    const newIndex = currentOrder.indexOf(over.id);
    if (oldIndex < 0 || newIndex < 0) return;
    const nextOrder = arrayMove(currentOrder, oldIndex, newIndex).map((id) => id.replace(/^section:/, ""));
    layout.reorderSections(nextOrder);
  };

  const orderedSections = (layout.sectionOrder || SECTIONS.map((s) => s.id))
    .map((sid) => SECTIONS.find((s) => s.id === sid))
    .filter(Boolean);

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={closestCenter}
      onDragStart={onSectionDragStart}
      onDragCancel={onSectionDragCancel}
      onDragEnd={onSectionDragEnd}
    >
      <SortableContext
        items={orderedSections.map((s) => `section:${s.id}`)}
        strategy={rectSortingStrategy}
      >
        <DndContext
          sensors={sensors}
          collisionDetection={closestCenter}
          onDragStart={onDragStart}
          onDragCancel={onDragCancel}
          onDragEnd={onDragEnd}
        >
          <SortableContext items={layout.visibleIds} strategy={rectSortingStrategy}>
            {orderedSections.map((section) => {
              const ids = bySection.get(section.id);
              if (!ids || ids.length === 0) return null;
              return (
                <SortableSection key={section.id} sectionId={section.id} label={section.label} hint={section.hint}>
                  <LayoutSection
                    section={section}
                    ids={ids}
                    layout={layout}
                    customById={customById}
                    renderPanel={renderPanel}
                    renderCustomPanel={renderCustomPanel}
                    openEditor={openEditor}
                    activeId={activeId}
                  />
                </SortableSection>
              );
            })}
          </SortableContext>
          <DragOverlay dropAnimation={null}>
            {activeId ? <DragGhost label={labelForId(activeId, layout, customById)} /> : null}
          </DragOverlay>
        </DndContext>
      </SortableContext>
      <DragOverlay dropAnimation={null}>
        {activeSectionId
          ? <DragGhost label={`Section: ${SECTIONS.find((s) => `section:${s.id}` === activeSectionId)?.label || activeSectionId}`} />
          : null}
      </DragOverlay>
    </DndContext>
  );
}

function labelForId(id, layout, customById) {
  if (customById.has(id)) return customById.get(id).title || "Widget";
  return getPanelMeta(id)?.title || id;
}

function DragGhost({ label }) {
  return (
    <Box sx={(t) => ({
      pointerEvents: "none",
      px: 1.5, py: 1,
      borderRadius: 1.25,
      border: "1px solid",
      borderColor: alpha(t.palette.primary.main, 0.8),
      bgcolor: alpha(t.palette.background.paper, 0.98),
      color: "text.primary",
      fontSize: 13, fontWeight: 700,
      boxShadow: "0 12px 32px -8px rgba(0,0,0,0.35)",
      display: "inline-flex", alignItems: "center", gap: 1,
    })}>
      <Iconify icon="solar:hamburger-menu-linear" width={14} />
      {label}
    </Box>
  );
}
DragGhost.propTypes = { label: PropTypes.node };

/* Quick rename — TextField in a dialog. Keeps the full editor for
   deeper reshapes and gives users a one-field escape hatch for
   the common "just fix the title" case. */
function RenameWidgetDialog({ open, title, onClose, onChange, onSave }) {
  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
      <Box sx={{ p: 2.5 }}>
        <Typography sx={{ fontSize: 15, fontWeight: 700, mb: 1.5 }}>Rename widget</Typography>
        <Box
          component="input"
          autoFocus
          value={title}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") onSave(); if (e.key === "Escape") onClose(); }}
          sx={{
            width: "100%",
            fontSize: 14, fontFamily: "inherit",
            px: 1.25, py: 1, borderRadius: 1,
            border: "1px solid", borderColor: "divider",
            bgcolor: "background.paper", color: "text.primary",
            outline: "none",
            "&:focus": { borderColor: "primary.main" },
          }}
        />
        <Stack direction="row" spacing={1} justifyContent="flex-end" sx={{ mt: 2 }}>
          <Box component="button" type="button" onClick={onClose} sx={ghostBtnSx}>Cancel</Box>
          <Box component="button" type="button" onClick={onSave} sx={{ ...ghostBtnSx, bgcolor: "primary.main", color: "#fff", borderColor: "primary.main" }}>Save</Box>
        </Stack>
      </Box>
    </Dialog>
  );
}
RenameWidgetDialog.propTypes = {
  open: PropTypes.bool, title: PropTypes.string,
  onClose: PropTypes.func, onChange: PropTypes.func, onSave: PropTypes.func,
};
const ghostBtnSx = {
  fontFamily: "inherit", fontSize: 12.5, fontWeight: 700,
  px: 1.5, py: 0.75, borderRadius: 1, cursor: "pointer",
  border: "1px solid", borderColor: "divider",
  bgcolor: "transparent", color: "text.primary",
  "&:hover": { bgcolor: "action.hover" },
};
LayoutBody.propTypes = {
  layout: PropTypes.object.isRequired,
  renderPanel: PropTypes.func.isRequired,
  renderCustomPanel: PropTypes.func.isRequired,
  openEditor: PropTypes.func.isRequired,
};

function sectionForId(id, layout, customById) {
  const override = layout.sectionOverrides?.[id];
  if (override) return override;
  if (customById.has(id)) return "custom";
  return panelSection(id);
}

/**
 * One section — a header + a responsive grid of its panels. All the
 * dnd wiring lives at the parent so users can drop cross-section;
 * this component just lays out its slice of the full visible list.
 */
function LayoutSection({ section, ids, layout, customById, renderPanel, renderCustomPanel, openEditor, activeId }) {
  const cols = section.columns || 2;

  return (
    /* Section header is rendered by SortableSection wrapping this
       component, so we only emit the grid here. */
    <Box>
      <Box sx={{
        display: "grid", gap: 1.5,
        gridTemplateColumns: {
          xs: "1fr",
          sm: cols >= 4 ? "repeat(2, 1fr)" : `repeat(${Math.min(cols, 2)}, 1fr)`,
          md: `repeat(${cols}, 1fr)`,
        },
        alignItems: "stretch",
      }}>
        {ids.map((id, index) => {
          const isCustom = customById.has(id);
          const customWidget = customById.get(id);
          const meta = getPanelMeta(id);
          const defaultSpan = isCustom ? cols : (meta?.defaultSpan || 1);
          const overrideSpan = layout.spans?.[id];
          const span = Math.min(overrideSpan || defaultSpan, cols);

          const actions = buildPanelActions({
            id, index, ids, layout, isCustom, customWidget,
            cols, currentSpan: span,
            openEditor,
            openRename: (targetId, title) => {
              /* Rename shows a lightweight dialog owned by the shell.
                 Broadcast via a custom event so this render loop
                 doesn't need to prop-thread setState three levels up. */
              window.dispatchEvent(new CustomEvent("run-analytics:rename", { detail: { id: targetId, title } }));
            },
            openCustomizeCopy: (targetId) => {
              const snap = snapshotAsCustom(targetId);
              layout.addCustomWidget(snap);
              layout.hide(targetId);
              openEditor(snap);
            },
          });

          return (
            <Box key={id} sx={{ gridColumn: { md: span > 1 ? `span ${span}` : "auto" } }}>
              <SortablePanel
                id={id}
                sectionColumns={cols}
                currentSpan={span}
                isFirstInSection={index === 0}
                isLastInSection={index === ids.length - 1}
                isCustom={isCustom}
                /* Only custom widgets are editable / renameable in
                   place. Built-ins are curated — users reshape them
                   via "Customize a copy" from the kebab. */
                hasEdit={isCustom}
                /* Menu-driven Download works for any panel — the click
                   walks up to the panel's own CSV button to trigger
                   the export, so the built-in + custom paths share
                   one affordance. */
                hasExport
                hasOverrides={isCustom && layout.hasOverrides(id)}
                {...actions}
              >
                {isCustom ? renderCustomPanel(customWidget) : renderPanel(id)}
              </SortablePanel>
            </Box>
          );
        })}
      </Box>
    </Box>
  );
}
LayoutSection.propTypes = {
  section: PropTypes.object.isRequired, ids: PropTypes.array.isRequired,
  layout: PropTypes.object.isRequired, customById: PropTypes.instanceOf(Map).isRequired,
  renderPanel: PropTypes.func.isRequired, renderCustomPanel: PropTypes.func.isRequired,
  openEditor: PropTypes.func.isRequired,
};

/**
 * Wires the kebab-menu actions for a single panel. Delegates all
 * mutations to the layout hook (single source of truth) and packages
 * the callbacks in the shape SortablePanel expects.
 */
function buildPanelActions({ id, index, ids, layout, isCustom, customWidget, cols, currentSpan, openEditor, openRename, openCustomizeCopy }) {
  const positionInGlobal = layout.visibleIds.indexOf(id);
  const sectionIndices = ids.map((sid) => layout.visibleIds.indexOf(sid));
  const firstGlobal = Math.min(...sectionIndices);
  const lastGlobal = Math.max(...sectionIndices);
  return {
    onMoveUp:     () => index > 0            && layout.moveTo(id, positionInGlobal - 1),
    onMoveDown:   () => index < ids.length-1 && layout.moveTo(id, positionInGlobal + 1),
    onMoveTop:    () => layout.moveTo(id, firstGlobal),
    onMoveBottom: () => layout.moveTo(id, lastGlobal),
    onSetSpan:    (span) => layout.setSpan(id, Math.min(span, cols)),
    onResetSpan:  () => layout.resetSpan(id),
    onHide:       () => layout.hide(id),
    onDuplicate:  isCustom ? () => layout.duplicateCustomWidget(id) : null,
    onEdit:       isCustom ? () => openEditor(customWidget) : null,
    onRename:     isCustom ? () => openRename?.(id, customWidget?.title || "") : null,
    onCustomizeCopy: isCustom ? null : () => openCustomizeCopy?.(id),
    onDelete:     isCustom ? () => layout.removeCustomWidget(id) : null,
    /* Menu Download → click the panel's own CSV icon so built-in
       and custom widgets share the same download path without
       prop-threading exportRows all the way back up here. */
    onExport:     () => triggerPanelExport(id),
    onCopyLink:   () => copyWidgetLink(id, layout.activeView),
    onPrintOnly:  () => printOnly(id),
    onResetOverride: () => layout.resetOverride(id),
  };
}

function copyWidgetLink(id, viewName) {
  try {
    const url = new URL(window.location.href);
    if (viewName && viewName !== "Default") url.searchParams.set("view", viewName);
    url.hash = `widget-${id}`;
    navigator.clipboard?.writeText(url.toString());
  } catch { /* clipboard may be blocked in dev — noop */ }
}

function triggerPanelExport(id) {
  /* Every panel's built-in CSV button carries aria-label
     "Download {title} as CSV". Find the button inside this
     widget's sortable wrapper and click it — one code path,
     both entry points. */
  const wrap = document.querySelector(`.sortable-panel-wrap[data-widget-id="${CSS.escape(id)}"]`);
  const btn = wrap?.querySelector('[aria-label^="Download "]');
  btn?.click();
}

function printOnly(id) {
  const root = document.querySelector(".analytics-root");
  if (!root) { window.print(); return; }
  const wraps = root.querySelectorAll(".sortable-panel-wrap");
  const suppressed = [];
  wraps.forEach((el) => {
    if (el.getAttribute("data-widget-id") !== id) {
      el.classList.add("print-suppress");
      suppressed.push(el);
    }
  });
  const cleanup = () => {
    suppressed.forEach((el) => el.classList.remove("print-suppress"));
    window.removeEventListener("afterprint", cleanup);
  };
  window.addEventListener("afterprint", cleanup);
  window.print();
}

/* Dispatch table for built-in panels — each registry id maps to its
   corresponding component with the right context props. Keeping this
   as a single switch beats prop-threading a factory through every
   component and stays easy to grep for. */
/* Built-ins keep their hand-tuned components — no in-place editing.
   Users who want to reshape a built-in use "Customize a copy" from
   the kebab, which snapshots it as a custom widget they own.
   Tool + Golden-set widgets are unified-renderer configs since
   they're new and don't need bespoke hand-coded components. */
function renderBuiltinPanel(id, ctx) {
  const { tasks, evals, biz, voice, env } = ctx;
  switch (id) {
    case "success_donut":        return <SuccessDonut tasks={tasks} />;
    case "outcome_donut":        return <OutcomeDonutChart tasks={tasks} biz={biz} />;
    case "sentiment_donut":      return <SentimentDonut tasks={tasks} />;
    case "disconnection_donut":  return <DisconnectionDonut tasks={tasks} />;
    case "dual_line_over_time":  return <TaskLatencyOverTime tasks={tasks} />;
    case "latency_percentiles":  return <LatencyPercentilesPanel tasks={tasks} />;
    case "agent_response_time":  return <AgentResponseTimePanel tasks={tasks} />;
    case "csat_distribution":    return <CsatDistributionPanel tasks={tasks} />;
    case "distribution_summary": return <DistributionSummary tasks={tasks} env={env} />;
    case "attribution":          return <AttributionTable tasks={tasks} />;
    case "use_case_risk_list":   return <UseCaseRiskList tasks={tasks} />;
    case "evals_table":          return <EvalsTable tasks={tasks} evals={evals} />;
    case "voice_latency":        return voice ? <VoiceLatencyPanel voice={voice} /> : null;
    case "voice_cost_breakdown": return voice ? <VoiceCostBreakdownPanel tasks={tasks} /> : null;
    case "slowest_tasks":        return <SlowestTable tasks={tasks} />;
    case "expensive_tasks":      return <ExpensiveTable tasks={tasks} />;

    /* Tools section — new per Monika's ask. Info tooltip flags
       infra vs prompt so users route the fix to the right team. */
    case "tool_call_volume":  return <ToolCallVolumePanel tasks={tasks} />;
    case "tool_failure_rate": return <ToolFailureRatePanel tasks={tasks} />;

    /* Golden set & divergence — per Ajeevansh. Section header hint
       tells users what "golden" means before they read the widgets. */
    case "golden_set_manager":  return <GoldenSetManager tasks={tasks} env={env} />;
    case "dropoff_funnel":      return <DropoffFunnel tasks={tasks} env={env} />;
    case "divergence_timeline": return <DivergenceTimeline tasks={tasks} env={env} />;
    case "pitch_break_themes":  return <PitchBreakThemes tasks={tasks} env={env} />;

    default: return null;
  }
}

/* Ctx enricher used by the tool + golden-set panels — attaches
   persona dims + gender/ageGroup so custom widgets can group by
   any user-defined dim without re-deriving. */
function ctxWithDerived(ctx) {
  attachPersonaDims(ctx.tasks || []);
  return { ...ctx, graderResults: deriveGraderResults(ctx.tasks, ctx.evals) };
}

function renderCustomPanel(widget, ctx, override) {
  /* Merge the override map on top of the base config so the
     rendered widget reflects any user tweaks (title, chart type,
     etc.) without mutating the "last-save" config underneath.
     Reset clears the override and the widget snaps back. */
  const config = override ? { ...widget, ...override } : widget;
  return (
    <Panel title={config.title} subtitle="Custom widget" info={config.info}>
      <CustomWidgetBody
        config={config}
        ctx={{ ...ctx, graderResults: deriveGraderResults(ctx.tasks, ctx.evals) }}
      />
    </Panel>
  );
}

/* Flatten per-task eval results into one row per (task × grader)
   so custom widgets can group by evaluator or filter by pass. Kept
   memo-free intentionally — cheap to compute and the tasks array
   identity already changes rarely. */
function deriveGraderResults(tasks, evals) {
  const evalsById = new Map((evals || []).map((e) => [e.id, e]));
  const out = [];
  (tasks || []).forEach((t) => {
    (t.evalResults || []).forEach((r) => {
      const e = evalsById.get(r.id);
      out.push({
        taskId: t.id,
        status: r.passed ? "passed" : "failed",
        passed: !!r.passed,
        evalId: r.id,
        evalName: e?.name || r.id,
        category: e?.category || "—",
        persona: t.persona,
        useCase: t.useCase,
      });
    });
  });
  return out;
}

/**
 * Section separator — bigger and quieter than the earlier version.
 * A hairline above the title turns the header into a proper "section
 * divider" so the eye can find the section boundaries at a glance,
 * matching Datadog/Grafana dashboard shape.
 */
function SectionHeader({ title, subtitle }) {
  return (
    <Box sx={{
      pt: 2, mt: 0.5, mb: 0.5, px: 0.25,
      borderTop: "1px solid",
      borderColor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.08 : 0.06),
    }}>
      <Typography sx={{
        fontSize: 18, fontWeight: 700, letterSpacing: -0.2, lineHeight: 1.2,
        color: "text.primary",
      }}>
        {title}
      </Typography>
      {subtitle && (
        <Typography sx={{
          typography: "s3", color: "text.subtitle", fontSize: 12.5, mt: 0.5,
        }}>
          {subtitle}
        </Typography>
      )}
    </Box>
  );
}
SectionHeader.propTypes = { title: PropTypes.node, subtitle: PropTypes.node };
RunAnalyticsV2.propTypes = {
  tasks: PropTypes.array,
  evals: PropTypes.array,
  env: PropTypes.object,
  stats: PropTypes.object,
  runHistory: PropTypes.array,
  currentRunId: PropTypes.string,
};
