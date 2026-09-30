import PropTypes from "prop-types";
import React, { useMemo, useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, Table, TableBody, TableCell, TableHead, TableRow,
  Checkbox, Tooltip, Button, Menu, MenuItem, ListItemIcon, ListItemText, Divider, IconButton,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { interpolateColorBasedOnScore } from "src/utils/utils";
import { subTasksFor } from "../_mock/contract";
import { neutralCheckboxSx } from "../components/primitives";

/**
 * The traces, as a table with optional grouping.
 *
 * Mirrors the shape of the Scenarios table used during env creation:
 * grouped rows with a sticky header per group, count pill on the
 * right, chevron-toggle to collapse.
 *
 * Groupings offered — via a compact `Group by` dropdown just above
 * the table:
 *   - None            → flat list (default)
 *   - Use case        → task.useCase (fallback: derive from id / title)
 *   - Failure pattern → Passed / Critical / Said-not-done /
 *                       Grader failed / Errored / Not measured
 *   - Status          → the coarser Passed / Failed / Not measured cut
 *
 * Each group header shows: chevron · label · task count · pass-rate
 * pill (green/amber/red). Clicking the header collapses that group.
 */
const hash = (str) => {
  let h = 0;
  for (let i = 0; i < String(str).length; i += 1) h = (h * 31 + String(str).charCodeAt(i)) >>> 0;
  return h;
};
export const csatOf = (t) => Math.max(1, Math.round((t.evalResults?.[0]?.score ?? 0.5) * 10) - 4);
export const latencyOf = (t) => 280 + (hash(t.id) % 320);

/*
  Only truly bad values get called out — a single-tier red signal reads
  louder than a red/amber pair and keeps the table quiet enough that
  when something does turn red, the eye lands on it. Thresholds are
  pitched at the top ~10-15% of the distribution for each metric so
  the highlight stays rare and meaningful.
*/
const METRIC_THRESHOLDS = {
  csat:    { direction: "lowIsBad",  bad: 4 },
  turns:   { direction: "highIsBad", bad: 12 },
  latency: { direction: "highIsBad", bad: 550 },
  tokens:  { direction: "highIsBad", bad: 6000 },
};

const isBad = (metric, value) => {
  const spec = METRIC_THRESHOLDS[metric];
  if (!spec || value == null) return false;
  return spec.direction === "lowIsBad" ? value <= spec.bad : value >= spec.bad;
};

/*
  Column definitions live outside the picker so both the picker and the
  table body read them from one source of truth. `defaultOn` decides
  what a user sees on first load — the important columns for reading a
  run are the run's own outputs (call details), the system metrics
  (CSAT, turns, latency, tokens) and how the evals scored. Scenario
  details (persona, scenario title, ideal outcome, conversation
  branch) restate the setup, useful for debugging but noisy for
  scanning, so they start hidden and turn on via the picker. `group`
  drives the section headers in the picker menu.
*/
export const TRACE_COLUMNS = [
  /* Run details always sits first (right after the checkbox) so the
     row's identity — its status/duration/id — reads before any of the
     optional scenario-detail columns. */
  { key: "callDetails", label: "Run details",          defaultOn: true,  width: 150, group: "Run details" },
  { key: "persona",     label: "Persona",              defaultOn: true,  width: 210, group: "Scenario details" },
  { key: "scenario",    label: "Scenario",             defaultOn: false, width: 300, group: "Scenario details" },
  { key: "expected",    label: "Ideal outcome",        defaultOn: false, width: 300, group: "Scenario details" },
  { key: "branch",      label: "Conversation branch", defaultOn: false, width: 260, group: "Scenario details" },
  { key: "csat",        label: "CSAT",                 defaultOn: true,  width: 84,  group: "System metrics" },
  { key: "turns",       label: "Turns",                defaultOn: true,  width: 92,  group: "System metrics" },
  { key: "latency",     label: "Latency",              defaultOn: true,  width: 96,  group: "System metrics" },
  { key: "tokens",      label: "Tokens",               defaultOn: true,  width: 96,  group: "System metrics" },
];

/* Each eval is its own column. Stored as an exclusion marker on the
   visibility Set (`eval:<id>:hidden`), so a new eval added later defaults
   to visible without touching the stored state. */
const HIDDEN_EVAL = (id) => `eval:${id}:hidden`;
export const isEvalHidden = (columns, evalId) => columns?.has(HIDDEN_EVAL(evalId));
export const visibleEvals = (columns, evals) =>
  (evals || []).filter((e) => !isEvalHidden(columns, e.id));

export const defaultTraceColumns = () =>
  new Set(TRACE_COLUMNS.filter((c) => c.defaultOn).map((c) => c.key));

/*
  Column visibility picker. Same outlined-button shape as the group-by
  picker so the two sit side-by-side without visual noise. Essential
  columns render as disabled ticks so the reader understands why they
  can't be turned off.
*/
export function TraceColumnsPicker({ value, onChange, evals = [] }) {
  const [anchor, setAnchor] = useState(null);
  /* Base columns are inclusion (ticked = shown); eval columns are exclusion
     (a marker means hidden), so the shown-count sums both correctly. */
  const shownBase = TRACE_COLUMNS.filter((c) => value.has(c.key)).length;
  const shownEvals = evals.filter((e) => !value.has(HIDDEN_EVAL(e.id))).length;
  const totalCols = TRACE_COLUMNS.length + evals.length;
  const shownCount = shownBase + shownEvals;
  const toggle = (key) => {
    const next = new Set(value);
    if (next.has(key)) next.delete(key); else next.add(key);
    onChange(next);
  };
  const toggleEval = (id) => {
    const key = HIDDEN_EVAL(id);
    const next = new Set(value);
    if (next.has(key)) next.delete(key); else next.add(key);
    onChange(next);
  };
  /* Bucket into sections in declaration order so the menu reads
     run → scenario → system → evals, top-to-bottom. Evals live in
     their own section that lists each applied grader individually. */
  const sections = TRACE_COLUMNS.reduce((acc, c) => {
    const last = acc[acc.length - 1];
    if (last && last.name === c.group) last.items.push(c);
    else acc.push({ name: c.group, items: [c] });
    return acc;
  }, []);
  if (evals.length) {
    sections.push({
      name: "Evaluations",
      items: evals.map((e) => ({
        key: `eval:${e.id}`,
        label: e.name || e.id,
        checked: !value.has(HIDDEN_EVAL(e.id)),
        onToggle: () => toggleEval(e.id),
      })),
    });
  }
  return (
    <>
      <Button
        size="small" variant="outlined"
        onClick={(e) => setAnchor(e.currentTarget)}
        startIcon={<Iconify icon="solar:widget-4-linear" width={15} sx={{ color: "text.subtitle" }} />}
        endIcon={<Iconify icon="solar:alt-arrow-down-linear" width={12} sx={{ color: "text.subtitle" }} />}
        sx={{
          typography: "s2", fontWeight: 700, textTransform: "none",
          height: 32,
          color: "text.primary",
          borderColor: "divider",
          "&:hover": { borderColor: "text.disabled", bgcolor: "transparent" },
        }}
      >
        Columns
        <Box component="span" sx={{ mx: 0.5, color: "text.subtitle", fontWeight: 400 }}>·</Box>
        <Box component="span" sx={{ color: "text.subtitle" }}>{shownCount}/{totalCols}</Box>
      </Button>
      <Menu
        anchorEl={anchor} open={!!anchor} onClose={() => setAnchor(null)}
        PaperProps={{ sx: { minWidth: 240 } }}
      >
        {sections.map((section, i) => [
          i > 0 && <Divider key={`div-${section.name}`} sx={{ my: 0.5 }} />,
          <Typography
            key={`h-${section.name}`}
            sx={{
              typography: "s3", fontWeight: 700, color: "text.subtitle",
              textTransform: "uppercase", letterSpacing: 0.4,
              px: 2, py: 0.5, mt: i === 0 ? 0.5 : 0,
            }}
          >
            {section.name}
          </Typography>,
          ...section.items.map((c) => {
            /* Base columns use `value.has(key)` (inclusion); eval columns
               come pre-computed with `checked` + `onToggle`. */
            const checked = c.onToggle ? c.checked : value.has(c.key);
            const onClick = c.onToggle || (() => toggle(c.key));
            return (
              <MenuItem key={c.key} onClick={onClick} sx={{ py: 0.5 }}>
                <ListItemIcon sx={{ minWidth: 32 }}>
                  <Checkbox size="small" checked={checked} sx={{ p: 0, ...neutralCheckboxSx }} />
                </ListItemIcon>
                <ListItemText primary={c.label} primaryTypographyProps={{ typography: "s2" }} />
              </MenuItem>
            );
          }),
        ])}
        <Divider sx={{ my: 0.5 }} />
        <MenuItem onClick={() => onChange(defaultTraceColumns())} sx={{ py: 0.5 }}>
          <ListItemIcon sx={{ minWidth: 32 }}>
            <Iconify icon="solar:restart-linear" width={16} />
          </ListItemIcon>
          <ListItemText primary="Reset to defaults" primaryTypographyProps={{ typography: "s2" }} />
        </MenuItem>
      </Menu>
    </>
  );
}
TraceColumnsPicker.propTypes = {
  value: PropTypes.instanceOf(Set).isRequired,
  onChange: PropTypes.func.isRequired,
  evals: PropTypes.array,
};

export const GROUPINGS = [
  { id: "useCase", label: "Goal",              icon: "solar:target-linear" },
  { id: "persona", label: "Persona",           icon: "solar:user-rounded-linear" },
  { id: "rule",    label: "Rule broken",       icon: "solar:shield-cross-linear" },
  { id: "subGoal", label: "Failure sub-goal", icon: "solar:map-linear" },
  { id: "pattern", label: "Failure pattern",   icon: "solar:danger-triangle-linear" },
  { id: "status",  label: "Status",            icon: "solar:check-circle-linear" },
  /* Persona-attribute buckets — same-accent / same-age-band callers
     cluster together, so a reader can spot regional or generational
     regressions ("older callers keep dropping mid-flow"). */
  { id: "accent",  label: "Accent",             icon: "solar:global-linear" },
  { id: "age",     label: "Age",                icon: "solar:calendar-linear" },
  /* Attack — adversarial pressure the scenario applies (rule probe,
     data trap, prompt-injection, edge branch). Groups the run by how
     it was trying to break the agent. */
  { id: "attack",  label: "Attack",             icon: "solar:bug-linear" },
  /* Task — bucket by scenario name. Trials of the same task cluster,
     so flakiness across trials becomes obvious. */
  { id: "task",    label: "Task",               icon: "solar:document-text-linear" },
  /* Escape hatch — flat table, no grouping at all. Kept last so the
     dropdown reads "…then no grouping" as the exit. */
  { id: "none",    label: "No grouping",        icon: "solar:list-linear" },
];

/*
  Which sub-goal did this task fail at?
  Sub-goals are the ordered checkpoints on the way to the scenario's
  goal (auth → look up → apply rule → present options → confirm →
  issue). A trace's `failStep` is an index into its `steps`; we map
  that onto the sub-goal list proportionally so long conversations
  and short ones both land in a sensible bucket. Passed / unmeasured
  traces have no failure sub-goal by definition.
*/
const failSubGoalOf = (task, env) => {
  if (!task) return null;
  if (task.status === "passed" || task.status === "unmeasured") return null;
  const subs = subTasksFor(task, env);
  if (!subs.length) return null;
  const stepCount = (task.steps || []).length;
  const failAt = typeof task.failStep === "number" ? task.failStep : null;
  if (failAt == null || stepCount === 0) return subs[subs.length - 1].label;
  const idx = Math.min(subs.length - 1, Math.floor((failAt / stepCount) * subs.length));
  return subs[idx].label;
};

/*
  Per-sub-goal outcome for the Run details cell — binary, matching prod:
  each sub-goal is either met (✓) or failed (✗). Prod scores every
  sub-goal independently; here the mock has one crux, so a failed task
  marks only that sub-goal as failed and the others as met.
*/
function subGoalStatuses(task, env) {
  if (!task || task.status === "unmeasured") return [];
  const subs = subTasksFor(task, env);
  if (!subs.length) return [];
  if (task.status === "passed" || task.status === "flaky") {
    return subs.map((s) => ({ id: s.id, label: s.label, status: "passed" }));
  }
  const stepCount = (task.steps || []).length;
  const failAt = typeof task.failStep === "number" ? task.failStep : null;
  const failIdx = failAt == null || stepCount === 0
    ? subs.length - 1
    : Math.min(subs.length - 1, Math.floor((failAt / stepCount) * subs.length));
  return subs.map((s, i) => ({
    id: s.id,
    label: s.label,
    status: i === failIdx ? "failed" : "passed",
  }));
}

/*
  Standalone group-by picker, so callers (the run header, a compare
  view) can hoist the control out of the table body and drop it wherever
  the layout wants it. The `value`/`onChange` shape lets the picker be
  fully controlled.
*/
export function TraceGroupByPicker({ value, onChange }) {
  const [anchor, setAnchor] = useState(null);
  const current = GROUPINGS.find((g) => g.id === value) || GROUPINGS[0];
  return (
    <>
      <Button
        size="small" variant="outlined"
        onClick={(e) => setAnchor(e.currentTarget)}
        startIcon={<Iconify icon={current.icon} width={15} sx={{ color: "primary.main" }} />}
        endIcon={<Iconify icon="solar:alt-arrow-down-linear" width={12} sx={{ color: "text.subtitle" }} />}
        sx={{
          typography: "s2", fontWeight: 700, textTransform: "none",
          height: 32,
          color: "text.primary",
          borderColor: "divider",
          "&:hover": { borderColor: "text.disabled", bgcolor: "transparent" },
        }}
      >
        Group by
        <Box component="span" sx={{ mx: 0.5, color: "text.subtitle", fontWeight: 400 }}>·</Box>
        <Box component="span" sx={{ color: "primary.main" }}>{current.label}</Box>
      </Button>
      <Menu anchorEl={anchor} open={!!anchor} onClose={() => setAnchor(null)}>
        {GROUPINGS.map((g) => (
          <MenuItem
            key={g.id}
            selected={g.id === value}
            onClick={() => { onChange(g.id); setAnchor(null); }}
            sx={{ py: 0.75, gap: 0.75 }}
          >
            <Iconify icon={g.icon} width={16} />
            <Typography sx={{ typography: "s2" }}>{g.label}</Typography>
          </MenuItem>
        ))}
      </Menu>
    </>
  );
}
TraceGroupByPicker.propTypes = {
  value: PropTypes.string.isRequired,
  onChange: PropTypes.func.isRequired,
};

/*
  Every task lands in exactly one bucket per grouping mode, or in null
  when it doesn't belong to any bucket for that mode. Pattern mode is a
  view of *failures* — passed and unmeasured tasks don't have a
  failure pattern, so they drop out of the grouped list entirely.
*/
const groupOfTask = (t, mode, env) => {
  if (mode === "useCase") {
    return deriveUseCaseLabel(t);
  }
  if (mode === "subGoal") {
    return failSubGoalOf(t, env);
  }
  if (mode === "pattern") {
    if (t.status === "passed" || t.status === "unmeasured") return null;
    if (t.status === "error") return "Errored";
    if (t.critical) return "Critical rule broken";
    if (t.callLog?.unsupportedClaim) return "Said, not done";
    if ((t.evalResults || []).some((r) => !r.passed)) return "Evaluation failed";
    return "Other failure";
  }
  if (mode === "status") {
    if (t.status === "passed") return "Passed";
    if (t.status === "unmeasured") return "Not measured";
    if (t.status === "error") return "Errored";
    return "Failed";
  }
  if (mode === "persona") {
    /* Persona attaches at the scenario level; if the seed didn't populate one
       (a plain twin scenario without a persona), the task lands in "No
       persona" rather than dropping out — otherwise the reader would think
       those tasks were skipped. */
    return t.persona?.name || "No persona";
  }
  if (mode === "rule") {
    /* Rule-broken is a view of policy violations: the rule the agent was
       supposed to hold. The rule-enforcement scenarios use titles shaped as
       "Refuse a request that would break: <the rule>" — everything after the
       colon is the rule text. Tasks that aren't rule-checks (a happy-path
       call, an edge-case trap) have no rule to break here and drop out. */
    const title = t.title || "";
    const m = title.match(/^Refuse a request that would break:\s*(.+)$/);
    if (m) return m[1].trim();
    return null;
  }
  if (mode === "accent") {
    /* Accent lives inside the persona's voice string ("US male",
       "IN female", "UK female"). The first token is the region — that
       is what clusters callers together for a reader looking at whether
       accent explains a failure. */
    const voice = t.persona?.voice || "";
    const region = voice.split(/\s+/)[0];
    return region || "No accent";
  }
  if (mode === "age") {
    /* Age bracket in 10-year bands, using `ageGroup` when the mock
       enriched it or deriving it from the raw age. Missing values
       land in "Unknown age" so the reader can see how many rows
       lacked persona demographics. */
    const bracket = t.persona?.ageGroup;
    if (bracket) return bracket;
    const age = t.persona?.age;
    if (typeof age === "number") {
      const decade = Math.floor(age / 10) * 10;
      return `${decade}-${decade + 10}`;
    }
    return "Unknown age";
  }
  if (mode === "attack") {
    /* Attack — how the scenario was trying to break the agent. Derived
       from the scenario id (rule / trap / adversarial / edge), with core
       scenarios falling into "No attack" so the reader can see how many
       of the run's rows were happy-path baselines. */
    const id = t.id || "";
    if (id.includes("-rule-")) return "Rule probe";
    if (id.includes("-adversarial-")) return "Adversarial";
    if (id.includes("-trap-")) return "Data trap";
    if (id.includes("-edge-")) return "Edge case";
    return "No attack";
  }
  if (mode === "task") {
    /* Task groups rows by scenario title so trials of the same
       scenario cluster together. Falls back to the scenario id when a
       row hasn't materialised a title yet. */
    return t.title || t.name || t.id || "Untitled task";
  }
  if (mode === "none") {
    /* Flat view — every row lands in the same group. The group header
       just prints a single "All tasks" band which the table hides
       when there is only one group. */
    return "All tasks";
  }
  return "All";
};

const GROUP_SORT_ORDER = {
  pattern: ["Critical rule broken", "Said, not done", "Evaluation failed", "Errored", "Other failure"],
  status:  ["Failed", "Errored", "Not measured", "Passed"],
};

export default function TraceTable({
  tasks, evals, selected, onToggle, onToggleAll, onOpen, onOpenEval,
  groupBy = "useCase", columns, env, onRerunEval, onDeleteEval, onEditEval, rescoringEvalId,
}) {
  /* Groups start collapsed. Lazy-primed with the group list once it resolves. */
  const [collapsed, setCollapsed] = useState(null);
  /* If the parent didn't pass column state, fall back to defaults so the
     table is usable in older callers that don't wire a picker. */
  const visible = columns || defaultTraceColumns();
  const show = (key) => visible.has(key);
  /* Which evals to render, in order. Any not explicitly hidden shows. */
  const shownEvals = useMemo(() => visibleEvals(visible, evals), [visible, evals]);
  const showEvals = shownEvals.length > 0;

  const allOn = tasks.length > 0 && tasks.every((t) => selected.has(t.id));
  const someOn = tasks.some((t) => selected.has(t.id)) && !allOn;

  const groups = useMemo(() => {
    const byKey = new Map();
    tasks.forEach((t) => {
      const label = groupOfTask(t, groupBy, env);
      /* null = this task has no bucket in the current mode (e.g. a
         passed task under "Failure pattern"). Drop it entirely so the
         view stays a clean partition of what the mode is about. */
      if (label == null) return;
      if (!byKey.has(label)) byKey.set(label, []);
      byKey.get(label).push(t);
    });
    const arr = Array.from(byKey, ([label, rows]) => {
      const measured = rows.filter((r) => r.status !== "unmeasured");
      const passed = measured.filter((r) => r.status === "passed").length;
      /* Group aggregates for the header row when collapsed.
         Averages for CSAT/Turns/Latency, total for Tokens; each
         eval column gets its own pass-rate. */
      const avg = (vals) => (vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null);
      const csatVals = rows.map(csatOf).filter((v) => Number.isFinite(v));
      const turnVals = rows.map((r) => r.steps?.length || 0);
      const latVals = rows.map(latencyOf);
      const tokTotal = rows.reduce((a, r) => a + (r.tokens || 0), 0);
      const evalAgg = {};
      rows.forEach((r) => {
        (r.evalResults || []).forEach((er) => {
          if (!evalAgg[er.id]) evalAgg[er.id] = { passed: 0, total: 0, scoreSum: 0 };
          evalAgg[er.id].total += 1;
          evalAgg[er.id].scoreSum += (er.score ?? 0);
          if (er.passed) evalAgg[er.id].passed += 1;
        });
      });
      return {
        label,
        rows,
        count: rows.length,
        pct: measured.length ? Math.round((passed / measured.length) * 100) : null,
        measured: measured.length,
        passed,
        agg: {
          csat: avg(csatVals) != null ? Math.round(avg(csatVals) * 10) / 10 : null,
          turns: avg(turnVals) != null ? Math.round(avg(turnVals) * 10) / 10 : null,
          latency: avg(latVals) != null ? Math.round(avg(latVals)) : null,
          tokens: tokTotal,
          evals: evalAgg,
        },
      };
    });
    /* Custom sort per mode so critical/failed lands above passed. */
    const order = GROUP_SORT_ORDER[groupBy];
    if (order) {
      arr.sort((a, b) => {
        const ai = order.indexOf(a.label);
        const bi = order.indexOf(b.label);
        if (ai === -1 && bi === -1) return a.label.localeCompare(b.label);
        if (ai === -1) return 1;
        if (bi === -1) return -1;
        return ai - bi;
      });
    } else {
      arr.sort((a, b) => b.count - a.count);
    }
    return arr;
  }, [tasks, groupBy, env]);

  /* Sticky at the top of the scroll area, without MUI's `stickyHeader`
     prop — that prop switches `borderCollapse` to `separate`, which makes
     the per-cell score tints on the eval columns bleed into one solid
     column-wide fill. Rolling our own sticky keeps `border-collapse: collapse`
     so each row's cell stays its own outlined rectangle. */
  const headCell = {
    typography: "s2", fontWeight: 500, color: "text.secondary",
    whiteSpace: "nowrap", bgcolor: "background.paper", height: 44, py: 0,
    position: "sticky", top: 0, zIndex: 2,
    borderBottom: "1px solid", borderColor: "divider",
    "&:not(:first-of-type)": { borderLeft: "1px solid", borderColor: "divider" },
  };
  const num = {
    verticalAlign: "top", py: 1.5,
    typography: "s2", color: "text.secondary", fontVariantNumeric: "tabular-nums",
    borderBottom: "1px solid", borderColor: "divider",
    "&:not(:first-of-type)": { borderLeft: "1px solid", borderColor: "divider" },
  };
  const bodyCell = {
    verticalAlign: "top", py: 1.5,
    borderBottom: "1px solid", borderColor: "divider",
    "&:not(:first-of-type)": { borderLeft: "1px solid", borderColor: "divider" },
  };
  /* Pinned to the left of the scroll area so the checkboxes stay reachable
     when the table scrolls horizontally. On the header row this cell also
     needs `top: 0` to pin at the corner — that's handled by combining
     `headCell` (which sets top: 0) with `checkCell` on that first cell. */
  const checkCell = {
    width: 48, p: 0, pl: 1.25, verticalAlign: "middle",
    borderBottom: "1px solid", borderColor: "divider",
    position: "sticky", left: 0, zIndex: 1,
    bgcolor: "background.paper",
  };

  /*
    Column span for the group header row. Only counts columns actually
    rendered — checkbox + every visible data column + one cell per
    visible eval when evals are shown. Wrong here and the sticky
    left/right cells stop lining up under horizontal scroll.
  */
  const dataColKeys = ["callDetails", "persona", "scenario", "expected", "branch", "csat", "turns", "latency", "tokens"];
  const visibleDataCols = dataColKeys.filter((k) => show(k)).length;
  const totalColSpan = 1 + visibleDataCols + shownEvals.length;

  /* Lazy-prime with all group labels so groups start collapsed. */
  const collapsedSet = collapsed ?? new Set(groups.map((g) => g.label));
  const toggleCollapsed = (label) => setCollapsed(() => {
    const next = new Set(collapsedSet);
    if (next.has(label)) next.delete(label); else next.add(label);
    return next;
  });

  const renderRow = (t) => (
    <TableRow
      key={t.id}
      hover
      sx={{
        cursor: "pointer",
        /* MUI's default hover is too heavy against the dark card — a
           quarter of the usual action.hover reads as a soft lift
           without swamping the outcome-tinted eval cells. */
        "&.MuiTableRow-hover:hover": {
          bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.02 : 0.015),
        },
      }}
    >
      <TableCell sx={checkCell}>
        <Checkbox
          size="small"
          checked={selected.has(t.id)}
          onChange={() => onToggle(t.id)}
          onClick={(e) => e.stopPropagation()}
          sx={neutralCheckboxSx}
        />
      </TableCell>

      {show("callDetails") && (() => {
        const outcome = runOutcome(t.status);
        return (
          <TableCell sx={bodyCell} onClick={() => onOpen(t)}>
            <Box minWidth={0}>
              <Stack direction="row" alignItems="center" spacing={0.75} sx={{ mb: 0.125 }}>
                <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>
                  {variantLabel(t.id)}
                </Typography>
                {t.critical && (
                  <Tooltip arrow title={`Release blocker — ${(t.blockerReason || "a failure here blocks the release.").replace(/^./, (c) => c.toLowerCase())}`}>
                    <Box sx={{ display: "flex" }}>
                      <Iconify icon="solar:danger-triangle-bold" width={12} sx={{ color: "#DC2626" }} />
                    </Box>
                  </Tooltip>
                )}
              </Stack>
              {t.summary && (
                <Typography sx={{ typography: "s3", color: "text.secondary", mb: 0.375, maxWidth: 340 }}>
                  {t.summary}
                </Typography>
              )}
              <Stack direction="row" alignItems="center" spacing={0.75}>
                <Typography sx={{ typography: "s3", fontWeight: 600, color: outcome.color }}>
                  {outcome.label}
                </Typography>
                <Box sx={{ width: "3px", height: "3px", borderRadius: "50%", bgcolor: "text.disabled" }} />
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                  {((t.durationMs || 0) / 1000).toFixed(1)}s
                </Typography>
              </Stack>
              <SubGoalsSummary task={t} env={env} />
            </Box>
          </TableCell>
        );
      })()}

      {show("persona") && (
        <TableCell sx={bodyCell} onClick={() => onOpen(t)}>
          <Stack spacing={0.5}>
            <Field icon="solar:user-id-linear" label="Name" value={t.persona?.name} />
            <Field icon="solar:user-linear" label="Voice" value={t.persona?.voice} />
            <Field icon="solar:users-group-rounded-linear" label="Age" value={t.persona?.age} />
            {t.persona?.traits?.length > 0 && (
              <Field icon="solar:tag-linear" label="Traits" value={t.persona.traits.join(", ")} />
            )}
          </Stack>
        </TableCell>
      )}

      {show("scenario") && (
        <TableCell sx={bodyCell} onClick={() => onOpen(t)}>
          <Stack direction="row" alignItems="flex-start" spacing={0.75}>
            <Typography sx={{ typography: "s2", fontWeight: 600 }}>{t.title}</Typography>
            {t.critical && (
              <Tooltip arrow title={`Release blocker — ${(t.blockerReason || "a failure here blocks the release.").replace(/^./, (c) => c.toLowerCase())}`}>
                <Box sx={{ display: "flex", mt: "2px" }}>
                  <Iconify icon="solar:danger-triangle-bold" width={12} sx={{ color: "text.subtitle" }} />
                </Box>
              </Tooltip>
            )}
          </Stack>
          <Typography sx={{ typography: "s2", color: "text.secondary", mt: 0.5 }}>{t.task}</Typography>
        </TableCell>
      )}

      {show("expected") && (
        <TableCell sx={{ ...bodyCell, typography: "s2", color: "text.secondary" }} onClick={() => onOpen(t)}>
          {t.expected}
        </TableCell>
      )}

      {show("branch") && (
        <TableCell sx={bodyCell} onClick={() => onOpen(t)}>
          <Typography sx={{ typography: "s3", color: "text.secondary", fontFamily: "ui-monospace, Menlo, monospace", lineHeight: 1.7 }}>
            {(t.steps || [])
              .filter((s) => s.role === "agent")
              .slice(0, 5)
              .map((_, i, arr) => (i === 0 ? "start" : `turn_${i}`) + (i < arr.length - 1 ? " → " : ""))
              .join("")}
          </Typography>
        </TableCell>
      )}

      {show("csat") && (
        <TableCell sx={num} onClick={() => onOpen(t)}>
          <MetricValue metric="csat" value={csatOf(t)} />
        </TableCell>
      )}
      {show("turns") && (
        <TableCell sx={num} onClick={() => onOpen(t)}>
          <MetricValue metric="turns" value={t.steps?.length || 0} />
        </TableCell>
      )}
      {show("latency") && (
        <TableCell sx={num} onClick={() => onOpen(t)}>
          <MetricValue metric="latency" value={latencyOf(t)} suffix="ms" />
        </TableCell>
      )}
      {show("tokens") && (
        <TableCell sx={num} onClick={() => onOpen(t)}>
          <MetricValue metric="tokens" value={t.tokens || 0} />
        </TableCell>
      )}

      {shownEvals.map((e) => {
        const r = t.evalResults?.find((x) => x.id === e.id);
        const rescoring = rescoringEvalId === e.id;
        return (
          <TableCell
            key={e.id}
            sx={{ ...bodyCell, p: 0, position: "relative" }}
            onClick={() => (r && onOpenEval ? onOpenEval(t, r) : onOpen(t))}
          >
            {rescoring ? (
              <Box sx={{ p: 2, display: "flex", justifyContent: "center" }}>
                <Iconify icon="solar:refresh-linear" width={14} sx={{ color: "text.disabled", animation: "tt-spin 0.8s linear infinite", "@keyframes tt-spin": { to: { transform: "rotate(360deg)" } } }} />
              </Box>
            ) : r ? <Score result={r} /> : (
              /* Missing on an adhoc column means this row wasn't in that
                 eval's re-run. Say so on hover; on the original run's
                 columns "—" is just "no result" and doesn't need a note. */
              e.adhoc ? (
                <Tooltip
                  arrow
                  title={`${e.name} was run on the other scenarios you selected — not on this one. Select this row and re-run the eval to score it.`}
                >
                  <Box sx={{ p: 2, typography: "s2", color: "text.disabled", cursor: "help" }}>—</Box>
                </Tooltip>
              ) : <Box sx={{ p: 2, typography: "s2", color: "text.disabled" }}>—</Box>
            )}
          </TableCell>
        );
      })}
    </TableRow>
  );

  /* Expand-all / collapse-all toggle. Reads `collapsedSet.size` against
     the group count: if every group is collapsed the button offers
     "Expand all"; otherwise "Collapse all". */
  const allCollapsed = groups.length > 0 && groups.every((g) => collapsedSet.has(g.label));
  const toggleAll = () => {
    if (allCollapsed) setCollapsed(new Set()); /* expand every group */
    else setCollapsed(new Set(groups.map((g) => g.label))); /* collapse every group */
  };

  return (
    /* Fills the vertical space left by the card's toolbar — the "Expand all"
       row stays put and the table below it scrolls, so the column headers
       (sticky inside the Table) always sit above whatever row is on screen. */
    <Box sx={{ display: "flex", flexDirection: "column", flex: 1, minHeight: 0 }}>
      <Stack
        direction="row" alignItems="center" spacing={1}
        sx={{ px: 1.5, py: 1, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}
      >
        <Button
          size="small" variant="text"
          disableRipple
          onClick={(e) => { toggleAll(); e.currentTarget.blur(); }}
          startIcon={<Iconify
            icon={allCollapsed ? "solar:alt-arrow-down-linear" : "solar:alt-arrow-right-linear"}
            width={14}
          />}
          sx={{
            typography: "s3", fontWeight: 600, color: "text.secondary",
            /* Drop the focus tint MUI leaves after a click — was staying
               grey even with the pointer elsewhere. */
            "&:hover": { bgcolor: "action.hover" },
            "&:focus, &:focus-visible": { bgcolor: "transparent" },
          }}
        >
          {allCollapsed ? "Expand all" : "Collapse all"}
        </Button>
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {groups.length} {groups.length === 1 ? "group" : "groups"}
        </Typography>
      </Stack>
      <Box sx={{ flex: 1, minHeight: 0, overflow: "auto" }}>
        {/*
          tableLayout: "auto" lets each column size to its widest cell
          (header or body) rather than being pinned to the width props.
          The width props on TableCells become hints — long text like
          "Fetch order by ID, email or phone" stretches its column, and
          short aggregates like "4 scenarios" collapse theirs. Reads
          more naturally than the previous fixed-layout, which
          truncated long labels while leaving short aggregate columns
          swimming in whitespace.

          `position: sticky` on the headCell style pins the column-header
          row to the top of this scroll area — the reader always sees which
          column each number belongs to as they scroll through the rows.
        */}
        <Table size="small" sx={{ minWidth: 1200, tableLayout: "auto" }}>
          <TableHead>
            <TableRow>
              {/* Corner cell — sticky top (from headCell) + sticky left (from
                  checkCell). `zIndex: 3` puts it above both the sticky header
                  row (z 2) and the sticky checkbox column (z 1) so nothing
                  slides underneath the corner. */}
              <TableCell sx={{ ...headCell, ...checkCell, zIndex: 3 }}>
                <Checkbox
                  size="small"
                  checked={allOn}
                  indeterminate={someOn}
                  onChange={onToggleAll}
                  sx={neutralCheckboxSx}
                />
              </TableCell>
              {show("callDetails") && <TableCell sx={{ ...headCell, width: 150 }}>Run details</TableCell>}
              {show("persona") && <TableCell sx={{ ...headCell, width: 210 }}>Persona</TableCell>}
              {show("scenario") && <TableCell sx={{ ...headCell, width: 300 }}>Scenario</TableCell>}
              {show("expected") && <TableCell sx={{ ...headCell, width: 300 }}>Ideal outcome</TableCell>}
              {show("branch") && <TableCell sx={{ ...headCell, width: 260 }}>Conversation branch</TableCell>}
              {show("csat") && <TableCell sx={{ ...headCell, width: 84 }}>CSAT</TableCell>}
              {show("turns") && <TableCell sx={{ ...headCell, width: 92 }}>Turns</TableCell>}
              {show("latency") && <TableCell sx={{ ...headCell, width: 96 }}>Latency</TableCell>}
              {show("tokens") && <TableCell sx={{ ...headCell, width: 96 }}>Tokens</TableCell>}
              {shownEvals.map((e) => (
                <TableCell key={e.id} sx={{ ...headCell, width: 150 }}>
                  <EvalHeadCell
                    name={e.name}
                    /* A column scoped to only the N scenarios the user
                       picked. Marked in the header so a row of "—"s reads
                       as "not part of this eval run" rather than "this
                       scenario failed the grader". */
                    partial={e.adhoc ? e.includedIds?.size : 0}
                    rescoring={rescoringEvalId === e.id}
                    onEdit={onEditEval ? () => onEditEval(e) : null}
                    onRerun={onRerunEval ? () => onRerunEval(e) : null}
                    onDelete={onDeleteEval ? () => onDeleteEval(e) : null}
                  />
                </TableCell>
              ))}
            </TableRow>
          </TableHead>

          <TableBody>
            {groupBy === "none"
              /* Flat view — skip the group header band entirely and render
                 every row as a plain list. Reader gets a clean single-column
                 stream when they don't want any bucketing. */
              ? groups.flatMap((g) => g.rows).map(renderRow)
              : groups.map((g) => (
                <React.Fragment key={g.label}>
                  <GroupHeaderRow
                    group={g}
                    collapsed={collapsedSet.has(g.label)}
                    onToggle={() => toggleCollapsed(g.label)}
                    colspan={totalColSpan}
                    show={show}
                    showEvals={showEvals}
                    evals={shownEvals}
                    selected={selected}
                    onToggleGroup={(rows) => {
                      /* Group checkbox behaves like the header select-all:
                         if every task in the group is already selected,
                         deselect them; otherwise select all. */
                      const ids = rows.map((r) => r.id);
                      const allSelected = ids.every((id) => selected.has(id));
                      if (allSelected) ids.forEach((id) => onToggle(id));
                      else ids.filter((id) => !selected.has(id)).forEach((id) => onToggle(id));
                    }}
                  />
                  {!collapsedSet.has(g.label) && g.rows.map(renderRow)}
                </React.Fragment>
              ))}
          </TableBody>
        </Table>
      </Box>
    </Box>
  );
}

TraceTable.propTypes = {
  tasks: PropTypes.array.isRequired,
  evals: PropTypes.array.isRequired,
  selected: PropTypes.object.isRequired,
  onToggle: PropTypes.func,
  onToggleAll: PropTypes.func,
  onOpen: PropTypes.func,
  /* Open the task focused on one eval's result (and its error localization). */
  onOpenEval: PropTypes.func,
  groupBy: PropTypes.string,
  columns: PropTypes.instanceOf(Set),
  env: PropTypes.object,
  onRerunEval: PropTypes.func,
  onDeleteEval: PropTypes.func,
  onEditEval: PropTypes.func,
  rescoringEvalId: PropTypes.string,
};

/*
  Group header row — full-width sticky-feel bar with chevron, label,
  count, and a color-coded pass-rate pill. Clicking anywhere on the
  row collapses/expands the group.
*/
function GroupHeaderRow({ group, collapsed, onToggle, show, showEvals, evals, selected, onToggleGroup }) {
  /* No band tint. A stack of collapsed groups previously read as a
     wall of grey; now the row uses the standard body background and
     picks up hierarchy from typography (bolder label, tabular
     aggregates) and a fine bottom divider instead. */
  const rowHover = (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.04 : 0.025);
  const cellSx = {
    bgcolor: "transparent",
    borderBottom: "1px solid", borderColor: "divider",
    borderLeft: "none",
    cursor: "pointer", py: 1.25, px: 1.5,
    ".MuiTableRow-root:hover &": { bgcolor: rowHover },
    "&:not(:first-of-type)": { borderLeft: "none" },
  };
  const numCellSx = { ...cellSx, textAlign: "left" };

  /* Descriptive columns render their own aggregate summary in the
     group header. Earlier we colSpan'd them all under the label,
     which left Persona / Scenario / Ideal outcome / Conversation
     branch reading blank on collapse. Now each column carries a
     small aggregate specific to its data. */
  const descColumns = [
    { key: "callDetails" },
    { key: "persona" },
    { key: "scenario" },
    { key: "expected" },
    { key: "branch" },
  ].filter((c) => show(c.key));

  const a = group.agg || {};

  const uniqueBy = (arr, fn) => new Set(arr.map(fn).filter(Boolean)).size;
  const personaCount = uniqueBy(group.rows, (t) => t.persona?.name);
  const branchCount = uniqueBy(group.rows, (t) => (t.conversationBranch || []).join(">"));
  const passedCount = group.passed || 0;
  const failedCount = (group.measured || 0) - passedCount;

  const descSummary = (key) => {
    if (key === "callDetails") return null; /* Run details cell hosts the group label itself, not a summary. */
    if (key === "persona") return personaCount ? `${personaCount} persona${personaCount === 1 ? "" : "s"}` : "—";
    if (key === "scenario") return `${group.count} scenario${group.count === 1 ? "" : "s"}`;
    if (key === "expected") return "—";
    if (key === "branch") return branchCount ? `${branchCount} branch${branchCount === 1 ? "" : "es"}` : "—";
    return "—";
  };
  /* Aggregate cell — right-aligned tabular numbers with the same
     bad-value warning treatment data rows use. Group averages that
     cross the threshold (CSAT ≤4, Turns ≥12, Latency ≥550ms) turn
     red with a warning glyph so an unhealthy use case reads even
     before the group is expanded. Tokens is a group total, not an
     average, so no threshold applies. */
  const numCell = (value, suffix = "", metric) => {
    const bad = metric ? isBad(metric, typeof value === "number" ? value : Number(value)) : false;
    return (
      <TableCell sx={numCellSx}>
        {value == null ? (
          <Typography sx={{ typography: "s3", color: "text.disabled" }}>—</Typography>
        ) : (
          <Stack direction="row" alignItems="center" justifyContent="flex-start" spacing={0.5}>
            {bad && (
              <Iconify icon="solar:danger-triangle-bold" width={13} sx={{ color: "#DC2626", flexShrink: 0 }} />
            )}
            <Typography sx={{
              typography: "s2", fontWeight: 700,
              fontVariantNumeric: "tabular-nums",
              color: bad ? "#DC2626" : "text.primary",
            }}>
              {typeof value === "number" ? value.toLocaleString() : value}{suffix}
            </Typography>
          </Stack>
        )}
      </TableCell>
    );
  };
  /* Pass-rate colouring for the per-eval group column — green ≥80,
     amber ≥50, red below. Reads as a heatmap at group level. */
  const evalRateColor = (v) => (v >= 80 ? "#16A34A" : v >= 50 ? "#CA8A04" : "#DC2626");

  return (
    <TableRow onClick={onToggle}>
      {/*
        Group checkbox — mirrors the header select-all behaviour but
        scoped to the tasks in this group. Padding/width match the
        data-row and select-all cells exactly (width 48, pl: 1.25,
        vertical-align: middle) so the checkboxes stack cleanly down
        one column edge instead of drifting off-axis.
      */}
      <TableCell
        sx={{
          width: 48, pl: 1.25, pr: 0, py: 0,
          verticalAlign: "middle",
          borderBottom: "1px solid", borderColor: "divider",
          cursor: "pointer",
          ".MuiTableRow-root:hover &": { bgcolor: rowHover },
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {(() => {
          const ids = group.rows.map((r) => r.id);
          const allOn = ids.length > 0 && ids.every((id) => selected?.has(id));
          const someOn = ids.some((id) => selected?.has(id)) && !allOn;
          return (
            <Checkbox
              size="small"
              checked={allOn}
              indeterminate={someOn}
              onChange={() => onToggleGroup?.(group.rows)}
              sx={neutralCheckboxSx}
            />
          );
        })()}
      </TableCell>
      {descColumns.length === 0 ? (
        <TableCell sx={{ ...cellSx, pl: 2, overflow: "hidden" }}>
          <Stack direction="row" alignItems="center" spacing={1.25}>
            <Iconify
              icon={collapsed ? "solar:alt-arrow-right-linear" : "solar:alt-arrow-down-linear"}
              width={13}
              sx={{ color: "text.subtitle", flexShrink: 0 }}
            />
            <Typography noWrap sx={{ typography: "s2", fontWeight: 700, color: "text.primary" }}>
              {group.label}
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.subtitle", whiteSpace: "nowrap" }}>
              · {group.count} task{group.count === 1 ? "" : "s"}
            </Typography>
          </Stack>
        </TableCell>
      ) : descColumns.map((c, i) => (
        <TableCell key={c.key} sx={{ ...cellSx, pl: i === 0 ? 2 : 1.5, overflow: "hidden" }}>
          {i === 0 ? (
            /* First visible descriptive column (Run details in the
               default order) carries the group's identity: chevron
               + label + count. */
            <Stack direction="row" alignItems="center" spacing={1.25} sx={{ minWidth: 0 }}>
              <Iconify
                icon={collapsed ? "solar:alt-arrow-right-linear" : "solar:alt-arrow-down-linear"}
                width={13}
                sx={{ color: "text.subtitle", flexShrink: 0 }}
              />
              <Typography noWrap sx={{ typography: "s2", fontWeight: 700, color: "text.primary" }}>
                {group.label}
              </Typography>
              <Typography sx={{ typography: "s3", color: "text.subtitle", whiteSpace: "nowrap" }}>
                · {group.count} task{group.count === 1 ? "" : "s"}
              </Typography>
            </Stack>
          ) : (
            <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
              {descSummary(c.key)}
            </Typography>
          )}
        </TableCell>
      ))}
      {show("csat")    && numCell(a.csat, "", "csat")}
      {show("turns")   && numCell(a.turns, "", "turns")}
      {show("latency") && numCell(a.latency, "ms", "latency")}
      {show("tokens")  && numCell(a.tokens)}
      {/* `evals` here is the caller's shownEvals — GroupHeaderRow lives in
         its own scope and doesn't see the outer shownEvals binding. */}
      {evals.map((e) => {
        const ea = a.evals?.[e.id];
        if (!ea || !ea.total) {
          return (
            <TableCell key={`eval-${e.id}`} sx={numCellSx}>
              <Typography sx={{ typography: "s3", color: "text.disabled" }}>—</Typography>
            </TableCell>
          );
        }
        /* Mean SCORE across the group's tasks — the same quantity the data-row
           Score cells show (they render `result.score`), so the tint truly is
           one heatmap band down the column. Tinting the header by pass-rate
           instead put a green 100% header above amber 65% cells whenever a
           group's tasks all passed but scored middlingly. */
        const meanScore = ea.scoreSum / ea.total;
        const rate = Math.round(meanScore * 100);
        const bg = interpolateColorBasedOnScore(meanScore, 1);
        return (
          <TableCell
            key={`eval-${e.id}`}
            sx={{ ...numCellSx, p: 0, position: "relative" }}
          >
            <Box sx={{
              position: "absolute", inset: 0,
              display: "flex", alignItems: "center",
              px: 2, py: 1.5, bgcolor: bg, color: "text.primary",
            }}>
              <Typography sx={{
                typography: "s2", fontWeight: 600,
                fontVariantNumeric: "tabular-nums",
              }}>
                {rate}%
              </Typography>
            </Box>
          </TableCell>
        );
      })}
    </TableRow>
  );
}
GroupHeaderRow.propTypes = {
  group: PropTypes.object,
  collapsed: PropTypes.bool,
  onToggle: PropTypes.func,
  show: PropTypes.func,
  showEvals: PropTypes.bool,
  evals: PropTypes.array,
  selected: PropTypes.object,
  onToggleGroup: PropTypes.func,
};

/* Eval column header — the name plus a per-column actions menu scoped to this
   column only: re-run just this eval (re-score its cells, no full simulation)
   or delete the whole column. The trigger appears on hover so the header stays
   clean. */
function EvalHeadCell({ name, partial, onEdit, onRerun, onDelete, rescoring }) {
  const [anchor, setAnchor] = useState(null);
  const hasActions = !!(onEdit || onRerun || onDelete);
  return (
    <Stack
      direction="row" alignItems="center" spacing={0.5}
      sx={{ "&:hover .eval-col-actions": { opacity: 1 } }}
    >
      <Typography noWrap sx={{ typography: "s2", fontWeight: 500, color: "text.secondary", minWidth: 0 }}>{name}</Typography>
      {partial > 0 && (
        <Tooltip arrow title={`Scored on ${partial} scenario${partial === 1 ? "" : "s"} you re-ran this eval on. The other rows show "—".`}>
          <Box
            sx={{
              display: "inline-flex", alignItems: "center",
              px: 0.5, height: 15, borderRadius: 0.5, flexShrink: 0,
              border: (t) => `1px solid ${alpha("#7857FC", 0.4)}`,
              color: "#7857FC",
              bgcolor: (t) => alpha("#7857FC", t.palette.mode === "dark" ? 0.14 : 0.08),
            }}
          >
            <Typography sx={{ typography: "s3", fontWeight: 700, letterSpacing: 0.3 }}>
              {partial}
            </Typography>
          </Box>
        </Tooltip>
      )}
      {rescoring && (
        <Iconify icon="solar:refresh-linear" width={12} sx={{ color: "text.subtitle", flexShrink: 0, animation: "tt-spin 0.8s linear infinite", "@keyframes tt-spin": { to: { transform: "rotate(360deg)" } } }} />
      )}
      {hasActions && (
        <>
          <IconButton
            size="small"
            className="eval-col-actions"
            onClick={(e) => { e.stopPropagation(); setAnchor(e.currentTarget); }}
            sx={{ p: 0.25, ml: "auto", opacity: anchor ? 1 : 0, transition: "opacity 120ms", flexShrink: 0 }}
          >
            <Iconify icon="solar:menu-dots-bold" width={14} sx={{ color: "text.subtitle" }} />
          </IconButton>
          <Menu
            anchorEl={anchor}
            open={!!anchor}
            onClose={() => setAnchor(null)}
            anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
            transformOrigin={{ vertical: "top", horizontal: "right" }}
            slotProps={{ paper: { sx: { minWidth: 190 } } }}
          >
            {/* Opens the same drawer the Evaluations tab uses, so mapping and
                config edits land in one place — and a saved change is picked up
                wherever this eval is applied. */}
            {onEdit && (
              <MenuItem onClick={() => { setAnchor(null); onEdit(); }} sx={{ typography: "s2", gap: 1 }}>
                <Iconify icon="solar:pen-linear" width={16} sx={{ flexShrink: 0 }} />
                <Box component="span" sx={{ typography: "s2" }}>Edit eval</Box>
              </MenuItem>
            )}
            {onRerun && (
              <MenuItem onClick={() => { setAnchor(null); onRerun(); }} sx={{ typography: "s2", gap: 1 }}>
                <Iconify icon="solar:refresh-linear" width={16} sx={{ flexShrink: 0 }} />
                <Box component="span" sx={{ typography: "s2" }}>Re-run this eval</Box>
              </MenuItem>
            )}
            {onDelete && (
              <MenuItem onClick={() => { setAnchor(null); onDelete(); }} sx={{ typography: "s2", gap: 1, color: "#DC2626" }}>
                <Iconify icon="solar:trash-bin-trash-linear" width={16} sx={{ flexShrink: 0, color: "#DC2626" }} />
                <Box component="span" sx={{ typography: "s2", color: "#DC2626" }}>Delete column</Box>
              </MenuItem>
            )}
          </Menu>
        </>
      )}
    </Stack>
  );
}
EvalHeadCell.propTypes = {
  name: PropTypes.string, partial: PropTypes.number,
  onEdit: PropTypes.func, onRerun: PropTypes.func, onDelete: PropTypes.func, rescoring: PropTypes.bool,
};

/*
  Use-case label for a task, matching the ScenariosStep grouping so
  a run's traces slice into the same buckets the env creation screen
  showed. Prefers the scenario's `useCase` sentence (populated by the
  scenario mock — "Look up an existing booking by reference or email");
  falls back to parsing title / id in the same way ScenariosStep does
  for legacy tasks that predate the field.
*/
const humanize = (s = "") => s
  .replace(/[_-]/g, " ")
  .replace(/\b\w/g, (c) => c.toUpperCase())
  .trim();

/* The useful half of a task id is the trailing variant — everything after the
   last numeric segment ("…-core-0-routine" → "Routine", "…-0-off-topic" →
   "Off Topic"). That's the one thing that differs row-to-row inside a group,
   so it becomes the run's title instead of the repeated raw id. */
function variantLabel(id) {
  const parts = String(id || "").split("-").filter(Boolean);
  let lastNum = -1;
  parts.forEach((p, i) => { if (/^\d+$/.test(p)) lastNum = i; });
  const tail = lastNum >= 0 ? parts.slice(lastNum + 1) : [];
  if (tail.length) return humanize(tail.join(" "));
  /* No trailing variant — fall back to the test-kind token before the number. */
  if (lastNum > 0) return humanize(parts[lastNum - 1]);
  return humanize(parts.slice(-1)[0] || "Run");
}

/* Outcome as colour + label, so a failure is scannable at the row level
   instead of reading identical "Completed" text down the whole column. */
function runOutcome(status) {
  switch (status) {
    case "passed": return { label: "Passed", color: "#16A34A" };
    case "failed": return { label: "Failed", color: "#DC2626" };
    case "flaky": return { label: "Mixed", color: "#CA8A04" };
    case "error": return { label: "Errored", color: "#DC2626" };
    default: return { label: "Not measured", color: "#94A3B8" };
  }
}

export function deriveUseCaseLabel(t) {
  if (t?.useCase) return t.useCase;
  const id = String(t?.id || "");
  const title = String(t?.title || "");
  if (id.includes("-core-")) {
    const m = title.match(/(?:using|needing|landing on)\s+([\w_-]+)/i);
    if (m) return `Complete a call that requires ${humanize(m[1])}`;
    return "Tool use";
  }
  if (id.includes("-rule-")) {
    const short = title.split(/[.:]/)[0].slice(0, 60).trim();
    return short || "Rule enforcement";
  }
  if (id.includes("-trap-")) {
    const table = title.split(":")[0].trim();
    if (table) return `${humanize(table)} data`;
    return "Data traps";
  }
  if (id.includes("-adversarial-")) return title || "Adversarial pressure";
  if (id.includes("-edge-")) return title || "Edge cases";
  return "Other";
}

/*
  Numeric metric cell with subtle severity tinting. A plain reader scans
  every row and only red or amber digits jump out — the layout, weight
  and glyphs stay identical to the neutral case so nothing "shifts" as
  you scroll. Dot sits before the number for bad values only, so red
  reads as a genuine flag and warn stays softly ambient.
*/
function MetricValue({ metric, value, suffix = "" }) {
  const bad = isBad(metric, typeof value === "number" ? value : Number(value));
  return (
    <Stack direction="row" alignItems="center" justifyContent="flex-start" spacing={0.625}>
      {bad && (
        <Iconify icon="solar:danger-triangle-bold" width={13} sx={{ color: "#DC2626", flexShrink: 0 }} />
      )}
      <Typography
        component="span"
        sx={{
          typography: "s2",
          fontVariantNumeric: "tabular-nums",
          color: bad ? "#DC2626" : "text.secondary",
          fontWeight: bad ? 600 : 400,
        }}
      >
        {typeof value === "number" ? value.toLocaleString() : value}{suffix}
      </Typography>
    </Stack>
  );
}
MetricValue.propTypes = { metric: PropTypes.string, value: PropTypes.any, suffix: PropTypes.string };

/**
 * Sub-goals list for the Run details cell — matches prod exactly.
 *
 * Every sub-goal named as it is (snake_case kept), in the scenario's
 * order, with a small tick or cross on the right:
 *
 *   maintains_warm_confident_tone      ✓
 *   maintains_spoken_turn_length       ✗
 *   uses_approved_filler_phrase        ✓
 *   rephrases_with_conversational_...  ✓
 */
/**
 * Sub-goals list for the Run details cell — peek + expand.
 *
 * Default state:
 *   - Every failed sub-goal named with a red cross (always).
 *   - Passed sub-goals fill the remaining slots up to `CAP` total lines,
 *     in scenario order.
 *   - If anything else is left out, a "Show all N" link expands the full
 *     list in place. Nothing is truly hidden — everything is one click away
 *     without leaving the row.
 *
 * For scenarios with N ≤ CAP: no link, every sub-goal shown as-is.
 * For scenarios with N > CAP: failed rows always visible, the rest one
 * click away.
 *
 * All-pass rows show every sub-goal up to CAP with a "Show all" for the
 * rest — matches prod for the common small-N case.
 */
const CAP = 6;

function SubGoalsSummary({ task, env }) {
  const [expanded, setExpanded] = useState(false);
  const rows = subGoalStatuses(task, env);
  if (rows.length === 0) return null;

  /* When collapsed and there are more than CAP, keep every failed and
     not-reached one, then fill with passed in scenario order. Preserves
     each row's original position so the eye can still trace "sub-goal 3
     failed" against the checklist. */
  let visible;
  if (expanded || rows.length <= CAP) {
    visible = rows;
  } else {
    const priorityIdx = new Set();
    rows.forEach((r, i) => { if (r.status !== "passed") priorityIdx.add(i); });
    let slots = Math.max(0, CAP - priorityIdx.size);
    rows.forEach((r, i) => {
      if (slots > 0 && r.status === "passed" && !priorityIdx.has(i)) {
        priorityIdx.add(i);
        slots -= 1;
      }
    });
    visible = rows.filter((_, i) => priorityIdx.has(i));
  }
  const hidden = rows.length - visible.length;

  return (
    <Stack spacing={0.125} sx={{ mt: 0.75 }}>
      {visible.map((r, i) => (
        <Stack key={r.id || i} direction="row" alignItems="center" spacing={0.5}>
          <Typography noWrap sx={{ typography: "s3", color: "text.secondary" }}>
            {r.label}
          </Typography>
          {r.status === "passed" ? (
            <Iconify icon="mdi:check" width={12} sx={{ color: "#16A34A", flexShrink: 0 }} />
          ) : (
            <Iconify icon="mdi:close" width={12} sx={{ color: "#DC2626", flexShrink: 0 }} />
          )}
        </Stack>
      ))}
      {(hidden > 0 || expanded) && rows.length > CAP && (
        <Stack
          role="button"
          tabIndex={0}
          direction="row"
          alignItems="center"
          spacing={0.25}
          onClick={(e) => { e.stopPropagation(); setExpanded((o) => !o); }}
          sx={{
            mt: 0.5,
            alignSelf: "flex-start",
            cursor: "pointer",
            color: "primary.main",
            "&:hover .subgoal-toggle-label": { textDecoration: "underline" },
          }}
        >
          <Typography
            className="subgoal-toggle-label"
            sx={{ typography: "s3", color: "inherit", fontWeight: "fontWeightMedium" }}
          >
            {expanded ? "Show less" : `Show all ${rows.length}`}
          </Typography>
          <Iconify
            icon={expanded ? "eva:chevron-up-fill" : "eva:chevron-down-fill"}
            width={14}
            sx={{ color: "inherit", flexShrink: 0 }}
          />
        </Stack>
      )}
    </Stack>
  );
}
SubGoalsSummary.propTypes = { task: PropTypes.object, env: PropTypes.object };

function Field({ icon, label, value }) {
  if (value == null || value === "") return null;
  return (
    <Stack
      direction="row" alignItems="center" spacing={0.75}
      sx={{ px: 1, py: 0.5, borderRadius: 0.75, bgcolor: "background.neutral" }}
    >
      <Iconify icon={icon} width={13} sx={{ color: "text.subtitle", flexShrink: 0 }} />
      <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>{label}:</Typography>
      <Typography noWrap sx={{ typography: "s3", color: "text.primary", fontWeight: 500 }}>{value}</Typography>
    </Stack>
  );
}
Field.propTypes = { icon: PropTypes.string, label: PropTypes.string, value: PropTypes.any };

function Score({ result }) {
  const bgcolor = interpolateColorBasedOnScore(result.score, 1);
  const failed = result.passed === false;
  /* Any eval short of 100% carries error localization. */
  const localized = (result.score ?? 0) < 1;
  return (
    <Tooltip
      arrow
      title={(
        <Box>
          {result.reason && <Box>{result.reason}</Box>}
          <Box sx={{ mt: result.reason ? 0.75 : 0, fontWeight: 600 }}>
            {failed
              ? "Click to see where it failed"
              : localized ? "Click to see where it lost points" : "Click to see the explanation"}
          </Box>
        </Box>
      )}
    >
      <Box
        sx={{
          position: "absolute", inset: 0,
          display: "flex", alignItems: "center", gap: 0.75,
          px: 2, py: 1.5, bgcolor, color: "text.primary", cursor: "pointer",
          "&:hover .el-target": { opacity: 1 },
        }}
      >
        <Typography sx={{ typography: "s2", fontWeight: 600, fontVariantNumeric: "tabular-nums" }}>
          {Math.round(result.score * 100)}%
        </Typography>
        {localized && (
          <Iconify
            className="el-target"
            icon="solar:target-linear"
            width={13}
            sx={{ opacity: 0.55, transition: "opacity 120ms", color: "text.primary" }}
          />
        )}
      </Box>
    </Tooltip>
  );
}
Score.propTypes = { result: PropTypes.object };
