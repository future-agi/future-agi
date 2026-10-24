import PropTypes from "prop-types";
import { creditsLabel } from "../_mock/metering";
import React, {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { useSearchParams } from "react-router-dom";
import { alpha } from "@mui/material/styles";
import {
  Box,
  Button,
  ButtonBase,
  Chip,
  Link,
  Stack,
  Tab,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { SegmentedTabs } from "src/components/tabs/tabs";
import { SectionCard, StatusDot } from "../components/primitives";
import {
  runInsightsOf,
  temperamentOf,
  MIN_MEASURED,
} from "../_mock/runInsights";
import { RunSummaryBlock } from "./RunSummaryChip";
import { useSimStore } from "../store";
import { envAgentKey } from "../_mock/agentRegistry";
import { limitsOf } from "../agents/agentLimits";
import { budgetsWithin } from "../_mock/improvementStart";
import { OUTCOMES } from "../_mock/decoupled";
import { RunRecordFooter } from "./RunRecord";
import { clickable, focusRing } from "../components/clickable";
import { scenarioTitle } from "../_mock/scenarios";

/**
 * Insights — the run read for what to do next (PRD v6, Story 20; v7 §4.10.10).
 *
 * Top to bottom: the run summary (pass^1 / pass^k, counts by verdict cause —
 * a report, never a gate), a brief written from graded scenarios only, the goal and
 * sub-goal scoreboard, the ranked issues with their Fix, and the evidence
 * behind each issue. Every claim links back to the scenarios it is about —
 * a click filters the Scenarios tab to them.
 */
const SEVERITY = {
  critical: { label: "Critical", color: "status.danger.fg", weight: 600 },
  high: { label: "High", color: "text.primary", weight: 600 },
  medium: { label: "Medium", color: "text.secondary", weight: 600 },
  low: { label: "Low", color: "text.tertiary", weight: 600 },
};
const GOALS_SHOWN = 8;

const colHead = {
  typography: "s3",
  fontWeight: 600,
  color: "text.tertiary",
  textTransform: "none",
  letterSpacing: 0.4,
};
const cellNum = {
  typography: "s2",
  fontWeight: 600,
  fontVariantNumeric: "tabular-nums",
  textAlign: "right",
};
/* Column headers read like the product's DataTable: no fill, 13px, quiet. */
const tableHead = { fontSize: 13, fontWeight: 500, color: "text.secondary" };

/* Where a claim links to when the tab is read outside the app — the shared
   report and its PDF. `({ ids, issue, trace }) => href`; null in the app. */
const HrefContext = createContext(null);

export default function InsightsTab({
  env,
  envState,
  tasks,
  run,
  goalOf,
  tracing,
  candidate,
  existingFixes,
  onFilter,
  onOpenTrace,
  onFix,
  onOpenFix,
  insights: given,
  summary,
  onSetBar,
  record,
  readOnly = false,
  hrefFor = null,
  history = null,
}) {
  /* The run summary is rebuilt every render; only what the brief reads off it
     should recompute the insights. */
  const runKey = `${run?.id}|${run?.ordinal}|${run?.agentVersion}|${run?.repeats}`;
  const insights = useMemo(
    () => given || runInsightsOf({ tasks, env, envState, run, goalOf }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [given, tasks, env, envState, runKey, goalOf],
  );
  const byId = useMemo(() => new Map(tasks.map((t) => [t.id, t])), [tasks]);
  const jump = (id) =>
    document
      .getElementById(id)
      ?.scrollIntoView({ behavior: "smooth", block: "start" });

  /* `?issue=RI-…` — a digest's issue link lands on that issue. */
  const [params] = useSearchParams();
  const issueParam = params.get("issue");
  useEffect(() => {
    if (!issueParam) return undefined;
    const id = setTimeout(() => jump(`insight-issue-${issueParam}`), 120);
    return () => clearTimeout(id);
  }, [issueParam]);

  /* The run this one is read against: the latest earlier run on the same
     environment version, else the latest earlier run at all. */
  const current = useMemo(
    () => (history || []).find((r) => r.id === run?.id) || null,
    [history, run?.id],
  );
  const prev = useMemo(() => {
    if (!current) return null;
    const earlier = (history || []).filter(
      (r) =>
        r.ordinal < current.ordinal &&
        !r.synthetic &&
        r.finishedAt &&
        r.measured > 0,
    );
    const same = earlier.filter((r) => r.envVersion === current.envVersion);
    return (
      [...(same.length ? same : earlier)].sort(
        (a, b) => b.ordinal - a.ordinal,
      )[0] || null
    );
  }, [history, current]);
  /* The change in scenario pass rate, with the noise two runs of this size
     carry — a swing inside it isn't a regression or a fix. */
  const delta = useMemo(() => {
    if (!current || !prev || !current.measured) return null;
    const p1 = current.passed / current.measured;
    const p2 = prev.passed / prev.measured;
    const pts = Math.round((p1 - p2) * 100);
    const noise = Math.max(
      1,
      Math.round(
        1.96 *
          Math.sqrt(
            (p1 * (1 - p1)) / current.measured +
              (p2 * (1 - p2)) / prev.measured,
          ) *
          100,
      ),
    );
    return {
      pts,
      noise,
      within: Math.abs(pts) <= noise,
      sameEnv: prev.envVersion === current.envVersion,
    };
  }, [current, prev]);
  /* Issues across runs: new here, still here, or gone since the run before. */
  const prevInsights = useMemo(
    () =>
      prev?.tasks
        ? runInsightsOf({ tasks: prev.tasks, env, envState, run: prev, goalOf })
        : null,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [prev?.id, env, goalOf],
  );
  const lifecycle = useMemo(() => {
    if (!prevInsights) return null;
    const before = new Set(prevInsights.issues.map((i) => i.id));
    const now = new Set(insights.issues.map((i) => i.id));
    return {
      prevOrdinal: prev.ordinal,
      status: Object.fromEntries(
        insights.issues.map((i) => [
          i.id,
          before.has(i.id) ? "recurring" : "new",
        ]),
      ),
      resolved: prevInsights.issues.filter((i) => !now.has(i.id)),
    };
  }, [prevInsights, insights, prev]);

  /* What a fix may spend at most — the agent's run budget. */
  const { state } = useSimStore();
  const fixCap = useMemo(() => {
    const list = budgetsWithin(
      limitsOf(state, envAgentKey(envState?.agent, env?.id)).budget.dollars,
    );
    return list[list.length - 1] || null;
  }, [state, envState?.agent, env?.id]);

  /* The verdict, in one sentence. */
  /* Cut at a word, and close a quote the cut left open. */
  const short = (t) => {
    if (t.length <= 72) return t;
    const cut = t.slice(0, 70).replace(/\s+\S*$/, "");
    return `${cut}…${(cut.match(/"/g) || []).length % 2 ? '"' : ""}`;
  };
  const top = insights.issues[0] || null;
  const headline = !summary?.scored
    ? "Nothing in this run reached a scored verdict."
    : top
      ? /* A title that already quotes a rule isn't wrapped in quotes again. */
        `${summary.passed} of ${summary.scored} scenarios passed — ${insights.issues.length} ${insights.issues.length === 1 ? "issue" : "issues"} to fix, starting with ${/["“]/.test(top.title) ? short(top.title) : `“${short(top.title)}”`}.`
      : summary.failed
        ? `${summary.passed} of ${summary.scored} scenarios passed — the ${summary.failed} ${summary.failed === 1 ? "failure doesn't" : "failures don't"} share a cause yet.`
        : `All ${summary.scored} scenarios passed.`;
  const eyebrow = run?.ordinal
    ? `Run ${run.ordinal} · agent ${run.agentVersion || "—"} · what broke and what to do`
    : "What broke and what to do";
  const deltaCell = current
    ? [
        delta
          ? {
              key: "delta",
              label: `vs Run ${prev.ordinal}`,
              value: `${delta.pts > 0 ? "+" : ""}${delta.pts} pts`,
              caption: `±${delta.noise} · ${delta.within ? "within noise" : delta.pts > 0 ? "better" : "worse"}${delta.sameEnv ? "" : " · other env version"}`,
              tone:
                !delta.within && delta.pts < 0 ? "status.danger.fg" : undefined,
              tip: `Scenario pass rate ${Math.round((current.passed / current.measured) * 100)}% against ${Math.round((prev.passed / prev.measured) * 100)}% in Run ${prev.ordinal}. Two runs of this size differ by up to ±${delta.noise} pts on chance alone (95%), so a change inside that band isn't a regression or a fix.${delta.sameEnv ? "" : " Run " + prev.ordinal + " was on another environment version — the world moved too."}`,
            }
          : {
              key: "delta",
              label: "vs last run",
              value: "—",
              caption: "First run here",
            },
      ]
    : [];
  const [details, setDetails] = useState(false);
  /* A digest link to an issue opens the details on it. */
  useEffect(() => {
    if (issueParam) setDetails(true);
  }, [issueParam]);

  return (
    <HrefContext.Provider value={hrefFor}>
      <Stack spacing={2} sx={{ pb: 4 }}>
        {/* The verdict leads: one sentence, the numbers behind it, then the
          issues worth fixing first. */}
        <RunSummaryBlock
          summary={summary}
          onFilter={onFilter}
          onSetBar={readOnly ? null : onSetBar}
          envId={env?.id}
          readOnly={readOnly}
          eyebrow={eyebrow}
          headline={headline}
          extraCells={deltaCell}
          footer={
            /* The foot of the verdict: the way into the details, and what the
             run metered and who it told. */
            <Stack
              direction={{ xs: "column", md: "row" }}
              alignItems={{ md: "center" }}
              spacing={{ xs: 0.75, md: 2 }}
              sx={{ px: 2, py: 1.25 }}
            >
              {/* A real button that says what's behind it — an issue table, the
                goals, the bar — so it isn't read as a caption and missed. */}
              <Button
                size="small"
                variant="outlined"
                aria-expanded={details}
                onClick={() => setDetails((d) => !d)}
                endIcon={
                  <Iconify
                    icon={
                      details
                        ? "solar:alt-arrow-up-linear"
                        : "solar:alt-arrow-down-linear"
                    }
                    width={14}
                  />
                }
                sx={{
                  flexShrink: 0,
                  alignSelf: { xs: "flex-start", md: "center" },
                  color: "text.primary",
                  borderColor: "divider",
                  "&:hover": {
                    borderColor: "text.disabled",
                    bgcolor: "action.hover",
                  },
                }}
              >
                {details ? "Hide details" : "Show details"}
                <Box
                  component="span"
                  sx={{
                    color: "text.tertiary",
                    fontWeight: 400,
                    ml: 0.75,
                    whiteSpace: "nowrap",
                  }}
                >
                  {[
                    insights.issues.length
                      ? `${insights.issues.length} ${insights.issues.length === 1 ? "issue" : "issues"}`
                      : null,
                    lifecycle?.resolved.length
                      ? `${lifecycle.resolved.length} resolved`
                      : null,
                    insights.goals.length
                      ? `${insights.goals.length} ${insights.goals.length === 1 ? "goal" : "goals"}`
                      : null,
                    "your bar",
                  ]
                    .filter(Boolean)
                    .map((x) => `· ${x}`)
                    .join(" ")}
                </Box>
              </Button>
              {!readOnly && record && (
                <Box sx={{ ml: { md: "auto !important" }, minWidth: 0 }}>
                  <RunRecordFooter
                    inline
                    metering={record.metering}
                    digest={record.digest}
                    destinations={record.destinations}
                    sentAt={record.sentAt}
                  />
                </Box>
              )}
            </Stack>
          }
        >
          <TopIssues
            insights={insights}
            run={run}
            fixCap={fixCap}
            tracing={tracing}
            candidate={candidate}
            existingFixes={existingFixes}
            onFix={onFix}
            onOpenFix={onOpenFix}
            onFilter={onFilter}
            readOnly={readOnly}
            onMore={() => {
              setDetails(true);
              setTimeout(() => jump("insight-details"), 80);
            }}
          />
        </RunSummaryBlock>

        {/* A quiet, standing note while the fix loop has nothing to stand on. */}
        {!readOnly && !tracing?.traced && (
          <Stack
            direction="row"
            spacing={1.25}
            alignItems="flex-start"
            sx={{
              px: 1.75,
              py: 1.25,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "8px",
              bgcolor: "background.neutral",
            }}
          >
            <Iconify
              icon="solar:info-circle-linear"
              width={16}
              sx={{ color: "text.tertiary", flexShrink: 0, mt: "1px" }}
            />
            <Typography
              sx={{ typography: "s2", color: "text.secondary", flex: 1 }}
            >
              {`Fix is off for this run. ${tracing?.reason || ""}`}
              {tracing?.docs && (
                <>
                  {" "}
                  <Link
                    href={tracing.docs}
                    target="_blank"
                    rel="noopener"
                    underline="always"
                    sx={{ color: "text.primary", fontWeight: 600 }}
                  >
                    Set up traceAI
                  </Link>
                </>
              )}
            </Typography>
          </Stack>
        )}

        {details && (
          <>
            <Box
              id="insight-details"
              sx={{ scrollMarginTop: 16, mt: "0 !important" }}
            />
            <Issues
              insights={insights}
              byId={byId}
              tracing={tracing}
              candidate={candidate}
              existingFixes={existingFixes}
              onFilter={onFilter}
              onOpenTrace={onOpenTrace}
              onFix={onFix}
              onOpenFix={onOpenFix}
              openId={issueParam}
              lifecycle={lifecycle}
              readOnly={readOnly}
            />
            <Box
              sx={{
                display: "grid",
                gridTemplateColumns: {
                  xs: "1fr",
                  lg: "minmax(0, 2fr) minmax(280px, 1fr)",
                },
                gap: 2,
                alignItems: "start",
              }}
            >
              <Scoreboard insights={insights} onFilter={onFilter} />
              <AgainstBar summary={summary} />
            </Box>
          </>
        )}
      </Stack>
    </HrefContext.Provider>
  );
}

InsightsTab.propTypes = {
  env: PropTypes.object,
  envState: PropTypes.object,
  tasks: PropTypes.array,
  run: PropTypes.object,
  goalOf: PropTypes.func,
  tracing: PropTypes.object,
  candidate: PropTypes.bool,
  existingFixes: PropTypes.object,
  onFilter: PropTypes.func,
  onOpenTrace: PropTypes.func,
  onFix: PropTypes.func,
  onOpenFix: PropTypes.func,
  insights: PropTypes.object,
  summary: PropTypes.object,
  onSetBar: PropTypes.func,
  record: PropTypes.object,
  readOnly: PropTypes.bool,
  hrefFor: PropTypes.func,
  /* Every run of this environment (runSummaries) — the one before, for the
     change and the issues that are new or resolved. */
  history: PropTypes.array,
};

/* A clickable row or chip that is also a real link when read outside the app. */
const linkProps = (href, onClick) =>
  href
    ? {
        component: "a",
        href,
        onClick: (e) => {
          e.preventDefault();
          onClick();
        },
        style: { color: "inherit", textDecoration: "none" },
      }
    : { role: "button", onClick };

/* A piece of copy that filters the Scenarios tab, or jumps to an issue. */
function Linked({ part, onFilter, onJump, strong }) {
  const hrefFor = useContext(HrefContext);
  if (!part.ids && !part.issue) return <>{part.text}</>;
  const go = () =>
    part.issue
      ? onJump?.(`insight-issue-${part.issue}`)
      : onFilter?.(part.label || part.text, part.ids);
  const href =
    hrefFor && !part.issue
      ? hrefFor({ ids: part.ids, label: part.label || part.text })
      : null;
  return (
    <Box
      component={href ? "a" : "span"}
      role="link"
      tabIndex={0}
      {...(href && { href })}
      onClick={(e) => {
        if (href) e.preventDefault();
        go();
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter") go();
      }}
      sx={{
        /* A dotted underline: a paragraph with six links stays a paragraph. */
        color: "text.primary",
        fontWeight: strong ? 600 : 500,
        cursor: "pointer",
        textDecoration: "underline",
        textDecorationStyle: "dotted",
        textDecorationThickness: "1px",
        textDecorationColor: (t) => alpha(t.palette.text.primary, 0.35),
        textUnderlineOffset: "4px",
        "&:hover": {
          textDecorationStyle: "solid",
          textDecorationColor: "currentColor",
        },
      }}
    >
      {part.text}
    </Box>
  );
}
Linked.propTypes = {
  part: PropTypes.object,
  onFilter: PropTypes.func,
  onJump: PropTypes.func,
  strong: PropTypes.bool,
};

function SeverityLabel({ severity }) {
  const s = SEVERITY[severity] || SEVERITY.low;
  return (
    <Typography
      sx={{
        typography: "s3",
        fontWeight: s.weight,
        color: s.color,
        textTransform: "none",
        letterSpacing: 0.4,
        flexShrink: 0,
        width: 58,
      }}
    >
      {s.label}
    </Typography>
  );
}
SeverityLabel.propTypes = { severity: PropTypes.string };

/* ── goal & sub-goal scoreboard ─────────────────────────────────────────── */

/* A thin bar of what didn't pass: failed, mixed, inconclusive / not measured.
   The track is what passed. */
function DistBar({ total, failed = 0, mixed = 0, unsure = 0 }) {
  const w = (n) => `${total ? (n / total) * 100 : 0}%`;
  return (
    <Tooltip
      arrow
      title={
        [
          failed && `${failed} failed`,
          mixed && `${mixed} mixed`,
          unsure && `${unsure} inconclusive`,
        ]
          .filter(Boolean)
          .join(" · ") || "Nothing failed"
      }
    >
      <Stack
        direction="row"
        sx={{
          height: 6,
          borderRadius: "4px",
          overflow: "hidden",
          bgcolor: "background.neutral",
          width: "100%",
        }}
      >
        <Box sx={{ width: w(failed), bgcolor: "status.danger.solid" }} />
        <Box sx={{ width: w(mixed), bgcolor: "status.warning.solid" }} />
        <Box
          sx={{
            width: w(unsure),
            backgroundImage: (t) =>
              `repeating-linear-gradient(135deg, ${t.palette.text.disabled} 0 2px, transparent 2px 4px)`,
          }}
        />
      </Stack>
    </Tooltip>
  );
}
DistBar.propTypes = {
  total: PropTypes.number,
  failed: PropTypes.number,
  mixed: PropTypes.number,
  unsure: PropTypes.number,
};

const GRID = "minmax(0, 1fr) 84px 52px 72px 120px 140px";

function Scoreboard({ insights, onFilter }) {
  /* Goals are use cases, often a handful of scenarios each — when most are
     under the minimum sample, open on the sub-goals across the run, which
     have one. */
  const rated = insights.goals.filter((g) => !g.lowVolume).length;
  const thin = insights.goals.length > 0 && rated < insights.goals.length / 3;
  const [view, setView] = useState(
    thin && insights.subGoals.some((g) => !g.lowVolume) ? "subgoals" : "goals",
  );
  const [open, setOpen] = useState(
    () => new Set(insights.goals.slice(0, 1).map((g) => g.goal)),
  );
  const [all, setAll] = useState(false);
  const goals = all ? insights.goals : insights.goals.slice(0, GOALS_SHOWN);
  const toggle = (g) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(g)) next.delete(g);
      else next.add(g);
      return next;
    });

  const pctCell = (row) =>
    row.lowVolume ? (
      <Tooltip
        arrow
        title={`${row.measured} measured — a rate needs at least ${MIN_MEASURED}.`}
      >
        <Typography
          sx={{ typography: "s3", color: "text.tertiary", textAlign: "right" }}
        >
          Not enough data yet
        </Typography>
      </Tooltip>
    ) : (
      <Typography sx={cellNum}>{`${row.pct}%`}</Typography>
    );

  const subRow = (sg, goal) => (
    <Box
      key={`${goal || "run"}:${sg.id}`}
      role="button"
      tabIndex={0}
      onClick={() => onFilter(`Sub-goal · ${sg.text}`, sg.ids)}
      onKeyDown={(e) => {
        if (e.key === "Enter") onFilter(`Sub-goal · ${sg.text}`, sg.ids);
      }}
      sx={{
        display: "grid",
        gridTemplateColumns: GRID,
        columnGap: 2,
        alignItems: "center",
        px: 2.5,
        py: 0.75,
        cursor: "pointer",
        borderBottom: "1px solid",
        borderColor: "divider",
        "&:hover": { bgcolor: "action.hover" },
      }}
    >
      <Typography
        noWrap
        sx={{
          typography: "s2",
          color: "text.secondary",
          pl: goal ? 3 : 0,
          minWidth: 0,
        }}
      >
        {sg.text}
      </Typography>
      <Typography
        sx={{
          typography: "s3",
          fontWeight: sg.critical ? 600 : 500,
          color: sg.critical ? "text.primary" : "text.tertiary",
        }}
      >
        {sg.critical ? "Critical" : "Supporting"}
      </Typography>
      <Typography sx={{ ...cellNum, fontWeight: 500 }}>{sg.met}</Typography>
      <Typography sx={{ ...cellNum, fontWeight: 500, color: "text.tertiary" }}>
        {sg.measured}
      </Typography>
      {pctCell(sg)}
      <DistBar
        total={sg.measured}
        failed={sg.failed}
        unsure={sg.inconclusive}
      />
    </Box>
  );

  return (
    <SectionCard
      title="Goals & sub-goals"
      subtitle="Worst first. Click a row to see its scenarios."
      action={
        <SegmentedTabs value={view} onChange={(_, v) => setView(v)}>
          <Tab value="goals" label="By goal" />
          <Tab value="subgoals" label="Sub-goals across the run" />
        </SegmentedTabs>
      }
    >
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: GRID,
          columnGap: 2,
          px: 2.5,
          py: 1.125,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Typography sx={tableHead}>
          {view === "goals" ? "Goal · sub-goal" : "Sub-goal"}
        </Typography>
        <Typography sx={tableHead}>Mark</Typography>
        <Tooltip
          arrow
          title={
            view === "goals"
              ? "Scenarios that passed every repeat"
              : "Scenarios where the sub-goal was met"
          }
        >
          <Typography sx={{ ...tableHead, textAlign: "right" }}>
            {view === "goals" ? "Pass" : "Met"}
          </Typography>
        </Tooltip>
        <Typography sx={{ ...tableHead, textAlign: "right" }}>
          Measured
        </Typography>
        <Typography sx={{ ...tableHead, textAlign: "right" }}>%</Typography>
        <Stack direction="row" spacing={1} alignItems="center">
          <Typography sx={tableHead}>Didn&apos;t pass</Typography>
        </Stack>
      </Box>

      {view === "goals" && thin && (
        <Typography
          sx={{
            px: 2.5,
            py: 1,
            typography: "s3",
            color: "text.tertiary",
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          {`${insights.goals.length - rated} of ${insights.goals.length} goals have fewer than ${MIN_MEASURED} measured scenarios on this run — read the fractions, not a rate.`}
        </Typography>
      )}
      {view === "goals" &&
        goals.map((g) => {
          const isOpen = open.has(g.goal);
          return (
            <React.Fragment key={g.goal}>
              <Box
                role="button"
                tabIndex={0}
                onClick={() => onFilter(`Goal · ${g.goal}`, g.ids)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") onFilter(`Goal · ${g.goal}`, g.ids);
                }}
                sx={{
                  display: "grid",
                  gridTemplateColumns: GRID,
                  columnGap: 2,
                  alignItems: "center",
                  px: 2.5,
                  py: 1,
                  cursor: "pointer",
                  borderBottom: "1px solid",
                  borderColor: "divider",
                  "&:hover": { bgcolor: "action.hover" },
                }}
              >
                <Stack
                  direction="row"
                  alignItems="center"
                  spacing={0.75}
                  minWidth={0}
                >
                  <Box
                    aria-label={isOpen ? "Hide sub-goals" : "Show sub-goals"}
                    {...clickable(
                      (e) => {
                        e.stopPropagation();
                        toggle(g.goal);
                      },
                      { expanded: isOpen },
                    )}
                    sx={{
                      display: "grid",
                      placeItems: "center",
                      width: 18,
                      height: 18,
                      borderRadius: "4px",
                      flexShrink: 0,
                      "&:hover": { bgcolor: "action.selected" },
                      ...focusRing,
                    }}
                  >
                    <Iconify
                      icon={
                        isOpen
                          ? "solar:alt-arrow-down-linear"
                          : "solar:alt-arrow-right-linear"
                      }
                      width={12}
                      sx={{ color: "text.tertiary" }}
                    />
                  </Box>
                  <Typography
                    noWrap
                    sx={{ typography: "s2", fontWeight: 600, minWidth: 0 }}
                  >
                    {g.goal}
                  </Typography>
                </Stack>
                <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                  {`${g.subGoals.length} sub-goal${g.subGoals.length === 1 ? "" : "s"}`}
                </Typography>
                <Typography sx={cellNum}>{g.passed}</Typography>
                <Typography sx={{ ...cellNum, color: "text.tertiary" }}>
                  {g.measured}
                </Typography>
                {g.measured ? (
                  pctCell(g)
                ) : (
                  <Typography
                    sx={{
                      typography: "s3",
                      color: "text.tertiary",
                      textAlign: "right",
                    }}
                  >
                    Not measured
                  </Typography>
                )}
                <DistBar
                  total={g.measured + g.unmeasured}
                  failed={g.failed}
                  mixed={g.mixed}
                  unsure={g.unmeasured}
                />
              </Box>
              {isOpen && g.subGoals.map((sg) => subRow(sg, g.goal))}
            </React.Fragment>
          );
        })}
      {view === "goals" && insights.goals.length > GOALS_SHOWN && (
        <Box sx={{ px: 2.5, py: 1 }}>
          <Button
            size="small"
            aria-expanded={all}
            onClick={() => setAll((v) => !v)}
            sx={{ color: "text.secondary" }}
          >
            {all
              ? "Show the worst 8"
              : `Show all ${insights.goals.length} goals`}
          </Button>
        </Box>
      )}

      {view === "subgoals" && insights.subGoals.map((sg) => subRow(sg, null))}
      {view === "subgoals" && !insights.subGoals.length && (
        <Typography
          sx={{ px: 2.5, py: 2, typography: "s2", color: "text.tertiary" }}
        >
          No sub-goals were graded on this run.
        </Typography>
      )}
    </SectionCard>
  );
}
Scoreboard.propTypes = { insights: PropTypes.object, onFilter: PropTypes.func };

/* ── issues ─────────────────────────────────────────────────────────────── */

/* ── the issues to fix first ───────────────────────────────────────────── */

/* The top three issues under the verdict — each with its Fix and the calls
   behind it. The rest, and each issue's evidence, are in the details. */
function TopIssues({
  insights,
  run,
  fixCap,
  tracing,
  candidate,
  existingFixes,
  onFix,
  onOpenFix,
  onFilter,
  onMore,
  readOnly,
}) {
  const { issues } = insights;
  const shown = issues.slice(0, 3);
  const repeats = run?.repeats || 1;
  return (
    <Box>
      {repeats < 3 && (
        <Stack
          direction="row"
          spacing={1}
          alignItems="center"
          sx={{
            px: 2,
            py: 1,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Iconify
            icon="solar:info-circle-linear"
            width={14}
            sx={{ color: "text.tertiary", flexShrink: 0 }}
          />
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            {`${repeats} ${repeats === 1 ? "repeat" : "repeats"} per scenario — run 3 or more for a decision-grade read.`}
          </Typography>
        </Stack>
      )}
      {shown.map((i, k) => (
        <Stack
          key={i.id}
          direction="row"
          alignItems="center"
          spacing={1.5}
          sx={{
            px: 2,
            py: 1,
            borderBottom:
              k < shown.length - 1 || issues.length > shown.length
                ? "1px solid"
                : "none",
            borderColor: "divider",
          }}
        >
          <Box
            sx={{
              width: 22,
              height: 22,
              borderRadius: "4px",
              border: "1px solid",
              borderColor: "divider",
              display: "grid",
              placeItems: "center",
              flexShrink: 0,
              typography: "s3",
              fontWeight: 600,
              color: "text.secondary",
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {k + 1}
          </Box>
          <Box flex={1} minWidth={0}>
            <Typography noWrap sx={{ typography: "s2_1", fontWeight: 500 }}>
              {i.title}
            </Typography>
            <Typography
              noWrap
              sx={{ typography: "s3", color: "text.tertiary" }}
            >
              {[
                `${i.members.length} ${i.members.length === 1 ? "scenario" : "scenarios"}`,
                i.blockers ? `${i.blockers} critical` : null,
                i.mixed ? `${i.mixed} mixed` : null,
                `fix in ${String(i.fix.layer).toLowerCase()}`,
              ]
                .filter(Boolean)
                .join(" · ")}
            </Typography>
          </Box>
          <Button
            size="small"
            variant="outlined"
            onClick={() => onFilter?.(i.title, i.memberIds)}
            sx={{
              borderColor: "divider",
              color: "text.primary",
              flexShrink: 0,
              whiteSpace: "nowrap",
            }}
          >
            See calls
          </Button>
          {!readOnly && (
            <RunFixButton
              issue={i}
              tracing={tracing}
              candidate={candidate}
              existing={existingFixes?.[i.id] || null}
              onFix={onFix}
              onOpenFix={onOpenFix}
              primary={k === 0}
              label={fixCap ? `Fix · up to ${creditsLabel(fixCap)}` : "Fix"}
            />
          )}
        </Stack>
      ))}
      {issues.length > shown.length && (
        <Typography
          role="button"
          tabIndex={0}
          onClick={onMore}
          onKeyDown={(e) => {
            if (e.key === "Enter") onMore();
          }}
          sx={{
            px: 2,
            py: 1,
            typography: "s3",
            fontWeight: 600,
            color: "text.tertiary",
            cursor: "pointer",
            "&:hover": { color: "text.primary" },
          }}
        >
          {`${issues.length - shown.length} more ${issues.length - shown.length === 1 ? "issue" : "issues"} in the details`}
        </Typography>
      )}
    </Box>
  );
}
TopIssues.propTypes = {
  insights: PropTypes.object,
  run: PropTypes.object,
  fixCap: PropTypes.number,
  tracing: PropTypes.object,
  candidate: PropTypes.bool,
  existingFixes: PropTypes.object,
  onFix: PropTypes.func,
  onOpenFix: PropTypes.func,
  onFilter: PropTypes.func,
  onMore: PropTypes.func,
  readOnly: PropTypes.bool,
};

/* Where the run stands against the bar you set. A report — the harness
   never blocks or ships a release (PRD v7 T58). */
function AgainstBar({ summary }) {
  if (!summary?.scored) return null;
  const barPct = Math.round(summary.bar.pass1 * 100);
  const notMeasured = ["inconclusive", "test-invalid", "error"].map((o) => [
    o,
    summary.outcomes?.[o] || 0,
  ]);
  const rows = [
    ["Passed", `${summary.pass1Pct}% against ${barPct}%`],
    ["Scored", `${summary.passed} of ${summary.scored} passed`],
    [
      "Critical",
      summary.critical?.total
        ? `${summary.critical.failed.length} of ${summary.critical.total} didn't pass`
        : "None marked",
    ],
  ];
  return (
    <SectionCard
      title="Against your bar"
      subtitle="It colours this report. It never blocks a release — that call is yours."
    >
      <Box sx={{ px: 2.5, py: 1.5 }}>
        <Typography
          sx={{
            typography: "m3",
            fontWeight: 600,
            color: summary.belowBar ? "status.danger.fg" : "text.primary",
          }}
        >
          {summary.belowBar ? "Under the bar" : "Clears the bar"}
        </Typography>
      </Box>
      {rows.map(([k, v]) => (
        <Stack
          key={k}
          direction="row"
          sx={{
            px: 2.5,
            py: 0.875,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <Typography
            sx={{
              typography: "s2",
              color: "text.tertiary",
              width: 96,
              flexShrink: 0,
            }}
          >
            {k}
          </Typography>
          <Typography
            sx={{ typography: "s2", fontVariantNumeric: "tabular-nums" }}
          >
            {v}
          </Typography>
        </Stack>
      ))}
      <Box
        sx={{ px: 2.5, py: 1, borderTop: "1px solid", borderColor: "divider" }}
      >
        <Typography sx={{ typography: "s3", color: "text.tertiary", mb: 0.5 }}>
          Not measured · left out of the rate
        </Typography>
        {notMeasured.map(([o, n]) => (
          <Tooltip key={o} arrow placement="left" title={OUTCOMES[o].note}>
            <Stack direction="row" sx={{ py: 0.25, cursor: "default" }}>
              <Typography
                sx={{ typography: "s2", color: "text.secondary", flex: 1 }}
              >
                {OUTCOMES[o].label}
              </Typography>
              <Typography
                sx={{
                  typography: "s2",
                  color: n ? "text.primary" : "text.tertiary",
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {n}
              </Typography>
            </Stack>
          </Tooltip>
        ))}
      </Box>
    </SectionCard>
  );
}
AgainstBar.propTypes = { summary: PropTypes.object };

/* The issue table's columns: open · severity · issue · scenarios · impact · fix in · action. */
const ISSUE_GRID = "20px 64px minmax(0, 1fr) 84px 120px 120px 112px";
/* The same quiet header the Goals & sub-goals table uses. */
const th = tableHead;

function Issues({
  insights,
  byId,
  tracing,
  candidate,
  existingFixes,
  onFilter,
  onOpenTrace,
  onFix,
  onOpenFix,
  openId,
  lifecycle = null,
  readOnly,
}) {
  const [view, setView] = useState("all");
  const counts = lifecycle
    ? {
        new: insights.issues.filter((i) => lifecycle.status[i.id] === "new")
          .length,
        recurring: insights.issues.filter(
          (i) => lifecycle.status[i.id] === "recurring",
        ).length,
        resolved: lifecycle.resolved.length,
      }
    : null;
  const issues =
    !lifecycle || view === "all" || view === "resolved"
      ? view === "resolved"
        ? []
        : insights.issues
      : insights.issues.filter((i) => lifecycle.status[i.id] === view);
  const nonPassing = insights.failed + insights.mixed;
  const covered = issues.reduce((n, i) => n + i.members.length, 0);
  return (
    <SectionCard
      title="What broke"
      action={
        lifecycle && (
          <SegmentedTabs value={view} onChange={(_, v) => setView(v)}>
            <Tab value="all" label={`All ${insights.issues.length}`} />
            <Tab value="new" label={`New ${counts.new}`} />
            <Tab value="recurring" label={`Recurring ${counts.recurring}`} />
            <Tab value="resolved" label={`Resolved ${counts.resolved}`} />
          </SegmentedTabs>
        )
      }
      subtitle={
        view === "resolved" && lifecycle
          ? `In Run ${lifecycle.prevOrdinal}, not seen in this one`
          : issues.length
            ? `${issues.length} ${issues.length === 1 ? "issue" : "issues"} · ${covered} of ${nonPassing} non-passing scenarios · ranked by impact`
            : "Each failing scenario counts once, under its root failure."
      }
    >
      {view === "resolved" && lifecycle ? (
        lifecycle.resolved.length ? (
          lifecycle.resolved.map((i) => (
            <Stack
              key={i.id}
              direction="row"
              alignItems="center"
              spacing={1.5}
              sx={{
                px: 2,
                minHeight: 44,
                borderTop: "1px solid",
                borderColor: "divider",
              }}
            >
              <Iconify
                icon="solar:check-circle-linear"
                width={16}
                sx={{ color: "text.secondary", flexShrink: 0 }}
              />
              <Typography
                noWrap
                sx={{
                  typography: "s2_1",
                  fontWeight: 500,
                  flex: 1,
                  minWidth: 0,
                }}
              >
                {i.title}
              </Typography>
              <Typography
                sx={{ typography: "s3", color: "text.tertiary", flexShrink: 0 }}
              >
                {`${i.members.length} ${i.members.length === 1 ? "scenario" : "scenarios"} in Run ${lifecycle.prevOrdinal} · none here`}
              </Typography>
            </Stack>
          ))
        ) : (
          <Typography
            sx={{ px: 2.5, py: 2, typography: "s2", color: "text.tertiary" }}
          >{`Every issue from Run ${lifecycle.prevOrdinal} is still here.`}</Typography>
        )
      ) : !issues.length ? (
        <Typography
          sx={{ px: 2.5, py: 2, typography: "s2", color: "text.tertiary" }}
        >
          {view !== "all"
            ? `No ${view} issues against Run ${lifecycle?.prevOrdinal}.`
            : nonPassing
              ? "No failure here shares a cause with another — see the scenarios one by one."
              : "Nothing failed on this run."}
        </Typography>
      ) : (
        <Box sx={{ overflowX: "auto" }}>
          <Box sx={{ minWidth: 760 }}>
            <Box
              sx={{
                display: "grid",
                gridTemplateColumns: ISSUE_GRID,
                columnGap: 1.5,
                alignItems: "center",
                px: 2,
                py: 0.875,
                borderBottom: "1px solid",
                borderColor: "divider",
              }}
            >
              <span />
              <Typography sx={th}>Severity</Typography>
              <Typography sx={th}>Issue</Typography>
              <Typography sx={{ ...th, textAlign: "right" }}>
                Scenarios
              </Typography>
              <Typography sx={th}>Impact</Typography>
              <Typography sx={th}>Fix in</Typography>
              <span />
            </Box>
            {issues.map((i) => (
              <IssueBlock
                key={i.id}
                issue={i}
                byId={byId}
                tracing={tracing}
                candidate={candidate}
                existing={existingFixes?.[i.id] || null}
                onFilter={onFilter}
                onOpenTrace={onOpenTrace}
                onFix={onFix}
                onOpenFix={onOpenFix}
                defaultOpen={openId ? openId === i.id : i.rank === 1}
                since={
                  lifecycle
                    ? lifecycle.status[i.id] === "new"
                      ? "New"
                      : `Since Run ${lifecycle.prevOrdinal}`
                    : null
                }
                readOnly={readOnly}
              />
            ))}
          </Box>
        </Box>
      )}
      {insights.unclustered.count > 0 && (
        <Typography
          sx={{
            px: 2,
            py: 1,
            typography: "s3",
            color: "text.tertiary",
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <Linked
            part={{
              text: `${insights.unclustered.count} more ${insights.unclustered.count === 1 ? "failure" : "failures"}`,
              ids: insights.unclustered.ids,
              label: "Unclustered failures",
            }}
            onFilter={onFilter}
          />
          {" with a cause seen only once — not an issue yet."}
        </Typography>
      )}
    </SectionCard>
  );
}
Issues.propTypes = {
  insights: PropTypes.object,
  byId: PropTypes.object,
  tracing: PropTypes.object,
  candidate: PropTypes.bool,
  existingFixes: PropTypes.object,
  onFilter: PropTypes.func,
  onOpenTrace: PropTypes.func,
  onFix: PropTypes.func,
  onOpenFix: PropTypes.func,
  openId: PropTypes.string,
  readOnly: PropTypes.bool,
  /* { prevOrdinal, status: { [issueId]: "new" | "recurring" }, resolved: [issue] } */
  lifecycle: PropTypes.object,
};

/* A small bordered label — the fix layer, a mark. */
function Tag({ children }) {
  return <Chip size="small" label={children} sx={{ maxWidth: "100%" }} />;
}
Tag.propTypes = { children: PropTypes.node };

function IssueBlock({
  issue,
  tracing,
  candidate,
  existing,
  onFilter,
  onOpenTrace,
  onFix,
  onOpenFix,
  defaultOpen = false,
  since = null,
  readOnly,
}) {
  const [open, setOpen] = useState(defaultOpen);
  /* Which failing call the evidence shows — stepped with Next failing call. */
  const [at, setAt] = useState(0);
  const failing = issue.evidence.failing;
  const shownCall = failing[Math.min(at, failing.length - 1)] || null;
  const toggle = () => setOpen((o) => !o);
  const impact =
    [
      issue.blockers ? `${issue.blockers} critical` : null,
      issue.mixed ? `${issue.mixed} mixed` : null,
    ]
      .filter(Boolean)
      .join(" · ") || "—";
  const stop = (e) => e.stopPropagation();
  return (
    <Box
      id={`insight-issue-${issue.id}`}
      sx={{
        borderBottom: "1px solid",
        borderColor: "divider",
        "&:last-of-type": { borderBottom: "none" },
        scrollMarginTop: 16,
        breakInside: "avoid",
      }}
    >
      <Box
        role="button"
        tabIndex={0}
        aria-expanded={open}
        onClick={toggle}
        onKeyDown={(e) => {
          if (e.key === "Enter") toggle();
        }}
        sx={{
          display: "grid",
          gridTemplateColumns: ISSUE_GRID,
          columnGap: 1.5,
          alignItems: "center",
          px: 2,
          minHeight: 48,
          cursor: "pointer",
          bgcolor: open ? "background.neutral" : "transparent",
          "&:hover": { bgcolor: "action.hover" },
        }}
      >
        <Iconify
          icon={
            open
              ? "solar:alt-arrow-down-linear"
              : "solar:alt-arrow-right-linear"
          }
          width={14}
          sx={{ color: "text.tertiary" }}
        />
        <SeverityLabel severity={issue.severity} />
        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          sx={{ minWidth: 0 }}
        >
          <Tooltip
            arrow
            title={issue.title}
            placement="top-start"
            enterDelay={400}
          >
            <Typography
              noWrap
              sx={{ typography: "s2", fontWeight: 600, minWidth: 0 }}
            >
              {issue.title}
            </Typography>
          </Tooltip>
          {since && (
            <Box sx={{ flexShrink: 0 }}>
              <Tag>{since}</Tag>
            </Box>
          )}
        </Stack>
        <Typography
          component="div"
          sx={{
            typography: "s2",
            textAlign: "right",
            fontVariantNumeric: "tabular-nums",
          }}
          onClick={stop}
        >
          <Linked
            part={{
              text: `${issue.members.length}`,
              ids: issue.memberIds,
              label: issue.title,
            }}
            onFilter={onFilter}
          />
        </Typography>
        <Typography
          noWrap
          sx={{
            typography: "s2",
            color: issue.blockers ? "text.primary" : "text.tertiary",
          }}
        >
          {impact}
        </Typography>
        <Box sx={{ minWidth: 0 }}>
          <Tag>{issue.fix.layer}</Tag>
        </Box>
        <Box onClick={stop} sx={{ justifySelf: "end" }}>
          {!readOnly && (
            <RunFixButton
              issue={issue}
              tracing={tracing}
              candidate={candidate}
              existing={existing}
              onFix={onFix}
              onOpenFix={onOpenFix}
            />
          )}
        </Box>
      </Box>

      {open && (
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: {
              xs: "1fr",
              md: "minmax(0, 1fr) minmax(0, 1fr)",
            },
            borderTop: "1px solid",
            borderColor: "divider",
            bgcolor: "background.neutral",
          }}
        >
          {/* ── why it failed ── */}
          <Box
            sx={{
              px: 2,
              py: 1.75,
              pl: "56px",
              minWidth: 0,
              borderRight: { md: "1px solid" },
              borderColor: { md: "divider" },
            }}
          >
            <Typography sx={{ ...colHead, mb: 1 }}>Why it failed</Typography>
            <Stack
              direction="row"
              spacing={1}
              alignItems="baseline"
              sx={{ minWidth: 0 }}
            >
              <Typography
                sx={{ typography: "s2", fontWeight: 600, minWidth: 0 }}
              >
                {issue.root.label}
              </Typography>
              {issue.root.mark && (
                <Tag>
                  {issue.root.mark === "critical" ? "Critical" : "Supporting"}
                </Tag>
              )}
            </Stack>
            <Typography
              sx={{ typography: "s3", color: "text.tertiary", mt: 0.25 }}
            >
              {issue.root.note}
            </Typography>
            {issue.symptoms.length > 0 && (
              <Box
                sx={{
                  mt: 1.25,
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: 1,
                }}
              >
                {issue.symptoms.slice(0, 5).map((sym, k) => (
                  <Stack
                    key={sym.key}
                    direction="row"
                    alignItems="center"
                    sx={{
                      px: 1.25,
                      py: 0.625,
                      borderTop: k ? "1px solid" : "none",
                      borderColor: "divider",
                    }}
                  >
                    <Typography
                      noWrap
                      sx={{ typography: "s2", flex: 1, minWidth: 0 }}
                    >
                      <Linked
                        part={{
                          text: sym.label,
                          ids: sym.ids,
                          label: sym.label,
                        }}
                        onFilter={onFilter}
                      />
                    </Typography>
                    <Typography
                      sx={{
                        typography: "s3",
                        color: "text.tertiary",
                        fontVariantNumeric: "tabular-nums",
                        flexShrink: 0,
                      }}
                    >{`${sym.ids.length} of ${issue.members.length}`}</Typography>
                  </Stack>
                ))}
              </Box>
            )}
            <Typography sx={{ ...colHead, mt: 1.75, mb: 0.5 }}>
              Suggested fix
            </Typography>
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              {issue.fix.text}
            </Typography>
          </Box>

          {/* ── evidence: a failing call beside the nearest passing one ── */}
          <Box
            sx={{ px: 2, py: 1.75, minWidth: 0 }}
            id={`insight-evidence-${issue.id}`}
          >
            <Stack direction="row" alignItems="baseline" sx={{ mb: 1 }}>
              <Typography sx={{ ...colHead, flex: 1 }}>Evidence</Typography>
              <Typography component="div" sx={{ typography: "s3" }}>
                <Linked
                  part={{
                    text: `All ${issue.members.length} ${issue.members.length === 1 ? "scenario" : "scenarios"}`,
                    ids: issue.memberIds,
                    label: issue.title,
                  }}
                  onFilter={onFilter}
                />
              </Typography>
            </Stack>
            <Stack direction="row" alignItems="center" sx={{ mb: 0.5 }}>
              <Typography
                sx={{ typography: "s3", color: "text.tertiary", flex: 1 }}
              >
                {failing.length > 1
                  ? `Failing call ${Math.min(at, failing.length - 1) + 1} of ${failing.length}`
                  : "Failing call"}
              </Typography>
              {failing.length > 1 && (
                <Typography
                  role="button"
                  tabIndex={0}
                  onClick={() => setAt((x) => (x + 1) % failing.length)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter")
                      setAt((x) => (x + 1) % failing.length);
                  }}
                  sx={{
                    typography: "s3",
                    fontWeight: 600,
                    color: "text.secondary",
                    cursor: "pointer",
                    "&:hover": { color: "text.primary" },
                  }}
                >
                  Next failing call ›
                </Typography>
              )}
            </Stack>
            {shownCall && (
              <Box
                sx={{
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: "8px",
                  bgcolor: "background.paper",
                  overflow: "hidden",
                }}
              >
                <CallRow task={shownCall} onOpenTrace={onOpenTrace} />
                <FailureMoment task={shownCall} rootLabel={issue.root.label} />
              </Box>
            )}
            <Typography
              sx={{
                typography: "s3",
                color: "text.tertiary",
                mt: 1.25,
                mb: 0.5,
              }}
            >
              Nearest passing
            </Typography>
            {issue.evidence.passing ? (
              <Box
                sx={{
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: "8px",
                  bgcolor: "background.paper",
                }}
              >
                <CallRow
                  task={issue.evidence.passing}
                  onOpenTrace={onOpenTrace}
                  passing
                />
              </Box>
            ) : (
              <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
                No passing call of a similar scenario in this run.
              </Typography>
            )}
            {issue.evidence.factors.length > 0 && (
              <>
                <Typography
                  sx={{
                    typography: "s3",
                    color: "text.tertiary",
                    mt: 1.25,
                    mb: 0.5,
                  }}
                >
                  What differs
                </Typography>
                <Stack component="ul" spacing={0.25} sx={{ m: 0, pl: 2.25 }}>
                  {issue.evidence.factors.map((f) => (
                    <Typography
                      key={f}
                      component="li"
                      sx={{ typography: "s2", color: "text.secondary" }}
                    >
                      {f.charAt(0).toUpperCase() + f.slice(1)}
                    </Typography>
                  ))}
                </Stack>
              </>
            )}
          </Box>
        </Box>
      )}
    </Box>
  );
}
IssueBlock.propTypes = {
  issue: PropTypes.object,
  tracing: PropTypes.object,
  candidate: PropTypes.bool,
  existing: PropTypes.object,
  onFilter: PropTypes.func,
  onOpenTrace: PropTypes.func,
  onFix: PropTypes.func,
  onOpenFix: PropTypes.func,
  defaultOpen: PropTypes.bool,
  readOnly: PropTypes.bool,
  /* "New" or "Since Run N" — against the run before. */
  since: PropTypes.string,
};

/* The moment it went wrong: the failing turn with the turns around it, so
   the failure reads without opening the call. */
function FailureMoment({ task, rootLabel }) {
  const steps = task.steps || [];
  if (task.failStep == null || !steps.length) return null;
  const at = Math.min(task.failStep, steps.length - 1);
  const from = Math.max(0, at - 2);
  const to = Math.min(steps.length, at + 2);
  const who = (st) =>
    st.role === "agent"
      ? "Agent"
      : st.role === "actor"
        ? st.actorName || "Third party"
        : task.persona?.name || "Caller";
  return (
    <Box sx={{ borderTop: "1px solid", borderColor: "divider" }}>
      <Stack
        direction="row"
        alignItems="baseline"
        spacing={1}
        sx={{ px: 1.25, pt: 1, pb: 0.5 }}
      >
        <Typography
          noWrap
          sx={{
            typography: "s3",
            fontWeight: 600,
            color: "status.danger.fg",
            minWidth: 0,
          }}
        >{`Failed: ${rootLabel}`}</Typography>
        <Typography
          sx={{
            typography: "s3",
            color: "text.tertiary",
            flexShrink: 0,
            ml: "auto !important",
          }}
        >{`turn ${at + 1} of ${steps.length}`}</Typography>
      </Stack>
      {steps.slice(from, to).map((st, k) => {
        const i = from + k;
        const bad = i === at;
        return (
          <Stack
            key={i}
            direction="row"
            spacing={1.25}
            sx={{
              px: 1.25,
              py: 0.625,
              bgcolor: (t) =>
                bad ? t.palette.status.danger.bg : "transparent",
            }}
          >
            <Typography
              noWrap
              sx={{
                typography: "s3",
                fontWeight: bad ? 600 : 500,
                color: bad ? "text.primary" : "text.tertiary",
                width: 84,
                flexShrink: 0,
              }}
            >
              {who(st)}
            </Typography>
            <Typography
              sx={{
                typography: "s2",
                color: bad ? "text.primary" : "text.secondary",
                minWidth: 0,
                display: "-webkit-box",
                WebkitLineClamp: 2,
                WebkitBoxOrient: "vertical",
                overflow: "hidden",
              }}
            >
              {st.text || "—"}
            </Typography>
          </Stack>
        );
      })}
    </Box>
  );
}
FailureMoment.propTypes = {
  task: PropTypes.object,
  rootLabel: PropTypes.string,
};

/*
  Fix on a run issue — the Error Feed's Fix button, gated the same way: it
  needs trace evidence. Fix runs Future AGI's engine; dispatching an issue to
  a cloud coding agent is Phase 3 (RSI PRD), so there's no caret for it.
*/
function RunFixButton({
  issue,
  tracing,
  candidate,
  existing,
  onFix,
  onOpenFix,
  label = "Fix",
  primary = false,
}) {
  if (existing) {
    return (
      <Button
        size="small"
        variant="outlined"
        onClick={() => onOpenFix(existing.id)}
        startIcon={<Iconify icon="solar:toolbox-linear" width={14} />}
        sx={{
          flexShrink: 0,
          whiteSpace: "nowrap",
          borderColor: "divider",
          color: "text.primary",
        }}
      >
        Open fix
      </Button>
    );
  }
  const blocked = candidate || !tracing?.traced;
  const why = candidate
    ? "Improvement runs aren't fixed from here — open the improvement that made this run."
    : !tracing?.traced
      ? tracing?.reason
      : "";
  return (
    <Tooltip arrow title={why || ""}>
      <span>
        {/* Outlined in a table — one filled button per row reads as noise;
            only the issue to fix first gets the filled one. */}
        <Button
          size="small"
          variant={primary ? "contained" : "outlined"}
          color={primary ? "primary" : "inherit"}
          disabled={blocked}
          startIcon={<Iconify icon="solar:toolbox-linear" width={14} />}
          onClick={() => onFix(issue)}
          sx={{
            flexShrink: 0,
            whiteSpace: "nowrap",
            ...(primary
              ? {}
              : {
                  borderColor: "divider",
                  color: "text.primary",
                  "&:hover": { borderColor: "text.disabled" },
                }),
          }}
        >
          {label}
        </Button>
      </span>
    </Tooltip>
  );
}
RunFixButton.propTypes = {
  issue: PropTypes.object,
  tracing: PropTypes.object,
  candidate: PropTypes.bool,
  existing: PropTypes.object,
  onFix: PropTypes.func,
  onOpenFix: PropTypes.func,
  label: PropTypes.string,
  primary: PropTypes.bool,
};

/* ── evidence ───────────────────────────────────────────────────────────── */

function CallRow({ task, onOpenTrace, passing }) {
  const hrefFor = useContext(HrefContext);
  const temp = temperamentOf(task.persona);
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1}
      tabIndex={0}
      {...linkProps(hrefFor?.({ trace: task.id }), () => onOpenTrace(task))}
      onKeyDown={(e) => {
        if (e.key === "Enter") onOpenTrace(task);
      }}
      sx={{
        px: 1.25,
        py: 0.75,
        cursor: "pointer",
        borderBottom: "1px solid",
        borderColor: "divider",
        "&:last-of-type": { borderBottom: "none" },
        "&:hover": { bgcolor: "action.hover" },
      }}
    >
      <StatusDot
        status={task.status === "error" ? "failed" : task.status}
        size={6}
      />
      <Box flex={1} minWidth={0}>
        <Typography noWrap sx={{ typography: "s2", fontWeight: 500 }}>
          {scenarioTitle(task.title) || task.name || task.id}
        </Typography>
        <Typography noWrap sx={{ typography: "s3", color: "text.tertiary" }}>
          {[
            task.persona?.name,
            temp,
            `${(task.steps || []).length} turns`,
            task.actor ? `with ${task.actor.name}` : null,
          ]
            .filter(Boolean)
            .join(" · ")}
        </Typography>
      </Box>
      <Typography
        sx={{
          typography: "s3",
          fontWeight: 600,
          color: "text.tertiary",
          flexShrink: 0,
          display: "inline-flex",
          alignItems: "center",
          gap: 0.25,
        }}
      >
        {passing ? "Trace" : "Trace at fault"}
        <Iconify icon="solar:alt-arrow-right-linear" width={12} />
      </Typography>
    </Stack>
  );
}
CallRow.propTypes = {
  task: PropTypes.object,
  onOpenTrace: PropTypes.func,
  passing: PropTypes.bool,
};
