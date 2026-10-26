import PropTypes from "prop-types";
import React, { useEffect, useMemo, useState } from "react";
import {
  Box,
  Stack,
  Typography,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Checkbox,
  Tooltip,
  Button,
  ButtonBase,
  Menu,
  MenuItem,
  ListItemIcon,
  ListItemText,
  Divider,
  IconButton,
} from "@mui/material";
import { useNavigate, useParams } from "react-router-dom";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { interpolateColorBasedOnScore } from "src/utils/utils";
import { subTasksFor } from "../_mock/contract";
import {
  subGoalResultsOf,
  rollupLine,
  subGoalBasisOf,
  MARK_NOTE,
} from "../_mock/scenarioSubGoals";
import { neutralCheckboxSx } from "../components/primitives";
import {
  OUTCOMES,
  VERDICT_CAUSES,
  causeOf,
  outcomeOfCause,
} from "../_mock/decoupled";
import { STALE_LABEL, causeShort, verdictLineOf } from "../_mock/runCauses";
import {
  csatOf,
  latencyOf,
  TRACE_COLUMNS,
  HIDDEN_EVAL,
  visibleEvals,
  defaultTraceColumns,
  GROUPINGS,
  humanize,
  deriveUseCaseLabel,
} from "./traceTableHelpers";
import { clickable, focusRing } from "../components/clickable";
import { scenarioTitle } from "../_mock/scenarios";
import { packOf } from "../workspace/scenarios/redTeamPacks";
import { memberPathOf } from "../_mock/memberAttribution";

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
 *                       Grader failed / Error / Not measured
 *   - Status          → the coarser Passed / Failed / Not measured cut
 *
 * Each group header shows: chevron · label · task count · pass-rate
 * pill (green/amber/red). Clicking the header collapses that group.
 */
/* An unmeasured row's outcome — Inconclusive, Test invalid or Error. */
const notCountedLabel = (t) =>
  OUTCOMES[t?.verdictCause?.outcome || outcomeOfCause(causeOf(t))]?.label ||
  "Inconclusive";

/*
  Only truly bad values get called out — a single-tier red signal reads
  louder than a red/amber pair and keeps the table quiet enough that
  when something does turn red, the eye lands on it. Thresholds are
  pitched at the top ~10-15% of the distribution for each metric so
  the highlight stays rare and meaningful.
*/
const METRIC_THRESHOLDS = {
  csat: { direction: "lowIsBad", bad: 4 },
  turns: { direction: "highIsBad", bad: 12 },
  latency: { direction: "highIsBad", bad: 550 },
  tokens: { direction: "highIsBad", bad: 6000 },
};

const isBad = (metric, value) => {
  const spec = METRIC_THRESHOLDS[metric];
  if (!spec || value == null) return false;
  return spec.direction === "lowIsBad" ? value <= spec.bad : value >= spec.bad;
};

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
    if (next.has(key)) next.delete(key);
    else next.add(key);
    onChange(next);
  };
  const toggleEval = (id) => {
    const key = HIDDEN_EVAL(id);
    const next = new Set(value);
    if (next.has(key)) next.delete(key);
    else next.add(key);
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
        size="small"
        variant="outlined"
        onClick={(e) => setAnchor(e.currentTarget)}
        startIcon={
          <Iconify
            icon="solar:widget-4-linear"
            width={16}
            sx={{ color: "text.tertiary" }}
          />
        }
        endIcon={
          <Iconify
            icon="solar:alt-arrow-down-linear"
            width={12}
            sx={{ color: "text.tertiary" }}
          />
        }
        sx={{
          color: "text.primary",
          borderColor: "divider",
          "&:hover": { borderColor: "text.disabled", bgcolor: "transparent" },
        }}
      >
        Columns
        <Box
          component="span"
          sx={{ mx: 0.5, color: "text.tertiary", fontWeight: 400 }}
        >
          ·
        </Box>
        <Box component="span" sx={{ color: "text.tertiary" }}>
          {shownCount}/{totalCols}
        </Box>
      </Button>
      <Menu
        anchorEl={anchor}
        open={!!anchor}
        onClose={() => setAnchor(null)}
        PaperProps={{ sx: { minWidth: 240 } }}
      >
        {sections.map((section, i) => [
          i > 0 && <Divider key={`div-${section.name}`} sx={{ my: 0.5 }} />,
          <Typography
            key={`h-${section.name}`}
            sx={{
              typography: "s3",
              fontWeight: 600,
              color: "text.tertiary",
              textTransform: "none",
              letterSpacing: 0.4,
              px: 2,
              py: 0.5,
              mt: i === 0 ? 0.5 : 0,
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
                  <Checkbox
                    size="small"
                    checked={checked}
                    sx={{ p: 0, ...neutralCheckboxSx }}
                  />
                </ListItemIcon>
                <ListItemText
                  primary={c.label}
                  primaryTypographyProps={{ typography: "s2" }}
                />
              </MenuItem>
            );
          }),
        ])}
        <Divider sx={{ my: 0.5 }} />
        <MenuItem
          onClick={() => onChange(defaultTraceColumns())}
          sx={{ py: 0.5 }}
        >
          <ListItemIcon sx={{ minWidth: 32 }}>
            <Iconify icon="solar:restart-linear" width={16} />
          </ListItemIcon>
          <ListItemText
            primary="Reset to defaults"
            primaryTypographyProps={{ typography: "s2" }}
          />
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
  const idx = Math.min(
    subs.length - 1,
    Math.floor((failAt / stepCount) * subs.length),
  );
  return subs[idx].label;
};

/*
  Per-sub-goal outcome for the Run details cell — met, failed or
  inconclusive, each with its mark (critical / supporting) and the scenario's
  roll-up: one failed critical sub-goal fails it, an unsure critical one makes
  it inconclusive, supporting misses only lower the score.
*/
function subGoalStatuses(task, env) {
  return subGoalResultsOf(task, env);
}

/*
  Standalone group-by picker, so callers (the run header, a compare
  view) can hoist the control out of the table body and drop it wherever
  the layout wants it. The `value`/`onChange` shape lets the picker be
  fully controlled.
*/
export function TraceGroupByPicker({ value, onChange, multiAgent = false }) {
  const [anchor, setAnchor] = useState(null);
  const options = GROUPINGS.filter((g) => multiAgent || !g.multiAgent);
  const current = options.find((g) => g.id === value) || options[0];
  return (
    <>
      <Button
        size="small"
        variant="outlined"
        onClick={(e) => setAnchor(e.currentTarget)}
        startIcon={
          <Iconify
            icon={current.icon}
            width={16}
            sx={{ color: "primary.main" }}
          />
        }
        endIcon={
          <Iconify
            icon="solar:alt-arrow-down-linear"
            width={12}
            sx={{ color: "text.tertiary" }}
          />
        }
        sx={{
          color: "text.primary",
          borderColor: "divider",
          "&:hover": { borderColor: "text.disabled", bgcolor: "transparent" },
        }}
      >
        Group by
        <Box
          component="span"
          sx={{ mx: 0.5, color: "text.tertiary", fontWeight: 400 }}
        >
          ·
        </Box>
        <Box component="span" sx={{ color: "primary.main" }}>
          {current.label}
        </Box>
      </Button>
      <Menu anchorEl={anchor} open={!!anchor} onClose={() => setAnchor(null)}>
        {options.map((g) => (
          <MenuItem
            key={g.id}
            selected={g.id === value}
            onClick={() => {
              onChange(g.id);
              setAnchor(null);
            }}
            sx={{ py: 0.75, gap: 0.75 }}
          >
            <Iconify icon={g.icon} width={16} />
            <Box sx={{ minWidth: 0 }}>
              <Typography sx={{ typography: "s2" }}>{g.label}</Typography>
              {g.hint && (
                <Typography
                  sx={{
                    typography: "s3",
                    color: "text.tertiary",
                    maxWidth: 240,
                    whiteSpace: "normal",
                  }}
                >
                  {g.hint}
                </Typography>
              )}
            </Box>
          </MenuItem>
        ))}
      </Menu>
    </>
  );
}
TraceGroupByPicker.propTypes = {
  value: PropTypes.string.isRequired,
  onChange: PropTypes.func.isRequired,
  /* Offer the multi-agent groupings (Member). */
  multiAgent: PropTypes.bool,
};

/*
  Every task lands in exactly one bucket per grouping mode, or in null
  when it doesn't belong to any bucket for that mode. Pattern mode is a
  view of *failures* — passed and unmeasured tasks don't have a
  failure pattern, so they drop out of the grouped list entirely.
*/
const NO_MEMBERS = [];
const groupOfTask = (t, mode, env, members = NO_MEMBERS) => {
  if (mode === "member") {
    /* The member the conversation ended with — who held it when it passed
       or failed. */
    const path = memberPathOf(t, members);
    return path[path.length - 1]?.name || "Unknown member";
  }
  if (mode === "useCase") {
    return deriveUseCaseLabel(t);
  }
  if (mode === "subGoal") {
    return failSubGoalOf(t, env);
  }
  if (mode === "allSubGoals") {
    const labels = [
      ...new Set(
        subTasksFor(t, env)
          .map((s) => s.label)
          .filter(Boolean),
      ),
    ];
    return labels.length ? labels : ["No sub-goals"];
  }
  if (mode === "pattern") {
    if (t.status === "passed" || t.status === "unmeasured") return null;
    if (t.status === "error") return "Error";
    if (t.critical) return "Critical rule broken";
    if (t.callLog?.unsupportedClaim) return "Said, not done";
    if ((t.evalResults || []).some((r) => !r.passed))
      return "Evaluation failed";
    return "Other failure";
  }
  if (mode === "cause") {
    const v = t.verdictCause;
    if (!v)
      return t.status === "passed"
        ? "Passed"
        : t.status === "unmeasured"
          ? notCountedLabel(t)
          : "Failed";
    return v.label;
  }
  if (mode === "status") {
    if (t.status === "passed") return "Passed";
    if (t.status === "unmeasured") return notCountedLabel(t);
    if (t.status === "error") return "Error";
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
       of the run's rows were happy-path baselines. A red-team pack's
       scenarios group under the pack, so each pack gets its pass rate. */
    const pack = packOf(t);
    if (pack) return pack.label;
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
    return scenarioTitle(t.title) || t.name || t.id || "Untitled task";
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
  pattern: [
    "Critical rule broken",
    "Said, not done",
    "Evaluation failed",
    "Error",
    "Other failure",
  ],
  status: ["Failed", "Inconclusive", "Test invalid", "Error", "Passed"],
  cause: [
    VERDICT_CAUSES["fail-required"].label,
    VERDICT_CAUSES["fail-agent"].label,
    VERDICT_CAUSES["inc-evidence"].label,
    VERDICT_CAUSES["inc-simulator"].label,
    VERDICT_CAUSES["inc-setup"].label,
    VERDICT_CAUSES["inc-judge"].label,
    VERDICT_CAUSES["inc-infra"].label,
    VERDICT_CAUSES["inc-transport"].label,
    VERDICT_CAUSES.pass.label,
    "Pulled for review",
  ],
};

export default function TraceTable({
  tasks,
  evals,
  selected,
  onToggle,
  onToggleAll,
  onOpen,
  onOpenEval,
  /* Opens the scenario's trace waterfall — the voice drawer has no trace
     tab, so this is how a voice row reaches it. Shown on row hover. */
  groupBy = "useCase",
  columns,
  env,
  onRerunEval,
  onDeleteEval,
  onEditEval,
  rescoringEvalId,
  /* The toolbar's Expand all switch: `expandCommand` ({ open, at }) opens or
     closes every group; `onAllOpenChange` reports whether every group is open,
     so the switch shows the table's real state when groups are opened one by
     one. */
  expandCommand = null,
  onAllOpenChange,
  /* How the run graded its sub-goals (`gradingOf(run).basis`): sealed runs
     check end state and tool calls, live runs judge from the conversation.
     Optional — without it the sub-goal lines read as before. */
  gradingBasis = null,
  /* The agent's members, entry first (membersOf) — for Group by Member. */
  members = NO_MEMBERS,
}) {
  /* Groups start collapsed. Lazy-primed with the group list once it resolves. */
  const [collapsed, setCollapsed] = useState(null);
  /* If the parent didn't pass column state, fall back to defaults so the
     table is usable in older callers that don't wire a picker. */
  const visible = columns || defaultTraceColumns();
  const show = (key) => visible.has(key);
  /* Which evals to render, in order. Any not explicitly hidden shows. */
  const shownEvals = useMemo(
    () => visibleEvals(visible, evals),
    [visible, evals],
  );
  const showEvals = shownEvals.length > 0;

  const allOn = tasks.length > 0 && tasks.every((t) => selected.has(t.id));
  const someOn = tasks.some((t) => selected.has(t.id)) && !allOn;

  const groups = useMemo(() => {
    const byKey = new Map();
    tasks.forEach((t) => {
      const label = groupOfTask(t, groupBy, env, members);
      /* null = this task has no bucket in the current mode (e.g. a
         passed task under "Failure pattern"). Drop it entirely so the
         view stays a clean partition of what the mode is about. */
      if (label == null) return;
      /* A list axis puts the call in every group it belongs to. */
      (Array.isArray(label) ? label : [label]).forEach((one) => {
        if (!byKey.has(one)) byKey.set(one, []);
        byKey.get(one).push(t);
      });
    });
    const arr = Array.from(byKey, ([label, rows]) => {
      const measured = rows.filter((r) => r.status !== "unmeasured");
      const passed = measured.filter((r) => r.status === "passed").length;
      /* Group aggregates for the header row when collapsed.
         Averages for CSAT/Turns/Latency, total for Tokens; each
         eval column gets its own pass-rate. */
      const avg = (vals) =>
        vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
      const csatVals = rows.map(csatOf).filter((v) => Number.isFinite(v));
      const turnVals = rows.map((r) => r.steps?.length || 0);
      const latVals = rows.map(latencyOf).filter((v) => typeof v === "number");
      const tokTotal = rows.reduce((a, r) => a + (r.tokens || 0), 0);
      const evalAgg = {};
      /* A call that wasn't measured has no grade — leave it out of the
         group's eval averages, as its cell shows "–". */
      measured.forEach((r) => {
        (r.evalResults || []).forEach((er) => {
          if (!evalAgg[er.id])
            evalAgg[er.id] = { passed: 0, total: 0, scoreSum: 0 };
          evalAgg[er.id].total += 1;
          evalAgg[er.id].scoreSum += er.score ?? 0;
          if (er.passed) evalAgg[er.id].passed += 1;
        });
      });
      /* How the group's scenarios went and how many failing critical
         scenarios it holds (counted the way the summary counts them) — used
         to put the groups that need attention first. */
      const outcomes = { passed: 0, mixed: 0, failing: 0, unmeasured: 0 };
      rows.forEach((r) => {
        if (r.status === "passed") outcomes.passed += 1;
        else if (r.status === "flaky") outcomes.mixed += 1;
        else if (r.status === "unmeasured") outcomes.unmeasured += 1;
        else outcomes.failing += 1;
      });
      const blockers = rows.filter(
        (r) => r.critical && r.status === "failed",
      ).length;
      /* Most common failed sub-goal — said on member groups, where "who
         fails, and at what" is the question. */
      let topFailure = null;
      if (groupBy === "member") {
        const counts = new Map();
        rows
          .filter((r) => r.status === "failed" || r.status === "flaky")
          .forEach((r) => {
            const why = failSubGoalOf(r, env);
            if (why) counts.set(why, (counts.get(why) || 0) + 1);
          });
        const top = [...counts.entries()].sort((x, y) => y[1] - x[1])[0];
        if (top) topFailure = { label: top[0], count: top[1] };
      }
      return {
        label,
        topFailure,
        rows,
        outcomes,
        blockers,
        count: rows.length,
        pct: measured.length
          ? Math.round((passed / measured.length) * 100)
          : null,
        measured: measured.length,
        passed,
        agg: {
          csat:
            avg(csatVals) != null ? Math.round(avg(csatVals) * 10) / 10 : null,
          turns:
            avg(turnVals) != null ? Math.round(avg(turnVals) * 10) / 10 : null,
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
      /* Problems first: failing critical scenarios, then failures, then mixed
         results — so what needs attention is at the top — then bigger
         groups. Status and failure-pattern views keep their own order. */
      arr.sort(
        (a, b) =>
          b.blockers - a.blockers ||
          b.outcomes.failing - a.outcomes.failing ||
          b.outcomes.mixed - a.outcomes.mixed ||
          b.count - a.count,
      );
    }
    return arr;
  }, [tasks, groupBy, env, members]);

  /* Sticky at the top of the scroll area, without MUI's `stickyHeader`
     prop — that prop switches `borderCollapse` to `separate`, which makes
     the per-cell score tints on the eval columns bleed into one solid
     column-wide fill. Rolling our own sticky keeps `border-collapse: collapse`
     so each row's cell stays its own outlined rectangle. */
  const headCell = {
    typography: "s2",
    fontWeight: 500,
    color: "text.secondary",
    whiteSpace: "nowrap",
    bgcolor: "background.paper",
    height: 44,
    py: 0,
    position: "sticky",
    top: BAND_H,
    zIndex: 2,
    borderBottom: "1px solid",
    borderColor: "divider",
    "&:not(:first-of-type)": {
      borderLeft: "1px solid",
      borderColor: "divider",
    },
  };
  const num = {
    verticalAlign: "top",
    py: 1.5,
    typography: "s2",
    color: "text.secondary",
    fontVariantNumeric: "tabular-nums",
    borderBottom: "1px solid",
    borderColor: "divider",
    "&:not(:first-of-type)": {
      borderLeft: "1px solid",
      borderColor: "divider",
    },
  };
  const bodyCell = {
    verticalAlign: "top",
    py: 1.5,
    borderBottom: "1px solid",
    borderColor: "divider",
    "&:not(:first-of-type)": {
      borderLeft: "1px solid",
      borderColor: "divider",
    },
  };
  /* Pinned to the left of the scroll area so the checkboxes stay reachable
     when the table scrolls horizontally. On the header row this cell also
     needs a sticky top to pin at the corner — that's handled by combining
     `headCell` (which sticks under the section band) with `checkCell`. */
  const checkCell = {
    width: 48,
    p: 0,
    pl: 1.25,
    verticalAlign: "middle",
    borderBottom: "1px solid",
    borderColor: "divider",
    position: "sticky",
    left: 0,
    zIndex: 1,
    bgcolor: "background.paper",
    /* Opaque, so the row's hover lift is layered on rather than lost. */
    ".MuiTableRow-hover:hover &": {
      backgroundImage: (t) => {
        const c = t.palette.action.hover;
        return `linear-gradient(${c}, ${c})`;
      },
    },
  };

  /*
    Column span for the group header row. Only counts columns actually
    rendered — checkbox + every visible data column + one cell per
    visible eval when evals are shown. Wrong here and the sticky
    left/right cells stop lining up under horizontal scroll.
  */
  const dataColKeys = [
    "callDetails",
    "persona",
    "scenario",
    "expected",
    "branch",
    "csat",
    "turns",
    "latency",
    "tokens",
  ];
  const visibleDataCols = dataColKeys.filter((k) => show(k)).length;
  const totalColSpan = 1 + visibleDataCols + shownEvals.length;

  /* Lazy-prime with all group labels so groups start collapsed. */
  const collapsedSet = collapsed ?? new Set(groups.map((g) => g.label));
  const toggleCollapsed = (label) =>
    setCollapsed(() => {
      const next = new Set(collapsedSet);
      if (next.has(label)) next.delete(label);
      else next.add(label);
      return next;
    });

  const renderRow = (t) => (
    <TableRow
      key={t.id}
      hover
      /* Every cell opens the scenario by mouse; by keyboard the row is one
         stop and Enter opens it. */
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.target === e.currentTarget && e.key === "Enter") {
          e.preventDefault();
          onOpen(t);
        }
      }}
      sx={{
        ...focusRing,
        cursor: "pointer",
        /* MUI's default hover is too heavy against the dark card — a
           quarter of the usual action.hover reads as a soft lift
           without swamping the outcome-tinted eval cells. */
        "&.MuiTableRow-hover:hover": {
          bgcolor: "action.hover",
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

      {show("callDetails") &&
        (() => {
          const outcome = runOutcome(t.status, t);
          return (
            <TableCell sx={bodyCell} onClick={() => onOpen(t)}>
              <Box minWidth={0}>
                <Stack
                  direction="row"
                  alignItems="center"
                  spacing={0.75}
                  sx={{ mb: 0.125 }}
                >
                  <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>
                    {variantLabel(t.id)}
                  </Typography>
                  {t.critical &&
                    (() => {
                      /* Red only when this critical scenario failed — the same
                     rows the summary counts. Otherwise a quiet outline. It
                     informs; it blocks nothing. */
                      const blocking = t.status === "failed";
                      return (
                        <Tooltip
                          classes={{ tooltip: "ds-hovercard" }}
                          arrow
                          title={
                            <Stack
                              spacing={0.375}
                              sx={{ py: 0.25, maxWidth: 300 }}
                            >
                              <Typography
                                sx={{
                                  typography: "s3",
                                  fontWeight: 600,
                                  color: "inherit",
                                }}
                              >
                                {`Critical scenario — ${BLOCKER_STATE[t.status] || BLOCKER_STATE.unmeasured}`}
                              </Typography>
                              {t.blockerReason && (
                                <Typography
                                  sx={{
                                    typography: "s3",
                                    color: "inherit",
                                    opacity: 0.8,
                                  }}
                                >
                                  {t.blockerReason}
                                </Typography>
                              )}
                            </Stack>
                          }
                        >
                          <Box sx={{ display: "flex" }}>
                            <Iconify
                              icon={
                                blocking
                                  ? "solar:danger-triangle-bold"
                                  : "solar:danger-triangle-linear"
                              }
                              width={12}
                              sx={{
                                color: blocking
                                  ? "status.danger.fg"
                                  : "text.disabled",
                              }}
                            />
                          </Box>
                        </Tooltip>
                      );
                    })()}
                </Stack>
                {t.summary && (
                  <Typography
                    sx={{
                      typography: "s3",
                      color: "text.secondary",
                      mb: 0.375,
                      maxWidth: 340,
                    }}
                  >
                    {t.summary}
                  </Typography>
                )}
                <Stack direction="row" alignItems="center" spacing={0.75}>
                  <VerdictLabel task={t} outcome={outcome} />
                  <Box
                    sx={{
                      width: "3px",
                      height: "3px",
                      borderRadius: "50%",
                      bgcolor: "text.disabled",
                    }}
                  />
                  <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                    {`${((t.durationMs || 0) / 1000).toFixed(1)}s ${env?.surface === "voice" ? "call" : "conversation"}`}
                  </Typography>
                </Stack>
                <SubGoalsSummary task={t} env={env} basis={gradingBasis} />
              </Box>
            </TableCell>
          );
        })()}

      {show("persona") && (
        <TableCell sx={bodyCell} onClick={() => onOpen(t)}>
          {/* One line to scan by — who, how old, how they sound — with the
              full card (traits included) on hover. Four boxed lines per row
              made every row tall and the table hard to compare down. */}
          <Tooltip
            classes={{ tooltip: "ds-hovercard" }}
            arrow
            placement="right"
            componentsProps={{
              tooltip: {
                sx: {
                  maxWidth: 320,
                },
              },
            }}
            title={
              <Stack spacing={0.5}>
                <Field
                  icon="solar:user-id-linear"
                  label="Name"
                  value={t.persona?.name}
                />
                <Field
                  icon="solar:user-linear"
                  label="Voice"
                  value={t.persona?.voice}
                />
                <Field
                  icon="solar:users-group-rounded-linear"
                  label="Age"
                  value={t.persona?.age}
                />
                {t.persona?.traits?.length > 0 && (
                  <Field
                    icon="solar:tag-linear"
                    label="Traits"
                    value={t.persona.traits.join(", ")}
                  />
                )}
              </Stack>
            }
          >
            <Box sx={{ minWidth: 0 }}>
              <Typography noWrap sx={{ typography: "s2", fontWeight: 500 }}>
                {t.persona?.name || "—"}
              </Typography>
              <Typography
                noWrap
                sx={{ typography: "s3", color: "text.tertiary" }}
              >
                {[t.persona?.age, t.persona?.voice]
                  .filter(Boolean)
                  .join(" · ") || " "}
              </Typography>
            </Box>
          </Tooltip>
        </TableCell>
      )}

      {show("scenario") && (
        <TableCell sx={bodyCell} onClick={() => onOpen(t)}>
          <Box sx={TEXT_COL.scenario}>
            <Stack direction="row" alignItems="flex-start" spacing={0.75}>
              <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                {t.title}
              </Typography>
              {t.critical && (
                <Tooltip
                  arrow
                  title={`Critical scenario — ${(t.blockerReason || "marked critical: a failure here is worth reading first.").replace(/^./, (c) => c.toLowerCase())}`}
                >
                  <Box sx={{ display: "flex", mt: "2px" }}>
                    <Iconify
                      icon="solar:danger-triangle-bold"
                      width={12}
                      sx={{ color: "text.tertiary" }}
                    />
                  </Box>
                </Tooltip>
              )}
            </Stack>
            <Typography
              sx={{ typography: "s2", color: "text.secondary", mt: 0.5 }}
            >
              {t.task}
            </Typography>
          </Box>
        </TableCell>
      )}

      {show("expected") && (
        <TableCell
          sx={{ ...bodyCell, typography: "s2", color: "text.secondary" }}
          onClick={() => onOpen(t)}
        >
          <Box sx={TEXT_COL.expected}>{t.expected}</Box>
        </TableCell>
      )}

      {show("branch") && (
        <TableCell sx={bodyCell} onClick={() => onOpen(t)}>
          <Typography
            sx={{
              typography: "s3",
              color: "text.secondary",
              fontFamily: "ui-monospace, Menlo, monospace",
              lineHeight: 1.7,
              ...TEXT_COL.branch,
            }}
          >
            {branchOf(t)}
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
          {latencyOf(t) == null && t.status !== "unmeasured" ? (
            <Tooltip
              arrow
              title="The agent's side didn't report a response time — unverified, not counted as met."
            >
              <Typography
                component="span"
                sx={{ typography: "s3", color: "text.tertiary" }}
              >
                unverified
              </Typography>
            </Tooltip>
          ) : (
            <MetricValue metric="latency" value={latencyOf(t)} suffix="ms" />
          )}
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
                <Iconify
                  icon="solar:refresh-linear"
                  width={14}
                  sx={{
                    color: "text.disabled",
                    animation: "tt-spin 0.8s linear infinite",
                    "@keyframes tt-spin": {
                      to: { transform: "rotate(360deg)" },
                    },
                  }}
                />
              </Box>
            ) : r && t.status === "unmeasured" ? (
              /* Not measured means not graded — a score here would contradict
                 the row's own status. */
              <Tooltip arrow title={`Not graded — ${unmeasuredWhy(t)}`}>
                <Box
                  sx={{
                    p: 2,
                    typography: "s2",
                    color: "text.disabled",
                    cursor: "help",
                  }}
                >
                  —
                </Box>
              </Tooltip>
            ) : r ? (
              <Score result={r} />
            ) : /* Missing on an adhoc column means this row wasn't in that
                 eval's re-run. Say so on hover; on the original run's
                 columns "—" is just "no result" and doesn't need a note. */
            e.adhoc ? (
              <Tooltip
                arrow
                title={`${e.name} was run on the other scenarios you selected — not on this one. Select this row and re-run the eval to score it.`}
              >
                <Box
                  sx={{
                    p: 2,
                    typography: "s2",
                    color: "text.disabled",
                    cursor: "help",
                  }}
                >
                  —
                </Box>
              </Tooltip>
            ) : (
              <Box sx={{ p: 2, typography: "s2", color: "text.disabled" }}>
                —
              </Box>
            )}
          </TableCell>
        );
      })}
    </TableRow>
  );

  /* Expand-all / collapse-all toggle. Reads `collapsedSet.size` against
     the group count: if every group is collapsed the button offers
     "Expand all"; otherwise "Collapse all". */
  /* On an overlapping axis (Sub-goal) each header says so where its count
     is read — totals add up to more than the run. */
  const axis = GROUPINGS.find((g) => g.id === groupBy);
  const overlap = axis?.overlaps
    ? { hint: axis.hint, total: tasks.length }
    : null;
  const allOpen =
    groups.length > 0 && groups.every((g) => !collapsedSet.has(g.label));
  useEffect(() => {
    onAllOpenChange?.(allOpen);
  }, [allOpen, onAllOpenChange]);
  useEffect(() => {
    if (!expandCommand) return;
    setCollapsed(
      expandCommand.open ? new Set() : new Set(groups.map((g) => g.label)),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expandCommand]);

  /* The band above the column headers — one label over each run of columns
     that belong together, the same sections the Columns picker uses. */
  const band = [
    { label: "Run details", span: 1 + (show("callDetails") ? 1 : 0) },
    {
      label: "Scenario details",
      span: ["persona", "scenario", "expected", "branch"].filter(show).length,
    },
    {
      label: "System metrics",
      span: ["csat", "turns", "latency", "tokens"].filter(show).length,
    },
    { label: "Evaluations", span: shownEvals.length },
  ].filter((b) => b.span > 0);

  return (
    <Box
      sx={{ display: "flex", flexDirection: "column", flex: 1, minHeight: 0 }}
    >
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
        <Table
          size="small"
          sx={{
            minWidth: 1200,
            tableLayout: "auto",
            /* Each cell draws its own borders. Collapsed borders belong to the
               table, and the sticky header cells' backgrounds paint over them,
               dropping the header's bottom lines in places. Cells only draw a
               bottom and a left edge, so nothing doubles up. */
            borderCollapse: "separate",
            borderSpacing: 0,
            /* The theme blanks the borders of a table's last row; here that's
               the column header and the final scenario, which would lose
               their column dividers. */
            "& .MuiTableRow-root:last-of-type .MuiTableCell-root": {
              borderColor: "divider",
            },
          }}
        >
          <TableHead>
            <TableRow>
              {band.map((b, i) => (
                <TableCell
                  key={b.label}
                  colSpan={b.span}
                  sx={{
                    position: "sticky",
                    top: 0,
                    zIndex: 2,
                    height: BAND_H,
                    py: 0,
                    bgcolor: "background.paper",
                    whiteSpace: "nowrap",
                    typography: "s3",
                    fontWeight: 600,
                    color: "text.tertiary",
                    textTransform: "none",
                    letterSpacing: 0.4,
                    borderBottom: "1px solid",
                    borderColor: "divider",
                    ...(i > 0 && {
                      borderLeft: "1px solid",
                      borderLeftColor: "divider",
                    }),
                  }}
                >
                  {b.label}
                </TableCell>
              ))}
            </TableRow>
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
              {show("callDetails") && (
                <TableCell sx={headCell}>
                  {/* A floor the table can't shrink — a cell's own width is only a hint. */}
                  <Box sx={{ minWidth: RUN_DETAILS_MIN }}>Run details</Box>
                </TableCell>
              )}
              {/* Shrink to its content (the 1% trick) so it sits next to CSAT;
                  the spare width goes to Run details, not a gap. */}
              {show("persona") && (
                <TableCell sx={{ ...headCell, width: "1%" }}>Persona</TableCell>
              )}
              {show("scenario") && (
                <TableCell sx={{ ...headCell, width: 300 }}>Scenario</TableCell>
              )}
              {show("expected") && (
                <TableCell sx={{ ...headCell, width: 300 }}>
                  Ideal outcome
                </TableCell>
              )}
              {show("branch") && (
                <TableCell sx={{ ...headCell, width: 260 }}>
                  Conversation branch
                </TableCell>
              )}
              {show("csat") && (
                <TableCell sx={{ ...headCell, width: 84 }}>
                  <Tooltip
                    arrow
                    title="The simulated caller's satisfaction with the call, 0–10. Not measured calls have none."
                  >
                    <span>CSAT</span>
                  </Tooltip>
                </TableCell>
              )}
              {show("turns") && (
                <TableCell sx={{ ...headCell, width: 92 }}>Turns</TableCell>
              )}
              {show("latency") && (
                <TableCell sx={{ ...headCell, width: 96 }}>Latency</TableCell>
              )}
              {show("tokens") && (
                <TableCell sx={{ ...headCell, width: 96 }}>Tokens</TableCell>
              )}
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
              ? /* Flat view — skip the group header band entirely and render
                 every row as a plain list. Reader gets a clean single-column
                 stream when they don't want any bucketing. */
                groups.flatMap((g) => g.rows).map(renderRow)
              : groups.map((g) => (
                  <React.Fragment key={g.label}>
                    <GroupHeaderRow
                      group={g}
                      overlap={overlap}
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
                        else
                          ids
                            .filter((id) => !selected.has(id))
                            .forEach((id) => onToggle(id));
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
  onOpenTrace: PropTypes.func,
  gradingBasis: PropTypes.object,
  expandCommand: PropTypes.shape({
    open: PropTypes.bool,
    at: PropTypes.number,
  }),
  onAllOpenChange: PropTypes.func,
  tasks: PropTypes.array.isRequired,
  evals: PropTypes.array.isRequired,
  selected: PropTypes.object.isRequired,
  onToggle: PropTypes.func,
  onToggleAll: PropTypes.func,
  onOpen: PropTypes.func,
  /* Open the task focused on one eval's result (and its error localization). */
  onOpenEval: PropTypes.func,
  groupBy: PropTypes.string,
  members: PropTypes.array,
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
function GroupHeaderRow({
  group,
  collapsed,
  onToggle,
  show,
  showEvals,
  evals,
  selected,
  onToggleGroup,
  overlap = null,
}) {
  const overlapMark = overlap && (
    <Tooltip
      arrow
      title={`${overlap.hint} Group totals add up to more than the ${overlap.total} scenarios in this run.`}
    >
      <Box
        component="span"
        aria-label="Scenarios can be in several groups"
        onClick={(e) => e.stopPropagation()}
        sx={{ display: "inline-flex", flexShrink: 0, cursor: "help" }}
      >
        <Iconify
          icon="solar:layers-minimalistic-linear"
          width={14}
          sx={{ color: "text.tertiary" }}
        />
      </Box>
    </Tooltip>
  );

  /* The group's pass rate, read the way the run summary reads it: passed
     out of the scenarios that were scored. */
  const passText =
    group.measured > 0
      ? ` · ${group.pct}% passed ${group.passed}/${group.measured}`
      : "";
  const passMark = group.measured > 0 && (
    <Typography
      sx={{
        typography: "s3",
        color: "text.tertiary",
        whiteSpace: "nowrap",
        flexShrink: 0,
        fontVariantNumeric: "tabular-nums",
      }}
    >
      {"· "}
      <Box component="span" sx={{ color: "text.primary", fontWeight: 600 }}>
        {`${group.pct}%`}
      </Box>
      {` passed ${group.passed}/${group.measured}`}
    </Typography>
  );
  /* Member groups also say what that member fails at most. */
  const failureLine = group.topFailure && (
    <Typography
      noWrap
      title={group.topFailure.label}
      sx={{ typography: "s3", color: "text.tertiary", pl: 3.25, mt: 0.25 }}
    >
      {"Most often failed: "}
      <Box component="span" sx={{ color: "text.secondary" }}>
        {group.topFailure.label}
      </Box>
      {` · ${group.topFailure.count}`}
    </Typography>
  );

  /* No band tint. A stack of collapsed groups previously read as a
     wall of grey; now the row uses the standard body background and
     picks up hierarchy from typography (bolder label, tabular
     aggregates) and a fine bottom divider instead. */
  const rowHover = (t) => t.palette.action.hover;
  const cellSx = {
    bgcolor: "transparent",
    borderBottom: "1px solid",
    borderColor: "divider",
    cursor: "pointer",
    py: 1.25,
    px: 1.5,
    ".MuiTableRow-root:hover &": { bgcolor: rowHover },
    /* Same column dividers as the data rows, so columns read top to bottom. */
    "&:not(:first-of-type)": {
      borderLeft: "1px solid",
      borderColor: "divider",
    },
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

  /* Each descriptive column counts its own distinct values across the
     group's scenarios — never the row count under another name. Every row of
     a run is on this page, so the counts describe the whole group. */
  const uniqueBy = (arr, fn) => new Set(arr.map(fn).filter(Boolean)).size;
  const counted = (n, one, many) => (n ? `${n} ${n === 1 ? one : many}` : "—");

  const descSummary = (key) => {
    if (key === "callDetails")
      return null; /* Run details cell hosts the group label itself, not a summary. */
    if (key === "persona")
      return counted(
        uniqueBy(group.rows, (t) => t.persona?.name),
        "persona",
        "personas",
      );
    if (key === "scenario")
      return counted(
        uniqueBy(group.rows, (t) => t.id),
        "scenario",
        "scenarios",
      );
    if (key === "expected")
      return counted(
        uniqueBy(group.rows, (t) => t.expected),
        "outcome",
        "outcomes",
      );
    if (key === "branch")
      return counted(uniqueBy(group.rows, branchOf), "branch", "branches");
    return "—";
  };
  /* Aggregate cell — right-aligned tabular numbers with the same
     bad-value warning treatment data rows use. Group averages that
     cross the threshold (CSAT ≤4, Turns ≥12, Latency ≥550ms) turn
     red with a warning glyph so an unhealthy use case reads even
     before the group is expanded. Tokens is a group total, not an
     average, so no threshold applies. */
  /* Each number says what it is — Turns and Latency are averages, Tokens is
     the group's total — so a total doesn't read as an outlier next to
     per-call values. */
  const numCell = (value, suffix = "", metric, note = "Avg") => {
    const bad = metric
      ? isBad(metric, typeof value === "number" ? value : Number(value))
      : false;
    return (
      <TableCell sx={numCellSx}>
        {value == null ? (
          <Typography sx={{ typography: "s3", color: "text.disabled" }}>
            —
          </Typography>
        ) : (
          <Stack
            direction="row"
            alignItems="center"
            justifyContent="flex-start"
            spacing={0.5}
          >
            {bad && (
              <Iconify
                icon="solar:danger-triangle-bold"
                width={14}
                sx={{ color: "status.danger.fg", flexShrink: 0 }}
              />
            )}
            <Typography
              sx={{
                typography: "s2",
                fontWeight: 600,
                fontVariantNumeric: "tabular-nums",
                color: bad ? "status.danger.fg" : "text.primary",
              }}
            >
              {typeof value === "number" ? value.toLocaleString() : value}
              {suffix}
            </Typography>
          </Stack>
        )}
        {value != null && <Typography sx={AGG_NOTE_SX}>{note}</Typography>}
      </TableCell>
    );
  };
  /* Pass-rate colouring for the per-eval group column — green ≥80,
     neutral ≥50, red below. Reads as a heatmap at group level. */
  const evalRateColor = (v) =>
    v >= 80
      ? "status.success.fg"
      : v >= 50
        ? "text.primary"
        : "status.danger.fg";

  return (
    <TableRow
      {...clickable(onToggle, { role: "row", expanded: !collapsed })}
      sx={focusRing}
    >
      {/*
        Group checkbox — mirrors the header select-all behaviour but
        scoped to the tasks in this group. Padding/width match the
        data-row and select-all cells exactly (width 48, pl: 1.25,
        vertical-align: middle) so the checkboxes stack cleanly down
        one column edge instead of drifting off-axis.
      */}
      <TableCell
        sx={{
          width: 48,
          pl: 1.25,
          pr: 0,
          py: 0,
          verticalAlign: "middle",
          borderBottom: "1px solid",
          borderColor: "divider",
          cursor: "pointer",
          /* Pinned with the data rows' checkboxes, so a group stays
             selectable while the table scrolls sideways. Opaque, with the
             row's hover tint layered on top. */
          position: "sticky",
          left: 0,
          zIndex: 1,
          bgcolor: "background.paper",
          ".MuiTableRow-root:hover &": {
            backgroundImage: (t) =>
              `linear-gradient(${rowHover(t)}, ${rowHover(t)})`,
          },
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
          <Stack
            direction="row"
            alignItems="center"
            spacing={1.25}
            sx={GROUP_LABEL_ROW}
          >
            <Iconify
              icon={
                collapsed
                  ? "solar:alt-arrow-right-linear"
                  : "solar:alt-arrow-down-linear"
              }
              width={14}
              sx={{ color: "text.tertiary", flexShrink: 0 }}
            />
            <Typography
              noWrap
              title={group.label}
              sx={{
                typography: "s2",
                fontWeight: 600,
                color: "text.primary",
                minWidth: 0,
                flex: "0 1 auto",
              }}
            >
              {group.label}
            </Typography>
            <Typography
              sx={{
                typography: "s3",
                color: "text.tertiary",
                whiteSpace: "nowrap",
                flexShrink: 0,
              }}
            >
              · {group.count} scenario{group.count === 1 ? "" : "s"}
            </Typography>
            {passMark}
            {overlapMark}
          </Stack>
          {failureLine}
          <GroupLabelSizer
            text={`${group.label} · ${group.count} scenarios${passText}`}
          />
        </TableCell>
      ) : (
        descColumns.map((c, i) => (
          <TableCell
            key={c.key}
            sx={{ ...cellSx, pl: i === 0 ? 2 : 1.5, overflow: "hidden" }}
          >
            {i === 0 ? (
              /* First visible descriptive column (Run details in the
               default order) carries the group's identity: chevron
               + label + count. */
              <>
                <Stack
                  direction="row"
                  alignItems="center"
                  spacing={1.25}
                  sx={GROUP_LABEL_ROW}
                >
                  <Iconify
                    icon={
                      collapsed
                        ? "solar:alt-arrow-right-linear"
                        : "solar:alt-arrow-down-linear"
                    }
                    width={14}
                    sx={{ color: "text.tertiary", flexShrink: 0 }}
                  />
                  <Typography
                    noWrap
                    title={group.label}
                    sx={{
                      typography: "s2",
                      fontWeight: 600,
                      color: "text.primary",
                      minWidth: 0,
                      flex: "0 1 auto",
                    }}
                  >
                    {group.label}
                  </Typography>
                  <Typography
                    sx={{
                      typography: "s3",
                      color: "text.tertiary",
                      whiteSpace: "nowrap",
                      flexShrink: 0,
                    }}
                  >
                    · {group.count} scenario{group.count === 1 ? "" : "s"}
                  </Typography>
                  {passMark}
                  {overlapMark}
                </Stack>
                {failureLine}
                <GroupLabelSizer
                  text={`${group.label} · ${group.count} scenarios${passText}`}
                />
              </>
            ) : (
              <Typography
                noWrap
                sx={{ typography: "s3", color: "text.tertiary" }}
              >
                {descSummary(c.key)}
              </Typography>
            )}
          </TableCell>
        ))
      )}
      {show("csat") && numCell(a.csat, "", "csat")}
      {show("turns") && numCell(a.turns, "", "turns")}
      {show("latency") && numCell(a.latency, "ms", "latency")}
      {show("tokens") && numCell(a.tokens, "", undefined, "Total")}
      {/* `evals` here is the caller's shownEvals — GroupHeaderRow lives in
         its own scope and doesn't see the outer shownEvals binding. */}
      {evals.map((e) => {
        const ea = a.evals?.[e.id];
        if (!ea || !ea.total) {
          return (
            <TableCell key={`eval-${e.id}`} sx={numCellSx}>
              <Typography sx={{ typography: "s3", color: "text.disabled" }}>
                —
              </Typography>
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
            <Box
              sx={{
                position: "absolute",
                inset: 0,
                display: "flex",
                flexDirection: "column",
                justifyContent: "center",
                px: 2,
                py: 1.25,
                bgcolor: bg,
                color: "text.primary",
              }}
            >
              <Typography
                sx={{
                  typography: "s2",
                  fontWeight: 600,
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {rate}%
              </Typography>
              <Typography
                sx={AGG_NOTE_SX}
              >{`Avg · ${ea.total} scored`}</Typography>
            </Box>
          </TableCell>
        );
      })}
    </TableRow>
  );
}
GroupHeaderRow.propTypes = {
  overlap: PropTypes.object,
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
      direction="row"
      alignItems="center"
      spacing={0.5}
      sx={{ "&:hover .eval-col-actions": { opacity: 1 } }}
    >
      <Typography
        noWrap
        sx={{
          typography: "s2",
          fontWeight: 500,
          color: "text.secondary",
          minWidth: 0,
        }}
      >
        {name}
      </Typography>
      {partial > 0 && (
        <Tooltip
          arrow
          title={`Scored on ${partial} scenario${partial === 1 ? "" : "s"} you re-ran this eval on. The other rows show "—".`}
        >
          <Box
            sx={{
              display: "inline-flex",
              alignItems: "center",
              px: 0.5,
              height: 15,
              borderRadius: "4px",
              flexShrink: 0,
              border: (t) => `1px solid ${t.palette.status.discovery.border}`,
              color: "primary.main",
              bgcolor: "status.discovery.bg",
            }}
          >
            <Typography
              sx={{ typography: "s3", fontWeight: 600, letterSpacing: 0.3 }}
            >
              {partial}
            </Typography>
          </Box>
        </Tooltip>
      )}
      {rescoring && (
        <Iconify
          icon="solar:refresh-linear"
          width={12}
          sx={{
            color: "text.tertiary",
            flexShrink: 0,
            animation: "tt-spin 0.8s linear infinite",
            "@keyframes tt-spin": { to: { transform: "rotate(360deg)" } },
          }}
        />
      )}
      {hasActions && (
        <>
          <IconButton
            size="small"
            className="eval-col-actions"
            onClick={(e) => {
              e.stopPropagation();
              setAnchor(e.currentTarget);
            }}
            sx={{
              ml: "auto",
              opacity: anchor ? 1 : 0,
              transition: "opacity 120ms",
              flexShrink: 0,
            }}
          >
            <Iconify
              icon="solar:menu-dots-bold"
              width={14}
              sx={{ color: "text.tertiary" }}
            />
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
              <MenuItem
                onClick={() => {
                  setAnchor(null);
                  onEdit();
                }}
                sx={{ typography: "s2", gap: 1 }}
              >
                <Iconify
                  icon="solar:pen-linear"
                  width={16}
                  sx={{ flexShrink: 0 }}
                />
                <Box component="span" sx={{ typography: "s2" }}>
                  Edit eval
                </Box>
              </MenuItem>
            )}
            {onRerun && (
              <MenuItem
                onClick={() => {
                  setAnchor(null);
                  onRerun();
                }}
                sx={{ typography: "s2", gap: 1 }}
              >
                <Iconify
                  icon="solar:refresh-linear"
                  width={16}
                  sx={{ flexShrink: 0 }}
                />
                <Box component="span" sx={{ typography: "s2" }}>
                  Re-run this eval
                </Box>
              </MenuItem>
            )}
            {onDelete && (
              <MenuItem
                onClick={() => {
                  setAnchor(null);
                  onDelete();
                }}
                // eslint-disable-next-line no-restricted-syntax -- ds: destructive menu item
                sx={{ typography: "s2", gap: 1, color: "status.danger.fg" }}
              >
                <Iconify
                  icon="solar:trash-bin-trash-linear"
                  width={16}
                  sx={{ flexShrink: 0, color: "status.danger.fg" }}
                />
                <Box
                  component="span"
                  sx={{ typography: "s2", color: "status.danger.fg" }}
                >
                  Delete column
                </Box>
              </MenuItem>
            )}
          </Menu>
        </>
      )}
    </Stack>
  );
}
EvalHeadCell.propTypes = {
  name: PropTypes.string,
  partial: PropTypes.number,
  onEdit: PropTypes.func,
  onRerun: PropTypes.func,
  onDelete: PropTypes.func,
  rescoring: PropTypes.bool,
};

/* The useful half of a task id is the trailing variant — everything after the
   last numeric segment ("…-core-0-routine" → "Routine", "…-0-off-topic" →
   "Off Topic"). That's the one thing that differs row-to-row inside a group,
   so it becomes the run's title instead of the repeated raw id. */
function variantLabel(id) {
  const parts = String(id || "")
    .split("-")
    .filter(Boolean);
  let lastNum = -1;
  parts.forEach((p, i) => {
    if (/^\d+$/.test(p)) lastNum = i;
  });
  const tail = lastNum >= 0 ? parts.slice(lastNum + 1) : [];
  if (tail.length) return humanize(tail.join(" "));
  /* No trailing variant — fall back to the test-kind token before the number. */
  if (lastNum > 0) return humanize(parts[lastNum - 1]);
  return humanize(parts.slice(-1)[0] || "Run");
}

/*
  Column bounds. The table sizes columns to their content, which is right for
  short aggregates — but a long group title or sub-goal line that never wraps
  stretched Run details across half the table and squeezed the text columns
  to a word per line. Titles no longer size the column, sub-goal lines stop at
  a width (full text on hover), and text columns keep a readable minimum.
*/
/* A group title fills whatever width Run details ends up with and shortens
   only when it truly doesn't fit — `width: 0; min-width: 100%` keeps it out
   of the column's sizing, so a long title can't stretch the column. The
   column's own floor is set on its header cell. */
const GROUP_LABEL_ROW = { width: 0, minWidth: "100%" };
/* Height of the section band above the column headers; the headers stick
   just under it. */
const BAND_H = 30;
const RUN_DETAILS_MIN = 360;
/* …but the column should still make room for titles: an invisible copy asks
   for the title's width, up to a cap, so titles fit where they reasonably
   can and only the longest shorten. */
const GROUP_LABEL_FIT = 560;
function GroupLabelSizer({ text }) {
  return (
    <Box
      aria-hidden
      sx={{
        height: 0,
        overflow: "hidden",
        visibility: "hidden",
        whiteSpace: "nowrap",
        maxWidth: GROUP_LABEL_FIT,
        pl: 3.5,
        typography: "s2",
        fontWeight: 600,
      }}
    >
      {text}
    </Box>
  );
}
GroupLabelSizer.propTypes = { text: PropTypes.string };
const SUBGOAL_LINE_MAX = 380;

/* The roll-up under each group number — "Avg", "Total", "Avg · 12 scored". */
const AGG_NOTE_SX = {
  typography: "s3",
  color: "text.tertiary",
  whiteSpace: "nowrap",
  mt: 0.125,
};

/* What a critical scenario's result means, and what to do. Information —
   the harness never blocks a release (PRD v7 §4.10.1). */
const BLOCKER_STATE = {
  failed: "failed. Open the call to see where the rule broke.",
  passed: "the rule held.",
  flaky: "the rule held on some repeats, not all. Worth a look.",
  error: "the call didn't finish, so it's not known whether the rule held.",
  unmeasured: "not graded, so it's not known whether the rule held.",
};
const TEXT_COL = {
  scenario: { minWidth: 260, maxWidth: 380 },
  expected: { minWidth: 240, maxWidth: 340 },
  branch: { minWidth: 170 },
};

/* The path the call took through the agent's turns — shown on the row and
   counted, the same way, in the group header. */
function branchOf(t) {
  return (t.steps || [])
    .filter((s) => s.role === "agent")
    .slice(0, 5)
    .map(
      (_, i, arr) =>
        (i === 0 ? "start" : `turn_${i}`) + (i < arr.length - 1 ? " → " : ""),
    )
    .join("");
}

/* Why a call wasn't measured, in words — something outside the agent. */
function unmeasuredWhy(t) {
  const f = t?.fault || {};
  if (f.toolGap)
    return "the environment couldn't answer a tool the agent called.";
  if (f.transport) return "the call dropped before it finished.";
  if (f.environment)
    return "the environment wasn't ready when the call started.";
  if (f.simulator || f.simulatorDrift)
    return "the simulated caller went off script.";
  if (f.grading) return "grading failed for this call.";
  return "something outside the agent stopped the call, so it isn't counted in pass rates.";
}

/*
  The row's verdict with its cause (PRD v7 §4.10.10, updated RSI PRD):
  "Failed · agent", "Test invalid · simulator", "Error · connection". A
  retry, a stale scenario and a re-grade trail it, quietly — none changes the
  verdict on its own.
*/
function VerdictLabel({ task, outcome }) {
  const navigate = useNavigate();
  const { envId } = useParams();
  const v = task.verdictCause;
  let label = outcome.label;
  let color = outcome.color;
  let tip = "";
  if (v?.pulled) {
    label = "Pulled for review";
    color = "text.tertiary";
    tip = `${v.reason} Left out of every count. Click to review it.`;
  } else if (v?.cause && v.cause !== "pass") {
    const inc = v.cause.startsWith("inc-");
    label = `${inc ? OUTCOMES[v.outcome || outcomeOfCause(v.cause)].label : outcome.label} · ${causeShort(v.cause)}`;
    tip =
      v.cause === "fail-required"
        ? `${verdictLineOf(v)}. ${VERDICT_CAUSES[v.cause].note}`
        : VERDICT_CAUSES[v.cause]?.note || "";
  }
  /* A pulled scenario links to itself in the environment, to review it. */
  const review =
    v?.pulled && envId
      ? clickable(
          (e) => {
            e?.stopPropagation?.();
            navigate(
              `${paths.dashboard.simulate.environmentStep(envId, "scenarios")}?scenario=${encodeURIComponent(task.id)}`,
            );
          },
          { role: "link" },
        )
      : null;
  return (
    <>
      <Tooltip arrow title={tip}>
        <Typography
          noWrap
          {...(review || {})}
          sx={{
            typography: "s3",
            fontWeight: 600,
            color,
            ...(review && {
              ...focusRing,
              display: "inline-flex",
              alignItems: "center",
              gap: 0.25,
              cursor: "pointer",
              "&:hover": { color: "text.primary", textDecoration: "underline" },
            }),
          }}
        >
          {label}
          {review && <Iconify icon="solar:arrow-right-up-linear" width={12} />}
        </Typography>
      </Tooltip>
      {(v?.retried || task.retried) &&
        (() => {
          const r = v?.retried || task.retried;
          return (
            <Tooltip
              arrow
              title={`First try ended ${r.kind === "error" ? "error" : "test invalid"} — ${String(r.first || "").replace(/\.$/, "")}. Retried once${r.recovered ? "; this is the second try." : " and it happened again."}`}
            >
              <Typography
                noWrap
                sx={{ typography: "s3", color: "text.tertiary" }}
              >
                · retried once
              </Typography>
            </Tooltip>
          );
        })()}
      {v?.stale && (
        <Tooltip
          arrow
          title={`${STALE_LABEL[v.stale] || "Stale"} when the run started — it ran, but counts toward no decision until it's updated or archived.`}
        >
          <Typography
            noWrap
            sx={{ typography: "s3", color: "text.tertiary" }}
          >{`· ${(STALE_LABEL[v.stale] || "Stale").toLowerCase()} · not counted`}</Typography>
        </Tooltip>
      )}
      {v?.exploration && (
        <Tooltip
          arrow
          title="A proposed scenario, not yet added to the environment. Shown separately from the run's rates."
        >
          <Typography noWrap sx={{ typography: "s3", color: "text.tertiary" }}>
            · proposed
          </Typography>
        </Tooltip>
      )}
      {task.regraded?.changed && (
        <Tooltip
          arrow
          title={`Re-graded with ${task.regraded.with} — it read ${task.regraded.from} under the old graders.`}
        >
          <Typography noWrap sx={{ typography: "s3", color: "text.tertiary" }}>
            · re-graded
          </Typography>
        </Tooltip>
      )}
    </>
  );
}
VerdictLabel.propTypes = { task: PropTypes.object, outcome: PropTypes.object };

/* Outcome as colour + label, so a failure is scannable at the row level
   instead of reading identical "Completed" text down the whole column. */
function runOutcome(status, task) {
  switch (status) {
    case "passed":
      return { label: "Passed", color: "status.success.fg" };
    case "failed":
      return { label: "Failed", color: "status.danger.fg" };
    case "flaky":
      return { label: "Flaky", color: "text.primary" };
    case "error":
      return { label: "Error", color: "status.danger.fg" };
    /* Inconclusive, test invalid or error — none counts for or against the agent. */
    default:
      return {
        label: task ? notCountedLabel(task) : "Inconclusive",
        color: "text.tertiary",
      };
  }
}

/*
  Numeric metric cell with subtle severity tinting. A plain reader scans
  every row and only red or amber digits jump out — the layout, weight
  and glyphs stay identical to the neutral case so nothing "shifts" as
  you scroll. Dot sits before the number for bad values only, so red
  reads as a genuine flag and warn stays softly ambient.
*/
/* Why a value turned red — the threshold, said plainly. */
const BAD_WHY = {
  csat: (v) =>
    `CSAT ${v} of 10 — ${METRIC_THRESHOLDS.csat.bad} or below is an unhappy caller.`,
  turns: (v) =>
    `${v} turns — ${METRIC_THRESHOLDS.turns.bad} or more is a long conversation for one task.`,
  latency: (v) =>
    `${v}ms average reply — ${METRIC_THRESHOLDS.latency.bad}ms or slower feels laggy on a call.`,
  tokens: (v) =>
    `${Number(v).toLocaleString()} tokens — ${METRIC_THRESHOLDS.tokens.bad.toLocaleString()} or more is among the heaviest calls.`,
};

function MetricValue({ metric, value, suffix = "" }) {
  if (value == null)
    return (
      <Typography
        component="span"
        sx={{ typography: "s2", color: "text.disabled" }}
      >
        —
      </Typography>
    );
  const bad = isBad(metric, typeof value === "number" ? value : Number(value));
  return (
    <Tooltip arrow title={bad && BAD_WHY[metric] ? BAD_WHY[metric](value) : ""}>
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="flex-start"
        spacing={0.625}
      >
        {bad && (
          <Iconify
            icon="solar:danger-triangle-bold"
            width={14}
            sx={{ color: "status.danger.fg", flexShrink: 0 }}
          />
        )}
        <Typography
          component="span"
          sx={{
            typography: "s2",
            fontVariantNumeric: "tabular-nums",
            color: bad ? "status.danger.fg" : "text.secondary",
            fontWeight: bad ? 600 : 400,
          }}
        >
          {typeof value === "number" ? value.toLocaleString() : value}
          {suffix}
        </Typography>
      </Stack>
    </Tooltip>
  );
}
MetricValue.propTypes = {
  metric: PropTypes.string,
  value: PropTypes.any,
  suffix: PropTypes.string,
};

/**
 * Sub-goals for the Run details cell — one summary line ("4 of 6 sub-goals
 * met"), the ones that weren't met listed under it, and the full checklist
 * one click away. A row that met every sub-goal stays a single line.
 */
function SubGoalsSummary({ task, env, basis = null }) {
  const [expanded, setExpanded] = useState(false);
  const { items: rows, rollup } = subGoalStatuses(task, env);
  if (rows.length === 0) return null;

  /* One line says how the sub-goals rolled up; only the ones that weren't
     met are listed under it, since they're what the row is read for. Every
     sub-goal is one click away. */
  const met = rows.filter((r) => r.status === "met").length;
  const missed = rows.filter((r) => r.status !== "met");
  const list = expanded ? rows : missed;
  const verdictIcon = {
    failed: "solar:close-linear",
    inconclusive: "solar:question-circle-linear",
    passed: "solar:check-linear",
  }[rollup.verdict];
  const verdictColor = {
    failed: "status.danger.fg",
    inconclusive: "text.tertiary",
    passed: "status.success.fg",
  }[rollup.verdict];

  return (
    <Stack spacing={0.125} sx={{ mt: 0.75 }}>
      <Stack direction="row" alignItems="center" spacing={0.5}>
        <Iconify
          icon={verdictIcon}
          width={12}
          sx={{ color: verdictColor, flexShrink: 0 }}
        />
        <Typography
          noWrap
          sx={{ typography: "s3", color: "text.secondary", fontWeight: 600 }}
        >
          {rollupLine(rollup)}
        </Typography>
        <Typography noWrap sx={{ typography: "s3", color: "text.tertiary" }}>
          {`· ${met} of ${rows.length} met`}
        </Typography>
        {basis && (
          <Tooltip arrow title={basis.note}>
            <Typography
              noWrap
              sx={{
                typography: "s3",
                color: "text.tertiary",
                cursor: "default",
              }}
            >
              {`· ${basis.judged ? "judged from the conversation" : "checked against end state and tool calls"}`}
            </Typography>
          </Tooltip>
        )}
        <ButtonBase
          aria-expanded={expanded}
          onClick={(e) => {
            e.stopPropagation();
            setExpanded((o) => !o);
          }}
          sx={{
            typography: "s3",
            color: "primary.main",
            fontWeight: 500,
            "&:hover": { textDecoration: "underline" },
            ...focusRing,
          }}
        >
          {expanded ? "Hide" : "Show all"}
        </ButtonBase>
      </Stack>
      {list.map((r) => (
        <Stack
          key={r.key}
          direction="row"
          alignItems="center"
          spacing={0.5}
          sx={{ pl: 2 }}
        >
          <Tooltip
            arrow
            title={MARK_NOTE[r.mark === "critical" ? "critical" : "supporting"]}
          >
            <Typography
              sx={{
                typography: "s3",
                fontWeight: 600,
                color: r.mark === "critical" ? "text.primary" : "text.disabled",
                width: 10,
                flexShrink: 0,
                fontFamily: "ui-monospace, Menlo, monospace",
                fontSize: 12,
              }}
            >
              {r.mark === "critical" ? "C" : "S"}
            </Typography>
          </Tooltip>
          <Typography
            noWrap
            title={r.text}
            sx={{
              typography: "s3",
              color: "text.secondary",
              maxWidth: SUBGOAL_LINE_MAX,
            }}
          >
            {r.text}
          </Typography>
          {(() => {
            /* What this one was checked against — per sub-goal, since a
               sealed run still judges a "said it" sub-goal from the words. */
            const b = subGoalBasisOf(r, basis, env);
            return (
              b && (
                <Tooltip arrow title={b.label}>
                  <Iconify
                    icon={b.icon}
                    width={12}
                    sx={{ color: "text.disabled", flexShrink: 0 }}
                  />
                </Tooltip>
              )
            );
          })()}
          {r.status === "met" && (
            <Iconify
              icon="solar:check-linear"
              width={12}
              sx={{ color: "status.success.fg", flexShrink: 0 }}
            />
          )}
          {r.status === "failed" && (
            <Iconify
              icon="solar:close-linear"
              width={12}
              sx={{ color: "status.danger.fg", flexShrink: 0 }}
            />
          )}
          {r.status === "inconclusive" && (
            <Tooltip arrow title="Inconclusive — the grader couldn't tell">
              <Iconify
                icon="solar:question-circle-linear"
                width={12}
                sx={{ color: "text.tertiary", flexShrink: 0 }}
              />
            </Tooltip>
          )}
        </Stack>
      ))}
    </Stack>
  );
}
SubGoalsSummary.propTypes = {
  task: PropTypes.object,
  env: PropTypes.object,
  basis: PropTypes.object,
};

function Field({ icon, label, value }) {
  if (value == null || value === "") return null;
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={0.75}
      sx={{
        px: 1,
        py: 0.5,
        borderRadius: "4px",
        bgcolor: "background.neutral",
      }}
    >
      <Iconify
        icon={icon}
        width={14}
        sx={{ color: "text.tertiary", flexShrink: 0 }}
      />
      <Typography noWrap sx={{ typography: "s3", color: "text.tertiary" }}>
        {label}:
      </Typography>
      <Typography
        noWrap
        sx={{ typography: "s3", color: "text.primary", fontWeight: 500 }}
      >
        {value}
      </Typography>
    </Stack>
  );
}
Field.propTypes = {
  icon: PropTypes.string,
  label: PropTypes.string,
  value: PropTypes.any,
};

function Score({ result }) {
  const bgcolor = interpolateColorBasedOnScore(result.score, 1);
  const failed = result.passed === false;
  /* Error localization exists for any score under 100%, but the marker only
     shows where the eval failed — on a passing 86% it read as a problem. The
     tooltip still offers "where it lost points" on click. */
  const localized = (result.score ?? 0) < 1;
  const marked = failed;
  return (
    <Tooltip
      classes={{ tooltip: "ds-hovercard" }}
      arrow
      title={
        <Box>
          {result.reason && <Box>{result.reason}</Box>}
          <Box sx={{ mt: result.reason ? 0.75 : 0, fontWeight: 600 }}>
            {failed
              ? "Click to see where it failed"
              : localized
                ? "Click to see where it lost points"
                : "Click to see the explanation"}
          </Box>
        </Box>
      }
    >
      <Box
        sx={{
          position: "absolute",
          inset: 0,
          display: "flex",
          alignItems: "center",
          gap: 0.75,
          px: 2,
          py: 1.5,
          bgcolor,
          color: "text.primary",
          cursor: "pointer",
          "&:hover .el-target": { opacity: 1 },
        }}
      >
        <Typography
          sx={{
            typography: "s2",
            fontWeight: 600,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {Math.round(result.score * 100)}%
        </Typography>
        {marked && (
          <Iconify
            className="el-target"
            icon="solar:target-linear"
            width={14}
            sx={{
              opacity: 0.55,
              transition: "opacity 120ms",
              color: "text.primary",
            }}
          />
        )}
      </Box>
    </Tooltip>
  );
}
Score.propTypes = { result: PropTypes.object };
