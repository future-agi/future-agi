import PropTypes from "prop-types";
import { isCredentialsVersion } from "../_mock/versions";
import { isGitAgent, versionNameOf } from "../_mock/codeRefs";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { paths } from "src/routes/paths";
import { mockClustersForProject } from "src/api/errorFeed/mockAdapter";
import { useIntegrations } from "../_mock/integrations";
import { fixStatusOf, issueFixNav } from "../errorFeed/issueFixModel";
import { Box, Button, MenuItem, Select, Stack, Typography } from "@mui/material";
import { alpha, useTheme } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import ReactApexChart from "../components/SafeApexChart";
import { useOptionalSimStore } from "../store";
import { runIdentityOf } from "../_mock/runIdentity";
import { agentStatusOf } from "../_mock/rsiModel";
import { MONO, ago, passRate, plural, showsSetup } from "./agentViewUtils";
import { QuietTag } from "./AgentBits";
import { clickable, focusRing } from "../components/clickable";
import FitDataTable from "../components/FitDataTable";

/* Colour is reserved for outcomes — pass rates, deltas, errors. Structure
   (charts, the anatomy, usage bars) stays neutral. */
const NEUTRAL = (t, a = 0.32) => alpha(t.palette.text.primary, a);
/* Good and bad earn colour; the middle reads plain (no amber text). */
/* Run bars stay neutral unless the run went badly. */
const NEUTRAL_BAR = "rgba(161,161,170,0.75)";
const rateTone = (r) => (r == null ? "text.disabled" : r >= 80 ? "#16A34A" : r >= 50 ? "text.primary" : "#DC2626");

/**
 * The agent at a glance — how it's doing, nothing more: its worst open
 * production issues, then the latest runs. Pass rate by version lives on Versions;
 * real calls live in its Observe project.
 */
export default function AgentOverviewTab({ agent, runs, inProgress, production, onOpenRun, fresh = false }) {
  /* Just connected, nothing set up: the Next steps card above is the page. */
  if (fresh) return null;
  return (
    <Stack spacing={2.5}>
      <AgentHealth agent={agent} production={production} />
      {/* No environment and no runs while Next steps is up: its "Build an
          environment" already says it — an empty list would repeat it. */}
      {!(showsSetup(agent) && !agent.usedIn.length && !(runs || []).length && !(inProgress || []).length) && (
        <RecentRuns agent={agent} runs={runs} inProgress={inProgress || []} onOpenRun={onOpenRun} />
      )}
    </Stack>
  );
}
AgentOverviewTab.propTypes = {
  agent: PropTypes.object, source: PropTypes.object, files: PropTypes.array, runs: PropTypes.array, inProgress: PropTypes.array,
  scopedRuns: PropTypes.array, statsOf: PropTypes.func, production: PropTypes.object, improvements: PropTypes.object,
  scopeEnv: PropTypes.object, scopeEnvVersion: PropTypes.string, scopes: PropTypes.array,
  onScopeEnv: PropTypes.func, onSwitch: PropTypes.func, onOpenRun: PropTypes.func, onTab: PropTypes.func,
  fresh: PropTypes.bool,
};

/* ── how it's doing in production, and what needs you ─────────────────────── */

/* Worst first, then the most traces. */
const SEVERITY_RANK = { critical: 0, high: 1, medium: 2, low: 3 };
/* Issues the Overview lists before "All in Error Feed". */
const TOP_ISSUES = 3;
const SEVERITY_DOT = { critical: "#DC2626", high: "#D97706" };

/*
  How it's doing in production: its worst open Error Feed issues, each row
  opening its fix, or the issue when there's none yet. Without an Observe project there's no production to read — it says
  Fix is off and how to turn it on, instead of empty numbers.
*/
function AgentHealth({ agent, production }) {
  const navigate = useNavigate();
  const store = useOptionalSimStore();
  const { prs } = useIntegrations();
  const prod = production || {};
  /* Linked to a new project that has had no trace yet — nothing in Observe to open. */
  const awaitingTrace = !!prod.project && !!prod.pending;
  const nm = (l) => versionNameOf(agent, l).main;

  /* Its open issues, from the Observe project its traces land in. */
  const projectId = prod.project?.project || prod.project?.id || null;
  const issues = useMemo(() => mockClustersForProject(projectId)
    .filter((c) => c.status !== "resolved")
    .map((c) => ({ issue: c, fix: store?.state ? fixStatusOf(c, store.state, prs || []) : null }))
    .sort((a, b) => (SEVERITY_RANK[a.issue.severity] ?? 9) - (SEVERITY_RANK[b.issue.severity] ?? 9) || (b.issue.trace_count || 0) - (a.issue.trace_count || 0)),
  [projectId, store?.state, prs]);
  const critical = issues.some((x) => x.issue.severity === "critical");

  const openIssue = (x) => {
    if (!x.fix?.envId) { navigate(paths.dashboard.errorFeed.detail(x.issue.cluster_id)); return; }
    const [href, opts] = issueFixNav(x.fix, x.issue.cluster_id);
    navigate(href, opts);
  };

  const columns = [
    {
      id: "issue", header: "Issue", meta: { flex: 1 }, minSize: 280,
      cell: ({ row: { original: x } }) => (
        <Typography noWrap title={x.issue.error?.name} sx={{ typography: "s2", fontWeight: 600 }}>{x.issue.error?.name}</Typography>
      ),
    },
    { id: "id", header: "ID", size: 104, cell: ({ row: { original: x } }) => <Typography noWrap sx={{ typography: "s3", fontFamily: MONO, color: "text.secondary" }}>{x.issue.cluster_id}</Typography> },
    {
      id: "severity", header: "Severity", size: 112,
      cell: ({ row: { original: x } }) => (
        <Stack direction="row" alignItems="center" spacing={0.75}>
          <Box sx={{ width: 6, height: 6, borderRadius: "50%", flexShrink: 0, bgcolor: SEVERITY_DOT[x.issue.severity] || "text.disabled" }} />
          <Typography noWrap sx={{ typography: "s3", textTransform: "capitalize" }}>{x.issue.severity}</Typography>
        </Stack>
      ),
    },
    {
      id: "traces", header: "Traces", size: 88,
      cell: ({ row: { original: x } }) => <Typography sx={{ typography: "s3", fontVariantNumeric: "tabular-nums" }}>{(x.issue.trace_count || 0).toLocaleString()}</Typography>,
    },
    { id: "seen", header: "Last seen", size: 110, cell: ({ row: { original: x } }) => <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>{x.issue.last_seen ? ago(x.issue.last_seen) : "—"}</Typography> },
    {
      id: "fix", header: "Fix", size: 200,
      cell: ({ row: { original: x } }) => (
        <Typography noWrap sx={{ typography: "s3", fontWeight: x.fix?.key === "ready" ? 700 : 400, color: x.fix ? "text.primary" : "text.subtitle" }}>
          {x.fix?.label || "No fix yet"}
        </Typography>
      ),
    },
  ];

  return (
    <Stack spacing={2.5}>
      {/* ── production: the open issues its traces raised ── */}
      <Box>
        <Stack direction="row" alignItems="baseline" spacing={1} sx={{ mb: 0.5 }}>
          <Typography sx={{ typography: "s1", fontWeight: 700 }}>Production</Typography>
          <Typography noWrap sx={{ typography: "s3", color: "text.subtitle", flex: 1, minWidth: 0 }}>
            {awaitingTrace
              ? `${prod.project.name} · waiting for its first trace`
              : prod.project
              ? [prod.project.name, agent.production ? nm(agent.production) : null, issues.length ? `${plural(issues.length, "open issue")}${critical ? ` · ${issues.filter((x) => x.issue.severity === "critical").length} critical` : ""}` : "no open issues"].filter(Boolean).join(" · ")
              : "Not watched"}
          </Typography>
          {prod.project && !awaitingTrace && (
            /* The Error Feed, filtered to this project — Observe's own pages
               only know projects that really receive traces. */
            <Typography
              noWrap {...clickable(prod.onViewIssues)}
              sx={{ typography: "s3", fontWeight: 700, cursor: "pointer", flexShrink: 0, "&:hover": { textDecoration: "underline" }, ...focusRing }}
            >
              {issues.length > TOP_ISSUES ? `All ${issues.length} in Error Feed →` : "Open in Error Feed →"}
            </Typography>
          )}
        </Stack>
        {!prod.project ? (
          <Stack direction="row" alignItems="center" spacing={1.5} sx={{ px: 2, py: 1.25, border: "1px solid", borderColor: "divider", borderRadius: 1.5 }}>
            <Typography sx={{ typography: "s2", color: "text.secondary", flex: 1, minWidth: 0 }}>
              Fix is off for this agent — it sends no traces or call logs yet. Simulations still run and grade.
            </Typography>
            <Button size="small" variant="outlined" onClick={prod.onLink} sx={{ typography: "s3", fontWeight: 700, height: 28, color: "text.primary", borderColor: "divider", flexShrink: 0 }}>
              Link a project
            </Button>
          </Stack>
        ) : awaitingTrace ? (
          /* Linked but empty: nothing to watch until traceAI sends its first trace. */
          <Typography sx={{ py: 1.5, typography: "s2", color: "text.secondary" }}>
            No traces yet — Error Feed starts watching with the first one.
          </Typography>
        ) : issues.length ? (
          <FitDataTable columns={columns} data={issues.slice(0, TOP_ISSUES)} getRowId={(x) => x.issue.cluster_id} onRowClick={openIssue} />
        ) : (
          <Typography sx={{ py: 1.5, typography: "s2", color: "text.secondary" }}>
            Error Feed hasn&apos;t found an issue in its traces.
          </Typography>
        )}
      </Box>
    </Stack>
  );
}
AgentHealth.propTypes = { agent: PropTypes.object, production: PropTypes.object };

/* ── pass rate by version ─────────────────────────────────────────────────── */

/* Only the versions that ran here, and the latest — a row of "not run" gaps
   says nothing; one line says how many were skipped. */
export function PassRateCard({ agent, statsOf, scopeEnv, scopeEnvVersion, scopes, onScopeEnv, heldBack = null }) {
  const theme = useTheme();
  const [chartReady, setChartReady] = useState(false);
  useEffect(() => {
    const id = requestAnimationFrame(() => setChartReady(true));
    return () => cancelAnimationFrame(id);
  }, []);
  /* A credentials-only version behaves as its base, so it's measured with
     it — one bar "v5 · v5.1" — rather than as a version of its own. */
  const all = agent.versions
    .reduce((groups, v) => {
      const base = isCredentialsVersion(v) && groups.find((g) => g.labels.includes(v.sameAs || v.basedOnVersion));
      if (base) base.labels.push(v.label);
      else groups.push({ v, labels: [v.label] });
      return groups;
    }, [])
    /* Bars are named by version number, every agent alike — the commit is on the Commits tab. */
    .map((g) => ({ labels: g.labels, v: { ...g.v, label: g.labels.join(" · ") }, s: statsOf(g.labels.length === 1 ? g.labels[0] : g.labels) }));
  /* Bars for what ran, and the newest — for a code agent, its production
     commit (a fix on its own branch isn't the agent yet). */
  const series = all.filter((x, i) => x.s || (isGitAgent(agent) ? x.labels.includes(agent.production) : i === all.length - 1));
  const skipped = all.length - series.length;
  const head = [...series].reverse().find((x) => x.s);
  return (
    <Panel
      title="Pass rate by version"
      sx={{ display: "flex", flexDirection: "column", height: "100%" }}
      action={scopes.length > 1 ? (
        <Select
          size="small" value={scopeEnv?.envId || ""}
          onChange={(e) => onScopeEnv(e.target.value)}
          renderValue={(id) => `In ${scopes.find((u) => u.envId === id)?.envName || "—"}`}
          sx={{ height: 26, typography: "s3", "& .MuiSelect-select": { py: 0.25 } }}
        >
          {scopes.map((u) => <MenuItem key={u.envId} value={u.envId} sx={{ typography: "s2" }}>{u.envName}</MenuItem>)}
        </Select>
      ) : (
        <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>{scopeEnv ? `In ${scopeEnv.envName}${scopeEnvVersion ? ` · environment ${scopeEnvVersion}` : ""}` : "Runs of every scenario"}</Typography>
      )}
    >
      {head ? (
        <Box sx={{ flex: 1, minHeight: 220, px: 1, pt: 1.5, width: "100%", minWidth: 0 }}>
          <ReactApexChart
            /* Re-measured when the layout settles — the grid sizes this
               column after the chart's first paint. */
            key={`w-${series.length}-${chartReady ? 1 : 0}`}
            type="bar"
            height={210}
            width="100%"
            series={[{ name: "Pass rate", data: series.map((x) => (x.s ? x.s.rate : null)) }]}
            options={{
              chart: { toolbar: { show: false }, zoom: { enabled: false }, animations: { enabled: false }, fontFamily: theme.typography.fontFamily, background: "transparent", parentHeightOffset: 0 },
              theme: { mode: theme.palette.mode },
              /* Latest measured version reads strongest; earlier ones recede. */
              colors: [({ dataPointIndex }) => (series[dataPointIndex]?.v.label === head.v.label ? NEUTRAL(theme, 0.62) : NEUTRAL(theme, 0.22))],
              plotOptions: { bar: { columnWidth: `${Math.min(36, 10 * series.length)}%`, borderRadius: 4, borderRadiusApplication: "end", dataLabels: { position: "top" } } },
              dataLabels: {
                enabled: true, offsetY: -18,
                formatter: (v, o) => (series[o.dataPointIndex]?.s ? `${v}%` : ""),
                style: { fontSize: "11px", fontWeight: 600, colors: [theme.palette.text.secondary] },
              },
              legend: { show: false },
              grid: { borderColor: theme.palette.divider, strokeDashArray: 4, xaxis: { lines: { show: false } }, padding: { left: 8, right: 16, top: 0, bottom: 0 } },
              xaxis: {
                categories: series.map((x) => (x.s ? x.v.label : [x.v.label, "not run"])),
                axisBorder: { show: false }, axisTicks: { show: false },
                labels: { style: { colors: theme.palette.text.secondary, fontSize: "11px", fontFamily: MONO } },
                tooltip: { enabled: false },
              },
              yaxis: { min: 0, max: 100, tickAmount: 4, labels: { style: { colors: theme.palette.text.secondary, fontSize: "11px" }, formatter: (v) => `${Math.round(v)}` } },
              states: { hover: { filter: { type: "none" } } },
              tooltip: { y: { formatter: (v, o) => { const x = series[o?.dataPointIndex ?? -1]; return x?.s ? `${v}% · ${plural(x.s.runs, "run")}` : "Not run yet"; } } },
            }}
          />
        </Box>
      ) : (
        <Stack alignItems="center" justifyContent="center" spacing={0.75} sx={{ flex: 1, minHeight: 220, px: 3, textAlign: "center" }}>
          <Iconify icon="solar:chart-2-linear" width={26} sx={{ color: "text.disabled" }} />
          <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
            {heldBack
              /* Runs exist here, but none on every scenario of this version. */
              ? `No run yet on every scenario of environment ${scopeEnvVersion || "the current version"}. Earlier runs here ${{ older: "were on an older version", subset: "ran only some scenarios", "older-or-subset": "were on an older version or ran only some scenarios" }[heldBack]}.`
              : "Each version's pass rate shows here once it runs every scenario."}
          </Typography>
        </Stack>
      )}
      {head && skipped > 0 && (
        <Typography sx={{ px: 2, pb: 1.25, typography: "s3", color: "text.subtitle" }}>
          {`${plural(skipped, "version")} in between ${skipped === 1 ? "wasn't" : "weren't"} run here.`}
        </Typography>
      )}
    </Panel>
  );
}
PassRateCard.propTypes = {
  agent: PropTypes.object, statsOf: PropTypes.func, scopeEnv: PropTypes.object, scopeEnvVersion: PropTypes.string, scopes: PropTypes.array, onScopeEnv: PropTypes.func,
  heldBack: PropTypes.oneOf(["older", "subset", "older-or-subset"]),
};


/* "agent v3 × environment v7" — a run is a pairing, named the same way everywhere. */
const usePairLabel = () => {
  const store = useOptionalSimStore();
  return (r) => {
    if (!r) return "";
    const st = store?.state;
    const env = st?.myEnvironments?.find((e) => e.id === r.envId) || null;
    try {
      return runIdentityOf(r, st?.byEnv?.[r.envId], env).label;
    } catch {
      return `agent ${r.agentVersion}`;
    }
  };
};

/* ── recent runs ──────────────────────────────────────────────────────────── */

function RecentRuns({ agent, runs, inProgress, onOpenRun }) {
  const pairOf = usePairLabel();
  /* Newest first — a run still going counts from when it started, and is
     marked: it isn't a result yet. */
  const when = (r) => new Date(r.finishedAt || r.startedAt || 0).getTime();
  const recent = [...inProgress.map((r) => ({ ...r, running: !r.stopped })), ...runs].sort((a, b) => when(b) - when(a)).slice(0, 5);
  const columns = [
    {
      id: "run", header: "Run", size: 150,
      cell: ({ row: { original: r } }) => (
        <Stack direction="row" alignItems="center" spacing={0.75} sx={{ minWidth: 0 }}>
          <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>{r.ordinal ? `Run ${r.ordinal}` : r.label || "Run"}</Typography>
          {r.running && <QuietTag>Running</QuietTag>}
          {r.stopped && (
            <Box component="span" title="It reported nothing for over a day and was timed out — an infrastructure problem, not the agent."><QuietTag>Timed out</QuietTag></Box>
          )}
          {!r.running && r.partial && (
            <Box component="span" title="Only some of the scenarios — not compared with runs of every scenario"><QuietTag>Partial</QuietTag></Box>
          )}
        </Stack>
      ),
    },
    { id: "env", header: "Environment", meta: { flex: 1.2 }, minSize: 160, cell: ({ row: { original: r } }) => <Typography noWrap title={r.envName} sx={{ typography: "s2" }}>{r.envName}</Typography> },
    { id: "pair", header: "Agent × environment", size: 250, cell: ({ row: { original: r } }) => <Typography noWrap title={pairOf(r)} sx={{ typography: "s3", fontWeight: 600, fontFamily: MONO }}>{pairOf(r)}</Typography> },
    {
      id: "rate", header: "Pass rate", size: 200,
      cell: ({ row: { original: r } }) => {
        const rate = r.running ? null : passRate(r);
        return (
          <Stack direction="row" alignItems="center" spacing={1} sx={{ width: "100%" }}>
            <Box sx={{ flex: 1, height: 6, borderRadius: 3, bgcolor: "background.neutral", overflow: "hidden" }}>
              <Box sx={{ width: `${rate || 0}%`, height: "100%", bgcolor: rate == null ? "transparent" : rate < 50 ? "#DC2626" : NEUTRAL_BAR }} />
            </Box>
            <Typography sx={{ typography: "s3", fontWeight: 600, fontVariantNumeric: "tabular-nums", width: 76, textAlign: "right", color: r.running ? "text.disabled" : rateTone(rate) }}>
              {rate == null ? "—" : `${rate}%`}
              {!r.running && <Box component="span" sx={{ color: "text.subtitle", fontWeight: 400 }}>{` ${r.passed}/${r.measured ?? r.total}`}</Box>}
            </Typography>
          </Stack>
        );
      },
    },
    { id: "when", header: "When", size: 120, cell: ({ row: { original: r } }) => <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>{r.running ? `started ${ago(r.startedAt)}` : ago(r.finishedAt)}</Typography> },
  ];
  return (
    <Box>
      <Stack direction="row" alignItems="baseline" spacing={1} sx={{ mb: 0.5 }}>
        <Typography sx={{ typography: "s1", fontWeight: 700 }}>Recent runs</Typography>
        {runs.length > 0 && (
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{`${plural(runs.length, "finished run")}${inProgress.filter((r) => !r.stopped).length ? ` · ${inProgress.filter((r) => !r.stopped).length} running` : ""}`}</Typography>
        )}
      </Stack>
      {recent.length === 0 ? (
        <Typography sx={{ py: 1.5, typography: "s2", color: "text.secondary" }}>
          {agent.usedIn.length ? "No simulations have run against this agent yet." : ["archived", "deleted"].includes(agentStatusOf(agent).status) ? "No simulations ran against this agent." : "No environment yet — build one from this agent to start running simulations."}
        </Typography>
      ) : (
        <FitDataTable columns={columns} data={recent} getRowId={(r) => `${r.envId}-${r.id}`} onRowClick={onOpenRun} />
      )}
    </Box>
  );
}
RecentRuns.propTypes = { agent: PropTypes.object, runs: PropTypes.array, inProgress: PropTypes.array, onOpenRun: PropTypes.func };

/* ── pieces ───────────────────────────────────────────────────────────────── */

function Panel({ title, action, children, sx }) {
  return (
    <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1.5, bgcolor: "background.paper", overflow: "hidden", minWidth: 0, ...sx }}>
      <Stack direction="row" alignItems="center" spacing={1} sx={{ px: 2, py: 1.25, borderBottom: "1px solid", borderColor: "divider" }}>
        <Typography sx={{ typography: "s2", fontWeight: 700, flex: 1 }}>{title}</Typography>
        {action}
      </Stack>
      {children}
    </Box>
  );
}
Panel.propTypes = { title: PropTypes.node, action: PropTypes.node, children: PropTypes.node, sx: PropTypes.object };

function LinkText({ onClick, children }) {
  return (
    <Typography {...clickable(onClick)} sx={{ typography: "s3", fontWeight: 700, color: "primary.main", cursor: "pointer", "&:hover": { textDecoration: "underline" }, ...focusRing }}>
      {children}
    </Typography>
  );
}
LinkText.propTypes = { onClick: PropTypes.func, children: PropTypes.node };
