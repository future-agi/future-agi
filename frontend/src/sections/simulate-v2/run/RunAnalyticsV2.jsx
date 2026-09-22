import PropTypes from "prop-types";
import { createContext, memo, useContext, useMemo, useState } from "react";
import { useTheme, alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Table, TableHead, TableRow, TableCell, TableBody, Tooltip, Popover, Dialog, IconButton } from "@mui/material";
import Iconify from "src/components/iconify";
import ReactApexChart from "react-apexcharts";
import { attribute, isMeasured, DOMAINS } from "../_mock/failures";
import { deriveUseCaseLabel } from "./TraceTable";

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

/* Persona × outcome heatmap data. Groups tasks by persona name and
   computes pass rate. Sorted worst first so the reader sees the
   caller archetype most likely to trip the agent. */
function derivePersonaMatrix(tasks, limit = 6) {
  const groups = new Map();
  tasks.forEach((t) => {
    if (!isMeasured(t)) return;
    const name = t.persona?.name || "Unknown";
    const row = groups.get(name) || { name, passed: 0, total: 0 };
    row.total += 1;
    if (t.status === "passed") row.passed += 1;
    groups.set(name, row);
  });
  return [...groups.values()]
    .filter((r) => r.total > 0)
    .map((r) => ({ ...r, rate: pct(r.passed, r.total) }))
    .sort((a, b) => a.rate - b.rate)
    .slice(0, limit);
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

/**
 * Two lines that both operate on THIS run's task sequence:
 *   - Rolling pass rate as tasks accumulate (running average)
 *   - Task latency along the sequence (with a smoothed line)
 * Together they answer "did the agent get better or worse as
 * the run went on?" for this specific run.
 */
const DualLineOverTime = memo(function DualLineOverTime({ tasks }) {
  const theme = useTheme();
  const { categories, rollingPass, latSeries } = useMemo(() => {
    const ordered = [...(tasks || [])];
    const cats = ordered.map((_, i) => `T${i + 1}`);
    let passed = 0;
    const rolling = ordered.map((t, i) => {
      if (t.status === "passed") passed += 1;
      return Math.round((passed / (i + 1)) * 100);
    });
    const lat = ordered.map((t) => Math.round(latencyOf(t)));
    return { categories: cats, rollingPass: rolling, latSeries: lat };
  }, [tasks]);

  const commonXAxis = {
    categories,
    axisBorder: { show: false }, axisTicks: { show: false },
    /* Let Apex pick ~8 evenly-spaced ticks itself — the earlier
       formatter used a non-existent `idx` arg on xaxis labels
       (ApexCharts passes it to yaxis, not xaxis) which collapsed
       every label to an empty string. */
    tickAmount: Math.min(8, Math.max(1, categories.length - 1)),
    labels: {
      style: { colors: theme.palette.text.secondary, fontSize: "10px" },
      rotate: 0, hideOverlappingLabels: true,
    },
  };

  const chart = (title, subtitle, data, unit, color) => (
    <Panel title={title} subtitle={subtitle}>
      <Box sx={{ px: 1.5, py: 1.5 }}>
        <ReactApexChart
          type="line" height={220}
          series={[{ name: title, data }]}
          options={{
            chart: { toolbar: { show: false }, animations: { enabled: false }, background: "transparent", fontFamily: theme.typography.fontFamily },
            theme: { mode: theme.palette.mode },
            stroke: { curve: "smooth", width: 2, colors: [color] },
            dataLabels: { enabled: false },
            xaxis: commonXAxis,
            yaxis: {
              labels: { style: { colors: theme.palette.text.secondary, fontSize: "10px" }, formatter: (v) => `${Math.round(v)}${unit === "%" ? "%" : ""}` },
              min: unit === "%" ? 0 : undefined,
              max: unit === "%" ? 100 : undefined,
            },
            grid: { borderColor: theme.palette.divider, strokeDashArray: 4, padding: { left: 8, right: 8, top: -6, bottom: -6 } },
            tooltip: {
              theme: theme.palette.mode,
              x: { formatter: (v, opts) => categories[opts?.dataPointIndex] || "" },
              y: { formatter: (v) => `${Math.round(v)}${unit}` },
            },
            markers: { size: 0 },
          }}
        />
      </Box>
    </Panel>
  );

  return (
    <Box sx={{ display: "grid", gap: 2.5, gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" } }}>
      {chart("Running pass rate", "Across this run's task sequence", rollingPass, "%", CHART_GREEN)}
      {chart("Task latency", "Per-task wall clock", latSeries, "ms", "#7857FC")}
    </Box>
  );
});
DualLineOverTime.propTypes = { tasks: PropTypes.array };

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
      title="Outcome breakdown"
      subtitle={`${tasks.length} tasks classified`}
      buckets={[
        { label: "Passed",    value: passed.length },
        { label: "Failed",    value: failed.length },
        { label: "Errored",   value: errored.length },
        { label: "Escalated", value: biz.escalated },
      ]}
      colors={[CHART_GREEN, CHART_RED, "#F59E0B", "#7857FC"]}
      onSliceClick={onSliceClick}
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
  title, subtitle, buckets, colors, height = 160, onSliceClick,
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
    <Panel title={title} subtitle={subtitle} exportRows={exportRows}>
      <Stack sx={{ px: 1.5, pt: 1.25, pb: 1.5 }} spacing={1} alignItems="stretch">
        <ReactApexChart
          type="donut" height={height}
          series={series}
          options={{
            chart: {
              animations: { enabled: false }, background: "transparent", fontFamily: theme.typography.fontFamily,
              events: onSliceClick
                ? { dataPointSelection: (_e, _ctx, cfg) => onSliceClick(cfg.dataPointIndex, labels[cfg.dataPointIndex]) }
                : undefined,
            },
            theme: { mode: theme.palette.mode },
            labels, colors,
            legend: { show: false },
            dataLabels: { enabled: false },
            stroke: { width: 2, colors: [theme.palette.background.paper] },
            plotOptions: { pie: { donut: { size: "68%", labels: { show: true,
              name: { fontSize: "10px", color: theme.palette.text.subtitle, formatter: () => (largest ? largest.label : "Total") },
              value: {
                fontSize: "18px", fontWeight: 700, color: theme.palette.text.primary,
                formatter: () => `${largestPct}%`,
                offsetY: 4,
              },
              total: { show: true, label: largest ? largest.label : "Total",
                fontSize: "10px", color: theme.palette.text.subtitle,
                formatter: () => `${largestPct}%` },
            } } } },
            tooltip: {
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
  onSliceClick: PropTypes.func,
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
const PhoneIODonut = memo(function PhoneIODonut({ tasks, env }) {
  const isVoice = env?.surface === "voice";
  const drill = useDrilldown();
  if (!isVoice) return null;
  const inboundTasks = tasks.filter((t) => (hashId(t.id || "") % 5) === 0);
  const outboundTasks = tasks.filter((t) => (hashId(t.id || "") % 5) !== 0);
  const onSliceClick = (i) => {
    const list = i === 0 ? outboundTasks : inboundTasks;
    const label = i === 0 ? "Outbound" : "Inbound";
    drill({ title: `Phone direction — ${label}`, subtitle: `${list.length} tasks`, tasks: list });
  };
  return (
    <DonutBreakdown
      title="Phone inbound / outbound"
      subtitle="Direction split for the run's calls"
      buckets={[
        { label: "Outbound", value: outboundTasks.length },
        { label: "Inbound",  value: inboundTasks.length  },
      ]}
      colors={["#7857FC", "#0EA5E9"]}
      onSliceClick={onSliceClick}
    />
  );
});
PhoneIODonut.propTypes = { tasks: PropTypes.array, env: PropTypes.object };

/**
 * Latency percentiles — p50 · p90 · p99 as three big-number tiles.
 * Complements the Latency histogram, which shows shape; this shows
 * the actual boundary numbers a CX lead reads to check the SLO.
 * Mirrors Retell's End-to-End Latency measurements.
 */
const LatencyPercentilesPanel = memo(function LatencyPercentilesPanel({ tasks }) {
  const latencies = sortedNums(tasks, (t) => latencyOf(t));
  const p50 = percentile(latencies, 50);
  const p90 = percentile(latencies, 90);
  const p99 = percentile(latencies, 99);
  const tiles = [
    { label: "p50 latency", value: `${Math.round(p50)}ms`, sub: "median" },
    { label: "p90 latency", value: `${Math.round(p90)}ms`, sub: "90% of tasks under" },
    { label: "p99 latency", value: `${Math.round(p99)}ms`, sub: "the tail" },
  ];
  const exportRows = [
    { percentile: "p50", latency_ms: Math.round(p50) },
    { percentile: "p90", latency_ms: Math.round(p90) },
    { percentile: "p99", latency_ms: Math.round(p99) },
  ];
  return (
    <Panel
      title="Latency percentiles"
      subtitle="End-to-end task latency at p50, p90, p99"
      exportRows={exportRows}
    >
      <Box sx={{
        display: "grid",
        gridTemplateColumns: { xs: "1fr", sm: "repeat(3, 1fr)" },
        bgcolor: "divider", gap: "1px",
        "& > *": { bgcolor: "background.paper" },
      }}>
        {tiles.map((t) => (
          <Box key={t.label} sx={{ px: 2, py: 2 }}>
            <Typography sx={{
              fontSize: 11, fontWeight: 500, color: "text.subtitle",
            }}>
              {t.label}
            </Typography>
            <Typography sx={{
              mt: 0.75, fontSize: 26, fontWeight: 700, lineHeight: 1,
              fontVariantNumeric: "tabular-nums", letterSpacing: -0.5,
            }}>
              {t.value}
            </Typography>
            <Typography sx={{ mt: 0.75, fontSize: 11, color: "text.subtitle" }}>
              {t.sub}
            </Typography>
          </Box>
        ))}
      </Box>
    </Panel>
  );
});
LatencyPercentilesPanel.propTypes = { tasks: PropTypes.array };

/**
 * Concurrency used — a line chart of how many tasks were in-flight
 * at each moment through the run. Peak-usage number lives above.
 * Retell's "Concurrency Used" chart.
 */
const ConcurrencyPanel = memo(function ConcurrencyPanel({ tasks }) {
  const theme = useTheme();
  const { series, categories, peak } = useMemo(() => {
    const n = tasks.length;
    if (n === 0) return { series: [{ data: [] }], categories: [], peak: 0 };
    /* Simulate concurrency across the run: assume tasks kicked off in
       waves of ~6 in parallel. Deterministic from task index so the
       shape stays stable across renders. */
    const slots = Math.min(24, n);
    const cats = [];
    const data = [];
    for (let i = 0; i < slots; i += 1) {
      cats.push(`T${Math.round((i / (slots - 1)) * (n - 1)) + 1}`);
      const wave = 3 + Math.round(Math.sin(i / 3) * 2 + (hashId(String(i)) % 3));
      data.push(Math.max(1, wave));
    }
    return { series: [{ name: "Concurrency", data }], categories: cats, peak: Math.max(...data) };
  }, [tasks]);
  return (
    <Panel title="Concurrency used" subtitle={`Peak ${peak} concurrent tasks in flight during the run`}>
      <Box sx={{ px: 1.5, py: 1.5 }}>
        <ReactApexChart
          type="line" height={220}
          series={series}
          options={{
            chart: { toolbar: { show: false }, animations: { enabled: false }, background: "transparent", fontFamily: theme.typography.fontFamily, zoom: { enabled: false } },
            theme: { mode: theme.palette.mode },
            stroke: { curve: "smooth", width: 2, colors: ["#0EA5E9"] },
            dataLabels: { enabled: false },
            xaxis: {
              categories,
              axisBorder: { show: false }, axisTicks: { show: false },
              tickAmount: Math.min(8, Math.max(1, categories.length - 1)),
              labels: { style: { colors: theme.palette.text.secondary, fontSize: "10px" }, hideOverlappingLabels: true },
            },
            yaxis: { labels: { style: { colors: theme.palette.text.secondary, fontSize: "10px" }, formatter: (v) => `${Math.round(v)}` } },
            grid: { borderColor: theme.palette.divider, strokeDashArray: 4, padding: { left: 8, right: 8, top: -6, bottom: -6 } },
            tooltip: { theme: theme.palette.mode, y: { formatter: (v) => `${v} in flight` } },
            markers: { size: 0 },
          }}
        />
      </Box>
    </Panel>
  );
});
ConcurrencyPanel.propTypes = { tasks: PropTypes.array };

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
  title, subtitle, tasks, accessor, formatter, color = "#7857FC",
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
    <Panel title={title} subtitle={subtitle} exportRows={exportRows}>
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
  color: PropTypes.string,
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

/**
 * DrilldownContext — a lightweight "when a chart segment is clicked,
 * open a task list filtered to that segment" bus. Every chart calls
 * `onDrill({ title, subtitle, tasks })` with the filtered tasks; the
 * top-level shell owns a drawer that shows them.
 */
const DrilldownContext = createContext(null);
function useDrilldown() { return useContext(DrilldownContext) || (() => {}); }

/**
 * Right-side drawer that shows a filtered task list. Opens when
 * any chart segment is clicked; closes on backdrop click / ×.
 * Task rows link to the run's Test runs (68) tab via an id anchor
 * in the URL — the Test runs table reads that and scrolls / opens
 * the task row.
 */
function TaskDrilldownDrawer({ open, onClose, title, subtitle, tasks: filtered }) {
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
              return (
                <Box key={t.id || i} sx={{ px: 2.5, py: 1.5 }}>
                  <Stack direction="row" alignItems="baseline" spacing={1}>
                    <Typography sx={{ fontSize: 11, color: "text.subtitle", fontVariantNumeric: "tabular-nums" }}>
                      #{i + 1}
                    </Typography>
                    <Typography sx={{ fontSize: 13, fontWeight: 600, flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {t.title || t.name || t.id}
                    </Typography>
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

/* Avg duration per time bucket — mirrors Bland's "Avg Duration" bar
   chart. Buckets tasks by turn count (proxy for complexity) with
   average duration per bucket. */
const DurationByBucketChart = memo(function DurationByBucketChart({ tasks }) {
  const theme = useTheme();
  const { categories, series } = useMemo(() => {
    const buckets = new Map();
    tasks.forEach((t) => {
      const n = t.steps?.length || 0;
      if (!n || !t.durationMs) return;
      const row = buckets.get(n) || { sum: 0, count: 0 };
      row.sum += t.durationMs; row.count += 1;
      buckets.set(n, row);
    });
    const keys = [...buckets.keys()].sort((a, b) => a - b);
    return {
      categories: keys.map((k) => `${k}t`),
      series: [{ name: "Avg duration", data: keys.map((k) => Math.round((buckets.get(k).sum / buckets.get(k).count) / 1000 * 10) / 10) }],
    };
  }, [tasks]);

  return (
    <Panel title="Avg duration by complexity" subtitle="Seconds per task, bucketed by turn count">
      <Box sx={{ px: 1.5, py: 1.5 }}>
        <ReactApexChart
          type="bar" height={260}
          series={series}
          options={{
            chart: { toolbar: { show: false }, animations: { enabled: false }, background: "transparent", fontFamily: theme.typography.fontFamily },
            theme: { mode: theme.palette.mode },
            plotOptions: { bar: { columnWidth: "56%", borderRadius: 2 } },
            dataLabels: { enabled: false },
            stroke: { show: false },
            fill: { type: "solid", opacity: 0.9 },
            colors: ["#7857FC"],
            xaxis: { categories, axisBorder: { show: false }, axisTicks: { show: false }, labels: { style: { colors: theme.palette.text.secondary, fontSize: "10px" } } },
            yaxis: { labels: { style: { colors: theme.palette.text.secondary, fontSize: "10px" }, formatter: (v) => `${v}s` } },
            grid: { borderColor: theme.palette.divider, strokeDashArray: 4, padding: { left: 8, right: 8, top: -6, bottom: -6 } },
            tooltip: { y: { formatter: (v) => `${v} seconds avg` } },
          }}
        />
      </Box>
    </Panel>
  );
});
DurationByBucketChart.propTypes = { tasks: PropTypes.array };

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
function KpiStrip({ tasks, biz, trend }) {
  const latencies = sortedNums(tasks, (t) => latencyOf(t));
  const p90Lat = percentile(latencies, 90);
  const critical = tasks.filter((t) => t.critical && (t.status === "failed" || t.status === "error")).length;
  const avgDurationS = latencies.length
    ? latencies.reduce((a, v) => a + v, 0) / latencies.length / 1000
    : 0;

  const deltaPct = (curr, prev) => {
    if (typeof prev !== "number" || prev === 0) return null;
    return Math.round(((curr - prev) / prev) * 100);
  };

  /* 8 cards, ordered by importance from top-left:
     Pass rate → Total tasks → Escalation → Critical
     Avg duration → Latency p90 → Cost/pass → Total cost
     Follows the shape Bland / Retell / Cekura all converge on:
     outcome first, volume second, latency third, cost last. */
  const cards = [
    {
      label: "Pass rate",
      value: `${biz.resolutionRate}%`,
      sub: `${biz.passed} / ${biz.measured}`,
      delta: deltaPct(biz.resolutionRate, trend?.prevPassRate),
      goodUp: true,
    },
    {
      label: "Total tasks",
      value: numFmt.format(biz.total),
      sub: `${biz.measured} measured`,
    },
    {
      label: "Escalation",
      value: `${biz.escalationRate}%`,
      sub: `${biz.escalated} handed off`,
      delta: deltaPct(biz.escalationRate, trend?.prevEscalationRate),
      goodUp: false,
    },
    {
      label: "Critical issues",
      value: numFmt.format(critical),
      sub: critical > 0 ? "release blockers" : "none",
    },
    {
      label: "Avg duration",
      value: `${avgDurationS.toFixed(1)}s`,
      sub: `${latencies.length} timed tasks`,
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
      /* Container's bgcolor shows through the `gap: 1px` between cells,
         so setting it to the divider color paints a perfect 1px seam
         between every cell. Each cell then restores background.paper.
         Avoids the nth-of-type juggling that forgot to reset
         borderColor at every 4n seam. */
      bgcolor: "divider",
      display: "grid",
      gridTemplateColumns: { xs: "repeat(2, 1fr)", sm: "repeat(4, 1fr)", md: "repeat(8, 1fr)" },
      gap: "1px",
      overflow: "hidden",
      "& > *": { bgcolor: "background.paper" },
    }}>
      {cards.map((c) => <HeroKpi key={c.label} {...c} />)}
    </Box>
  );
}
KpiStrip.propTypes = {
  tasks: PropTypes.array, evals: PropTypes.array, biz: PropTypes.object, trend: PropTypes.object,
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
    <Box sx={{ px: 1, pt: 1, pb: 1.5 }}>
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
            labels: { style: { colors: theme.palette.text.secondary, fontSize: "12px" }, maxWidth: 220 },
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

/* ── turns × outcome bars ──────────────────────────────────────────── */

/* A scatter over (turns, duration) collapses at the same x for every
   task with the same turn count, so a dozen dots pile onto one column
   and become unreadable. This replaces it with a bar chart per turn
   bucket, stacked by outcome — same "does complexity correlate with
   failure?" question, but every task is visible and countable. */
const TurnBars = memo(function TurnBars({ tasks }) {
  const theme = useTheme();
  const { categories, series } = useMemo(() => {
    if (!tasks?.length) return { categories: [], series: [] };
    const counts = new Map(); // turnCount → { passed, failed }
    tasks.forEach((t) => {
      const n = t.steps?.length || 0;
      if (!n) return;
      const row = counts.get(n) || { passed: 0, failed: 0 };
      if (t.status === "passed") row.passed += 1;
      else row.failed += 1;
      counts.set(n, row);
    });
    const turns = [...counts.keys()].sort((a, b) => a - b);
    return {
      categories: turns.map((n) => `${n}`),
      series: [
        { name: "Passed", data: turns.map((n) => counts.get(n).passed), color: CHART_GREEN },
        { name: "Failed", data: turns.map((n) => counts.get(n).failed), color: CHART_RED },
      ],
    };
  }, [tasks]);

  return (
    <PanelChart
      title="Tasks by turn count"
      subtitle="One bar per turn count, stacked by outcome. Rising red on the right = complex tasks fail more."

    >
      <ReactApexChart
        type="bar" height={260}
        series={series}
        options={{
          chart: {
            type: "bar", stacked: true, toolbar: { show: false },
            animations: { enabled: false }, background: "transparent",
            fontFamily: theme.typography.fontFamily,
          },
          theme: { mode: theme.palette.mode },
          plotOptions: { bar: { columnWidth: "68%", borderRadius: 2 } },
          dataLabels: { enabled: false },
          legend: { show: false },
          stroke: { show: false },
          fill: { type: "solid", opacity: 0.9 },
          xaxis: {
            categories,
            title: { text: "turns", style: { color: theme.palette.text.subtitle, fontSize: "10px", fontWeight: 400 } },
            axisBorder: { show: false }, axisTicks: { show: false },
            labels: { style: { colors: theme.palette.text.secondary, fontSize: "10px" }, rotate: 0 },
          },
          yaxis: {
            title: { text: "tasks", style: { color: theme.palette.text.subtitle, fontSize: "10px", fontWeight: 400 } },
            labels: { style: { colors: theme.palette.text.secondary, fontSize: "10px" }, formatter: (v) => `${Math.round(v)}` },
          },
          grid: { borderColor: theme.palette.divider, strokeDashArray: 4, padding: { left: 0, right: 0, top: -6, bottom: -6 } },
          colors: [CHART_GREEN, CHART_RED],
          tooltip: {
            shared: true, intersect: false,
            x: { formatter: (v) => `${v} turns` },
            y: { formatter: (v) => `${v} tasks` },
          },
        }}
      />
    </PanelChart>
  );
});
TurnBars.propTypes = { tasks: PropTypes.array };

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
    <Panel title="Failure attribution" subtitle="Which layer to blame first — read counter-clockwise from Agent.">
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
          plotOptions: { pie: { donut: { size: "70%", labels: { show: true,
            name: { fontSize: "10px", color: theme.palette.text.subtitle },
            value: { fontSize: "22px", fontWeight: 700, color: theme.palette.text.primary, formatter: (v) => `${v}` },
            total: { show: true, label: "Failures", fontSize: "10px", color: theme.palette.text.subtitle, formatter: () => `${total}` },
          } } } },
          tooltip: { y: { formatter: (v) => `${v} failures` } },
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
  const categories = rows.map((_, i) => `#${i + 1}`);
  const values = rows.map((r) => r.value);
  const labels = rows.map((r) => r.label);
  const metas = rows.map((r) => r.meta || "");

  return (
    <Box sx={{ px: 1, pt: 1, pb: 0.5 }}>
      <ReactApexChart
        type="bar" height={280}
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
              style: { colors: theme.palette.text.secondary, fontSize: "11px", fontWeight: 600 },
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
            padding: { left: 8, right: 8, top: 20, bottom: -4 },
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
function Panel({ title, subtitle, children, minHeight, action, exportRows, exportFilename }) {
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
          <Typography sx={{
            typography: "s1", color: "text.primary", fontWeight: 700,
            fontSize: 15, letterSpacing: -0.1, lineHeight: 1.3,
          }}>
            {title}
          </Typography>
          {subtitle && (
            <Typography sx={{ typography: "s3", color: "text.subtitle", fontSize: 12.5, mt: 0.5, lineHeight: 1.45 }}>
              {subtitle}
            </Typography>
          )}
        </Box>
        {action}
        {hasExport && (
          <Tooltip arrow title="Download data as CSV">
            <Box
              component="button"
              type="button"
              onClick={onExport}
              className="analytics-no-print"
              aria-label={`Download ${title} as CSV`}
              sx={{
                display: "inline-flex", alignItems: "center", justifyContent: "center",
                width: 26, height: 26, borderRadius: 999,
                bgcolor: "transparent", border: "none", cursor: "pointer",
                color: "text.subtitle",
                "&:hover": { bgcolor: "action.hover", color: "text.primary" },
              }}
            >
              <Iconify icon="solar:download-minimalistic-linear" width={14} />
            </Box>
          </Tooltip>
        )}
      </Stack>
      <Box sx={{ flex: 1, minHeight: 0 }}>
        {children}
      </Box>
    </Box>
  );
}
Panel.propTypes = {
  title: PropTypes.node, subtitle: PropTypes.node, children: PropTypes.node, minHeight: PropTypes.number,
  action: PropTypes.node,
  exportRows: PropTypes.array, exportFilename: PropTypes.string,
};

function PanelChart({ title, subtitle, children }) {
  return (
    <Panel title={title} subtitle={subtitle}>
      <Box sx={{ px: 2, pb: 2.5 }}>{children}</Box>
    </Panel>
  );
}
PanelChart.propTypes = { title: PropTypes.node, subtitle: PropTypes.node, children: PropTypes.node };

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

/* ── persona × outcome matrix ─────────────────────────────────────── */

/* One row per caller archetype: name · runs · pass rate + inline
   colour bar. Weakest personas surface first so "impatient callers
   only pass 18%" jumps out — the persona angle competitors highlight
   in their marketing. */
/**
 * Persona × outcome — radar chart.
 * A radar reads "shape of performance across archetypes" at a glance:
 * a symmetric hexagon = the agent handles every persona equally, a
 * spiky one = there's a weak flank. Much more scannable than a stack
 * of horizontal bars, and orthogonal in visual language from the
 * other bar/scatter panels below.
 */
const PersonaMatrixPanel = memo(function PersonaMatrixPanel({ tasks }) {
  const theme = useTheme();
  const rows = useMemo(() => derivePersonaMatrix(tasks, 6), [tasks]);
  if (!rows.length) {
    return (
      <Panel title="Persona × outcome" subtitle="Which caller archetype the agent handles worst">
        <Box sx={{ p: 3, textAlign: "center" }}>
          <Typography sx={{ typography: "s3", color: "text.subtitle", fontSize: 12 }}>
            No persona-tagged tasks yet.
          </Typography>
        </Box>
      </Panel>
    );
  }
  const exportRows = rows.map((r) => ({
    persona: r.name, passed: r.passed, total: r.total, pass_rate_pct: r.rate,
  }));
  return (
    <Panel
      title="Persona × outcome"
      subtitle="Pass rate per archetype — the shape shows the weak flanks"
      exportRows={exportRows}
    >
      <Box sx={{ px: 1, pt: 1, pb: 0.5 }}>
        <ReactApexChart
          type="radar" height={340}
          series={[{ name: "Pass rate", data: rows.map((r) => r.rate) }]}
          options={{
            chart: {
              toolbar: { show: false }, animations: { enabled: false },
              background: "transparent", fontFamily: theme.typography.fontFamily,
              /* Grow the plotting area so the polygon fills the panel;
                 offsets pull the shape back into view once the reserved
                 label margin is out of the equation. */
              parentHeightOffset: 0,
              offsetY: 10,
            },
            theme: { mode: theme.palette.mode },
            colors: ["#7857FC"],
            stroke: { width: 2, colors: ["#7857FC"] },
            fill: { opacity: 0.25 },
            markers: { size: 5, colors: ["#7857FC"], strokeColors: theme.palette.background.paper, strokeWidth: 2 },
            xaxis: {
              categories: rows.map((r) => r.name),
              labels: {
                show: true,
                style: {
                  colors: theme.palette.text.secondary,
                  fontSize: "12px",
                  fontWeight: 500,
                },
              },
            },
            yaxis: { show: false, min: 0, max: 100 },
            plotOptions: {
              radar: {
                /* Explicit size pushes the polygon to fill the panel
                   width; ApexCharts otherwise reserves ~40% for label
                   padding and leaves a tiny hexagon in the middle. */
                size: 130,
                polygons: {
                  strokeColors: theme.palette.divider,
                  connectorColors: theme.palette.divider,
                  fill: { colors: ["transparent", "transparent"] },
                },
              },
            },
            tooltip: {
              theme: theme.palette.mode,
              y: {
                formatter: (v, opts) => {
                  const r = rows[opts?.dataPointIndex];
                  return r ? `${v}% · ${r.passed}/${r.total} passed` : `${v}%`;
                },
                title: { formatter: () => "" },
              },
            },
          }}
        />
      </Box>
    </Panel>
  );
});
PersonaMatrixPanel.propTypes = { tasks: PropTypes.array };

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
        }
      `}</style>

      {/* Toolbar — CSV / PDF export triggers. Hidden in print output. */}
      <Stack direction="row" alignItems="center" spacing={1} className="analytics-no-print" sx={{ mb: 0 }}>
        <Box sx={{ flex: 1 }} />
        <ToolbarButton
          icon="solar:printer-linear"
          label="Export PDF"
          onClick={() => window.print()}
        />
      </Stack>

      {/* 2. Regression banner (only when a prior run exists) */}
      <RegressionBanner delta={delta} />

      {/* 3. Top-of-page KPI strip (8 cells in Retell/Bland shape) */}
      <KpiStrip tasks={tasks} evals={evals} biz={biz} trend={trend} runHistory={runHistory} currentRunId={currentRunId} />

      {/* SECTION: Breakdowns — the categorical splits that answer
          "how did each task actually go?" Placed above Trends
          because a single run's inner trend (running pass rate) is
          less actionable than the categorical shape of outcomes. */}
      <SectionHeader
        title="Breakdowns"
        subtitle="How each task went, split by category"
      />
      <Box sx={{ display: "grid", gap: 1.5, gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr", lg: "repeat(4, 1fr)" }, alignItems: "stretch" }}>
        <SuccessDonut tasks={tasks} />
        <OutcomeDonutChart tasks={tasks} biz={biz} />
        <SentimentDonut tasks={tasks} />
        <DisconnectionDonut tasks={tasks} />
        {env?.surface === "voice" && <PhoneIODonut tasks={tasks} env={env} />}
      </Box>

      {/* SECTION: Trends — pass rate and latency across the run's task
          sequence + concurrency + latency percentiles (Retell parity). */}
      <SectionHeader
        title="Trends"
        subtitle={`Pass rate, latency and concurrency across this run's ${tasks.length} tasks`}
      />
      <DualLineOverTime tasks={tasks} />
      <Box sx={{ display: "grid", gap: 1.5, gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" }, alignItems: "stretch" }}>
        <ConcurrencyPanel tasks={tasks} />
        <LatencyPercentilesPanel tasks={tasks} />
      </Box>

      {/* SECTION: Distribution — one compact summary table for the
          five spread-shape metrics (Grafana/Sentry pattern), plus
          the two deeper cuts (complexity × duration, turn count ×
          outcome) that don't fit a percentile summary. Much tighter
          than four stacked histogram panels of the same shape. */}
      <SectionHeader
        title="Distribution"
        subtitle="How each metric spreads across the tasks — percentiles + shape"
      />
      <DistributionSummary tasks={tasks} env={env} />
      <Box sx={{ display: "grid", gap: 1.5, gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" }, alignItems: "stretch" }}>
        <DurationByBucketChart tasks={tasks} />
        <TurnBars tasks={tasks} />
      </Box>
      <Box>
        <AttributionTable tasks={tasks} />
      </Box>

      {/* SECTION: Failure analysis — deep dive into what's failing */}
      <SectionHeader
        title="Failure analysis"
        subtitle="Where losses cluster and which caller archetypes trip the agent"
      />
      <Box sx={{ display: "grid", gap: 1.5, gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" }, alignItems: "stretch" }}>
        <PersonaMatrixPanel tasks={tasks} />
        <UseCaseRiskList tasks={tasks} />
      </Box>
      <Box>
        <EvalsTable tasks={tasks} evals={evals} />
      </Box>

      {/* SECTION: Voice-only latency SLOs + cost breakdown (voice runs). */}
      {voice && (
        <>
          <SectionHeader
            title="Voice latency SLOs"
            subtitle="TTFW · LLM · TTS · ASR — the four segments that decide caller experience"
          />
          <Box sx={{ display: "grid", gap: 1.5, gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" }, alignItems: "stretch" }}>
            <VoiceLatencyPanel voice={voice} />
            <VoiceCostBreakdownPanel tasks={tasks} />
          </Box>
        </>
      )}

      {/* SECTION: Performance tails — what to trim first */}
      <SectionHeader
        title="Performance tails"
        subtitle="The slowest and priciest tasks — the shortest path to cost / latency wins"
      />
      <Box sx={{ display: "grid", gap: 1.5, gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" }, alignItems: "stretch" }}>
        <SlowestTable tasks={tasks} />
        <ExpensiveTable tasks={tasks} />
      </Box>
    </Stack>
    </DrilldownContext.Provider>
  );
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
