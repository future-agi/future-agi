import PropTypes from "prop-types";
import React, { useMemo, useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, Table, TableBody, TableCell, TableHead, TableRow,
  Checkbox, Tooltip, Button, Menu, MenuItem, ListItemIcon, ListItemText, Divider,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { interpolateColorBasedOnScore } from "src/utils/utils";
import { subTasksFor } from "../_mock/contract";
import { Verdict } from "../components/primitives";

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
const csatOf = (t) => Math.max(1, Math.round((t.evalResults?.[0]?.score ?? 0.5) * 10) - 4);
const latencyOf = (t) => 280 + (hash(t.id) % 320);

/*
  Only truly bad values get called out — a single-tier red signal reads
  louder than a red/amber pair and keeps the table quiet enough that
  when something does turn red, the eye lands on it. Thresholds are
  pitched at the top ~10-15% of the distribution for each metric so
  the highlight stays rare and meaningful.
*/
const METRIC_THRESHOLDS = {
  /* Only genuinely low satisfaction is an alarm. CSAT runs ~1-6 here, so a
     4 is a good result — flagging it red next to a "Passed" verdict read as a
     contradiction. Reserve the red triangle for 1-2. */
  csat:    { direction: "lowIsBad",  bad: 2 },
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
  { key: "persona",     label: "Persona",              defaultOn: false, width: 210, group: "Scenario details" },
  { key: "scenario",    label: "Scenario",             defaultOn: false, width: 300, group: "Scenario details" },
  { key: "expected",    label: "Ideal outcome",        defaultOn: false, width: 300, group: "Scenario details" },
  { key: "branch",      label: "Conversation branch", defaultOn: false, width: 260, group: "Scenario details" },
  { key: "callDetails", label: "Run details",          defaultOn: true,  width: 150, group: "Run details" },
  { key: "csat",        label: "CSAT",                 defaultOn: true,  width: 84,  group: "System metrics" },
  { key: "turns",       label: "Turns",                defaultOn: true,  width: 92,  group: "System metrics" },
  { key: "latency",     label: "Latency",              defaultOn: true,  width: 96,  group: "System metrics" },
  { key: "tokens",      label: "Tokens",               defaultOn: true,  width: 96,  group: "System metrics" },
  { key: "evals",       label: "Evaluations",         defaultOn: true,               group: "Evaluations" },
];

export const defaultTraceColumns = () =>
  new Set(TRACE_COLUMNS.filter((c) => c.defaultOn).map((c) => c.key));

/*
  Column visibility picker. Same outlined-button shape as the group-by
  picker so the two sit side-by-side without visual noise. Essential
  columns render as disabled ticks so the reader understands why they
  can't be turned off.
*/
export function TraceColumnsPicker({ value, onChange }) {
  const [anchor, setAnchor] = useState(null);
  const shownCount = TRACE_COLUMNS.filter((c) => value.has(c.key)).length;
  const toggle = (key) => {
    const next = new Set(value);
    if (next.has(key)) next.delete(key); else next.add(key);
    onChange(next);
  };
  /* Bucket into sections in declaration order so the menu reads
     scenario → run → system → evals, top-to-bottom. */
  const sections = TRACE_COLUMNS.reduce((acc, c) => {
    const last = acc[acc.length - 1];
    if (last && last.name === c.group) last.items.push(c);
    else acc.push({ name: c.group, items: [c] });
    return acc;
  }, []);
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
        <Box component="span" sx={{ color: "text.subtitle" }}>{shownCount}/{TRACE_COLUMNS.length}</Box>
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
          ...section.items.map((c) => (
            <MenuItem key={c.key} onClick={() => toggle(c.key)} sx={{ py: 0.5 }}>
              <ListItemIcon sx={{ minWidth: 32 }}>
                <Checkbox size="small" checked={value.has(c.key)} sx={{ p: 0 }} />
              </ListItemIcon>
              <ListItemText primary={c.label} primaryTypographyProps={{ typography: "s2" }} />
            </MenuItem>
          )),
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
};

export const GROUPINGS = [
  { id: "useCase", label: "Goal",              icon: "solar:target-linear" },
  { id: "subGoal", label: "Failure sub-goal", icon: "solar:map-linear" },
  { id: "pattern", label: "Failure pattern",   icon: "solar:danger-triangle-linear" },
  { id: "status",  label: "Status",            icon: "solar:check-circle-linear" },
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
  return "All";
};

const GROUP_SORT_ORDER = {
  pattern: ["Critical rule broken", "Said, not done", "Evaluation failed", "Errored", "Other failure"],
  status:  ["Failed", "Errored", "Not measured", "Passed"],
};

export default function TraceTable({
  tasks, evals, selected, onToggle, onToggleAll, onOpen,
  groupBy = "useCase", columns, env,
}) {
  const [collapsed, setCollapsed] = useState(() => new Set());
  /* If the parent didn't pass column state, fall back to defaults so the
     table is usable in older callers that don't wire a picker. */
  const visible = columns || defaultTraceColumns();
  const show = (key) => visible.has(key);
  const showEvals = show("evals");

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
      return {
        label,
        rows,
        count: rows.length,
        pct: measured.length ? Math.round((passed / measured.length) * 100) : null,
        measured: measured.length,
        passed,
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

  /* Row separators only — the vertical grid lines that used to border every
     cell made a dense results table read as a spreadsheet from a decade ago. */
  const headCell = {
    typography: "s2", fontWeight: 500, color: "text.secondary",
    whiteSpace: "nowrap", bgcolor: "background.paper", height: 44, py: 0,
    borderBottom: "1px solid", borderColor: "divider",
  };
  const num = {
    verticalAlign: "top", py: 1.5,
    typography: "s2", color: "text.secondary", fontVariantNumeric: "tabular-nums",
    borderBottom: "1px solid", borderColor: "divider",
  };
  const bodyCell = {
    verticalAlign: "top", py: 1.5,
    borderBottom: "1px solid", borderColor: "divider",
  };
  const checkCell = {
    width: 48, p: 0, pl: 1.25, verticalAlign: "middle",
    borderBottom: "1px solid", borderColor: "divider",
  };

  /*
    Column span for the group header row. Only counts columns actually
    rendered — checkbox + every visible data column + one cell per
    visible eval when evals are shown. Wrong here and the sticky
    left/right cells stop lining up under horizontal scroll.
  */
  const dataColKeys = ["persona", "scenario", "expected", "branch", "callDetails", "csat", "turns", "latency", "tokens"];
  const visibleDataCols = dataColKeys.filter((k) => show(k)).length;
  const totalColSpan = 1 + visibleDataCols + (showEvals ? evals.length : 0);

  const toggleCollapsed = (label) => setCollapsed((prev) => {
    const next = new Set(prev);
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
        />
      </TableCell>

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
              <Tooltip arrow title="Critical — a failure here is a release blocker">
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

      {show("callDetails") && (
        <TableCell sx={bodyCell} onClick={() => onOpen(t)}>
          {/* Lead with the verdict, not the lifecycle status: a reader
              scanning results wants "did it pass" first, and every passed
              row used to read a grey "Completed" that buried the answer.
              Then the specific scenario name (not the goal template, which
              repeats down the group), its human summary, then duration. */}
          <Verdict status={t.status} passes={t.passes} repeats={t.repeats} />
          <Typography noWrap sx={{ typography: "s3", color: "text.secondary", fontWeight: 600, mt: 0.375 }}>
            {t.name || t.title || t.id}
          </Typography>
          {(t.summary || t.title) && (
            <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
              {t.summary || t.title}
            </Typography>
          )}
          <Typography noWrap sx={{ typography: "s3", color: "text.disabled" }}>
            {((t.durationMs || 0) / 1000).toFixed(1)}s
          </Typography>
        </TableCell>
      )}

      {show("csat") && (
        <TableCell align="right" sx={num} onClick={() => onOpen(t)}>
          <MetricValue metric="csat" value={csatOf(t)} />
        </TableCell>
      )}
      {show("turns") && (
        <TableCell align="right" sx={num} onClick={() => onOpen(t)}>
          <MetricValue metric="turns" value={t.steps?.length || 0} />
        </TableCell>
      )}
      {show("latency") && (
        <TableCell align="right" sx={num} onClick={() => onOpen(t)}>
          <MetricValue metric="latency" value={latencyOf(t)} suffix="ms" />
        </TableCell>
      )}
      {show("tokens") && (
        <TableCell align="right" sx={num} onClick={() => onOpen(t)}>
          <MetricValue metric="tokens" value={t.tokens || 0} />
        </TableCell>
      )}

      {showEvals && evals.map((e) => {
        const r = t.evalResults?.find((x) => x.id === e.id);
        /* A task with no verdict (infra never let it be judged) must not show a
           grader score — a "Not measured" row that reads "Passed 81" is the
           contradiction reviewers notice first. */
        const measured = t.status !== "unmeasured" && t.status !== "error";
        return (
          <TableCell key={e.id} sx={{ ...bodyCell, p: 0, position: "relative" }} onClick={() => onOpen(t)}>
            {r && measured ? <Score result={r} /> : <Box sx={{ p: 2, typography: "s2", color: "text.disabled" }}>—</Box>}
          </TableCell>
        );
      })}
    </TableRow>
  );

  return (
    <Box>
      <Box sx={{ overflowX: "auto" }}>
        <Table size="small" sx={{ minWidth: 1720, tableLayout: "fixed" }}>
          <TableHead>
            <TableRow>
              <TableCell sx={{ ...headCell, ...checkCell }}>
                <Checkbox
                  size="small"
                  checked={allOn}
                  indeterminate={someOn}
                  onChange={onToggleAll}
                />
              </TableCell>
              {show("persona") && <TableCell sx={{ ...headCell, width: 210 }}>Persona</TableCell>}
              {show("scenario") && <TableCell sx={{ ...headCell, width: 300 }}>Scenario</TableCell>}
              {show("expected") && <TableCell sx={{ ...headCell, width: 300 }}>Ideal outcome</TableCell>}
              {show("branch") && <TableCell sx={{ ...headCell, width: 260 }}>Conversation branch</TableCell>}
              {show("callDetails") && <TableCell sx={{ ...headCell, width: 150 }}>Run details</TableCell>}
              {show("csat") && <TableCell sx={{ ...headCell, width: 84 }} align="right">CSAT</TableCell>}
              {show("turns") && <TableCell sx={{ ...headCell, width: 92 }} align="right">Turns</TableCell>}
              {show("latency") && <TableCell sx={{ ...headCell, width: 96 }} align="right">Latency</TableCell>}
              {show("tokens") && <TableCell sx={{ ...headCell, width: 96 }} align="right">Tokens</TableCell>}
              {showEvals && evals.map((e) => (
                <TableCell key={e.id} sx={{ ...headCell, width: 150 }}>{e.name}</TableCell>
              ))}
            </TableRow>
          </TableHead>

          <TableBody>
            {groups.map((g) => (
              <React.Fragment key={g.label}>
                <GroupHeaderRow
                  group={g}
                  collapsed={collapsed.has(g.label)}
                  onToggle={() => toggleCollapsed(g.label)}
                  colspan={totalColSpan}
                />
                {!collapsed.has(g.label) && g.rows.map(renderRow)}
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
  groupBy: PropTypes.string,
  columns: PropTypes.instanceOf(Set),
  env: PropTypes.object,
};

/*
  Group header row — full-width sticky-feel bar with chevron, label,
  count, and a color-coded pass-rate pill. Clicking anywhere on the
  row collapses/expands the group.
*/
function GroupHeaderRow({ group, collapsed, onToggle, colspan }) {
  /* Only the label pins to the left edge; the rest of the row is a
     plain filler so the header reads as a single clean band. The
     right-side pass-rate pill was more visual noise than it was worth
     — the row-level status is already visible in the traces below. */
  const cellSx = {
    bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.05 : 0.03),
    borderBottom: "1px solid", borderColor: "divider",
    cursor: "pointer", py: 0.875, px: 1.5,
    "&:hover": {
      bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.08 : 0.05),
    },
  };
  return (
    <TableRow onClick={onToggle}>
      <TableCell sx={{ ...cellSx, position: "sticky", left: 0, zIndex: 1, width: 260 }}>
        <Stack direction="row" alignItems="center" spacing={1.25}>
          <Iconify
            icon={collapsed ? "solar:alt-arrow-right-linear" : "solar:alt-arrow-down-linear"}
            width={13}
            sx={{ color: "text.subtitle" }}
          />
          <Typography sx={{ typography: "s2", fontWeight: 700, color: "text.primary", whiteSpace: "nowrap" }}>
            {group.label}
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle", whiteSpace: "nowrap" }}>
            · {group.count} task{group.count === 1 ? "" : "s"}
          </Typography>
        </Stack>
      </TableCell>
      {colspan > 1 && <TableCell colSpan={colspan - 1} sx={cellSx} />}
    </TableRow>
  );
}
GroupHeaderRow.propTypes = {
  group: PropTypes.object, collapsed: PropTypes.bool, onToggle: PropTypes.func, colspan: PropTypes.number,
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
    <Stack direction="row" alignItems="center" justifyContent="flex-end" spacing={0.625}>
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
  return (
    <Tooltip arrow title={result.reason || ""}>
      <Box
        sx={{
          position: "absolute", inset: 0,
          px: 2, py: 1.5, bgcolor, color: "text.primary",
        }}
      >
        <Typography sx={{ typography: "s2", fontWeight: 500 }}>
          {result.passed ? "Passed" : "Failed"}
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.secondary", fontVariantNumeric: "tabular-nums" }}>
          {Math.round(result.score * 100)}
        </Typography>
      </Box>
    </Tooltip>
  );
}
Score.propTypes = { result: PropTypes.object };
