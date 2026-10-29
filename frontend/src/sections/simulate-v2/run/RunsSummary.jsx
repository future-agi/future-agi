import { staleSince } from "../_mock/optimizationRuns";
import { runViaOf } from "../_mock/runVia";
import PropTypes from "prop-types";
import { requiredEvalIds } from "../_mock/evals";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { alpha } from "@mui/material/styles";
import RunsOverview from "./RunsOverview";
import {
  previousRunOf,
  runStatsOf,
  sharedDeltaOf,
  sameEnvVersion,
} from "../_mock/runCompare";
import {
  Box,
  Stack,
  Typography,
  Button,
  Checkbox,
  Popover,
  Tooltip,
  ButtonBase,
  IconButton,
  Menu,
  MenuItem,
  ListItemText,
  ListItemIcon,
  Chip,
} from "@mui/material";
import { ConfirmDialog } from "src/components/custom-dialog";
import { statusStyles } from "src/sections/common/simulation";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import {
  runSummaries,
  evalSeries,
  trialSummaries,
  RUN_COLORS,
} from "../_mock/comparison";
import { TRIAL_CAP, DEMO_QUEUED_ROW } from "../_mock/runList";
import { currentEnvVersion, currentAgentVersion } from "../_mock/versions";
import {
  improvementOf,
  isCandidateRun,
  runIdentityOf,
} from "../_mock/runIdentity";
import { currentEnvSnapshot } from "../_mock/envSnapshot";
import { staleScenarios } from "../_mock/proofs";
import WinnerDrawer from "./WinnerDrawer";
import FromImprovement from "./FromImprovement";
import AppliedEvalsDrawer from "../workspace/evals/AppliedEvalsDrawer";
import { allMetrics, deltaAgainst } from "../_mock/winner";
import { estimateRunPlan, rangeLabel } from "../_mock/pricing";
import { runCreditsOf } from "../_mock/usageLedger";
import { resolveEval } from "../_mock/evals";
import { useConnectedAgents, useEnvState } from "../store";
import { improvementRowsOf } from "../improvements/improvementRows";
import { isReferenceRun } from "../_mock/referenceRuns";
import { neutralCheckboxSx } from "../components/primitives";
import { Head, MetricCell, ScoreCell } from "./RunsTableCells";
import { compactTokens } from "./runsTableFormat";
import { textTone } from "../components/textTone";
import AlsoRunIn from "./AlsoRunIn";
import useRunGroup from "./useRunGroup";
import { clickable, focusRing } from "../components/clickable";

/* Timestamp for a run row. Falls back to startedAt and never prints
   "Invalid Date" for an in-progress run that has no finishedAt yet. */
const runTimeLabel = (r) => {
  const raw = r?.finishedAt || r?.startedAt;
  const d = raw ? new Date(raw) : null;
  if (!d || Number.isNaN(d.getTime())) return "in progress";
  return d.toLocaleString(undefined, {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
};

/**
 * Every run this environment has had, as one table.
 *
 * A finished run used to land on its own results, which answers "how did that
 * go" and quietly makes the more useful question unanswerable: an agent is
 * modified and run again, and the only thing anyone wants to know is whether
 * it moved. So a run now lands here — the run you just finished is the last
 * row, the one before it is the row above, and the trend across every eval is
 * drawn above them.
 *
 * Selecting rows and pressing Compare opens the run detail in comparison mode.
 * The comparison is only meaningful because the scenarios belong to the
 * environment: nothing was rewritten between the runs, so a row that moved
 * moved because of the agent.
 */
export default function RunsSummary({ env, envState, onGo, onStart }) {
  /* Each finished run's charge, priced as its Cost tab. */
  const creditsByRun = useMemo(
    () => runCreditsOf(env, envState),
    [env, envState],
  );
  /* The cost column's delta against the compared run, on the credits each
     was charged — kept from when it compared the agent's reported cost. */
  const creditsMetric = {
    id: "credits",
    lowerIsBetter: true,
    get: (x) => creditsByRun[x.id],
  };
  const creditsComparable = (r, against) =>
    !!against && creditsByRun[r.id] != null && creditsByRun[against.id] != null;
  const navigate = useNavigate();
  const { patch } = useEnvState(env.id);
  const [selected, setSelected] = useState(() => []);
  const [addAnchor, setAddAnchor] = useState(null);
  /* Run again can start a run group: the same agent version in other
     environments testing it, one run each. */
  const [alsoRunIn, setAlsoRunIn] = useState([]);
  const runGroup = useRunGroup(env.id);
  const [pickingWinner, setPickingWinner] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [addEvalsOpen, setAddEvalsOpen] = useState(false);
  /* Candidate runs — an improvement's trials and its winner run — are off
     until asked for. */

  /* The winner is kept with the environment rather than derived, because it is
     a decision someone made under stated weights — not a fact about the runs
     that could be recomputed later from different ones. */
  const winner = envState.winner || null;

  /*
    The baseline is what every other row is read against. It is a choice rather
    than a default — "the oldest run" and "the latest run" are both wrong half
    the time, and a table of deltas against a run nobody picked is worse than
    no deltas at all.
  */
  const baselineId = envState.baselineRunId || null;

  const summaries = useMemo(() => runSummaries(env, envState), [env, envState]);

  /*
    Trials, exposed as first-class runs.

    Each trial of an improvement run was a full execution of the candidate
    prompt against the environment — a run, by any honest reading. Product
    feedback: they should be peer runs in the list, not hidden behind a
    Trials tab. So we merge manual and trial rows into one chronological
    stream and assign a single continuous ordinal across both — Run 4, 5,
    6, … regardless of which one is a manual re-run and which is a trial.
  */
  /* Cap (TRIAL_CAP): the 8 most recent improvement runs, so the merged list
     stays readable — the latest improvements are the ones people come to
     check; "Show all" lifts it. Kept oldest-first here (the merge sorts by
     time); the table shows newest first like every other run. */
  const trialsAll = useMemo(
    () =>
      trialSummaries(env, envState)
        .slice()
        .sort(
          (a, b) => new Date(a.finishedAt || 0) - new Date(b.finishedAt || 0),
        ),
    [env, envState],
  );
  const trials = useMemo(() => trialsAll.slice(-TRIAL_CAP), [trialsAll]);

  /*
    The one source of row-level truth. Chronological, then stamped with
    a fresh ordinal so numbering runs 1..N across manual + trial without
    gaps. Chip letter is the ordinal so the badge always matches "Run N".
    parentRunOrdinal is stamped onto trial rows so the "from Run N" chip
    reads the same ordinal you can find in the list.
  */
  const mergedRaw = useMemo(() => {
    /* A run still going has no finish time yet — it sorts by when it
       started, not as the oldest run there is. */
    const when = (r) =>
      new Date(r.finishedAt || r.startedAt || Date.now()).getTime();
    const combined = [...summaries, ...trials]
      .filter((r) => !r.synthetic)
      .sort((a, b) => when(a) - when(b));
    /* A run keeps the number it was stamped with when it ran — the same one
       its own page, Insights, the digest and Compare print. Trials read as
       "Trial N" with their T badge, so they never shift a run's number. */
    const stamped = combined.map((r) =>
      r.kind === "trial" ? { ...r, ordinal: null } : r,
    );
    /* For every trial, find the ordinal of the manual run that spawned
       its improvement run — used by the "from Run N" chip on
       trial rows. Trial → optimization.fromRunId → run.ordinal. */
    const ordinalById = new Map(stamped.map((r) => [r.id, r.ordinal]));
    const parentByOptId = new Map(
      (envState.optimizations || []).map((o) => [o.id, o.fromRunId]),
    );
    return stamped.map((r) => {
      if (r.kind !== "trial") return r;
      const parentRunId = parentByOptId.get(r.selfImprovementId);
      return {
        ...r,
        parentRunOrdinal: parentRunId ? ordinalById.get(parentRunId) : null,
      };
    });
  }, [summaries, trials, envState.optimizations]);

  /*
    What the list shows: the runs of the agent only. An improvement lives on
    the run it started from (that run's Improvements tab) — interleaved here
    they buried the runs someone started. Each run says how many it has.
  */
  const { agents, state: simState } = useConnectedAgents();
  const improvementRows = useMemo(() => {
    const rowsHere = improvementRowsOf({ state: simState, agents }).filter(
      (x) => x.envId === env.id,
    );
    return rowsHere.map((row) => {
      const own = trialsAll.filter((t) => t.selfImprovementId === row.recordId);
      const record = (envState.optimizations || []).find(
        (o) => o.id === row.recordId,
      );
      const last = own[own.length - 1];
      return {
        id: `improvement:${row.recordId}`,
        kind: "improvement",
        row,
        trials: own.length,
        best: own.length ? Math.max(...own.map((t) => t.passRate || 0)) : null,
        finishedAt: last?.finishedAt || null,
        startedAt: record?.startedAt || record?.createdAt || null,
      };
    });
  }, [simState, agents, env.id, trialsAll, envState.optimizations]);
  const listed = useMemo(() => {
    const when = (r) =>
      new Date(r.finishedAt || r.startedAt || Date.now()).getTime();
    return [
      /* A fix's first step is today's agent, unchanged — a run of the
         agent like any other. Only its trials are the improvement's. */
      ...mergedRaw.filter(
        (r) =>
          !isCandidateRun(r, envState) ||
          improvementOf(r, envState)?.role === "baseline",
      ),
    ].sort((a, b) => when(a) - when(b));
  }, [mergedRaw, envState]);
  /* The improvements started from each run, newest first. */
  const improvementsByRun = useMemo(() => {
    const m = new Map();
    improvementRows.forEach((x) => {
      const id = x.row.fromRunId;
      if (id) m.set(id, [x.row, ...(m.get(id) || [])]);
    });
    return m;
  }, [improvementRows]);
  const openImprovements = (r) =>
    navigate(
      `${paths.dashboard.simulate.simulationRun(env.id, r.id)}?tab=improvements`,
    );

  /* Newest first for display — the run someone just finished is the top row. */
  const rows = useMemo(() => [...listed].reverse(), [listed]);

  /* Demo only: one run waiting in the queue on top, so the Queued status is
     visible. It has no results yet and stays out of the chart / compare /
     winner, which all read `rows` / `allSummaries` — but it can be selected
     and deleted, like any run nobody wants any more. */
  const tableRows = useMemo(() => {
    if (!DEMO_QUEUED_ROW || envState.demoQueuedRemoved || !rows.length)
      return rows;
    const next = Math.max(...rows.map((r) => r.ordinal || 0)) + 1;
    return [
      {
        id: QUEUED_ID,
        kind: "queued-demo",
        status: "queued",
        ordinal: next,
        letter: String(next),
        color: RUN_COLORS[(next - 1) % RUN_COLORS.length],
        agentVersion: currentAgentVersion(envState)?.label || "—",
        envVersion: currentEnvVersion(env, envState)?.label,
      },
      ...rows,
    ];
  }, [rows, env, envState]);

  /* Chart, compare, baseline lookups all read from the same merged list. */
  const allSummaries = mergedRaw;
  /* Finished runs someone started — what each row's change is read against. */
  const finishedRuns = useMemo(
    () => mergedRaw.filter((r) => r.kind !== "trial" && r.finishedAt),
    [mergedRaw],
  );

  /* Eval series is now built off the merged list so trials contribute
     points, not just manual runs. Same shape evalSeries produces. */
  const series = useMemo(() => {
    const evalDefs = (envState?.evals || [])
      .map((e) => ({ id: e.id }))
      .filter((e) => e.id);
    const rawSeries = evalSeries(
      summaries,
      envState,
    ); /* keeps colour/name mapping */
    return rawSeries.map((s) => ({
      ...s,
      data: listed.map((r) =>
        r.scores?.[s.id] == null ? null : r.scores[s.id],
      ),
    }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [listed, summaries, envState]);

  /* One winner on this list — the one someone chose. The winner of an
     improvement run is named inside that run, not crowned here. */

  /* What every row is called — "agent v3 × env v2", and the improvement
     that produced it, if one did (its trials, and "Run winner on full
     suite"). The same helper names the run on its own page. */
  const identityOf = (r) => runIdentityOf(r, envState, env);
  /* The environment version people see — rows are read against each other
     only on the same one. */
  const versionOf = (r) => identityOf(r).env;

  /*
    The improvement's projection, held against what happened.

    Recorded when the change lands, with the number the screen showed. Only a
    run that took the same test answers it — same agent version, same env
    version, same scenario set — or the comparison says nothing.
  */
  const expectation = envState.omegaExpectation;
  const verified = useMemo(() => {
    if (!expectation) return null;
    const all = envState.scenarios.map((sc) => sc.id);
    const setOf = (ids) => [...new Set(ids)].sort().join(",");
    const wanted = setOf(
      Array.isArray(expectation.scenarios) ? expectation.scenarios : all,
    );
    return (
      summaries.find(
        (r) =>
          r.agentVersion === expectation.version &&
          r.finishedAt &&
          (!expectation.envVersion ||
            r.envVersion === expectation.envVersion) &&
          setOf(r.scenarioIds?.length ? r.scenarioIds : all) === wanted,
      ) || null
    );
  }, [expectation, summaries, envState.scenarios]);
  /* Required ones are marked — only they weigh like a pass/fail signal. */
  const evals = useMemo(() => {
    const required = new Set(requiredEvalIds(envState?.evals || []));
    return series.map((s) => ({
      id: s.id,
      name: s.name,
      color: s.color,
      required: required.has(s.id),
    }));
  }, [series, envState?.evals]);

  /* The same metric definitions the winner weights use, so a column that reads
     as an improvement here cannot count as a regression there. */
  const metrics = useMemo(
    () => Object.fromEntries(allMetrics(evals).map((m) => [m.id, m])),
    [evals],
  );

  const toggle = (id) =>
    setSelected((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    );

  /*
    The baseline is what everything else is read as a change from, so it has to
    be the earlier run unless someone said otherwise. Passing the ticks in table
    order made the newest run the baseline and inverted the whole screen — a
    regression in the new version came back labelled "fixed".
  */
  const compare = () => {
    /* allSummaries includes trials, so a trial ticked in the runs list still
       reaches the compare page. Chronology is preserved by finishedAt so a
       trial slots in beside the runs it can be read against. */
    const chronological = [...allSummaries]
      .filter((r) => selected.includes(r.id))
      .sort((a, b) => new Date(a.finishedAt) - new Date(b.finishedAt))
      .map((r) => r.id);
    const ordered =
      baselineId && chronological.includes(baselineId)
        ? [baselineId, ...chronological.filter((id) => id !== baselineId)]
        : chronological;
    navigate(
      `${paths.dashboard.simulate.simulationCompare(env.id)}?runs=${ordered.join(",")}`,
    );
  };

  const setBaseline = () => {
    /* Selecting the baseline itself clears it — otherwise the only way out of
       a baseline is to pick a different one, and "compare everything against
       nothing" stops being reachable. */
    patch({ baselineRunId: selected[0] === baselineId ? null : selected[0] });
    setSelected([]);
  };

  const isTrialId = (id) => /^OPT-\d+-t\d+$/.test(id || "");
  const selectedHasTrials = selected.some(isTrialId);
  const selectedManualOnly = selected.filter((id) => !isTrialId(id));
  /* A queued run has no results, so it can be deleted but can't be a
     baseline or one side of a comparison — those count finished runs only. */
  const selectedQueued = selected.includes(QUEUED_ID);
  const selectedFinished = selected.filter((id) => id !== QUEUED_ID);

  /* Deleting runs takes the winner and the baseline with them when they
     point at something that no longer exists. Trials live under their
     improvement run — removing them means removing the run — so we
     only allow manual runs through this path and surface a note about
     any trials in the selection. */
  const removeSelected = () => {
    const gone = new Set(selectedManualOnly);
    patch({
      runs: envState.runs.filter((r) => !gone.has(r.id)),
      ...(gone.has(baselineId) ? { baselineRunId: null } : {}),
      ...(winner && gone.has(winner.runId) ? { winner: null } : {}),
      /* Taking the queued run off the queue — it never started, so there is
         nothing else to clear. */
      ...(gone.has(QUEUED_ID) ? { demoQueuedRemoved: true } : {}),
    });
    setSelected([]);
    setDeleting(false);
  };

  const baseline = allSummaries.find((r) => r.id === baselineId) || null;

  /*
    One grid for the header and every row, because the alternative — a flex row
    per line — cannot line numbers up. Each value sits in a fixed column and, when
    there is a baseline, its delta sits in a second fixed sub-column beside it, so
    the figures form a straight edge instead of drifting with the width of
    whatever moved next to them.

    The `1fr` after the run name is a spacer: on a wide screen it absorbs the
    slack so the numbers stay together rather than stretching apart.
  */
  const grid = useMemo(() => {
    /* Metric columns stretch to fill rather than sitting at a fixed size after
       a dead spacer: the spacer put a hand's width of nothing between a run's
       name and its numbers on a wide screen, which is a long way for an eye to
       travel to read one row. */
    /* Sized to each header so every label sits on one line — a header
       row wrapping to three lines read as clutter. With a baseline each
       column also carries a delta, so they share one wider size. */
    /* Each floor is its header's one-line width plus the cell's padding, and no
       more: the old floors added slack on top, so two graders already pushed
       the last one ("Task s…") past a ~920px card on a laptop screen. */
    /* Scenario pass · Call pass · Change · Flaky · Failing · Calls · Run time ·
       Started by, then the per-task numbers. Only the older ones carry a
       baseline delta, so only they widen with one. */
    /* Every number carries its change beside it, so each column is sized for both. */
    const added = [172, 128, 96, 104, 64, 76, 156];
    const nums = [...added, 128, 124, 124, 148, 120];
    /* Grader names may wrap onto two lines, so their columns can be narrow. */
    const score = evals.length >= 4 ? 124 : 136;
    const columns = [...nums, ...evals.map(() => score)];
    /* The run column fits a whole name — "Run 1 · agent v1 × environment v1". */
    return {
      template: `26px minmax(288px, 380px) ${STATUS_COL}px ${columns.map((c) => `minmax(${c}px, 1fr)`).join(" ")}`,
      /* Below this the table scrolls rather than crushing the run names: the
         row's left padding, every column's floor, and the slice of the fade
         that overhangs the last grader's own right padding. */
      min: 20 + 26 + 288 + STATUS_COL + columns.reduce((a, c) => a + c, 0) + 8,
      deltaWidth: 56,
      /* Where the system numbers end and the graders begin. */
      firstEval: 12,
    };
  }, [evals]);

  return (
    <Box
      sx={{ p: 2, display: "flex", flexDirection: "column", minHeight: "100%" }}
    >
      {/* No "Simulations summary" heading — the tab names the page. Add Evals
          and Choose winner sit on the runs bar, beside the run count. */}
      {/* Pass rate by run, in its own card above the runs. */}
      <Box sx={{ mb: 2, flexShrink: 0 }}>
        <RunsOverview
          runs={listed}
          versionOf={versionOf}
          evals={evals}
          barPct={Math.round((envState.reportBar?.pass1 ?? 0.8) * 100)}
          onOpenRun={(r) =>
            navigate(paths.dashboard.simulate.simulationRun(env.id, r.id))
          }
        />
      </Box>
      {/* ── the runs ──
          Design system table: no box around it. The bar sits above the
          table and hairlines divide the rows. */}
      <Box
        sx={{
          overflow: "hidden",
          /* Grow to fill any remaining vertical space so the card
             reaches the bottom of the surrounding scroll container
             instead of leaving a blank strip beneath it (e.g. on
             Improvements L2 where the header is shorter than the
             env workspace's). */
          flex: 1,
          display: "flex",
          flexDirection: "column",
        }}
      >
        {/* ── did the fix do what it was projected to do ── */}
        {expectation && verified && (
          <Stack
            direction="row"
            alignItems="flex-start"
            spacing={1.5}
            sx={{
              mb: 2,
              px: 2.5,
              py: 1.75,
              borderRadius: "8px",
              border: "1px solid",
              borderColor:
                verified.passRate >= expectation.projected
                  ? "status.success.border"
                  : "status.warning.border",
              bgcolor:
                verified.passRate >= expectation.projected
                  ? "status.success.bg"
                  : "status.warning.bg",
            }}
          >
            <Iconify
              icon={
                verified.passRate >= expectation.projected
                  ? "solar:check-circle-bold"
                  : "solar:info-circle-bold"
              }
              width={16}
              sx={{
                color:
                  verified.passRate >= expectation.projected
                    ? "status.success.fg"
                    : "status.warning.fg",
                flexShrink: 0,
                mt: "1px",
              }}
            />
            <Box flex={1} minWidth={0}>
              <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                Projected {expectation.projected}%
                {expectation.projectedOn
                  ? ` (${expectation.projectedOn === "held out" ? "held-back scenarios" : expectation.projectedOn})`
                  : ""}{" "}
                for agent {expectation.version} — it came in at{" "}
                {verified.passRate}%
              </Typography>
              <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                {verified.passRate >= expectation.projected
                  ? `The change addressed ${expectation.addresses.length} ${expectation.addresses.length === 1 ? "scenario" : "scenarios"} and the run met the projection. The projection is only ever a claim until a run answers it.`
                  : `The change addressed ${expectation.addresses.length} ${expectation.addresses.length === 1 ? "scenario" : "scenarios"} and the run fell ${expectation.projected - verified.passRate} points short. Worth reading which of them still fail before proposing the next fix.`}
              </Typography>
            </Box>
            <Button
              size="small"
              onClick={() => patch({ omegaExpectation: null })}
              sx={{ color: "text.secondary", flexShrink: 0 }}
            >
              Dismiss
            </Button>
          </Stack>
        )}

        {/* ── the runs ── */}
        <Stack direction="row" alignItems="center" spacing={2} sx={{ pb: 1.5 }}>
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "s1", fontWeight: 600 }}>
              Runs ({tableRows.filter((r) => r.kind !== "improvement").length})
            </Typography>
            <Typography
              noWrap
              sx={{ typography: "s3", color: "text.tertiary" }}
            >
              {baseline ? (
                <>
                  Measured against{" "}
                  <Box
                    component="span"
                    sx={{ color: "text.primary", fontWeight: 600 }}
                  >
                    {baseline.kind === "trial"
                      ? `Run ${baseline.trialN}${baseline.selfImprovementName ? ` · ${baseline.selfImprovementName}` : ""}`
                      : `Run ${baseline.ordinal}`}{" "}
                    · {identityOf(baseline).label}
                  </Box>{" "}
                  — rates in points, everything else in percent; runs on other
                  environment versions show no change.
                </>
              ) : (
                "Select two or more to compare them scenario by scenario"
              )}
            </Typography>
          </Box>
          {/* The page's own actions — on the runs bar while nothing is
              selected; a selection swaps in its own actions. */}
          {selected.length === 0 && (
            <Stack direction="row" spacing={1} flexShrink={0}>
              {/* Only where the page has no Run simulation button of its own
                  (Improvements) — the env workspace header already starts runs. */}
              {onStart && (
                <Button
                  variant="outlined"
                  color="inherit"
                  size="small"
                  onClick={(e) => setAddAnchor(e.currentTarget)}
                  startIcon={
                    <Iconify icon="solar:test-tube-linear" width={16} />
                  }
                  sx={{ borderColor: "divider" }}
                >
                  Add more runs
                </Button>
              )}
              <Button
                variant="outlined"
                color="inherit"
                size="small"
                onClick={() => setAddEvalsOpen(true)}
                startIcon={
                  <Iconify icon="solar:add-circle-linear" width={16} />
                }
                sx={{ borderColor: "divider" }}
              >
                Add Evals
              </Button>
              {/* The label does not change once a winner exists. Picking one is the
                  same act every time — open the weights, decide, apply — and
                  "Change winner" implied a different, smaller thing. */}
              <Button
                variant="outlined"
                color="inherit"
                size="small"
                onClick={() => setPickingWinner(true)}
                startIcon={<Iconify icon="solar:cup-star-bold" width={16} />}
                sx={{ borderColor: "divider" }}
              >
                Choose winner
              </Button>
            </Stack>
          )}
          {selected.length === 0 && baseline ? (
            <Button
              size="small"
              onClick={() => patch({ baselineRunId: null })}
              startIcon={<Iconify icon="solar:close-linear" width={14} />}
              sx={{ color: "primary.main", flexShrink: 0 }}
            >
              Remove baseline
            </Button>
          ) : (
            selected.length > 0 && (
              <Stack direction="row" alignItems="center" spacing={1}>
                <Typography
                  sx={{ typography: "s2", color: "text.secondary", mr: 0.5 }}
                >
                  {selected.length} selected
                </Typography>

                {/* One run is a baseline; two or more is a comparison. Offering
                  both at once would ask people to work out which button their
                  selection is even eligible for. A queued run is neither — with
                  one in the selection, only Delete applies to it. */}
                {selected.length === 1 && !selectedQueued ? (
                  <Button
                    variant="contained"
                    color="primary"
                    size="small"
                    onClick={setBaseline}
                    startIcon={
                      <Iconify
                        icon="solar:transfer-vertical-linear"
                        width={16}
                      />
                    }
                  >
                    {selected[0] === baselineId
                      ? "Clear baseline"
                      : "Set as baseline"}
                  </Button>
                ) : (
                  selectedFinished.length >= 2 &&
                  !selectedQueued && (
                    <Button
                      variant="contained"
                      color="primary"
                      size="small"
                      onClick={compare}
                      startIcon={
                        <Iconify
                          icon="solar:transfer-horizontal-linear"
                          width={16}
                        />
                      }
                    >
                      Compare
                    </Button>
                  )
                )}

                <Button
                  variant="outlined"
                  color="inherit"
                  size="small"
                  onClick={() => setDeleting(true)}
                  startIcon={
                    <Iconify icon="solar:trash-bin-trash-linear" width={16} />
                  }
                  sx={{ borderColor: "divider" }}
                >
                  Delete
                </Button>

                <Tooltip arrow title="Clear selection">
                  <IconButton size="small" onClick={() => setSelected([])}>
                    <Iconify
                      icon="solar:close-linear"
                      width={16}
                      sx={{ color: "text.tertiary" }}
                    />
                  </IconButton>
                </Tooltip>
              </Stack>
            )
          )}
        </Stack>

        {/*
          Scrolls, and says so. A table that runs past its own card with no
          affordance reads as a rendering bug rather than as more content — the
          fade is the only thing that tells you the last grader is not the last
          column. `pr` keeps that final column clear of the fade.
        */}
        <Box
          sx={{
            overflow: "auto",
            position: "relative",
            /* Grow to consume any leftover vertical space inside the
               card so the card visually reaches the bottom of the
               surrounding scroll container. */
            flex: 1,
            minHeight: 0,
            "&::after": {
              content: '""',
              position: "sticky",
              right: 0,
              top: 0,
              float: "right",
              width: 28,
              height: "100%",
              pointerEvents: "none",
              backgroundImage: (t) =>
                `linear-gradient(to right, transparent, ${t.palette.background.paper})`,
            },
          }}
        >
          <Box sx={{ minWidth: grid.min }}>
            <Box
              sx={{
                display: "grid",
                gridTemplateColumns: grid.template,
                alignItems: "flex-end",
                columnGap: 0,
                pl: 1.5,
                pr: 0,
                pt: 0,
                pb: 1,
                borderBottom: "1px solid",
                borderColor: "divider",
              }}
            >
              <Box />
              <Head>Run</Head>
              <Head>Status</Head>
              <Head
                right
                title="Scenarios that passed a majority of their repeats. The change beside it is on the scenarios both runs measured — grey when it's within noise."
              >
                Scenario pass
              </Head>
              <Head right title="Every repeat counted">
                Call pass
              </Head>
              <Head right title="Passed some repeats and failed others">
                Flaky
              </Head>
              <Head
                right
                title="Scenarios that failed a majority of their repeats"
              >
                Failing
              </Head>
              <Head right title="Scenarios × repeats">
                Calls
              </Head>
              <Head right title="Start to finish">
                Run time
              </Head>
              <Head gap title="Who started it">
                Started by
              </Head>
              <Head right title="Average duration per task">
                Duration
              </Head>
              <Head right>Tokens</Head>
              <Head right>Cost</Head>
              <Head
                right
                title="Tasks where the agent said it was done but the world shows it wasn't"
              >
                Said, not done
              </Head>
              <Head
                right
                title="Mean return — the environment's score, averaged over conversations"
              >
                Return
              </Head>
              {evals.map((e, i) => (
                <Head
                  key={e.id}
                  right
                  divider={i === 0}
                  last={i === evals.length - 1}
                  title={e.name}
                  wrap
                >
                  {/* snake_case names are one long word — a break opportunity
                      after each underscore lets them wrap like the rest. */}
                  {e.name.replace(/_/g, "_​")}
                </Head>
              ))}
            </Box>

            <Stack
              divider={
                <Box
                  sx={{ borderBottom: "1px solid", borderColor: "divider" }}
                />
              }
              sx={{ borderBottom: "1px solid", borderColor: "divider" }}
            >
              {tableRows.map((r, i) => {
                const picked = selected.includes(r.id);
                const won = winner?.runId === r.id;
                /* What this row's red and green numbers are read against: the
                   baseline when one is set, else the run before it — on the
                   same environment version only. A run with no results, or on
                   another version than the baseline, shows no deltas. */
                const against = !r.finishedAt
                  ? null
                  : baseline
                    ? sameEnvVersion(baseline, r, versionOf)
                      ? baseline
                      : null
                    : r.kind === "trial"
                      ? null
                      : previousRunOf(finishedRuns, r, versionOf);

                if (r.kind === "queued-demo") {
                  return (
                    <Box
                      key={r.id}
                      sx={{
                        display: "grid",
                        gridTemplateColumns: grid.template,
                        alignItems: "stretch",
                        columnGap: 0,
                        pl: 1.5,
                        pr: 0,
                        py: 0,
                        minHeight: 40,
                        borderLeft: "2px solid",
                        borderColor: "transparent",
                      }}
                    >
                      <Box
                        sx={{
                          display: "flex",
                          alignItems: "center",
                          cursor: "pointer",
                        }}
                        onClick={(e) => {
                          if (e.target === e.currentTarget) toggle(r.id);
                        }}
                      >
                        <Checkbox
                          size="small"
                          checked={picked}
                          onChange={() => toggle(r.id)}
                          inputProps={{
                            "aria-label": `Select Run ${r.kind === "trial" ? r.trialN : r.ordinal}`,
                          }}
                          sx={{
                            p: 0.5,
                            pointerEvents: "none",
                            ...neutralCheckboxSx,
                          }}
                        />
                      </Box>
                      <Stack
                        direction="row"
                        alignItems="center"
                        spacing={1.25}
                        sx={{
                          minWidth: 0,
                          py: 0.875,
                          pr: 1.5,
                          overflow: "hidden",
                        }}
                      >
                        <Box
                          sx={{
                            minWidth: 26,
                            height: 22,
                            px: 0.75,
                            borderRadius: "4px",
                            flexShrink: 0,
                            display: "grid",
                            placeItems: "center",
                            typography: "s3",
                            fontWeight: 600,
                            fontVariantNumeric: "tabular-nums",
                            color: textTone(r.color),
                            bgcolor: (t) =>
                              alpha(
                                r.color,
                                t.palette.mode === "dark" ? 0.22 : 0.14,
                              ),
                          }}
                        >
                          {r.letter}
                        </Box>
                        <Stack
                          direction="row"
                          alignItems="baseline"
                          spacing={0.75}
                          sx={{ minWidth: 0, overflow: "hidden" }}
                        >
                          <Typography
                            noWrap
                            sx={{
                              typography: "s2",
                              fontWeight: 600,
                              flexShrink: 0,
                            }}
                          >
                            {r.kind === "trial"
                              ? `Run ${r.trialN}${r.selfImprovementName ? ` · ${r.selfImprovementName}` : ""}`
                              : `Run ${r.ordinal}${isReferenceRun(r, envState) ? " · Reference" : ""}`}{" "}
                            · {identityOf(r).label}
                          </Typography>
                          <RunDetail text="waiting to start" />
                        </Stack>
                      </Stack>
                      <StatusCell status="Queued" />
                      {Array.from({ length: 12 }).map((_, k) => (
                        <MetricCell
                          key={k}
                          quiet
                          deltaWidth={grid.deltaWidth}
                          text="—"
                        />
                      ))}
                      {evals.map((e, ei) => (
                        <ScoreCell
                          key={e.id}
                          value={null}
                          divider={ei === 0}
                          last={ei === evals.length - 1}
                          deltaWidth={grid.deltaWidth}
                        />
                      ))}
                    </Box>
                  );
                }

                /* Trial rows share the same table shape but carry different
                   identity (parent search, winner-of-search rather than
                   env-wide winner) so their label and their bordered-row
                   accent are computed slightly differently. */
                if (r.kind === "trial") {
                  return (
                    <Box
                      key={r.id}
                      sx={{
                        display: "grid",
                        gridTemplateColumns: grid.template,
                        alignItems: "stretch",
                        columnGap: 0,
                        pl: 1.5,
                        pr: 0,
                        py: 0,
                        minHeight: 40,
                        cursor: "pointer",
                        borderLeft: "2px solid",
                        borderColor:
                          r.id === baselineId ? "primary.main" : "transparent",
                        bgcolor: picked
                          ? (t) => alpha(t.palette.primary.main, 0.05)
                          : "transparent",
                        "&:hover": { bgcolor: "action.hover" },
                        ...focusRing,
                      }}
                      {...clickable(
                        () =>
                          navigate(
                            paths.dashboard.simulate.simulationRun(
                              env.id,
                              r.id,
                            ),
                          ),
                        { role: "link" },
                      )}
                    >
                      <Box
                        sx={{ display: "flex", alignItems: "center" }}
                        onClick={(e) => {
                          e.stopPropagation();
                          if (e.target === e.currentTarget) toggle(r.id);
                        }}
                      >
                        <Checkbox
                          size="small"
                          checked={picked}
                          onChange={() => toggle(r.id)}
                          inputProps={{
                            "aria-label": `Select Run ${r.kind === "trial" ? r.trialN : r.ordinal}`,
                          }}
                          sx={{
                            p: 0.5,
                            pointerEvents: "none",
                            ...neutralCheckboxSx,
                          }}
                        />
                      </Box>

                      <Stack
                        direction="row"
                        alignItems="center"
                        spacing={1.25}
                        sx={{
                          minWidth: 0,
                          py: 0.875,
                          pr: 1.5,
                          overflow: "hidden",
                        }}
                      >
                        <Box
                          sx={{
                            minWidth: 26,
                            height: 22,
                            px: 0.75,
                            borderRadius: "4px",
                            flexShrink: 0,
                            display: "grid",
                            placeItems: "center",
                            typography: "s3",
                            fontWeight: 600,
                            fontVariantNumeric: "tabular-nums",
                            color: textTone(r.color),
                            bgcolor: (t) =>
                              alpha(
                                r.color,
                                t.palette.mode === "dark" ? 0.22 : 0.14,
                              ),
                          }}
                        >
                          {r.letter}
                        </Box>
                        <Stack
                          direction="row"
                          alignItems="baseline"
                          spacing={0.75}
                          sx={{ minWidth: 0, overflow: "hidden" }}
                        >
                          <Typography
                            noWrap
                            sx={{
                              typography: "s2",
                              fontWeight: 600,
                              flexShrink: 0,
                            }}
                          >
                            {`Run ${r.trialN}`} · {identityOf(r).label}
                          </Typography>
                          <RunDetail
                            improvement={identityOf(r).improvement}
                            text={runTimeLabel(r)}
                          />
                          {r.toolGap?.length > 0 && (
                            <Tooltip
                              arrow
                              title={`Run anyway on environment ${identityOf(r).env || r.envVersion}, which can't answer ${r.toolGap.join(", ")}. Scenarios that called ${r.toolGap.length === 1 ? "it" : "them"} got no answer and are not measured.`}
                            >
                              <Box
                                sx={{
                                  display: "inline-flex",
                                  flexShrink: 0,
                                  color: "text.secondary",
                                }}
                              >
                                <Iconify
                                  icon="solar:plug-circle-linear"
                                  width={16}
                                />
                              </Box>
                            </Tooltip>
                          )}
                          {r.id === baselineId && (
                            <Typography
                              sx={{
                                px: 0.75,
                                py: 0.125,
                                borderRadius: "4px",
                                flexShrink: 0,
                                typography: "s3",
                                fontWeight: 600,
                                color: "primary.main",
                                bgcolor: "status.discovery.bg",
                              }}
                            >
                              Baseline
                            </Typography>
                          )}
                        </Stack>
                      </Stack>

                      <StatusCell
                        status={runStatus(r)}
                        improvements={improvementsByRun.get(r.id)}
                        onOpenImprovements={() => openImprovements(r)}
                      />
                      {scenarioCell(r, against)}
                      <MetricCell
                        anchor
                        deltaWidth={grid.deltaWidth}
                        text={`${r.passRate}%`}
                        delta={deltaAgainst(metrics.passRate, r, against)}
                      />
                      {addedCells(r, against)}
                      <MetricCell
                        quiet
                        deltaWidth={grid.deltaWidth}
                        text={`${(r.avgDurationMs / 1000).toFixed(1)}s`}
                        delta={deltaAgainst(metrics.duration, r, against)}
                      />
                      <MetricCell
                        quiet
                        deltaWidth={grid.deltaWidth}
                        text={compactTokens(r.tokens)}
                        delta={deltaAgainst(metrics.tokens, r, against)}
                      />
                      <MetricCell
                        quiet
                        deltaWidth={grid.deltaWidth}
                        text={costText(creditsByRun[r.id])}
                        delta={
                          creditsComparable(r, against)
                            ? deltaAgainst(creditsMetric, r, against)
                            : null
                        }
                      />
                      <MetricCell quiet deltaWidth={grid.deltaWidth} text="—" />
                      <MetricCell quiet deltaWidth={grid.deltaWidth} text="—" />
                      {evals.map((e, ei) => (
                        <ScoreCell
                          key={e.id}
                          value={r.scores[e.id]}
                          divider={ei === 0}
                          last={ei === evals.length - 1}
                          deltaWidth={grid.deltaWidth}
                          delta={deltaAgainst(
                            metrics[`eval:${e.id}`],
                            r,
                            against,
                          )}
                        />
                      ))}
                    </Box>
                  );
                }

                return (
                  <Box
                    key={r.id}
                    sx={{
                      display: "grid",
                      gridTemplateColumns: grid.template,
                      alignItems: "stretch",
                      columnGap: 0,
                      pl: 1.5,
                      pr: 0,
                      py: 0,
                      minHeight: 40,
                      cursor: "pointer",
                      /*
                        Marked by a rule and a chip, not by a filled row. Two
                        tinted rows in a table of five — one amber, one blue —
                        turned a list of numbers into a set of coloured bands,
                        and the numbers are what people came to read.
                      */
                      borderLeft: "2px solid",
                      borderColor: won
                        ? "status.warning.solid"
                        : r.id === baselineId
                          ? "primary.main"
                          : "transparent",
                      bgcolor: picked
                        ? (t) => alpha(t.palette.primary.main, 0.05)
                        : "transparent",
                      /* `relative` for the overlaid RunRowMenu. `.row-menu` fades
                         in with `.row-open`; both stay visible when the menu is
                         open so the row doesn't flicker while the user reads it. */
                      position: "relative",
                      "&:hover": {
                        bgcolor: "action.hover",
                        "& .row-open": { opacity: 1 },
                        "& .row-menu": { opacity: 1 },
                      },
                      ...focusRing,
                    }}
                    {...clickable(
                      () =>
                        navigate(
                          paths.dashboard.simulate.simulationRun(env.id, r.id),
                        ),
                      { role: "link" },
                    )}
                  >
                    {/* Row menu — delete. Overlaid on the right
                        of the row on hover so the grid columns stay comparable
                        across rows. Only for finished, non-trial runs (this
                        branch). Nothing here ships, promotes or blocks a version. */}
                    {r.status !== "queued" && (
                      <RunRowMenu
                        run={r}
                        onDelete={() => {
                          setSelected([r.id]);
                          setDeleting(true);
                        }}
                      />
                    )}
                    {/* The whole cell toggles. An 18px box inside a row that
                        navigates is a target you have to aim at, and missing it
                        opens the run instead of selecting it. */}
                    <Box
                      sx={{ display: "flex", alignItems: "center" }}
                      onClick={(e) => {
                        e.stopPropagation();
                        if (e.target === e.currentTarget) toggle(r.id);
                      }}
                    >
                      <Checkbox
                        size="small"
                        checked={picked}
                        onChange={() => toggle(r.id)}
                        inputProps={{
                          "aria-label": `Select Run ${r.kind === "trial" ? r.trialN : r.ordinal}`,
                        }}
                        sx={{
                          p: 0.5,
                          pointerEvents: "none",
                          ...neutralCheckboxSx,
                        }}
                      />
                    </Box>

                    <Stack
                      direction="row"
                      alignItems="center"
                      spacing={1.25}
                      sx={{ minWidth: 0, py: 0.875, pr: 1.5 }}
                    >
                      {/* Numeric chip = the continuous ordinal, same
                          scheme across manual runs and trials so a row's
                          badge always matches its "Run N" label. */}
                      <Box
                        sx={{
                          minWidth: 26,
                          height: 22,
                          px: 0.75,
                          borderRadius: "4px",
                          flexShrink: 0,
                          display: "grid",
                          placeItems: "center",
                          typography: "s3",
                          fontWeight: 600,
                          fontVariantNumeric: "tabular-nums",
                          color: textTone(r.color),
                          bgcolor: (t) =>
                            alpha(
                              r.color,
                              t.palette.mode === "dark" ? 0.22 : 0.14,
                            ),
                        }}
                      >
                        {r.letter}
                      </Box>
                      {/*
                        Named by what distinguishes it. Every run of this
                        environment carries the same auto-generated label, so
                        showing it in every row identified nothing and pushed
                        the agent version — the thing that actually changed —
                        out of sight.
                      */}
                      <Box
                        minWidth={0}
                        sx={{
                          display: "flex",
                          alignItems: "center",
                          minWidth: 0,
                          overflow: "hidden",
                        }}
                      >
                        <Stack
                          direction="row"
                          alignItems="baseline"
                          spacing={0.75}
                          sx={{ minWidth: 0, overflow: "hidden" }}
                        >
                          {/* The run's name never shrinks — "Ru…" identifies
                              nothing. The env version it was pinned to and
                              the timestamp are the detail, so they are what
                              truncates when the column is narrow (the full
                              text stays in the tooltip). Env is only shown
                              if the run recorded one; older runs omit it
                              rather than reading as "env unknown". Short
                              timestamp, no task count: every run here runs
                              the same scenarios. */}
                          <Typography
                            noWrap
                            sx={{
                              typography: "s2",
                              fontWeight: 600,
                              flexShrink: 0,
                            }}
                          >
                            {r.kind === "trial"
                              ? `Run ${r.trialN}${r.selfImprovementName ? ` · ${r.selfImprovementName}` : ""}`
                              : `Run ${r.ordinal}${isReferenceRun(r, envState) ? " · Reference" : ""}`}{" "}
                            · {identityOf(r).label}
                          </Typography>
                          <RunDetail
                            improvement={identityOf(r).improvement}
                            text={runTimeLabel(r)}
                          />
                          {/* Started with runs in other environments — the
                              same agent version, one run each. The row stays
                              this environment's run; its page switches between
                              the others. */}
                          {r.group?.members?.length > 1 && (
                            <Tooltip
                              arrow
                              title={`Run group · agent ${r.group.agentVersion || r.agentVersion} also ran in ${r.group.members
                                .filter((m) => m.envId !== env.id)
                                .map((m) => m.envName)
                                .join(
                                  ", ",
                                )}. Open the run to read them side by side.`}
                            >
                              <Typography
                                noWrap
                                sx={{
                                  typography: "s3",
                                  color: "text.tertiary",
                                  flexShrink: 0,
                                }}
                              >
                                · group of {r.group.members.length}
                              </Typography>
                            </Tooltip>
                          )}
                          {r.toolGap?.length > 0 && (
                            <Tooltip
                              arrow
                              title={`Run anyway on environment ${identityOf(r).env || r.envVersion}, which can't answer ${r.toolGap.join(", ")}. Scenarios that called ${r.toolGap.length === 1 ? "it" : "them"} got no answer and are not measured.`}
                            >
                              <Box
                                sx={{
                                  display: "inline-flex",
                                  flexShrink: 0,
                                  color: "text.secondary",
                                }}
                              >
                                <Iconify
                                  icon="solar:plug-circle-linear"
                                  width={16}
                                />
                              </Box>
                            </Tooltip>
                          )}
                          {/*
                            "X of N", "N flaky" and the measured fraction
                            used to trail here. All three read as run
                            health, not as what the run said — and stacking
                            them made the row so long the label was fighting
                            for space with the PASS column. Coverage lives
                            in the drilled-in run view; flakiness has its
                            own dedicated screen. The row keeps the name
                            and timestamp and stops there.
                          */}
                          {/* The chip marks the row; the bar above the table is
                              where it can be removed, because a control nobody
                              can see is not a control. */}
                          {r.id === baselineId && (
                            <Typography
                              sx={{
                                px: 0.75,
                                py: 0.125,
                                borderRadius: "4px",
                                flexShrink: 0,
                                typography: "s3",
                                fontWeight: 600,
                                color: "primary.main",
                                bgcolor: "status.discovery.bg",
                              }}
                            >
                              Baseline
                            </Typography>
                          )}
                          {won && (
                            <Stack
                              direction="row"
                              alignItems="center"
                              spacing={0.375}
                              sx={{
                                px: 0.75,
                                py: 0.125,
                                borderRadius: "4px",
                                flexShrink: 0,
                                bgcolor: "status.warning.bg",
                              }}
                            >
                              <Iconify
                                icon="solar:cup-star-bold"
                                width={12}
                                sx={{ color: "status.warning.fg" }}
                              />
                              <Typography
                                sx={{
                                  typography: "s3",
                                  fontWeight: 600,
                                  color: "text.primary",
                                }}
                              >
                                Winner
                              </Typography>
                            </Stack>
                          )}
                          {/* Twin-write badge, present only on twin-backed runs
                              that stamped the count at record time. Older runs
                              (before this field existed) omit the chip silently
                              rather than reading as "0 writes". */}
                          {typeof r.twinWrites === "number" && (
                            <Stack
                              direction="row"
                              alignItems="center"
                              spacing={0.375}
                              sx={{
                                px: 0.75,
                                py: 0.125,
                                borderRadius: "4px",
                                flexShrink: 0,
                                bgcolor: "status.discovery.bg",
                              }}
                            >
                              <Iconify
                                icon="solar:pen-2-linear"
                                width={12}
                                sx={{ color: "primary.main" }}
                              />
                              <Typography
                                sx={{
                                  typography: "s3",
                                  fontWeight: 600,
                                  color: "primary.main",
                                }}
                              >
                                {r.twinWrites} write
                                {r.twinWrites === 1 ? "" : "s"}
                              </Typography>
                            </Stack>
                          )}
                        </Stack>
                      </Box>
                      {/* The row opens the run. Nothing else on it said so. */}
                      <Iconify
                        icon="solar:alt-arrow-right-linear"
                        width={16}
                        className="row-open"
                        sx={{
                          opacity: 0,
                          transition: "opacity .12s",
                          color: "text.tertiary",
                          flexShrink: 0,
                        }}
                      />
                    </Stack>

                    <StatusCell
                      status={runStatus(r)}
                      improvements={improvementsByRun.get(r.id)}
                      onOpenImprovements={() => openImprovements(r)}
                    />
                    {scenarioCell(r, against)}
                    <MetricCell
                      anchor
                      deltaWidth={grid.deltaWidth}
                      text={`${r.passRate}%`}
                      delta={deltaAgainst(metrics.passRate, r, against)}
                    />
                    {addedCells(r, against)}
                    <MetricCell
                      quiet
                      deltaWidth={grid.deltaWidth}
                      text={`${(r.avgDurationMs / 1000).toFixed(1)}s`}
                      delta={deltaAgainst(metrics.duration, r, against)}
                    />
                    <MetricCell
                      quiet
                      deltaWidth={grid.deltaWidth}
                      text={compactTokens(r.tokens)}
                      delta={deltaAgainst(metrics.tokens, r, against)}
                    />
                    <MetricCell
                      quiet
                      deltaWidth={grid.deltaWidth}
                      text={costText(creditsByRun[r.id])}
                      delta={
                        creditsComparable(r, against)
                          ? deltaAgainst(creditsMetric, r, against)
                          : null
                      }
                    />
                    {/* The one system number that is a finding rather than a
                        measurement, so it is the one that gets an accent. */}
                    <MetricCell
                      quiet={!r.saidNotDone}
                      deltaWidth={grid.deltaWidth}
                      text={`${r.saidNotDone}`}
                      delta={deltaAgainst(metrics.saidNotDone, r, against)}
                    />
                    {/* The environment's own number, beside the graders it is
                        computed from so the two can be read against each other
                        rather than living on different pages. */}
                    <MetricCell
                      anchor
                      deltaWidth={grid.deltaWidth}
                      text={
                        r.meanReturn == null ? "—" : r.meanReturn.toFixed(2)
                      }
                      delta={deltaAgainst(metrics.meanReturn, r, against)}
                    />

                    {evals.map((e, ei) => (
                      <ScoreCell
                        key={e.id}
                        value={r.scores[e.id]}
                        divider={ei === 0}
                        last={ei === evals.length - 1}
                        deltaWidth={grid.deltaWidth}
                        delta={deltaAgainst(
                          metrics[`eval:${e.id}`],
                          r,
                          against,
                        )}
                      />
                    ))}
                  </Box>
                );
              })}
            </Stack>
          </Box>
        </Box>
      </Box>

      <ConfirmDialog
        open={deleting}
        onClose={() => setDeleting(false)}
        title={`Delete ${selectedManualOnly.length} run${selectedManualOnly.length === 1 ? "" : "s"}?`}
        content={
          selectedHasTrials
            ? `${selectedManualOnly.length} run${selectedManualOnly.length === 1 ? "" : "s"} will be deleted. ${selected.length - selectedManualOnly.length} improvement run${selected.length - selectedManualOnly.length === 1 ? "" : "s"} will be left alone — they belong to their improvement and go when it does.`
            : selectedQueued && selectedFinished.length === 0
              ? "It hasn't started, so it comes off the queue and there are no results to lose."
              : `Their results go with them, and anything measured against them — a baseline, a winner — is cleared. The scenarios and the environment are untouched.${selectedQueued ? " The queued run comes off the queue before it starts." : ""}`
        }
        action={
          <Button
            size="small"
            variant="contained"
            color="error"
            onClick={removeSelected}
            disabled={selectedManualOnly.length === 0}
          >
            Delete
          </Button>
        }
      />

      <AppliedEvalsDrawer
        open={addEvalsOpen}
        onClose={() => setAddEvalsOpen(false)}
        env={env}
        envState={envState}
        patch={patch}
      />

      <WinnerDrawer
        open={pickingWinner}
        onClose={() => setPickingWinner(false)}
        summaries={allSummaries}
        evals={evals}
        /* The winner's run summary reads coverage against the full set. */
        scenarioCount={envState.scenarios.length}
        env={env}
        envState={envState}
        /* Ranked on the environment version in use — none yet says so. */
        currentOnly
        initial={winner?.weights}
        onApply={(w) => {
          patch({ winner: w });
          setPickingWinner(false);
        }}
      />

      <Popover
        open={!!addAnchor}
        anchorEl={addAnchor}
        onClose={() => setAddAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{ paper: { sx: { width: 320, borderRadius: "8px" } } }}
      >
        <Box sx={{ p: 2 }}>
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            Run again
          </Typography>
          <Typography
            sx={{ typography: "s3", color: "text.tertiary", mb: 1.5 }}
          >
            The same scenarios against whatever the agent is now — that is what
            makes the rows comparable.
          </Typography>
          <Stack spacing={0.75} sx={{ mb: 1.75 }}>
            {/* The pairing, quoted at the one moment it is a decision: a run is
                this environment version × this agent version, and that is what
                the result will be attributed to. */}
            <Line
              label="Runs"
              value={
                currentAgentVersion(envState)
                  ? identityOf({
                      agentVersion: currentAgentVersion(envState).label,
                      envVersion: currentEnvVersion(env, envState)?.label,
                    }).label
                  : `no agent connected × environment ${currentEnvSnapshot(env, envState)?.label || "—"}`
              }
            />
            <Line
              label="Scenarios"
              value={`${envState.scenarios.length} tasks`}
            />
            {/* Sampling is part of the cost and part of what the next rate
                will mean, so it is quoted before the run, not discovered
                afterwards in a tooltip. */}
            <Line label="Repeats" value="3 per scenario" />
            <Line label="Evals" value={`${envState.evals.length} applied`} />
            {/* Priced with the metering rates, scenarios × repeats, in credits. */}
            <Line
              label="Est. cost"
              value={(() => {
                const e = estimateRunPlan({
                  env,
                  envState,
                  scenarios: envState.scenarios.length,
                  attempts: 3,
                  evals: (envState.evals || [])
                    .map(resolveEval)
                    .filter(Boolean),
                });
                return `≈ ${rangeLabel(e.platform.low, e.platform.high, e.platform.unknown)}`;
              })()}
            />
          </Stack>

          {/* A run with no graders is not a measurement. It still produces
              traces worth reading, and saying so here is cheaper than letting
              someone wait out a run and then find there is no pass rate. */}
          {envState.evals.length === 0 && (
            <Stack
              direction="row"
              spacing={1}
              alignItems="flex-start"
              sx={{
                mb: 1.75,
                px: 1.25,
                py: 1,
                borderRadius: 1,
                bgcolor: "status.warning.bg",
              }}
            >
              <Iconify
                icon="solar:info-circle-bold"
                width={14}
                sx={{ color: "status.warning.fg", flexShrink: 0, mt: "1px" }}
              />
              <Typography sx={{ typography: "s3", color: "text.secondary" }}>
                No evals are applied, so this run will produce traces but no
                pass rate to compare against the runs above.
              </Typography>
            </Stack>
          )}

          {/* A run whose scenarios were proved against an older world still
              produces numbers; it just cannot claim they rest on a proof. */}
          {staleScenarios(envState.scenarios, env, envState).length > 0 && (
            <Stack
              direction="row"
              spacing={1}
              alignItems="flex-start"
              sx={{
                mb: 1.75,
                px: 1.25,
                py: 1,
                borderRadius: 1,
                bgcolor: "status.warning.bg",
              }}
            >
              <Iconify
                icon="solar:danger-triangle-bold"
                width={14}
                sx={{ color: "status.warning.fg", flexShrink: 0, mt: "1px" }}
              />
              <Typography sx={{ typography: "s3", color: "text.secondary" }}>
                {staleScenarios(envState.scenarios, env, envState).length}{" "}
                scenarios were proved against an older world. Re-prove them on
                the Scenarios step if this run has to stand up.
              </Typography>
            </Stack>
          )}
          {runGroup.candidates.length > 0 && (
            <Box sx={{ mb: 1.75 }}>
              <AlsoRunIn
                variant="inline"
                candidates={runGroup.candidates}
                value={alsoRunIn}
                onChange={setAlsoRunIn}
                agentVersion={currentAgentVersion(envState)?.label}
              />
            </Box>
          )}
          {(() => {
            const groupWith = alsoRunIn.filter((id) =>
              runGroup.candidates.some((c) => c.envId === id && c.ready),
            );
            return (
              <Button
                fullWidth
                variant="contained"
                color="primary"
                size="small"
                onClick={() => {
                  setAddAnchor(null);
                  if (groupWith.length)
                    runGroup.start(groupWith, { trials: 3 });
                  else onStart();
                }}
                startIcon={<Iconify icon="solar:play-bold" width={16} />}
              >
                {groupWith.length
                  ? `Start in ${groupWith.length + 1} environments`
                  : "Start simulation"}
              </Button>
            );
          })()}
        </Box>
      </Popover>
    </Box>
  );
}

RunsSummary.propTypes = {
  env: PropTypes.object.isRequired,
  envState: PropTypes.object.isRequired,
  onGo: PropTypes.func,
  onStart: PropTypes.func,
};

/**
 * A column heading.
 *
 * It spans the whole cell rather than sitting over the value sub-column: a
 * heading squeezed into two thirds of its own column truncates "Policy
 * adherence" into "Policy ad…", and a column nobody can name is worse than one
 * whose title is a few pixels off centre. Wrapping over aligning, for the same
 * reason.
 */
/* ── run status ─────────────────────────────────────────────────────────
   Where the run is in its life, not how it scored — Pass and the graders
   already say that. Same soft chip and colours as the legacy runs table
   (BaseStatusCellRenderer + shared statusStyles), so status reads the same
   everywhere in the product. */
const STATUS_COL = 128;

const QUEUED_ID = "demo-queued";

/* Recorded runs carry status "running" until they finish, then "passed" /
   "failed" — which is the verdict, not the state. Trials are always done. */
const runStatus = (r) => {
  const raw = String(r?.status || "").toLowerCase();
  if (r?.kind === "trial") return "Completed";
  /* The spending limit (or the person) stopped new scenarios starting. */
  if (r?.finishedAt && r?.budget?.stoppedBy)
    return r.budget.stoppedBy === "stopped" ? "Cancelled" : "At limit";
  /* Unfinished for over a day: it ran past its deadline — an infrastructure
     outcome, not an agent failure. */
  if ((raw === "running" || raw === "queued") && !r.finishedAt && staleSince(r))
    return "Timed out";
  if (raw === "queued") return "Queued";
  if (raw === "pending") return "Pending";
  if (raw === "running" && !r.finishedAt) return "Running";
  if (["cancelled", "canceled", "stopped", "aborted"].includes(raw))
    return "Cancelled";
  if (["error", "errored", "crashed"].includes(raw)) return "Failed";
  return "Completed";
};

/* Running reads blue here; the neutral chip the legacy table uses for
   Running is kept for Queued — waiting, not yet doing anything. */
const RUN_CHIP_STYLES = {
  ...statusStyles,
  Running: statusStyles.Pending,
  Queued: statusStyles.Running,
  /* Stopped by the spending limit: amber, like the limit everywhere else. */
  "At limit": statusStyles.Inactive,
  "Timed out": statusStyles.Failed,
};

/**
 * Per-row kebab. Overlaid on the right edge of the row on hover.
 *
 * Delete opens the same confirm dialog the multi-select bar uses.
 */
function RunRowMenu({ onDelete }) {
  const [anchor, setAnchor] = useState(null);
  const stop = (e) => {
    e.stopPropagation();
  };
  return (
    <>
      <IconButton
        size="small"
        className="row-menu"
        onClick={(e) => {
          e.stopPropagation();
          setAnchor(e.currentTarget);
        }}
        onMouseDown={stop}
        aria-label="Run actions"
        sx={{
          position: "absolute",
          right: 6,
          top: "50%",
          transform: "translateY(-50%)",
          zIndex: 2,
          bgcolor: "background.paper",
          border: "1px solid",
          borderColor: "divider",
          opacity: anchor ? 1 : 0,
          transition: "opacity .12s ease",
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
        onClick={stop}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{ paper: { sx: { minWidth: 240 } } }}
      >
        <MenuItem
          onClick={(e) => {
            stop(e);
            setAnchor(null);
            onDelete();
          }}
          // eslint-disable-next-line no-restricted-syntax -- ds: destructive menu item
          sx={{ typography: "s2", gap: 1, color: "status.danger.fg" }}
        >
          <ListItemIcon sx={{ minWidth: "0 !important", mr: 0 }}>
            <Iconify
              icon="solar:trash-bin-trash-linear"
              width={16}
              sx={{ color: "status.danger.fg" }}
            />
          </ListItemIcon>
          <ListItemText
            primary="Delete run"
            primaryTypographyProps={{
              typography: "s2",
              color: "status.danger.fg",
            }}
          />
        </MenuItem>
      </Menu>
    </>
  );
}
RunRowMenu.propTypes = {
  run: PropTypes.object,
  onDelete: PropTypes.func,
};

function StatusCell({ status, improvements, onOpenImprovements }) {
  const n = improvements?.length || 0;
  const latest = improvements?.[0];
  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 0.75,
        minWidth: 0,
        pr: 1,
      }}
    >
      <Chip
        variant="soft"
        label={status}
        size="small"
        sx={{
          pointerEvents: "none",
          ...RUN_CHIP_STYLES[status],
        }}
      />
      {/* Improvements started from this run — they open on its own tab. */}
      {n > 0 && (
        <Tooltip
          arrow
          title={`${n} ${n === 1 ? "improvement" : "improvements"} from this run${latest?.status?.label ? ` · latest ${latest.status.label.toLowerCase()}` : ""}. Opens the run's Improvements tab.`}
        >
          <ButtonBase
            onClick={(e) => {
              e.stopPropagation();
              onOpenImprovements();
            }}
            aria-label={`${n} improvements from this run`}
            sx={{
              gap: 0.375,
              px: 0.5,
              height: 22,
              borderRadius: "4px",
              typography: "s3",
              fontVariantNumeric: "tabular-nums",
              color: "text.secondary",
              flexShrink: 0,
              "&:hover": { bgcolor: "action.hover", color: "text.primary" },
            }}
          >
            <Iconify icon="solar:graph-up-linear" width={14} />
            {n}
          </ButtonBase>
        </Tooltip>
      )}
    </Box>
  );
}
StatusCell.propTypes = {
  status: PropTypes.string,
  improvements: PropTypes.array,
  onOpenImprovements: PropTypes.func,
};

/* A run's env pairing and timestamp — the part of its name that gives way,
   with an ellipsis and the full text on hover, when the column is narrow. */
function RunDetail({ text, improvement }) {
  return (
    <Tooltip
      arrow
      title={[improvement && `From ${improvement.name}`, text]
        .filter(Boolean)
        .join(" · ")}
      placement="top-start"
    >
      <Typography
        noWrap
        sx={{
          typography: "s3",
          color: "text.tertiary",
          minWidth: 0,
          flex: "0 1 auto",
          overflow: "hidden",
          textOverflow: "ellipsis",
        }}
      >
        {/* The improvement that produced the run leads, and links to it. */}
        {improvement && (
          <>
            <FromImprovement improvement={improvement} />
            {" · "}
          </>
        )}
        {text}
      </Typography>
    </Tooltip>
  );
}
RunDetail.propTypes = { text: PropTypes.string, improvement: PropTypes.object };

/** A legend key: the eval's colour as a dot. */
function LegendDot({ color }) {
  return (
    <Box
      sx={{
        width: 8,
        height: 8,
        borderRadius: "50%",
        bgcolor: color,
        flexShrink: 0,
      }}
    />
  );
}
LegendDot.propTypes = { color: PropTypes.string };

function Line({ label, value }) {
  return (
    <Stack direction="row" alignItems="center" spacing={1}>
      <Typography sx={{ typography: "s3", color: "text.tertiary", flex: 1 }}>
        {label}
      </Typography>
      <Typography sx={{ typography: "s3", fontWeight: 600 }}>
        {value}
      </Typography>
    </Stack>
  );
}
Line.propTypes = { label: PropTypes.string, value: PropTypes.string };

/* ── the newer columns ─────────────────────────────────────────────────────
   Read from the run's tasks (runCompare): scenario pass is the share of
   scenarios that passed a majority of their repeats; the old Pass column,
   every repeat counted, is now headed Call pass. */
const fmtTook = (ms) => {
  if (ms == null) return "—";
  const m = Math.round(ms / 60000);
  if (m < 1) return `${Math.max(1, Math.round(ms / 1000))}s`;
  return m < 60 ? `${m}m` : `${Math.floor(m / 60)}h ${m % 60}m`;
};
const statsOf = (r) => (r?.finishedAt ? runStatsOf(r) : null);

/* What the run was charged, in credits — the same number as its Cost tab.
   The agent's own model cost is billed to the customer's key and never
   mixed into it. */
const costText = (credits) =>
  credits == null ? "—" : `${Math.round(credits).toLocaleString()} credits`;

/* A count against the same count in another run: lower is better. */
const countDelta = (now, before) =>
  now == null || before == null
    ? null
    : now === before
      ? { flat: true }
      : {
          text: `${Math.abs(now - before)}`,
          up: now > before,
          better: now < before,
        };

/* "65%  37/57" — the rate, the fraction it came from, and how it moved on
   the scenarios both runs measured (grey inside the noise). */
function scenarioCell(r, against) {
  const st = statsOf(r);
  const d =
    st && against && against.id !== r.id ? sharedDeltaOf(r, against) : null;
  const vs = against
    ? against.kind === "trial"
      ? `Run ${against.trialN}${against.selfImprovementName ? ` · ${against.selfImprovementName}` : ""}`
      : `Run ${against.ordinal}`
    : "";
  const delta =
    d &&
    (d.pts === 0
      ? { flat: true }
      : {
          text: `${Math.abs(d.pts)} pts`,
          up: d.pts > 0,
          better: d.pts > 0,
          muted: d.within,
        });
  const onlySome = r.kind === "trial" && r.partial;
  return (
    <Tooltip
      key="scenario"
      arrow
      disableHoverListener={!d && !onlySome}
      title={[
        /* An improvement's run measures only the scenarios it worked on. */
        onlySome
          ? "Target scenarios only — held-back scenarios are checked on the change it picks."
          : null,
        d
          ? `${d.pts > 0 ? "+" : ""}${d.pts} pts against ${vs} on the ${d.shared} scenarios both measured. Two runs of that size differ by up to ±${d.noise} pts by chance (95%)${d.within ? ", so this is within noise." : "."}`
          : null,
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <Box sx={{ display: "grid", minWidth: 0 }}>
        <MetricCell
          anchor
          quiet={!st?.measured}
          deltaWidth={64}
          delta={delta}
          text={
            st?.measured ? (
              <>
                {`${st.scenarioRate}%`}
                <Box
                  component="span"
                  sx={{ color: "text.tertiary", fontWeight: 400, ml: 0.75 }}
                >{`${st.scenarioPassed}/${st.measured}`}</Box>
              </>
            ) : (
              "—"
            )
          }
        />
      </Box>
    </Tooltip>
  );
}

/* Flaky · Failing · Calls · Run time · Started by. */
function addedCells(r, against) {
  const st = statsOf(r);
  const was = against ? statsOf(against) : null;
  return [
    <MetricCell
      key="flaky"
      quiet
      deltaWidth={56}
      text={st ? `${st.flaky}` : "—"}
      delta={st && was ? countDelta(st.flaky, was.flaky) : null}
    />,
    <MetricCell
      key="failing"
      quiet={!st?.failed}
      deltaWidth={56}
      text={st ? `${st.failed}` : "—"}
      delta={st && was ? countDelta(st.failed, was.failed) : null}
    />,
    <MetricCell
      key="calls"
      quiet
      text={
        st ? (
          <Tooltip
            arrow
            title={`${(r.tasks?.length ? r.tasks : r.trialTasks || []).length} scenarios × ${r.repeats || 1} ${(r.repeats || 1) === 1 ? "repeat" : "repeats"}`}
          >
            <Box component="span" sx={{ cursor: "default" }}>
              {st.calls.toLocaleString()}
            </Box>
          </Tooltip>
        ) : (
          "—"
        )
      }
    />,
    <MetricCell key="took" quiet text={fmtTook(st?.tookMs)} />,
    <Box
      key="who"
      sx={{
        pl: 2.5,
        pr: 1.25,
        display: "flex",
        alignItems: "center",
        minWidth: 0,
      }}
    >
      <Typography
        noWrap
        sx={{
          typography: "s2",
          color: r.startedBy ? "text.secondary" : "text.tertiary",
        }}
      >
        {r.startedBy || "—"}
        <Box component="span" sx={{ color: "text.tertiary" }}>
          {" · "}
          {runViaOf(r).label}
        </Box>
      </Typography>
    </Box>,
  ];
}
