import PropTypes from "prop-types";
import { isCredentialsVersion } from "../_mock/versions";
import { isGitAgent, versionNameOf } from "../_mock/codeRefs";
import { useEffect, useState } from "react";
import { Box, MenuItem, Select, Stack, Typography } from "@mui/material";
import { alpha, useTheme } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import ReactApexChart from "../components/SafeApexChart";
import { useOptionalSimStore } from "../store";
import { runIdentityOf } from "../_mock/runIdentity";
import { agentStatusOf, isComingSoon } from "../_mock/rsiModel";
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
 * The agent at a glance — how it's doing and what to do next, nothing more:
 * three numbers (the version in production, the last simulation,
 * improvements), the one next step worth taking, then the latest runs. Pass
 * rate by version lives on Versions; real calls live in its Observe project.
 */
export default function AgentOverviewTab({
  agent, runs, inProgress, scopedRuns, statsOf, production, improvements,
  scopeEnv, onSwitch, onOpenRun, onTab, fresh = false,
}) {
  const scoped = scopedRuns || runs;
  /* Just connected, nothing set up: the Next steps card above is the page. */
  if (fresh) return null;
  return (
    <Stack spacing={2.5}>
      <KpiStrip
        agent={agent} runs={scoped} anyRuns={(runs || []).length > 0} statsOf={statsOf} scopeEnv={scopeEnv} production={production} improvements={improvements}
        onSwitch={onSwitch} onOpenRun={onOpenRun} onTab={onTab}
      />
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

/* ── three numbers and the next step ───────────────────────────────────────── */

function KpiStrip({ agent, runs, anyRuns, statsOf, scopeEnv, production, improvements, onSwitch, onOpenRun, onTab }) {
  const latestStats = statsOf(agent.latest);
  const running = scopeEnv ? agent.usedIn.find((u) => u.envId === scopeEnv.envId) : null;
  /* Behind what's in production — a draft nobody released isn't something
     to switch an environment to. */
  const behind = running && running.running !== agent.production && running.behind;
  const lastRun = runs[0];
  const lastRate = lastRun ? passRate(lastRun) : null;
  const failing = lastRun
    ? [
      lastRun.failed != null ? lastRun.failed && `${lastRun.failed} failing` : (lastRun.measured ?? lastRun.total) - lastRun.passed > 0 && `${(lastRun.measured ?? lastRun.total) - lastRun.passed} failing`,
      lastRun.flaky ? `${lastRun.flaky} mixed` : null,
    ].filter(Boolean).join(" · ")
    : "";
  const prod = production || {};
  const imp = improvements || { total: 0 };
  /* Archived or deleted: read-only, so nothing new is offered — nor for a
     kind improvements can't run on yet. */
  const stopped = ["archived", "deleted"].includes(agentStatusOf(agent).status) || isComingSoon(agent);

  /* A code agent's versions read as commits ("main @ 3f9a2c1"); others by number. */
  const git = isGitAgent(agent);
  const nm = (l) => versionNameOf(agent, l).main;
  const latestRef = git ? versionNameOf(agent, agent.latest).ref : null;
  /* The one next step — the first of these that applies. */
  const next = behind
    ? { text: `${running.envName}'s default is still ${nm(running.running)} — ${nm(agent.production)} is in production.`, label: "Default to production", onClick: () => onSwitch(running.envId) }
    : !latestStats && agent.latest !== agent.production && agent.usedIn.length
      ? {
        text: latestRef
          ? `A fix is waiting on ${latestRef.branch} (${latestRef.sha}) and hasn't run yet — test it before it's merged.`
          : `${agent.latest} is a draft that hasn't run yet — test it before it's released.`,
        label: git ? "See commits" : "See versions",
        onClick: () => onTab("versions"),
      }
      : failing && lastRun
        ? { text: `${failing} in ${lastRun.ordinal ? `Run ${lastRun.ordinal}` : "the last run"}.`, label: "Open run", onClick: () => onOpenRun(lastRun) }
        : null;

  return (
    <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1.5, bgcolor: "background.paper", overflow: "hidden" }}>
      <Box sx={{
        display: "grid", gridTemplateColumns: { xs: "1fr", md: "repeat(3, minmax(0, 1fr))" },
        "& > *": { borderColor: "divider", borderStyle: "solid", borderWidth: 0 },
        "& > *:not(:first-of-type)": { borderLeftWidth: { md: 1 }, borderTopWidth: { xs: 1, md: 0 } },
      }}>
        {/* The version in production; its real calls live in its Observe project. */}
        <Kpi
          label="In production"
          value={agent.production ? nm(agent.production) : "—"}
          mono
          /* A link nobody confirmed reads as a suggestion — Next steps confirms it. */
          sub={prod.project ? `Observe project: ${prod.project.name} · ${agent.values?.observeChosenAt || prod.project.how === "watching" ? "linked" : "suggested"}` : "Observe project not linked"}
          action={prod.project
            ? { label: "Open in Observe", onClick: prod.onOpenProject }
            : { label: "Link a project", onClick: prod.onLink }}
        />
        <Kpi
          label={lastRun ? <>Last simulation · <Box component="span" sx={{ fontFamily: MONO }}>{nm(lastRun.agentVersion)}</Box></> : "Last simulation"}
          value={lastRate == null ? "—" : `${lastRate}%`}
          tone={rateTone(lastRate)}
          sub={lastRun
            ? `${lastRun.passed} of ${lastRun.measured ?? lastRun.total} passed · ${ago(lastRun.finishedAt)}`
            : anyRuns ? "No full run on the current environment version yet" : "No finished run yet"}
          action={lastRun ? { label: "Open run", onClick: () => onOpenRun(lastRun) } : null}
        />
        <Kpi
          label="Improvements"
          value={String(imp.total)}
          sub={imp.ready ? `${imp.ready} ready for review` : imp.running ? `${imp.running} running` : imp.total ? "None waiting on you" : "None yet"}
          action={imp.total || !stopped ? { label: imp.total ? "Open improvements" : "Start one", onClick: () => onTab("improvements") } : null}
        />
      </Box>
      {/* No traces or call logs reach Observe: Fix has nothing to find this
          agent by, so it's off — said here; "Link a project" sits in the
          production card just above. */}
      {!prod.project && (
        <Stack direction="row" alignItems="center" spacing={1.25} sx={{ px: 2, py: 1, borderTop: "1px solid", borderColor: "divider" }}>
          <Iconify icon="solar:info-circle-linear" width={14} sx={{ color: "text.subtitle", flexShrink: 0 }} />
          <Typography noWrap sx={{ typography: "s2", color: "text.secondary", flex: 1, minWidth: 0 }}>
            Fix is off for this agent — it sends no traces or call logs yet. Simulations still run and grade.
          </Typography>
        </Stack>
      )}
      {next && (
        <Stack direction="row" alignItems="center" spacing={1.25} sx={{ px: 2, py: 1, borderTop: "1px solid", borderColor: "divider" }}>
          <Iconify icon="solar:arrow-right-linear" width={14} sx={{ color: "text.subtitle", flexShrink: 0 }} />
          <Typography noWrap sx={{ typography: "s2", color: "text.secondary", flex: 1, minWidth: 0 }}>{next.text}</Typography>
          <Typography
            noWrap {...clickable(next.onClick)}
            sx={{ typography: "s2", fontWeight: 700, cursor: "pointer", flexShrink: 0, "&:hover": { textDecoration: "underline" }, ...focusRing }}
          >
            {next.label}
          </Typography>
        </Stack>
      )}
    </Box>
  );
}
KpiStrip.propTypes = {
  anyRuns: PropTypes.bool,
  agent: PropTypes.object, runs: PropTypes.array, statsOf: PropTypes.func, scopeEnv: PropTypes.object, production: PropTypes.object,
  improvements: PropTypes.object, onSwitch: PropTypes.func, onOpenRun: PropTypes.func, onTab: PropTypes.func,
};

/* One number: a label, the value, one line under it, one action. */
function Kpi({ label, value, tone, extra, sub, action, mono }) {
  const word = !mono && !/^[—\d]/.test(value);
  return (
    <Stack sx={{ px: 2, py: 1.75, minWidth: 0 }} spacing={0.5}>
      <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>{label}</Typography>
      <Stack direction="row" alignItems="baseline" spacing={1} sx={{ minWidth: 0 }}>
        <Typography noWrap sx={{
          fontSize: word ? 18 : 24, fontWeight: 700, lineHeight: "30px", fontVariantNumeric: "tabular-nums",
          color: word ? "text.secondary" : tone || "text.primary",
          ...(mono && { fontFamily: MONO }),
        }}>
          {value}
        </Typography>
        {extra}
      </Stack>
      <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>{sub}</Typography>
      {action && (
        <Typography
          noWrap {...clickable(action.onClick)}
          sx={{
            typography: "s3", fontWeight: 700, width: "fit-content", maxWidth: "100%", cursor: "pointer", pt: 0.25,
            color: action.danger ? "#DC2626" : "text.primary", "&:hover": { textDecoration: "underline" },
            ...focusRing,
          }}
        >
          {`${action.label} →`}
        </Typography>
      )}
    </Stack>
  );
}
Kpi.propTypes = { label: PropTypes.node, value: PropTypes.string, tone: PropTypes.string, extra: PropTypes.node, sub: PropTypes.node, action: PropTypes.object, mono: PropTypes.bool };

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
    /* A code agent's bars are named by commit hash. */
    .map((g) => ({ labels: g.labels, v: { ...g.v, label: g.labels.map((l) => (isGitAgent(agent) ? versionNameOf(agent, l).ref?.sha || l : l)).join(" · ") }, s: statsOf(g.labels.length === 1 ? g.labels[0] : g.labels) }));
  /* Bars for what ran, and the newest — for a code agent, its production
     commit (a fix on its own branch isn't the agent yet). */
  const series = all.filter((x, i) => x.s || (isGitAgent(agent) ? x.labels.includes(agent.production) : i === all.length - 1));
  const skipped = all.length - series.length;
  const head = [...series].reverse().find((x) => x.s);
  return (
    <Panel
      title={isGitAgent(agent) ? "Pass rate by commit" : "Pass rate by version"}
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
          {`${plural(skipped, isGitAgent(agent) ? "commit" : "version")} in between ${skipped === 1 ? "wasn't" : "weren't"} run here.`}
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
