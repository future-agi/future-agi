import PropTypes from "prop-types";
import { requiredEvalIds } from "../_mock/evals";
import { useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Box, Button, Checkbox, Stack, Tab, Tabs, Tooltip, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import { neutralCheckboxSx } from "../components/primitives";
import { textTone } from "../components/textTone";
import { clickable, focusRing } from "../components/clickable";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { useConnectedAgents } from "../store";
import { improverSessionFor, useIntegrations } from "../_mock/integrations";
import ImproverAttempts from "../run/fixmyagent/ImproverAttempts";
import { evalSeries, runSummaries, trialSummaries } from "../_mock/comparison";
import { allMetrics, deltaAgainst } from "../_mock/winner";
import { Head, MetricCell, ScoreCell } from "../run/RunsTableCells";
import { compactTokens, creditsText } from "../run/runsTableFormat";
import { candidatesOf, chosenVerified, shippedCandidate } from "../run/fixmyagent/tradeoffs";
import WinnerChanges from "../run/fixmyagent/WinnerChanges";
import ScenarioOutcomes from "../run/fixmyagent/ScenarioOutcomes";
import { membersChanged, proposalMembers } from "../_mock/improvementPolicy";
import { improvementOutcome, IMPROVEMENT_PHASES, searchSpend } from "../_mock/optimizationRuns";
import RunsOverview from "../run/RunsOverview";
import WinnerDrawer from "../run/WinnerDrawer";
import { improvementRowsOf } from "./improvementRows";
import { chartRunOf, trialRowsFor } from "./improvementRunsData";
import StatusPill from "./ImprovementStatusPill";
import { agentDefinitionOf, envAgentKey } from "../_mock/agentRegistry";
import { versionNumber } from "../_mock/versions";
import { isGitAgent, versionNameOf } from "../_mock/codeRefs";
import ImprovementStages from "./ImprovementStages";

const RED = "#DC2626";
const DELTA = 56;

/**
 * Improvements, level 2 — one improvement and the runs its search tried.
 * The pass rate across them, then the runs: pick two or more to compare them
 * scenario by scenario, or choose the winner by what matters here. A run
 * opens its own page (level 3).
 *
 * Choosing a winner only picks among runs that passed the checks; one the
 * search didn't verify reads as projected on the held-back set until it's
 * run on the full suite.
 *
 * While it's still running (`progress`), it's the same page: the stages it
 * is in where the verdict will be, and its runs arriving in the table as
 * they finish — each one a real run that opens on its own page.
 */
const STAGE_LABEL = { reproduce: IMPROVEMENT_PHASES.reproducing, search: IMPROVEMENT_PHASES.searching, verify: IMPROVEMENT_PHASES.verifying };

export default function ImprovementRunsView({ env, envState, record, patch, onBack, backLabel, onOpenSearch, onVerify, onShip, progress = null, onOpenIssue, onOpenStartRun }) {
  const running = !!progress;
  /* The model the agent runs on — what a model lock holds every run to. */
  const agentModel = useMemo(() => {
    if (!envState?.agent) return null;
    const def = agentDefinitionOf({ agent: envState.agent, env, envState });
    return def.model?.visible ? def.model.name : null;
  }, [env, envState]);
  const navigate = useNavigate();
  const location = useLocation();
  const { agents, state: simState } = useConnectedAgents();
  const { prs, improverSessions } = useIntegrations();
  /* A coding agent working on this improvement alongside the search. */
  const session = useMemo(() => improverSessionFor(env.id, record.id, { improverSessions }), [env.id, record.id, improverSessions]);
  const [selected, setSelected] = useState([]);
  const [picking, setPicking] = useState(false);
  /* Flow 5's tabs once it has finished: the runs, what the kept change
     changes (each with its reason), and what it did to each scenario. */
  const [tab, setTab] = useState("runs");

  const row = useMemo(
    () => improvementRowsOf({ state: simState, agents, prs }).find((r) => r.envId === env.id && r.recordId === record.id) || null,
    [simState, agents, prs, env.id, record.id],
  );
  const candidates = useMemo(() => candidatesOf(record), [record]);
  const allRuns = useMemo(() => trialRowsFor(env, envState, record, candidates), [env, envState, record, candidates]);
  /* Running: only the runs that have finished so far. */
  const runsDone = running ? progress.runsDone : null;
  const runs = useMemo(() => (runsDone == null ? allRuns : allRuns.slice(0, runsDone)), [allRuns, runsDone]);
  /* The run still going, shown as a row so the table never looks stalled. */
  const pendingN = running && progress.stage === "search" && progress.runsDone < progress.runsTotal ? progress.runsDone + 1 : null;
  /* While running, the best run so far that nothing rejected. */
  const bestSoFarN = running ? [...runs].reverse().find((r) => r.improved && !r.why)?.n || null : null;
  const chartRuns = useMemo(() => runs.map((r) => chartRunOf(r, record)), [runs, record]);
  const searchWinner = record.result?.winner?.n || null;
  const keptN = running ? null : record.preferredTrial || searchWinner;
  const kept = runs.find((r) => r.n === keptN) || null;
  /* A winner you chose wasn't the one the search verified — it runs on the
     full suite, held-back scenarios included, before it can be reviewed. */
  const needsVerify = !!kept && !chosenVerified(record);
  /* The verdict leads: what it found, the held-back figure, the constraints. */
  const outcome = useMemo(() => improvementOutcome(record, { model: agentModel }), [record, agentModel]);
  const shippable = ["ready", "budget"].includes(outcome.ending.key) && !!record.result?.winner;
  const result = record.result;
  /* What ships — the search's pick or the run someone chose — and which
     members of a multi-agent its changes touch. */
  const shipped = shippedCandidate(record) || result?.winner || null;
  const members = useMemo(() => {
    const ctx = { agent: envState?.agent || null, limits: record.limits, target: record.targetMember };
    const proposals = shipped?.proposals || [];
    return membersChanged(proposals, ctx).hasMembers ? proposalMembers(proposals, ctx) : null;
  }, [shipped, envState, record.limits, record.targetMember]);
  const tabbed = !running && !!result;
  const view = tabbed ? tab : "runs";
  /*
    Every pass rate here is on the target scenarios, so a run's change is
    read against what the agent scored on those same scenarios before
    (result.base) — never the full suite the starting run covered. A
    baseline set from this page's own runs is the same set too.
  */
  const targetDelta = (rate) => {
    if (rate == null || result?.base == null) return null;
    const d = Math.round(rate - result.base);
    return d === 0 ? { text: "same", better: null, flat: true } : { text: `${Math.abs(d)} pts`, up: d > 0, better: d > 0, flat: false };
  };

  /* Each run's numbers, and the run the improvement started from — what
     every change in the table is read against. */
  const summaries = useMemo(() => trialSummaries(env, envState).filter((t) => t.selfImprovementId === record.id), [env, envState, record.id]);
  const envRuns = useMemo(() => runSummaries(env, envState), [env, envState]);
  const startRun = envRuns.find((r) => r.id === record.fromRunId) || null;
  /* Production may have moved on since this fix started: its proof is against
     the version it started from, so a fix still waiting to ship says so. */
  const behind = useMemo(() => {
    const regAgent = envState?.agent ? agents.find((a) => a.key === envAgentKey(envState.agent, env.id)) : null;
    const base = record.baseAgentVersion || startRun?.agentVersion || null;
    const prod = regAgent?.production || null;
    if (!regAgent || !base || !prod || base === prod || versionNumber(prod) <= versionNumber(base)) return null;
    const since = regAgent.versions.filter((v) => versionNumber(v.label) > versionNumber(base) && versionNumber(v.label) <= versionNumber(prod) && !v.pendingDraft && v.kind !== "credentials").length;
    if (!since) return null;
    return { since, base: versionNameOf(regAgent, base).main, prod: versionNameOf(regAgent, prod).main, git: isGitAgent(regAgent) };
  }, [agents, envState, env.id, record.baseAgentVersion, startRun]);
  /* Required ones are marked — only they weigh like a pass/fail signal. */
  const evals = useMemo(() => {
    const required = new Set(requiredEvalIds(envState?.evals || []));
    return evalSeries(envRuns, envState).map((e) => ({ id: e.id, name: e.name, required: required.has(e.id) }));
  }, [envRuns, envState]);
  const metrics = useMemo(() => Object.fromEntries(allMetrics(evals).map((m) => [m.id, m])), [evals]);
  const tableRows = useMemo(() => runs.map((r) => ({ ...r, s: summaries.find((t) => t.trialN === r.n) || null })), [runs, summaries]);
  /* What the table's changes are read against — the run it started from,
     unless one of its own runs was set as the baseline (the same choice the
     environment's Runs tab offers). Kept on the improvement. */
  const baselineRow = record.baselineRunId ? tableRows.find((r) => r.runId === record.baselineRunId && r.s) || null : null;
  const baseline = baselineRow?.s || startRun;
  const setBaseline = (runId) => patch({
    optimizations: (envState?.optimizations || []).map((o) => (o.id === record.id
      ? { ...o, baselineRunId: runId && runId !== o.baselineRunId ? runId : undefined }
      : o)),
  });
  /* The chart leads with the winner, read against the run the improvement
     started from — the same change its row shows — not the latest run. */
  /* While it runs, the best run so far leads — never whichever finished last. */
  const lead = kept || (running ? runs.find((r) => r.n === bestSoFarN) || null : null);
  const headline = useMemo(() => {
    if (!lead) return null;
    const run = chartRuns.find((c) => c.id === lead.runId);
    if (!run) return null;
    const m = tableRows.find((r) => r.n === lead.n)?.s;
    return {
      run,
      /* Scored on the target scenarios, not the full suite its run page shows. */
      metric: "pass rate on the target scenarios",
      label: `${lead.name} · ${kept ? "winner" : "best so far"}`,
      delta: targetDelta(m?.passRate ?? lead.score),
      /* Not "Run N" — this page's own runs are numbered too. */
      against: "the agent before the change, on the same scenarios",
    };
  }, [lead, kept, chartRuns, tableRows]); // eslint-disable-line react-hooks/exhaustive-deps
  /* Run · Status · Pass · Duration · Tokens · Cost · Said, not done · Return,
     then a column per grader — the Runs table's columns. */
  const template = `40px minmax(220px, 380px) 150px ${[...Array(6).fill(124), ...evals.map(() => 128)].map((c) => `minmax(${c}px, 1fr)`).join(" ")}`;
  const minWidth = 20 + 40 + 220 + 150 + 6 * 124 + evals.length * 128 + 8;

  /* Only runs that passed the checks are in the race. */
  const field = useMemo(() => {
    const eligible = new Set(candidates.filter((c) => c.eligible).map((c) => c.n));
    return trialSummaries(env, envState)
      .filter((t) => t.selfImprovementId === record.id && eligible.has(t.trialN))
      .map((t) => ({ ...t, partial: false }));
  }, [env, envState, record.id, candidates]);

  const choose = ({ runId }) => {
    const n = Number(/-t(\d+)$/.exec(runId)?.[1]) || null;
    patch({
      optimizations: (envState?.optimizations || []).map((o) => (o.id === record.id
        ? { ...o, preferredTrial: n && n !== searchWinner ? n : undefined }
        : o)),
    });
    setPicking(false);
  };

  const toggle = (id) => setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));
  /* Compare comes back here, to this improvement's runs. */
  const compare = () => navigate(`${paths.dashboard.simulate.simulationCompare(env.id)}?runs=${selected.join(",")}`, {
    state: { back: { to: `${location.pathname}${location.search}`, label: row?.name || "the improvement", state: location.state } },
  });
  const openRun = (r) => navigate(paths.dashboard.simulate.simulationRun(env.id, r.runId));

  const statusOf = (r) => {
    if (running) {
      if (r.why) {
        return (
          <Tooltip arrow title={`Not eligible — ${r.why}`}>
            <Typography noWrap sx={{ typography: "s2", color: RED }}>Rejected</Typography>
          </Tooltip>
        );
      }
      return r.n === bestSoFarN
        ? <Typography noWrap sx={{ typography: "s2", fontWeight: 700 }}>Best so far</Typography>
        : <Typography noWrap sx={{ typography: "s2", color: "text.subtitle" }}>No better</Typography>;
    }
    if (r.n === keptN) {
      return (
        <Tooltip arrow title={r.n === searchWinner ? "The search kept this run — verified on the held-back scenarios." : needsVerify ? "Chosen by you — not yet run on the held-back scenarios. Verify it before review." : "Chosen by you and run on the full suite."}>
          <Typography noWrap sx={{ typography: "s2", fontWeight: 700 }}>
            Winner
            <Box component="span" sx={{ fontWeight: 400, color: "text.subtitle" }}>{r.n === searchWinner ? " · verified" : needsVerify ? " · not verified" : " · your pick"}</Box>
          </Typography>
        </Tooltip>
      );
    }
    if (r.why) {
      return (
        <Tooltip arrow title={`Not eligible — ${r.why}`}>
          <Typography noWrap sx={{ typography: "s2", color: RED }}>Rejected</Typography>
        </Tooltip>
      );
    }
    return <Typography noWrap sx={{ typography: "s2", color: "text.subtitle" }}>Not kept</Typography>;
  };

  return (
    <Box sx={{ height: "100%", overflow: "auto", p: 2, display: "flex", flexDirection: "column" }}>
      <Box sx={{ mb: 2, flexShrink: 0 }}>
        <Button
          size="small" onClick={onBack}
          startIcon={<Iconify icon="solar:arrow-left-linear" width={14} />}
          sx={{ typography: "s2", fontWeight: 600, color: "text.secondary", ml: -0.75, mb: 0.75, minWidth: 0 }}
        >
          {`Back to ${backLabel}`}
        </Button>
        <Stack direction="row" alignItems="center" spacing={1.5}>
          <Box flex={1} minWidth={0}>
            <Stack direction="row" alignItems="center" spacing={1}>
              <Typography noWrap sx={{ typography: "m3", fontWeight: 700 }}>{row?.name || record.name}</Typography>
              {running
                ? <StatusPill ending={{ key: "running", label: STAGE_LABEL[progress.stage] || IMPROVEMENT_PHASES.searching }} />
                : <StatusPill ending={row?.status} headline={row?.headline} />}
            </Stack>
            {/* The version pair, shown the same way on every screen (RSI PRD),
                then where it ran and what started it; the id on hover. */}
            <Tooltip arrow placement="bottom-start" title={record.id}>
              <Typography noWrap sx={{ typography: "s2", color: "text.subtitle", mt: 0.25, width: "fit-content", maxWidth: "100%" }}>
                {[row?.identity, env.name, record.trigger?.issueId || row?.trigger?.label].filter(Boolean).join(" · ")}
              </Typography>
            </Tooltip>
          </Box>
          {!running && (
            <Button
              size="small" variant="outlined" onClick={onOpenSearch}
              startIcon={<Iconify icon="solar:shield-check-linear" width={15} />}
              sx={{ typography: "s2", fontWeight: 700, color: "text.primary", borderColor: "divider", flexShrink: 0 }}
            >
              How it was decided
            </Button>
          )}
          {kept && (
            <Button
              size="small" variant="outlined" onClick={() => openRun(kept)}
              endIcon={<Iconify icon="solar:arrow-right-linear" width={14} />}
              sx={{ typography: "s2", fontWeight: 700, color: "text.primary", borderColor: "divider", flexShrink: 0 }}
            >
              {`Review ${kept.name}`}
            </Button>
          )}
          {/* Only a verified change ships: a run chosen over the search's
              pick is verified first. */}
          {kept && shippable && (needsVerify ? (
            <Tooltip arrow title="Runs it on the full suite, held-back and must-pass scenarios included — the check the search's own pick already passed. Only a verified change can ship.">
              <Button
                size="small" variant="contained" color="primary" onClick={onVerify}
                startIcon={<Iconify icon="solar:play-bold" width={14} />}
                sx={{ typography: "s2", fontWeight: 700, flexShrink: 0 }}
              >
                {`Verify ${kept.name}`}
              </Button>
            </Tooltip>
          ) : onShip ? (
            <Button
              size="small" variant="contained" color="primary" onClick={onShip}
              startIcon={<Iconify icon="solar:upload-minimalistic-linear" width={14} />}
              sx={{ typography: "s2", fontWeight: 700, flexShrink: 0 }}
            >
              {row?.output ? "Hand-over" : "Ship"}
            </Button>
          ) : null)}
        </Stack>
      </Box>

      {/* Fell behind production while it waited — re-check before shipping. */}
      {behind && !running && !["released", "watching", "resolved"].includes(row?.status?.key) && (
        <Stack direction="row" spacing={1.25} sx={{ mb: 1.5, px: 2, py: 1.125, flexShrink: 0, border: "1px solid", borderColor: "divider", borderRadius: 1.5 }}>
          <Iconify icon="solar:history-linear" width={16} sx={{ color: "text.secondary", flexShrink: 0, mt: "2px" }} />
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            <Box component="span" sx={{ color: "text.primary", fontWeight: 600 }}>
              {behind.git
                ? `main has moved on ${behind.since} commit${behind.since === 1 ? "" : "s"} since this fix started.`
                : `${behind.prod} is in production now — ${behind.since} version${behind.since === 1 ? "" : "s"} after this fix started.`}
            </Box>
            {` Its proof is on ${behind.base}; production is ${behind.prod}. Re-check it there before it ${behind.git ? "is merged" : "ships"} — a change that held then may not hold now.`}
          </Typography>
        </Stack>
      )}

      {/* The verdict first: what it found, the held-back figure, the
          constraints. While it runs, the stage it's in takes that place. */}
      {running
        ? <ImprovementStages record={record} envState={envState} progress={progress} model={agentModel} onOpenIssue={onOpenIssue} onOpenRun={onOpenStartRun} />
        : <Verdict outcome={outcome} result={result} targetNow={kept?.score ?? null} />}

      {tabbed && (
        <Tabs
          value={view} onChange={(_, v) => setTab(v)}
          sx={{ minHeight: 40, mb: 2, flexShrink: 0, borderBottom: "1px solid", borderColor: "divider" }}
        >
          <Tab value="runs" label={`Runs (${runs.length})`} sx={{ minHeight: 40 }} />
          <Tab value="changes" label="Changes" sx={{ minHeight: 40 }} />
          <Tab value="scenarios" label="Per scenario" sx={{ minHeight: 40 }} />
        </Tabs>
      )}

      {view !== "runs" && (
        <Box sx={{ flexShrink: 0, border: "1px solid", borderColor: "divider", borderRadius: 1.5, bgcolor: "background.paper" }}>
          {view === "changes"
            ? <WinnerChanges env={env} winner={shipped} record={record} members={members} />
            : <ScenarioOutcomes result={result} winner={result.winner || null} />}
        </Box>
      )}

      {view === "runs" && chartRuns.length > 0 && (
        <Box sx={{ mb: 2, flexShrink: 0 }}>
          <RunsOverview runs={chartRuns} evals={[]} headline={headline} onOpenRun={(r) => navigate(paths.dashboard.simulate.simulationRun(env.id, r.id))} />
        </Box>
      )}

      {view === "runs" && (
      <>
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1.25, flexShrink: 0 }}>
        {/* Finished, the Runs tab names them; running, the heading does. */}
        {running && <Typography sx={{ typography: "s1", fontWeight: 700 }}>{`Runs (${runs.length} of ${progress.runsTotal})`}</Typography>}
        <Typography sx={{ typography: "s3", color: "text.subtitle", flex: 1 }}>
          {running && progress.stage === "reproduce" ? "Each run is a full simulation on this environment — open one to see its conversations" : selected.length ? `${selected.length} selected` : baselineRow ? (
            <>
              Measured against{" "}
              <Box component="span" sx={{ color: "text.primary", fontWeight: 700 }}>{baselineRow.name || `Run ${baselineRow.n}`}</Box>
              {" "}— rates in points, everything else in percent
            </>
          ) : `Select two or more to compare them scenario by scenario · pass rates are on the target scenarios, against ${result?.base ?? "—"}% before the change${startRun ? `; other columns against ${env.name} Run ${startRun.ordinal}` : ""}`}
        </Typography>
        {selected.length > 0 && (
          <Button size="small" onClick={() => setSelected([])} sx={{ typography: "s2", fontWeight: 600, color: "text.secondary", minWidth: 0 }}>Clear</Button>
        )}
        {/* One run is a baseline; two or more is a comparison — as on the
            environment's Runs tab. */}
        {selected.length === 1 && patch ? (
          <Button
            size="small" variant="contained" color="primary"
            onClick={() => { setBaseline(selected[0]); setSelected([]); }}
            startIcon={<Iconify icon="solar:transfer-vertical-linear" width={15} />}
            sx={{ typography: "s2", fontWeight: 700 }}
          >
            {selected[0] === record.baselineRunId ? "Clear baseline" : "Set as baseline"}
          </Button>
        ) : selected.length === 0 && baselineRow && patch ? (
          <Button
            size="small" onClick={() => setBaseline(null)}
            startIcon={<Iconify icon="mingcute:close-line" width={14} />}
            sx={{ typography: "s2", fontWeight: 700, color: "primary.main" }}
          >
            Remove baseline
          </Button>
        ) : null}
        <Button
          size="small" variant="outlined" disabled={selected.length < 2} onClick={compare}
          startIcon={<Iconify icon="solar:transfer-horizontal-linear" width={15} />}
          sx={{ typography: "s2", fontWeight: 700, color: "text.primary", borderColor: "divider" }}
        >
          Compare
        </Button>
        <Tooltip arrow title={running ? "Once every run has finished and been checked." : field.length < 2 ? "Fewer than two runs passed the checks — there's nothing to choose between." : ""} disableHoverListener={!running && field.length >= 2}>
          <Box component="span">
            <Button
              size="small" variant="outlined" disabled={running || field.length < 2} onClick={() => setPicking(true)}
              startIcon={<Iconify icon="solar:cup-star-bold" width={15} />}
              sx={{ typography: "s2", fontWeight: 700, color: "text.primary", borderColor: "divider" }}
            >
              Choose winner
            </Button>
          </Box>
        </Tooltip>
      </Stack>

      <Box sx={{ flex: 1, minHeight: 240, border: "1px solid", borderColor: "divider", borderRadius: 1.5, overflow: "auto", bgcolor: "background.paper" }}>
        <Box sx={{ minWidth }}>
          <Box sx={{ display: "grid", gridTemplateColumns: template, alignItems: "flex-end", pl: 2.5, pt: 1.5, pb: 1, borderBottom: "1px solid", borderColor: "divider" }}>
            <Box />
            <Head>Run</Head>
            <Head>Status</Head>
            <Head right title="Scenarios this run passed, on the target scenarios it was scored on">Pass</Head>
            <Head right title="Average duration per task">Duration</Head>
            <Head right>Tokens</Head>
            <Head right>Cost</Head>
            <Head right title="Tasks where the agent said it was done but the world shows it wasn't">Said, not done</Head>
            <Head right title="Average score the environment gave each conversation">Return</Head>
            {evals.map((e, i) => (
              <Head key={e.id} right divider={i === 0} last={i === evals.length - 1} title={e.name} wrap>
                {e.name.replace(/_/g, "_\u200b")}
              </Head>
            ))}
          </Box>
          <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
            {tableRows.map((r) => {
              const picked = selected.includes(r.runId);
              const m = r.s;
              return (
                <Box
                  key={r.runId}
                  {...clickable(() => openRun(r), { role: "link" })}
                  sx={{
                    display: "grid", gridTemplateColumns: template, alignItems: "stretch", pl: 2.5, cursor: "pointer",
                    /* The baseline is marked by a rule and a chip, not a filled row. */
                    borderLeft: "2px solid", borderColor: r.runId === baselineRow?.runId ? "primary.main" : "transparent",
                    bgcolor: picked ? (t) => alpha(t.palette.primary.main, 0.05) : "transparent",
                    "&:hover": { bgcolor: "action.hover" },
                    ...focusRing,
                  }}
                >
                  {/* Selecting a run for Compare never opens it. The whole cell
                      toggles by mouse; Space on the checkbox toggles by keyboard. */}
                  <Box
                    sx={{ display: "flex", alignItems: "center" }}
                    onClick={(e) => { e.stopPropagation(); if (e.target === e.currentTarget) toggle(r.runId); }}
                  >
                    <Checkbox
                      size="small" checked={picked} onChange={() => toggle(r.runId)}
                      inputProps={{ "aria-label": `Select ${r.name || `Run ${r.n}`}` }}
                      sx={{ p: 0.5, pointerEvents: "none", ...neutralCheckboxSx }}
                    />
                  </Box>
                  <Stack direction="row" alignItems="center" spacing={1.25} sx={{ minWidth: 0, py: 0.875, pr: 1.5 }}>
                    {/* The run's chip — the same R-badge and colour it wears on Compare. */}
                    <Box
                      sx={{
                        minWidth: 30, height: 22, px: 0.75, borderRadius: 0.75, flexShrink: 0,
                        display: "grid", placeItems: "center",
                        typography: "s3", fontWeight: 700, fontVariantNumeric: "tabular-nums",
                        color: r.s?.color ? textTone(r.s.color) : "text.secondary",
                        bgcolor: r.s?.color ? (t) => alpha(r.s.color, t.palette.mode === "dark" ? 0.22 : 0.14) : "background.neutral",
                      }}
                    >
                      {r.s?.letter || `R${r.n}`}
                    </Box>
                    <Box minWidth={0}>
                      <Stack direction="row" alignItems="center" spacing={0.75} sx={{ minWidth: 0 }}>
                        <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>{r.name}</Typography>
                        {r.runId === baselineRow?.runId && (
                          <Typography
                            sx={{
                              px: 0.75, py: 0.125, borderRadius: 0.5, flexShrink: 0,
                              typography: "s3", fontWeight: 700, color: "primary.main",
                              bgcolor: (t) => alpha(t.palette.primary.main, t.palette.mode === "dark" ? 0.2 : 0.12),
                            }}
                          >
                            Baseline
                          </Typography>
                        )}
                      </Stack>
                      {r.tried && <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>{r.tried}</Typography>}
                    </Box>
                  </Stack>
                  <Box sx={{ display: "flex", alignItems: "center", minWidth: 0, pr: 1 }}>{statusOf(r)}</Box>
                  <MetricCell anchor deltaWidth={DELTA} text={`${m?.passRate ?? r.score}%`} delta={baselineRow ? m && deltaAgainst(metrics.passRate, m, baselineRow.s) : targetDelta(m?.passRate ?? r.score)} />
                  <MetricCell quiet deltaWidth={DELTA} text={m ? `${(m.avgDurationMs / 1000).toFixed(1)}s` : "—"} delta={m && deltaAgainst(metrics.duration, m, baseline)} />
                  <MetricCell quiet deltaWidth={DELTA} text={m ? compactTokens(m.tokens) : "—"} delta={m && deltaAgainst(metrics.tokens, m, baseline)} />
                  <MetricCell quiet deltaWidth={DELTA} text={m ? creditsText(m.cost) : "—"} delta={m && deltaAgainst(metrics.cost, m, baseline)} />
                  <MetricCell quiet={!m?.saidNotDone} deltaWidth={DELTA} text={m ? `${m.saidNotDone}` : "—"} delta={m && deltaAgainst(metrics.saidNotDone, m, baseline)} />
                  <MetricCell anchor deltaWidth={DELTA} text={m?.meanReturn == null ? "—" : m.meanReturn.toFixed(2)} delta={m && deltaAgainst(metrics.meanReturn, m, baseline)} />
                  {evals.map((e, i) => (
                    <ScoreCell
                      key={e.id}
                      value={m?.scores?.[e.id] ?? null}
                      deltaWidth={DELTA}
                      delta={m && metrics[`eval:${e.id}`] ? deltaAgainst(metrics[`eval:${e.id}`], m, baseline) : null}
                      divider={i === 0}
                      last={i === evals.length - 1}
                    />
                  ))}
                </Box>
              );
            })}
            {pendingN && (
              <Box sx={{ display: "grid", gridTemplateColumns: template, alignItems: "center", pl: 2.5, minHeight: 49 }}>
                <Box />
                <Stack direction="row" alignItems="center" spacing={1.25} sx={{ minWidth: 0, pr: 1.5 }}>
                  <Box sx={{ minWidth: 30, height: 22, px: 0.75, borderRadius: 0.75, display: "grid", placeItems: "center", typography: "s3", fontWeight: 700, color: "text.disabled", bgcolor: "background.neutral", fontVariantNumeric: "tabular-nums" }}>
                    {`R${pendingN}`}
                  </Box>
                  <Typography noWrap sx={{ typography: "s2", fontWeight: 600, color: "text.subtitle" }}>{`Run ${pendingN}`}</Typography>
                </Stack>
                <Stack direction="row" alignItems="center" spacing={0.75}>
                  <Box
                    sx={{
                      width: 10, height: 10, borderRadius: "50%", border: "1.5px solid", borderColor: "primary.main", borderTopColor: "transparent",
                      animation: "runs-spin 0.7s linear infinite", "@keyframes runs-spin": { to: { transform: "rotate(360deg)" } },
                    }}
                  />
                  <Typography noWrap sx={{ typography: "s2", color: "text.subtitle" }}>Running</Typography>
                </Stack>
              </Box>
            )}
            {running && !runs.length && !pendingN && (
              <Typography sx={{ px: 2.5, py: 2, typography: "s2", color: "text.subtitle" }}>
                {progress.stage === "reproduce"
                  ? "Runs appear here as they finish. First it checks the failure happens on today's version."
                  : "Runs appear here as they finish."}
              </Typography>
            )}
          </Stack>
        </Box>
      </Box>

      {/* A coding agent handed this improvement: its attempts, each judged
          the same way as the runs above, under them. */}
      {session && (
        <Box sx={{ mt: 2, border: "1px solid", borderColor: "divider", borderRadius: 1 }}>
          <ImproverAttempts
            record={record} env={env} envState={envState} session={session}
            name={row?.name || record.name} searchSpent={result ? searchSpend(result, env.surface) : 0}
          />
        </Box>
      )}
      </>
      )}

      <WinnerDrawer
        open={picking}
        onClose={() => setPicking(false)}
        summaries={field}
        evals={evals}
        scenarioCount={envState?.scenarios?.length || 0}
        env={env}
        envState={envState}
        nameOf={(r) => `Run ${r.trialN}`}
        showSummary={false}
        onApply={choose}
      />
    </Box>
  );
}

/*
  The improvement's verdict above its chart (RSI PRD, Flow 5), compact: the
  sentence; the numbers side by side — held-back change, target change,
  overfit gap, cost and latency; then the constraints as one line — how many
  passed, any that didn't named in red — with the full checklist a click
  away. Colour only on the change and on a broken constraint.
*/
const signedPct = (n) => `${n > 0 ? "+" : n < 0 ? "−" : "±"}${Math.abs(Math.round(n * 10) / 10)}%`;
function Verdict({ outcome, result, targetNow }) {
  const [allChecks, setAllChecks] = useState(false);
  if (!result?.winner && !outcome.headline) return null;
  const thin = !!result?.split?.thin;
  const heldNow = outcome.heldScore ?? result?.heldScore;
  const lift = heldNow != null && result?.heldBase != null ? heldNow - result.heldBase : null;
  const targetLift = targetNow != null && result?.base != null ? targetNow - result.base : null;
  const checks = outcome.checks || [];
  const projected = !!outcome.chosen?.projected;
  const effect = outcome.effect || null;
  const cells = [
    result?.winner && { k: "Held back", v: thin ? "Too few to measure" : `${result.heldBase}% → ${heldNow}%`, sub: projected ? "projected" : "never seen by the search", pts: thin || !lift ? null : lift },
    result?.winner && targetLift != null && { k: "Target", v: `${result.base}% → ${targetNow}%`, sub: "on the scenarios it was tuned on", pts: targetLift || null },
    result?.winner && outcome.gap != null && !thin && { k: "Overfit gap", v: `${outcome.gap} pts`, sub: "between target and held back" },
    result?.winner && effect && { k: "Cost · latency", v: `${outcome.costKnown === false ? "—" : signedPct(effect.cost)} · ${signedPct(effect.latency)}`, sub: outcome.costKnown === false ? "cost not measured" : "per conversation" },
  ].filter(Boolean);
  const failed = checks.filter((c) => c.pass === false);
  const passed = checks.filter((c) => c.pass === true).length;
  const line = { typography: "s3", color: "text.subtitle" };
  return (
    <Box sx={{ mb: 2, flexShrink: 0, border: "1px solid", borderColor: "divider", borderRadius: 1.5, bgcolor: "background.paper", overflow: "hidden" }}>
      <Typography sx={{ px: 2, py: 1, typography: "s1", fontWeight: 600, lineHeight: 1.45 }}>{outcome.headline}</Typography>

      {/* ── the numbers, side by side ── */}
      {cells.length > 0 && (
        <Box sx={{
          display: "grid", gridTemplateColumns: { xs: "repeat(2, minmax(0, 1fr))", md: `repeat(${cells.length}, minmax(0, 1fr))` },
          borderTop: "1px solid", borderColor: "divider",
          "& > *": { borderColor: "divider", borderStyle: "solid", borderWidth: 0 },
          "& > *:not(:first-of-type)": { borderLeftWidth: 1 },
        }}>
          {cells.map((c) => (
            <Box key={c.k} sx={{ px: 2, py: 0.875, minWidth: 0 }}>
              <Typography noWrap sx={line}>{c.k}</Typography>
              <Stack direction="row" alignItems="baseline" spacing={0.75} sx={{ minWidth: 0 }}>
                <Typography noWrap sx={{ typography: "s1", fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>{c.v}</Typography>
                {!!c.pts && (
                  <Typography noWrap sx={{ typography: "s3", fontWeight: 600, fontVariantNumeric: "tabular-nums", color: c.pts > 0 ? "#16A34A" : RED }}>
                    {`${c.pts > 0 ? "▲" : "▼"} ${Math.abs(c.pts)} pts`}
                  </Typography>
                )}
              </Stack>
              <Typography noWrap sx={line}>{c.sub}</Typography>
            </Box>
          ))}
        </Box>
      )}

      {/* ── the constraints: one line, the failures named, the rest on demand ── */}
      {checks.length > 0 && (
        <Box sx={{ borderTop: "1px solid", borderColor: "divider" }}>
          <Stack direction="row" alignItems="center" spacing={1} sx={{ px: 2, py: 0.75, minWidth: 0 }}>
            <Iconify
              icon={failed.length ? "solar:close-circle-bold" : "solar:check-circle-linear"}
              width={14} sx={{ color: failed.length ? RED : "text.secondary", flexShrink: 0 }}
            />
            <Typography noWrap sx={{ typography: "s2", fontWeight: 600, flexShrink: 0 }}>
              {`Constraints · ${passed} of ${checks.length} passed`}
            </Typography>
            <Typography noWrap sx={{ typography: "s2", color: RED, flex: 1, minWidth: 0 }}>
              {failed.map((c) => `${c.label}${c.actual ? ` (${c.actual})` : ""}`).join(" · ")}
            </Typography>
            <Button
              size="small" onClick={() => setAllChecks((o) => !o)}
              endIcon={<Iconify icon={allChecks ? "solar:alt-arrow-up-linear" : "solar:alt-arrow-down-linear"} width={12} />}
              sx={{ typography: "s3", fontWeight: 700, color: "text.secondary", flexShrink: 0, minWidth: 0 }}
            >
              {allChecks ? "Hide" : "Show all"}
            </Button>
          </Stack>
          {allChecks && (
            <Box sx={{ px: 2, pb: 1, pl: 4.25, display: "grid", gridTemplateColumns: { xs: "1fr", md: "repeat(2, minmax(0, 1fr))" }, columnGap: 3, rowGap: 0.5 }}>
              {checks.map((c) => (
                <Stack key={c.id} direction="row" alignItems="center" spacing={0.75} sx={{ minWidth: 0 }}>
                  <Iconify
                    icon={c.pass === null ? "solar:question-circle-linear" : c.pass ? "solar:check-circle-linear" : "solar:close-circle-bold"}
                    width={14} sx={{ color: c.pass === false ? RED : c.pass === null ? "text.disabled" : "text.secondary", flexShrink: 0 }}
                  />
                  <Typography noWrap sx={{ typography: "s2", fontWeight: 600, color: c.pass === false ? RED : "text.primary", flexShrink: 0 }}>{c.label}</Typography>
                  <Typography noWrap sx={{ typography: "s3", color: "text.subtitle", minWidth: 0 }}>{c.actual}</Typography>
                </Stack>
              ))}
            </Box>
          )}
        </Box>
      )}
    </Box>
  );
}
Verdict.propTypes = { outcome: PropTypes.object.isRequired, result: PropTypes.object, targetNow: PropTypes.number };

ImprovementRunsView.propTypes = {
  env: PropTypes.object.isRequired,
  envState: PropTypes.object,
  record: PropTypes.object.isRequired,
  patch: PropTypes.func.isRequired,
  onBack: PropTypes.func.isRequired,
  backLabel: PropTypes.string,
  onOpenSearch: PropTypes.func,
  /* Runs the chosen winner on the full suite. */
  onVerify: PropTypes.func,
  /* Opens the hand-over for a verified winner. */
  onShip: PropTypes.func,
  /* Running: from improvementProgressOf — the stage and runs finished so far. */
  progress: PropTypes.object,
  onOpenIssue: PropTypes.func,
  /* The run it started from, when it started from one. */
  onOpenStartRun: PropTypes.func,
};
