import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { alpha, useTheme } from "@mui/material/styles";
import ReactApexChart from "../components/SafeApexChart";
import {
  Box, Stack, Typography, Button, Checkbox, Popover, Tooltip, IconButton,
  TextField, MenuItem, ListItemText, Chip,
} from "@mui/material";
import { ConfirmDialog } from "src/components/custom-dialog";
import { statusStyles } from "src/sections/common/simulation";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { runSummaries, evalSeries, trialSummaries, RUN_COLORS } from "../_mock/comparison";
import { TRIAL_CAP, DEMO_QUEUED_ROW } from "../_mock/runList";
import { currentEnvVersion, currentAgentVersion } from "../_mock/versions";
import { staleScenarios } from "../_mock/proofs";
import WinnerDrawer from "./WinnerDrawer";
import AppliedEvalsDrawer from "../workspace/evals/AppliedEvalsDrawer";
import { allMetrics, deltaAgainst } from "../_mock/winner";
import { useEnvState } from "../store";
import { neutralCheckboxSx } from "../components/primitives";

/* How many evals the chart legend names before folding the rest into "+N more". */
const LEGEND_MAX = 4;
/* Up to this many evals, the chart starts with all of them drawn. */
const ALL_SHOWN_MAX = 6;
/* With more evals than that, how many the chart starts with. */
const DEFAULT_SHOWN = 3;
/* How many of the most recent runs the chart shows before "Show all". */
const RUN_WINDOW = 10;
/* Length of each colour's segment where several evals share one line. */
const STRIPE = 10;
/* The runs chart's height. */
const CHART_HEIGHT = 200;

/* Timestamp for a run row. Falls back to startedAt and never prints
   "Invalid Date" for an in-progress run that has no finishedAt yet. */
const runTimeLabel = (r) => {
  const raw = r?.finishedAt || r?.startedAt;
  const d = raw ? new Date(raw) : null;
  if (!d || Number.isNaN(d.getTime())) return "in progress";
  return d.toLocaleString(undefined, {
    day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
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
  const navigate = useNavigate();
  const theme = useTheme();
  const { patch, release } = useEnvState(env.id);
  const [selected, setSelected] = useState(() => []);
  const [addAnchor, setAddAnchor] = useState(null);
  const [pickingWinner, setPickingWinner] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [addEvalsOpen, setAddEvalsOpen] = useState(false);
  /*
    Null means "all of them" rather than a copied list of ids: the evals arrive
    after the store hydrates, so seeding this with what exists on the first
    render would lock the chart to an empty set.
  */
  const [shownEvalIds, setShownEvalIds] = useState(null);
  /* The eval picker is opened from the legend's "+N more" as well as from
     its own control, so its open state lives here. */
  const [evalPickerOpen, setEvalPickerOpen] = useState(false);
  /* The chart shows the last RUN_WINDOW runs until someone asks for all. */
  const [allRunsInChart, setAllRunsInChart] = useState(false);
  /* The eval being hovered in the legend — its line is drawn on top and the
     rest fade, so one eval can be followed through a stack of others. */
  const [focusEvalId, setFocusEvalId] = useState(null);
  /* The legend's "+N more" dropdown. */
  const [moreAnchor, setMoreAnchor] = useState(null);
  /* An eval clicked in the legend — the chart shows only its line until it's
     clicked again. */
  const [isolatedEvalId, setIsolatedEvalId] = useState(null);

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

    Each trial of a self improvement was a full execution of the candidate
    prompt against the environment — a run, by any honest reading. Product
    feedback: they should be peer runs in the list, not hidden behind a
    Trials tab. So we merge manual and trial rows into one chronological
    stream and assign a single continuous ordinal across both — Run 4, 5,
    6, … regardless of which one is a manual re-run and which is a trial.
  */
  /* Demo-friendly cap (TRIAL_CAP, shared with the Runs tab badge): show at
     most 8 trials so the merged list stays readable at a glance. The mock
     generates far more; the search is the same story with 8 candidates. Take
     the earliest 8 so the narrative of the SI reads left-to-right. */
  const trials = useMemo(() => {
    const all = trialSummaries(env, envState);
    return all
      .slice()
      .sort((a, b) => new Date(a.finishedAt || 0) - new Date(b.finishedAt || 0))
      .slice(0, TRIAL_CAP);
  }, [env, envState]);

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
    const when = (r) => new Date(r.finishedAt || r.startedAt || Date.now()).getTime();
    const combined = [...summaries, ...trials]
      .filter((r) => !r.synthetic)
      .sort((a, b) => when(a) - when(b));
    const stamped = combined.map((r, i) => {
      const ord = i + 1;
      return {
        ...r,
        ordinal: ord,
        letter: String(ord),
        color: RUN_COLORS[(ord - 1) % RUN_COLORS.length],
      };
    });
    /* For every trial, find the ordinal of the manual run that spawned
       its self-improvement search — used by the "from Run N" chip on
       trial rows. Trial → optimization.fromRunId → run.ordinal. */
    const ordinalById = new Map(stamped.map((r) => [r.id, r.ordinal]));
    const parentByOptId = new Map(
      (envState.optimizations || []).map((o) => [o.id, o.fromRunId]),
    );
    return stamped.map((r) => {
      if (r.kind !== "trial") return r;
      const parentRunId = parentByOptId.get(r.selfImprovementId);
      return { ...r, parentRunOrdinal: parentRunId ? ordinalById.get(parentRunId) : null };
    });
  }, [summaries, trials, envState.optimizations]);

  /* Newest first for display — the run someone just finished is the top row. */
  const rows = useMemo(() => [...mergedRaw].reverse(), [mergedRaw]);

  /* Demo only: one run waiting in the queue on top, so the Queued status is
     visible. It has no results yet and stays out of the chart / compare /
     winner, which all read `rows` / `allSummaries` — but it can be selected
     and deleted, like any run nobody wants any more. */
  const tableRows = useMemo(() => {
    if (!DEMO_QUEUED_ROW || envState.demoQueuedRemoved || !rows.length) return rows;
    const next = Math.max(...rows.map((r) => r.ordinal || 0)) + 1;
    return [{
      id: QUEUED_ID,
      kind: "queued-demo",
      status: "queued",
      ordinal: next,
      letter: String(next),
      color: RUN_COLORS[(next - 1) % RUN_COLORS.length],
      agentVersion: currentAgentVersion(envState).label,
      envVersion: currentEnvVersion(env, envState).label,
    }, ...rows];
  }, [rows, env, envState]);

  /* Chart, compare, baseline lookups all read from the same merged list. */
  const allSummaries = mergedRaw;

  /* Eval series is now built off the merged list so trials contribute
     points, not just manual runs. Same shape evalSeries produces. */
  const series = useMemo(() => {
    const evalDefs = (envState?.evals || []).map((e) => ({ id: e.id })).filter((e) => e.id);
    const rawSeries = evalSeries(summaries, envState); /* keeps colour/name mapping */
    return rawSeries.map((s) => ({
      ...s,
      data: mergedRaw.map((r) => (r.scores?.[s.id] == null ? null : r.scores[s.id])),
    }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mergedRaw, summaries, envState]);

  /* One winner across the environment, not one per self improvement.
     `trialSummaries` marks the winner of each search — useful inside
     the self improvement view — but on the runs list a trophy per
     search reads as "many best runs". Reduce to the single trial with
     the highest passRate; earlier self improvements' winners lose the
     row-level winner treatment. */
  const overallWinnerId = useMemo(() => {
    const wins = trials.filter((t) => t.isWinner);
    if (!wins.length) return null;
    return wins.reduce((a, b) => (b.passRate > a.passRate ? b : a)).id;
  }, [trials]);
  /* Runs that covered everything — the ones the "same scenarios" claim is
     actually true of. */
  const full = summaries.filter((r) => r.total >= envState.scenarios.length);

  /*
    Fix my agent's projection, held against what happened.

    A proposed fix that claims +18 points and is never checked is a sales
    pitch. The expectation is recorded when the fix is applied; the first run of
    that version answers it — and an answer that is worse than the claim is the
    more useful of the two outcomes.
  */
  const expectation = envState.omegaExpectation;
  const verified = expectation
    ? summaries.find((r) => r.agentVersion === expectation.version)
    : null;
  const evals = useMemo(
    () => series.map((s) => ({ id: s.id, name: s.name, color: s.color })),
    [series],
  );
  const toggleIsolate = (id) => setIsolatedEvalId((cur) => (cur === id ? null : id));

  /* Four graders on one axis is already a lot; eight would be a scribble. The
     chart draws the ones asked for, and the table keeps all of them — the
     question "how is this moving" is narrower than "what are the numbers".
     With more than a handful, it starts on the few that need a look — the
     lowest-scoring in the latest run (the first few before anything has
     scored) — and the picker adds the rest. */
  const defaultShownIds = useMemo(() => {
    /* The graph exists to show every eval moving together, and nudging
       each eval's dots apart keeps half a dozen readable — so all of them
       up to ALL_SHOWN_MAX; the legend still folds past LEGEND_MAX. */
    if (evals.length <= ALL_SHOWN_MAX) return evals.map((e) => e.id);
    const latest = (id) => {
      const data = series.find((x) => x.id === id)?.data || [];
      for (let i = data.length - 1; i >= 0; i -= 1) if (data[i] != null) return data[i];
      return null;
    };
    const scored = evals.filter((e) => latest(e.id) != null);
    const picked = scored.length
      ? [...scored].sort((a, b) => latest(a.id) - latest(b.id)).slice(0, DEFAULT_SHOWN)
      : evals.slice(0, DEFAULT_SHOWN);
    const ids = new Set(picked.map((e) => e.id));
    /* Keep the evals' own order so colours and the picker line up. */
    return evals.filter((e) => ids.has(e.id)).map((e) => e.id);
  }, [evals, series]);
  const shown = useMemo(
    () => evals.filter((e) => (shownEvalIds || defaultShownIds).includes(e.id)),
    [evals, shownEvalIds, defaultShownIds],
  );
  const shownSeries = useMemo(
    () => series.filter((x) => shown.some((e) => e.id === x.id)),
    [series, shown],
  );

  /*
    Chart extension: after the manual runs, splice in trial points per
    self improvement so the chart shows the recovery a search produces
    rather than ending on the last manual run's decline. Trial scores per
    eval are synthesised as a monotonically ascending curve from the last
    manual point to a seeded winner value — the mock does not carry per-
    eval trial scores of its own, and inventing them here is honest as
    long as they stay monotone (self improvement, in the way the reader
    reads it, only goes up).
  */
  /*
    All evals on one line chart, runs along the bottom — the most recent
    RUN_WINDOW of them until "Show all".
  */
  const chartData = useMemo(() => {
    /* Chart plots the merged chronological stream — every run in the
       list contributes a point, trials included. That's what the
       "trials are peer runs" rule means: the trend line has to include
       them, otherwise the chart contradicts the table below it. */
    const windowed = !allRunsInChart && mergedRaw.length > RUN_WINDOW;
    const start = windowed ? mergedRaw.length - RUN_WINDOW : 0;
    const runs = mergedRaw.slice(start);
    const categories = runs.map((r, i) => `Run ${r.ordinal}${i === runs.length - 1 ? " · latest" : ""}`);
    const seriesOut = shownSeries.map((x) => ({
      id: x.id,
      name: x.name,
      color: x.color,
      data: x.data.slice(start),
    }));
    return { runs, start, categories, series: seriesOut, windowed };
  }, [shownSeries, mergedRaw, allRunsInChart]);
  /* One run can't be a line — it's drawn as a bar per eval until a second
     run exists, then the same graph becomes lines. */
  const singleRun = chartData.runs.length === 1;


  /* The same metric definitions the winner weights use, so a column that reads
     as an improvement here cannot count as a regression there. */
  const metrics = useMemo(
    () => Object.fromEntries(allMetrics(evals).map((m) => [m.id, m])),
    [evals],
  );

  const toggle = (id) =>
    setSelected((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));

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
    const ordered = baselineId && chronological.includes(baselineId)
      ? [baselineId, ...chronological.filter((id) => id !== baselineId)]
      : chronological;
    navigate(`${paths.dashboard.simulate.simulationCompare(env.id)}?runs=${ordered.join(",")}`);
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
     self improvement — removing them means removing the search — so we
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
    const nums = baseline ? [100, 100, 100, 100, 100, 100] : [60, 76, 60, 64, 110, 64];
    /* Grader names may wrap onto two lines, so their columns can be narrow. */
    const score = evals.length >= 4
      ? (baseline ? 108 : 84)
      : (baseline ? 132 : 88);
    const columns = [...nums, ...evals.map(() => score)];
    return {
      template: `26px minmax(152px, 360px) ${STATUS_COL}px ${columns.map((c) => `minmax(${c}px, 1fr)`).join(" ")}`,
      /* Below this the table scrolls rather than crushing the run names: the
         row's left padding, every column's floor, and the slice of the fade
         that overhangs the last grader's own right padding. */
      min: 20 + 26 + 152 + STATUS_COL + columns.reduce((a, c) => a + c, 0) + 8,
      deltaWidth: baseline ? 52 : 0,
      /* Where the system numbers end and the graders begin. */
      firstEval: 6,
    };
  }, [baseline, evals]);

  return (
    <Box sx={{ p: 2, display: "flex", flexDirection: "column", minHeight: "100%" }}>
      <Stack direction={{ xs: "column", md: "row" }} alignItems={{ md: "center" }} spacing={1.5} sx={{ mb: 2, flexShrink: 0 }}>
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "m2", fontWeight: 600 }}>Simulations summary</Typography>
          {/* Only claim the runs are comparable when they are. A partial re-run
              is a normal thing to do and a normal thing to say — what is not
              acceptable is a subtitle that keeps promising "the same scenarios"
              while one of the rows below covered three of them. */}
          {/* Counts what the table below lists — finished runs, plus any still
              waiting — so the header and "Runs (N)" never disagree. */}
          <Typography sx={{ typography: "s1", color: "text.secondary" }}>
            {mergedRaw.length} run{mergedRaw.length === 1 ? "" : "s"}
            {tableRows.length > mergedRaw.length && ` · ${tableRows.length - mergedRaw.length} queued`}
            {" · "}{envState.scenarios.length} scenarios
            {trials.length > 0 && ` · ${summaries.length} manual, ${trials.length} SI trials`}
          </Typography>
        </Box>
        <Stack direction="row" spacing={1} flexShrink={0}>
          {/* Only where the page has no Run simulation button of its own
              (Improvements) — the env workspace header already starts runs. */}
          {onStart && (
            <Button
              variant="outlined" color="inherit" size="small"
              onClick={(e) => setAddAnchor(e.currentTarget)}
              startIcon={<Iconify icon="solar:test-tube-linear" width={15} />}
              sx={{ typography: "s2", fontWeight: 700, borderColor: "divider" }}
            >
              Add more runs
            </Button>
          )}
          <Button
            variant="outlined" color="inherit" size="small"
            onClick={() => setAddEvalsOpen(true)}
            startIcon={<Iconify icon="solar:add-circle-linear" width={15} />}
            sx={{ typography: "s2", fontWeight: 700, borderColor: "divider" }}
          >
            Add Evals
          </Button>
          {/* The label does not change once a winner exists. Picking one is the
              same act every time — open the weights, decide, apply — and
              "Change winner" implied a different, smaller thing. */}
          <Button
            variant="outlined" color="inherit" size="small"
            onClick={() => setPickingWinner(true)}
            startIcon={<Iconify icon="solar:cup-star-bold" width={15} />}
            sx={{ typography: "s2", fontWeight: 700, borderColor: "divider" }}
          >
            Choose winner
          </Button>
        </Stack>
      </Stack>

      {/* ── one surface ──
          The chart and the runs were two bordered cards stacked with a gap,
          which spent three horizontal rules and 40px of air on a boundary
          nobody needed: the chart is a summary *of* these runs. One card, one
          divider. */}
      <Box
        sx={{
          border: "1px solid", borderColor: "divider", borderRadius: 1.5,
          bgcolor: "background.paper", overflow: "hidden",
          /* Grow to fill any remaining vertical space so the card
             reaches the bottom of the surrounding scroll container
             instead of leaving a blank strip beneath it (e.g. on
             Improvements L2 where the header is shorter than the
             env workspace's). */
          flex: 1, display: "flex", flexDirection: "column",
        }}
      >
        {/*
          Its own row rather than the card's action slot. Seven graders make
          both the selector's summary and the legend long, and in the header
          they grow leftward into the title until the two collide.
        */}
        <Stack
          direction="row" alignItems="center" spacing={2}
          sx={{ px: 2.5, pt: 1, pb: 0.5 }}
        >
          {/*
            Which graders to draw. The last one cannot be unticked — an empty
            chart is not a view anybody wanted, and the axis with nothing on
            it reads as a bug rather than as a choice.
          */}
          <TextField
            select size="small"
            value={shown.map((e) => e.id)}
            onChange={(e) => {
              const next = e.target.value;
              if (next.length) setShownEvalIds(next);
            }}
            SelectProps={{
              multiple: true,
              open: evalPickerOpen,
              onOpen: () => setEvalPickerOpen(true),
              onClose: () => setEvalPickerOpen(false),
              /* Summarised, not listed: four names are already wider than the
                 control and seven are a paragraph. */
              renderValue: (ids) => {
                if (ids.length === evals.length) return `All ${evals.length} evals`;
                if (ids.length === 1) return evals.find((x) => x.id === ids[0])?.name || "1 eval";
                return `${ids.length} of ${evals.length} evals`;
              },
              MenuProps: { PaperProps: { sx: { maxHeight: 320 } } },
            }}
            sx={{
              width: 200, flexShrink: 0,
              "& .MuiInputBase-input": {
                typography: "s2", py: 0.5,
                whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
              },
            }}
          >
            {evals.map((e) => {
              const on = shown.some((x) => x.id === e.id);
              return (
                <MenuItem key={e.id} value={e.id} sx={{ typography: "s2", py: 0.5 }}>
                  <Checkbox
                    size="small"
                    checked={on}
                    disabled={on && shown.length === 1}
                    sx={{ p: 0.5, mr: 0.75, ...neutralCheckboxSx }}
                  />
                  <Box sx={{ width: 8, height: 8, borderRadius: "50%", bgcolor: e.color, mr: 1, flexShrink: 0 }} />
                  <ListItemText primaryTypographyProps={{ typography: "s2" }} primary={e.name} />
                </MenuItem>
              );
            })}
          </TextField>

          {mergedRaw.length > RUN_WINDOW && (
            <Button
              size="small"
              onClick={() => setAllRunsInChart((v) => !v)}
              sx={{ typography: "s3", fontWeight: 600, color: "text.secondary", flexShrink: 0, whiteSpace: "nowrap", minWidth: 0 }}
            >
              {chartData.windowed
                ? `Last ${RUN_WINDOW} of ${mergedRaw.length} runs · Show all`
                : `All ${mergedRaw.length} runs · Show last ${RUN_WINDOW}`}
            </Button>
          )}

          {/*
            One line, always. With a handful of evals the legend lists them
            all; past that it lists the first few and folds the rest into
            "+N more", which opens the picker beside it — the picker already
            carries every eval with its colour, so nothing is lost. Ten
            names wrapping into three lines over a 160px chart was the
            legend outgrowing the chart it labels.
          */}
          {(() => {
            const room = shown.length > LEGEND_MAX ? LEGEND_MAX - 1 : shown.length;
            const listed = shown.slice(0, room);
            const rest = shown.length - listed.length;
            return (
              <Stack
                direction="row" alignItems="center" spacing={1.5}
                sx={{ flex: 1, minWidth: 0, justifyContent: "flex-end", overflow: "hidden" }}
              >
                {/* While one eval is isolated the chart is filtered — say so,
                    with the way back, even when that eval sits in "+N more". */}
                {isolatedEvalId && shown.some((e) => e.id === isolatedEvalId) && (
                  <Button
                    size="small"
                    onClick={() => setIsolatedEvalId(null)}
                    startIcon={<Iconify icon="mingcute:close-line" width={12} />}
                    sx={{
                      typography: "s3", fontWeight: 700, color: "text.primary", flexShrink: 0, minWidth: 0,
                      px: 0.75, py: 0.125, border: "1px solid", borderColor: "divider", borderRadius: 0.75,
                      "& .MuiButton-startIcon": { mr: 0.5 },
                    }}
                  >
                    Show all evals
                  </Button>
                )}
                {listed.map((e) => {
                  const isolated = isolatedEvalId === e.id;
                  const faded = (isolatedEvalId && !isolated) || (focusEvalId && focusEvalId !== e.id);
                  return (
                    <Tooltip
                      key={e.id} arrow
                      title={isolated ? `${e.name} — click to show all evals` : `${e.name} — click to show only this`}
                    >
                      <Stack
                        direction="row" alignItems="center" spacing={0.625}
                        /* Hover lifts this eval's line above the rest; click
                           shows only its line. */
                        onMouseEnter={() => setFocusEvalId(e.id)}
                        onMouseLeave={() => setFocusEvalId(null)}
                        onClick={() => toggleIsolate(e.id)}
                        sx={{
                          minWidth: 0, cursor: "pointer",
                          px: 0.5, py: 0.25, mx: -0.5, borderRadius: 0.75,
                          bgcolor: isolated ? "action.selected" : "transparent",
                          opacity: faded ? 0.45 : 1,
                          transition: "opacity .12s",
                          "&:hover": { bgcolor: isolated ? "action.selected" : "action.hover" },
                        }}
                      >
                        <LegendDot color={e.color} />
                        <Typography noWrap sx={{ typography: "s3", color: "text.secondary", maxWidth: 140 }}>{e.name}</Typography>
                      </Stack>
                    </Tooltip>
                  );
                })}
                {rest > 0 && (
                  <>
                    <Typography
                      component="button"
                      onClick={(ev) => setMoreAnchor(ev.currentTarget)}
                      sx={{
                        display: "inline-flex", alignItems: "center", gap: 0.25,
                        typography: "s3", fontWeight: 700, color: "text.primary", flexShrink: 0,
                        border: "1px solid", borderColor: moreAnchor ? "text.disabled" : "divider", borderRadius: 0.75,
                        bgcolor: moreAnchor ? "action.hover" : "transparent", px: 0.75, py: 0.125, cursor: "pointer",
                        "&:hover": { borderColor: "text.disabled", bgcolor: "action.hover" },
                      }}
                    >
                      {`+${rest} more`}
                      <Iconify icon={moreAnchor ? "solar:alt-arrow-up-linear" : "solar:alt-arrow-down-linear"} width={11} />
                    </Typography>
                    {/* The rest of the legend, in a dropdown — same dot, full
                        name, and hovering one lifts its line like the legend
                        items above. */}
                    <Popover
                      open={!!moreAnchor}
                      anchorEl={moreAnchor}
                      onClose={() => { setMoreAnchor(null); setFocusEvalId(null); }}
                      anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
                      transformOrigin={{ vertical: "top", horizontal: "right" }}
                      slotProps={{ paper: { sx: { mt: 0.5, py: 0.5, minWidth: 220, maxWidth: 380, maxHeight: 300, borderRadius: 1.25 } } }}
                    >
                      {shown.slice(room).map((e) => (
                        <Stack
                          key={e.id}
                          direction="row" alignItems="center" spacing={1}
                          onMouseEnter={() => setFocusEvalId(e.id)}
                          onMouseLeave={() => setFocusEvalId(null)}
                          onClick={() => { toggleIsolate(e.id); setMoreAnchor(null); setFocusEvalId(null); }}
                          sx={{
                            px: 1.5, py: 0.75, cursor: "pointer",
                            bgcolor: isolatedEvalId === e.id ? "action.selected" : "transparent",
                            "&:hover": { bgcolor: isolatedEvalId === e.id ? "action.selected" : "action.hover" },
                          }}
                        >
                          <LegendDot color={e.color} />
                          <Typography sx={{ typography: "s2", color: "text.primary", wordBreak: "break-word", flex: 1 }}>
                            {e.name}
                          </Typography>
                          {isolatedEvalId === e.id && (
                            <Typography sx={{ typography: "s3", color: "text.subtitle", flexShrink: 0 }}>only this</Typography>
                          )}
                        </Stack>
                      ))}
                    </Popover>
                  </>
                )}
              </Stack>
            );
          })()}
        </Stack>

        <Box sx={{ px: 0, pt: 0.5, pb: 0.5, width: "100%" }}>
          {(() => {
            /* A clicked legend item shows only that eval's line. */
            const isolated = isolatedEvalId && chartData.series.some((x) => x.id === isolatedEvalId) ? isolatedEvalId : null;
            const visible = isolated ? chartData.series.filter((x) => x.id === isolated) : chartData.series;
            const focus = !isolated && focusEvalId && visible.some((x) => x.id === focusEvalId) ? focusEvalId : null;
            /* Hovering an eval in the legend draws it last (on top) and fades the rest. */
            const ordered = focus
              ? [...visible.filter((x) => x.id !== focus), ...visible.filter((x) => x.id === focus)]
              : visible;
            const colorOf = (x) => (focus && x.id !== focus ? alpha(x.color, 0.18) : x.color);

            /*
              Evals that score the same on every run draw exactly the same
              line, so only the one drawn last shows. Those lines — and only
              those — are dashed with the same pattern, each shifted along by
              one segment, so the colours take turns: green, blue, orange,
              pink, green… The shared line reads as striped, every eval on it
              is visible, and lines that don't overlap stay solid. The line
              stays exactly on the score — nothing is moved. Hovering or
              isolating an eval draws it solid on its own.
            */
            const stripeGroups = (() => {
              if (singleRun || focus || isolated) return [];
              const byLine = new Map();
              ordered.forEach((x, i) => {
                if (x.data.filter((v) => v != null).length < 2) return;
                const key = JSON.stringify(x.data);
                if (!byLine.has(key)) byLine.set(key, []);
                byLine.get(key).push(i);
              });
              return [...byLine.values()].filter((idx) => idx.length > 1);
            })();
            /* The chart library takes one dash length per line but no offset,
               so the offset is set on the drawn paths after each render. */
            const applyStripes = (ctx) => {
              const root = ctx?.el;
              if (!root) return;
              stripeGroups.forEach((idx) => {
                const n = idx.length;
                idx.forEach((si, k) => {
                  const path = root.querySelector(`.apexcharts-series[data\\:realIndex="${si}"] path.apexcharts-line`);
                  if (!path) return;
                  path.setAttribute("stroke-dasharray", `${STRIPE} ${STRIPE * (n - 1)}`);
                  path.setAttribute("stroke-dashoffset", String(((n - k) % n) * STRIPE));
                });
              });
            };
            return (
              <ReactApexChart
                key={singleRun ? "bars" : "lines"}
                type={singleRun ? "bar" : "line"}
                height={CHART_HEIGHT}
                width="100%"
                series={ordered.map((x) => ({ name: x.name, data: x.data }))}
                options={{
                  chart: {
                    toolbar: { show: false },
                    zoom: { enabled: false },
                    animations: { enabled: false },
                    fontFamily: theme.typography.fontFamily,
                    background: "transparent",
                    parentHeightOffset: 0,
                    events: { mounted: applyStripes, updated: applyStripes },
                  },
                  theme: { mode: theme.palette.mode },
                  colors: ordered.map(colorOf),
                  ...(singleRun ? {
                    plotOptions: {
                      bar: {
                        /* Narrow bars side by side: wide enough to read,
                           never a slab across half the card. */
                        columnWidth: `${Math.min(36, 7 * ordered.length)}%`,
                        borderRadius: 3,
                        borderRadiusApplication: "end",
                        dataLabels: { position: "top" },
                      },
                    },
                  } : {}),
                  /* Bars: a transparent outline is the gap between them.
                     Lines: an eval with a single point in view has no line
                     to draw — and the chart library draws one anyway, from
                     the point down to the axis — so it gets its dot only. */
                  stroke: singleRun
                    ? { show: true, width: 4, colors: ["transparent"] }
                    : {
                      width: ordered.map((x) => (x.data.filter((v) => v != null).length >= 2 ? 2 : 0)),
                      curve: "straight",
                    },
                  markers: singleRun ? { size: 0 } : { size: 4, strokeWidth: 0, hover: { size: 6 } },
                  legend: { show: false },
                  dataLabels: singleRun ? {
                    enabled: true,
                    formatter: (v) => (v == null ? "" : `${v}%`),
                    offsetY: -16,
                    style: { fontSize: "11px", fontWeight: 600, colors: [theme.palette.text.secondary] },
                  } : { enabled: false },
                  grid: {
                    borderColor: theme.palette.divider,
                    strokeDashArray: 4,
                    xaxis: { lines: { show: false } },
                    padding: { left: 8, right: 16, top: 8, bottom: 0 },
                  },
                  xaxis: {
                    categories: chartData.categories,
                    axisBorder: { show: false },
                    axisTicks: { show: false },
                    labels: {
                      style: { colors: theme.palette.text.secondary, fontSize: "11px" },
                      rotate: 0,
                      hideOverlappingLabels: true,
                      trim: false,
                    },
                    tooltip: { enabled: false },
                  },
                  yaxis: {
                    min: 0, max: 100, tickAmount: 4,
                    labels: { style: { colors: theme.palette.text.secondary, fontSize: "11px" }, formatter: (v) => `${Math.round(v)}` },
                  },
                  tooltip: {
                    shared: !singleRun,
                    intersect: singleRun,
                    y: {
                      /* A subset run belongs on the line — it happened — but
                         a point from four scenarios next to one from
                         seventeen invites the wrong read, so it's labelled. */
                      formatter: (v, opts) => {
                        if (v == null) return "—";
                        const run = chartData.runs[opts?.dataPointIndex ?? -1];
                        const partial = run && run.total < envState.scenarios.length;
                        return `${v}%${partial ? ` · ${run.total} of ${envState.scenarios.length} scenarios` : ""}${run?.kind === "trial" ? " · trial" : ""}`;
                      },
                    },
                  },
                }}
              />
            );
          })()}
        </Box>

        {/* ── did the fix do what it was projected to do ── */}
      {expectation && verified && (
        <Stack
          direction="row" alignItems="flex-start" spacing={1.5}
          sx={{
            mb: 2, px: 2.5, py: 1.75, borderRadius: 1.5, border: "1px solid",
            borderColor: (t) => alpha(verified.passRate >= expectation.projected ? "#16A34A" : "#CA8A04", 0.35),
            bgcolor: (t) => alpha(
              verified.passRate >= expectation.projected ? "#16A34A" : "#CA8A04",
              t.palette.mode === "dark" ? 0.1 : 0.05,
            ),
          }}
        >
          <Iconify
            icon={verified.passRate >= expectation.projected ? "solar:check-circle-bold" : "solar:info-circle-bold"}
            width={16}
            sx={{ color: verified.passRate >= expectation.projected ? "#16A34A" : "#CA8A04", flexShrink: 0, mt: "1px" }}
          />
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "s2", fontWeight: 700 }}>
              Fix my agent projected {expectation.projected}% for agent {expectation.version} — it came in at {verified.passRate}%
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
            sx={{ typography: "s2", fontWeight: 600, color: "text.secondary", flexShrink: 0 }}
          >
            Dismiss
          </Button>
        </Stack>
      )}

      {/* ── the runs ── */}
        <Stack
          direction="row" alignItems="center" spacing={2}
          sx={{ px: 2.5, py: 1.25, borderTop: "1px solid", borderColor: "divider" }}
        >
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "s1", fontWeight: 600 }}>Runs ({tableRows.length})</Typography>
            <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
              {baseline ? (
                <>
                  Measured against{" "}
                  <Box component="span" sx={{ color: "text.primary", fontWeight: 700 }}>
                    Run {baseline.ordinal} · agent {baseline.agentVersion}
                  </Box>
                  {" "}— rates in points, everything else in percent.
                </>
              ) : "Select two or more to compare them scenario by scenario"}
            </Typography>
          </Box>
          {
          selected.length === 0 && baseline ? (
            <Button
              size="small"
              onClick={() => patch({ baselineRunId: null })}
              startIcon={<Iconify icon="mingcute:close-line" width={14} />}
              sx={{ typography: "s2", fontWeight: 700, color: "primary.main", flexShrink: 0 }}
            >
              Remove baseline
            </Button>
          ) : (
            selected.length > 0 && (
            <Stack direction="row" alignItems="center" spacing={1}>
              <Typography sx={{ typography: "s2", color: "text.secondary", mr: 0.5 }}>
                {selected.length} selected
              </Typography>

              {/* One run is a baseline; two or more is a comparison. Offering
                  both at once would ask people to work out which button their
                  selection is even eligible for. A queued run is neither — with
                  one in the selection, only Delete applies to it. */}
              {selected.length === 1 && !selectedQueued ? (
                <Button
                  variant="contained" color="primary" size="small"
                  onClick={setBaseline}
                  startIcon={<Iconify icon="solar:transfer-vertical-linear" width={15} />}
                  sx={{ typography: "s2", fontWeight: 700 }}
                >
                  {selected[0] === baselineId ? "Clear baseline" : "Set as baseline"}
                </Button>
              ) : selectedFinished.length >= 2 && !selectedQueued && (
                <Button
                  variant="contained" color="primary" size="small"
                  onClick={compare}
                  startIcon={<Iconify icon="solar:transfer-horizontal-linear" width={15} />}
                  sx={{ typography: "s2", fontWeight: 700 }}
                >
                  Compare
                </Button>
              )}

              <Button
                variant="outlined" color="inherit" size="small"
                onClick={() => setDeleting(true)}
                startIcon={<Iconify icon="solar:trash-bin-trash-linear" width={15} />}
                sx={{ typography: "s2", fontWeight: 700, borderColor: "divider" }}
              >
                Delete
              </Button>

              <Tooltip arrow title="Clear selection">
                <IconButton size="small" onClick={() => setSelected([])}>
                  <Iconify icon="mingcute:close-line" width={16} sx={{ color: "text.subtitle" }} />
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
            flex: 1, minHeight: 0,
            "&::after": {
              content: '""',
              position: "sticky", right: 0, top: 0, float: "right",
              width: 28, height: "100%", pointerEvents: "none",
              backgroundImage: (t) => `linear-gradient(to right, transparent, ${t.palette.background.paper})`,
            },
          }}
        >
          <Box sx={{ minWidth: grid.min }}>
            <Box
              sx={{
                display: "grid", gridTemplateColumns: grid.template,
                alignItems: "flex-end", columnGap: 0,
                pl: 2.5, pr: 0, pt: 1.5, pb: 1,
                borderBottom: "1px solid", borderColor: "divider",
              }}
            >
              <Box />
              <Head>Run</Head>
              <Head>Status</Head>
              <Head right>Pass</Head>
              <Head right title="Average duration per task">Duration</Head>
              <Head right>Tokens</Head>
              <Head right>Cost</Head>
              <Head right title="Tasks where the agent said it was done but the world shows it wasn't">Said, not done</Head>
              <Head right title="Mean return — the environment's reward, averaged over tasks">Return</Head>
              {evals.map((e, i) => (
                <Head key={e.id} right divider={i === 0} last={i === evals.length - 1} title={e.name} wrap>
                  {/* snake_case names are one long word — a break opportunity
                      after each underscore lets them wrap like the rest. */}
                  {e.name.replace(/_/g, "_​")}
                </Head>
              ))}
            </Box>

            <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
              {tableRows.map((r, i) => {
                const picked = selected.includes(r.id);
                const won = winner?.runId === r.id;

                if (r.kind === "queued-demo") {
                  return (
                    <Box
                      key={r.id}
                      sx={{
                        display: "grid", gridTemplateColumns: grid.template,
                        alignItems: "stretch", columnGap: 0,
                        pl: 2.5, pr: 0, py: 0, minHeight: 40,
                        borderLeft: "2px solid", borderColor: "transparent",
                      }}
                    >
                      <Box
                        sx={{ display: "flex", alignItems: "center", cursor: "pointer" }}
                        onClick={() => toggle(r.id)}
                      >
                        <Checkbox
                          size="small" checked={picked} readOnly tabIndex={-1}
                          sx={{ p: 0.5, pointerEvents: "none", ...neutralCheckboxSx }}
                        />
                      </Box>
                      <Stack direction="row" alignItems="center" spacing={1.25} sx={{ minWidth: 0, py: 0.875, pr: 1.5, overflow: "hidden" }}>
                        <Box
                          sx={{
                            minWidth: 26, height: 22, px: 0.75, borderRadius: 0.75, flexShrink: 0,
                            display: "grid", placeItems: "center",
                            typography: "s3", fontWeight: 700, fontVariantNumeric: "tabular-nums",
                            color: r.color,
                            bgcolor: (t) => alpha(r.color, t.palette.mode === "dark" ? 0.22 : 0.14),
                          }}
                        >
                          {r.letter}
                        </Box>
                        <Stack direction="row" alignItems="baseline" spacing={0.75} sx={{ minWidth: 0, overflow: "hidden" }}>
                          <Typography noWrap sx={{ typography: "s2", fontWeight: 600, flexShrink: 0 }}>
                            Run {r.ordinal} · agent {r.agentVersion}
                          </Typography>
                          <RunDetail text={`× env ${r.envVersion} · waiting to start`} />
                        </Stack>
                      </Stack>
                      <StatusCell status="Queued" />
                      {Array.from({ length: 6 }).map((_, k) => (
                        <MetricCell key={k} quiet deltaWidth={grid.deltaWidth} text="—" />
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
                        display: "grid", gridTemplateColumns: grid.template,
                        alignItems: "stretch", columnGap: 0,
                        pl: 2.5, pr: 0, py: 0, minHeight: 40, cursor: "pointer",
                        borderLeft: "2px solid",
                        borderColor: r.id === overallWinnerId
                          ? "#EA580C"
                          : r.id === baselineId ? "primary.main" : "transparent",
                        bgcolor: picked ? (t) => alpha(t.palette.primary.main, 0.05) : "transparent",
                        "&:hover": { bgcolor: "action.hover" },
                      }}
                      onClick={() => navigate(paths.dashboard.simulate.simulationRun(env.id, r.id))}
                    >
                      <Box
                        sx={{ display: "flex", alignItems: "center" }}
                        onClick={(e) => { e.stopPropagation(); toggle(r.id); }}
                      >
                        <Checkbox
                          size="small" checked={picked} readOnly tabIndex={-1}
                          sx={{ p: 0.5, pointerEvents: "none", ...neutralCheckboxSx }}
                        />
                      </Box>

                      <Stack direction="row" alignItems="center" spacing={1.25} sx={{ minWidth: 0, py: 0.875, pr: 1.5, overflow: "hidden" }}>
                        <Box
                          sx={{
                            minWidth: 26, height: 22, px: 0.75, borderRadius: 0.75, flexShrink: 0,
                            display: "grid", placeItems: "center",
                            typography: "s3", fontWeight: 700,
                            fontVariantNumeric: "tabular-nums",
                            color: r.color,
                            bgcolor: (t) => alpha(r.color, t.palette.mode === "dark" ? 0.22 : 0.14),
                          }}
                        >
                          {r.letter}
                        </Box>
                        {r.id === overallWinnerId && (
                          <Tooltip arrow title="Best-scoring run in this environment">
                            <Box sx={{ display: "flex", alignItems: "center", flexShrink: 0 }}>
                              <Iconify icon="solar:cup-star-bold" width={14} sx={{ color: "#EA580C" }} />
                            </Box>
                          </Tooltip>
                        )}
                        <Stack direction="row" alignItems="baseline" spacing={0.75} sx={{ minWidth: 0, overflow: "hidden" }}>
                          <Typography noWrap sx={{ typography: "s2", fontWeight: 600, flexShrink: 0 }}>
                            Run {r.ordinal} · agent {r.agentVersion}
                          </Typography>
                          <RunDetail text={[r.envVersion && `× env ${r.envVersion}`, runTimeLabel(r)].filter(Boolean).join(" · ")} />
                          {r.toolGap?.length > 0 && (
                            <Tooltip
                              arrow
                              title={`Run anyway on environment ${r.envVersion}, which can't answer ${r.toolGap.join(", ")}. Scenarios that called ${r.toolGap.length === 1 ? "it" : "them"} got no answer and are not measured.`}
                            >
                              <Box sx={{ display: "inline-flex", flexShrink: 0, color: "text.secondary" }}>
                                <Iconify icon="solar:plug-circle-linear" width={15} />
                              </Box>
                            </Tooltip>
                          )}
                          {/* SI provenance chip — this trial belongs to the
                              self-improvement search launched from Run N.
                              Clicking navigates to that parent run so the
                              SI story is reachable in one hop. */}
                          {r.id === baselineId && (
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
                      </Stack>

                      <StatusCell status={runStatus(r)} />
                      <MetricCell
                        anchor deltaWidth={grid.deltaWidth} text={`${r.passRate}%`}
                        delta={deltaAgainst(metrics.passRate, r, baseline)}
                      />
                      <MetricCell
                        quiet deltaWidth={grid.deltaWidth} text={`${(r.avgDurationMs / 1000).toFixed(1)}s`}
                        delta={deltaAgainst(metrics.duration, r, baseline)}
                      />
                      <MetricCell
                        quiet deltaWidth={grid.deltaWidth} text={compactTokens(r.tokens)}
                        delta={deltaAgainst(metrics.tokens, r, baseline)}
                      />
                      <MetricCell
                        quiet deltaWidth={grid.deltaWidth} text={`$${r.cost.toFixed(2)}`}
                        delta={deltaAgainst(metrics.cost, r, baseline)}
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
                          delta={deltaAgainst(metrics[`eval:${e.id}`], r, baseline)}
                        />
                      ))}
                    </Box>
                  );
                }

                return (
                  <Box
                    key={r.id}
                    sx={{
                      display: "grid", gridTemplateColumns: grid.template,
                      alignItems: "stretch", columnGap: 0,
                      pl: 2.5, pr: 0, py: 0, minHeight: 40, cursor: "pointer",
                      /*
                        Marked by a rule and a chip, not by a filled row. Two
                        tinted rows in a table of five — one amber, one blue —
                        turned a list of numbers into a set of coloured bands,
                        and the numbers are what people came to read.
                      */
                      borderLeft: "2px solid",
                      borderColor: won
                        ? "#EA580C"
                        : r.id === baselineId ? "primary.main" : "transparent",
                      bgcolor: picked ? (t) => alpha(t.palette.primary.main, 0.05) : "transparent",
                      "&:hover": { bgcolor: "action.hover", "& .row-open": { opacity: 1 } },
                    }}
                    onClick={() => navigate(paths.dashboard.simulate.simulationRun(env.id, r.id))}
                  >
                    {/* The whole cell toggles. An 18px box inside a row that
                        navigates is a target you have to aim at, and missing it
                        opens the run instead of selecting it. */}
                    <Box
                      sx={{ display: "flex", alignItems: "center" }}
                      onClick={(e) => { e.stopPropagation(); toggle(r.id); }}
                    >
                      <Checkbox
                        size="small"
                        checked={picked}
                        readOnly
                        tabIndex={-1}
                        sx={{ p: 0.5, pointerEvents: "none", ...neutralCheckboxSx }}
                      />
                    </Box>

                    <Stack direction="row" alignItems="center" spacing={1.25} sx={{ minWidth: 0, py: 0.875, pr: 1.5 }}>
                      {/* Numeric chip = the continuous ordinal, same
                          scheme across manual runs and trials so a row's
                          badge always matches its "Run N" label. */}
                      <Box
                        sx={{
                          minWidth: 26, height: 22, px: 0.75, borderRadius: 0.75, flexShrink: 0,
                          display: "grid", placeItems: "center",
                          typography: "s3", fontWeight: 700,
                          fontVariantNumeric: "tabular-nums",
                          color: r.color,
                          bgcolor: (t) => alpha(r.color, t.palette.mode === "dark" ? 0.22 : 0.14),
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
                      <Box minWidth={0} sx={{ display: "flex", alignItems: "center", minWidth: 0, overflow: "hidden" }}>
                        <Stack direction="row" alignItems="baseline" spacing={0.75} sx={{ minWidth: 0, overflow: "hidden" }}>
                          {/* The run's name never shrinks — "Ru…" identifies
                              nothing. The env version it was pinned to and
                              the timestamp are the detail, so they are what
                              truncates when the column is narrow (the full
                              text stays in the tooltip). Env is only shown
                              if the run recorded one; older runs omit it
                              rather than reading as "env unknown". Short
                              timestamp, no task count: every run here runs
                              the same scenarios. */}
                          <Typography noWrap sx={{ typography: "s2", fontWeight: 600, flexShrink: 0 }}>
                            Run {r.ordinal} · agent {r.agentVersion}
                          </Typography>
                          <RunDetail text={[r.envVersion && `× env ${r.envVersion}`, runTimeLabel(r)].filter(Boolean).join(" · ")} />
                          {r.toolGap?.length > 0 && (
                            <Tooltip
                              arrow
                              title={`Run anyway on environment ${r.envVersion}, which can't answer ${r.toolGap.join(", ")}. Scenarios that called ${r.toolGap.length === 1 ? "it" : "them"} got no answer and are not measured.`}
                            >
                              <Box sx={{ display: "inline-flex", flexShrink: 0, color: "text.secondary" }}>
                                <Iconify icon="solar:plug-circle-linear" width={15} />
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
                          {/* What is actually live, on the row that put it
                              there — so "compare against production" is a fact
                              on the screen rather than a memory. */}
                          {envState.releases?.[0]?.version === r.agentVersion && (
                            <Typography
                              sx={{
                                px: 0.75, py: 0.125, borderRadius: 0.5, flexShrink: 0,
                                typography: "s3", fontWeight: 700, color: "#16A34A",
                                bgcolor: (t) => alpha("#16A34A", t.palette.mode === "dark" ? 0.2 : 0.12),
                              }}
                            >
                              Live
                            </Typography>
                          )}
                          {r.id === baselineId && (
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
                          {won && (
                            <Stack
                              direction="row" alignItems="center" spacing={0.375}
                              sx={{
                                px: 0.75, py: 0.125, borderRadius: 0.5, flexShrink: 0,
                                bgcolor: (t) => alpha("#EA580C", t.palette.mode === "dark" ? 0.2 : 0.12),
                              }}
                            >
                              <Iconify icon="solar:cup-star-bold" width={12} sx={{ color: "#EA580C" }} />
                              <Typography sx={{ typography: "s3", fontWeight: 700, color: "#EA580C" }}>
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
                              direction="row" alignItems="center" spacing={0.375}
                              sx={{
                                px: 0.75, py: 0.125, borderRadius: 0.5, flexShrink: 0,
                                bgcolor: (t) => alpha("#7857FC", t.palette.mode === "dark" ? 0.16 : 0.08),
                              }}
                            >
                              <Iconify icon="solar:pen-2-linear" width={11} sx={{ color: "#7857FC" }} />
                              <Typography sx={{ typography: "s3", fontWeight: 700, color: "#7857FC" }}>
                                {r.twinWrites} write{r.twinWrites === 1 ? "" : "s"}
                              </Typography>
                            </Stack>
                          )}
                        </Stack>
                      </Box>
                      {/* The row opens the run. Nothing else on it said so. */}
                      <Iconify
                        icon="eva:arrow-ios-forward-fill"
                        width={15}
                        className="row-open"
                        sx={{ opacity: 0, transition: "opacity .12s", color: "text.subtitle", flexShrink: 0 }}
                      />
                    </Stack>

                    <StatusCell status={runStatus(r)} />
                    <MetricCell
                      anchor deltaWidth={grid.deltaWidth} text={`${r.passRate}%`}
                      delta={deltaAgainst(metrics.passRate, r, baseline)}
                    />
                    <MetricCell
                      quiet deltaWidth={grid.deltaWidth} text={`${(r.avgDurationMs / 1000).toFixed(1)}s`}
                      delta={deltaAgainst(metrics.duration, r, baseline)}
                    />
                    <MetricCell
                      quiet deltaWidth={grid.deltaWidth} text={compactTokens(r.tokens)}
                      delta={deltaAgainst(metrics.tokens, r, baseline)}
                    />
                    <MetricCell
                      quiet deltaWidth={grid.deltaWidth} text={`$${r.cost.toFixed(2)}`}
                      delta={deltaAgainst(metrics.cost, r, baseline)}
                    />
                    {/* The one system number that is a finding rather than a
                        measurement, so it is the one that gets an accent. */}
                    <MetricCell
                      quiet={!r.saidNotDone}
                      tone={r.saidNotDone ? "#DC2626" : undefined}
                      deltaWidth={grid.deltaWidth} text={`${r.saidNotDone}`}
                      delta={deltaAgainst(metrics.saidNotDone, r, baseline)}
                    />
                    {/* The environment's own number, beside the graders it is
                        computed from so the two can be read against each other
                        rather than living on different pages. */}
                    <MetricCell
                      anchor deltaWidth={grid.deltaWidth}
                      text={r.meanReturn == null ? "—" : r.meanReturn.toFixed(2)}
                      delta={deltaAgainst(metrics.meanReturn, r, baseline)}
                    />

                    {evals.map((e, ei) => (
                      <ScoreCell
                        key={e.id}
                        value={r.scores[e.id]}
                        divider={ei === 0}
                        last={ei === evals.length - 1}
                        deltaWidth={grid.deltaWidth}
                        delta={deltaAgainst(metrics[`eval:${e.id}`], r, baseline)}
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
            ? `${selectedManualOnly.length} manual run${selectedManualOnly.length === 1 ? "" : "s"} will be deleted. ${selected.length - selectedManualOnly.length} self-improvement trial${selected.length - selectedManualOnly.length === 1 ? "" : "s"} will be left alone — trials live under their search and are removed by deleting the self improvement.`
            : selectedQueued && selectedFinished.length === 0
              ? "It hasn't started, so it comes off the queue and there are no results to lose."
              : `Their results go with them, and anything measured against them — a baseline, a winner — is cleared. The scenarios and the environment are untouched.${selectedQueued ? " The queued run comes off the queue before it starts." : ""}`
        }
        action={
          <Button
            variant="contained" color="error"
            onClick={removeSelected}
            disabled={selectedManualOnly.length === 0}
            sx={{ typography: "s2", fontWeight: 700 }}
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
        /* The gate needs something to regress against and the full set to
           check coverage against — both live out here. */
        baseline={baseline}
        scenarioCount={envState.scenarios.length}
        initial={winner?.weights}
        released={envState.releases?.[0]?.version}
        onRelease={(entry) => { release(entry); setPickingWinner(false); }}
        onApply={(w) => { patch({ winner: w }); setPickingWinner(false); }}
      />

      <Popover
        open={!!addAnchor}
        anchorEl={addAnchor}
        onClose={() => setAddAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{ paper: { sx: { width: 320, borderRadius: 1.5 } } }}
      >
        <Box sx={{ p: 2 }}>
          <Typography sx={{ typography: "s2", fontWeight: 700 }}>Run again</Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle", mb: 1.5 }}>
            The same scenarios against whatever the agent is now — that is what makes the rows comparable.
          </Typography>
          <Stack spacing={0.75} sx={{ mb: 1.75 }}>
            {/* The pairing, quoted at the one moment it is a decision: a run is
                this environment version × this agent version, and that is what
                the result will be attributed to. */}
            <Line
              label="Runs"
              value={`env ${currentEnvVersion(env, envState).label} × agent ${currentAgentVersion(envState).label}`}
            />
            <Line label="Scenarios" value={`${envState.scenarios.length} tasks`} />
            {/* Sampling is part of the cost and part of what the next rate
                will mean, so it is quoted before the run, not discovered
                afterwards in a tooltip. */}
            <Line label="Samples" value="3 per scenario" />
            <Line label="Evals" value={`${envState.evals.length} applied`} />
            <Line label="Est. cost" value={`$${(envState.scenarios.length * 3 * 0.08).toFixed(2)}`} />
          </Stack>

          {/* A run with no graders is not a measurement. It still produces
              traces worth reading, and saying so here is cheaper than letting
              someone wait out a run and then find there is no pass rate. */}
          {envState.evals.length === 0 && (
            <Stack
              direction="row" spacing={1} alignItems="flex-start"
              sx={{
                mb: 1.75, px: 1.25, py: 1, borderRadius: 1,
                bgcolor: (t) => alpha("#CA8A04", t.palette.mode === "dark" ? 0.12 : 0.06),
              }}
            >
              <Iconify icon="solar:info-circle-bold" width={14} sx={{ color: "#CA8A04", flexShrink: 0, mt: "1px" }} />
              <Typography sx={{ typography: "s3", color: "text.secondary" }}>
                No evals are applied, so this run will produce traces but no pass rate to compare
                against the runs above.
              </Typography>
            </Stack>
          )}

          {/* A run whose scenarios were proved against an older world still
              produces numbers; it just cannot claim they rest on a proof. */}
          {staleScenarios(envState.scenarios, env, envState).length > 0 && (
            <Stack
              direction="row" spacing={1} alignItems="flex-start"
              sx={{
                mb: 1.75, px: 1.25, py: 1, borderRadius: 1,
                bgcolor: (t) => alpha("#CA8A04", t.palette.mode === "dark" ? 0.12 : 0.06),
              }}
            >
              <Iconify icon="solar:danger-triangle-bold" width={14} sx={{ color: "#CA8A04", flexShrink: 0, mt: "1px" }} />
              <Typography sx={{ typography: "s3", color: "text.secondary" }}>
                {staleScenarios(envState.scenarios, env, envState).length} scenarios were proved against an older
                world. Re-prove them on the Scenarios step if this run has to stand up.
              </Typography>
            </Stack>
          )}
          <Button
            fullWidth variant="contained" color="primary" size="small"
            onClick={() => { setAddAnchor(null); onStart(); }}
            startIcon={<Iconify icon="solar:play-bold" width={15} />}
            sx={{ typography: "s2", fontWeight: 700 }}
          >
            Start simulation
          </Button>
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
const STATUS_COL = 96;

const QUEUED_ID = "demo-queued";

/* Recorded runs carry status "running" until they finish, then "passed" /
   "failed" — which is the verdict, not the state. Trials are always done. */
const runStatus = (r) => {
  const raw = String(r?.status || "").toLowerCase();
  if (r?.kind === "trial") return "Completed";
  if (raw === "queued") return "Queued";
  if (raw === "pending") return "Pending";
  if (raw === "running" && !r.finishedAt) return "Running";
  if (["cancelled", "canceled", "stopped", "aborted"].includes(raw)) return "Cancelled";
  if (["error", "errored", "crashed"].includes(raw)) return "Failed";
  return "Completed";
};

/* Running reads blue here; the neutral chip the legacy table uses for
   Running is kept for Queued — waiting, not yet doing anything. */
const RUN_CHIP_STYLES = {
  ...statusStyles,
  Running: statusStyles.Pending,
  Queued: statusStyles.Running,
};

function StatusCell({ status }) {
  return (
    <Box sx={{ display: "flex", alignItems: "center", minWidth: 0, pr: 1 }}>
      <Chip
        variant="soft"
        label={status}
        size="small"
        sx={{
          typography: "s3",
          fontWeight: "fontWeightRegular",
          pointerEvents: "none",
          ...RUN_CHIP_STYLES[status],
        }}
      />
    </Box>
  );
}
StatusCell.propTypes = { status: PropTypes.string };

/* 294,000 → 294K: the column is read for magnitude, and the full figure
   was the widest thing in the row. */
const compactTokens = (n) => {
  if (n == null || Number.isNaN(Number(n))) return "—";
  const v = Number(n);
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(v >= 10_000_000 ? 0 : 1)}M`;
  if (v >= 1_000) return `${Math.round(v / 1_000)}K`;
  return String(v);
};

function Head({ children, right, divider, last, title, wrap }) {
  return (
    <Typography
      title={title}
      sx={{
        /* Sentence case, not shouted uppercase — the ALL-CAPS pass on
           long labels ("PROFESSION | ALISM") broke mid-word and read
           heavy alongside the values below. Small-caps weight + a
           lighter tint gives the same "this is a header" signal
           without the noise. */
        typography: "s3", fontWeight: 600, color: "text.subtitle",
        letterSpacing: 0.2, lineHeight: 1.3,
        textAlign: right ? "right" : "left", minWidth: 0,
        px: right ? 1.25 : 0,
        ...(last && { pr: 2.5 }),
        /* Metric headers stay on one line (their columns are sized to fit);
           grader names may wrap on word boundaries, never mid-word. */
        overflow: "hidden",
        ...(wrap
          ? {
            whiteSpace: "normal", overflowWrap: "normal", wordBreak: "normal",
            /* Two lines at most; a longer name ends in an ellipsis and the
               full name is in the tooltip. */
            display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical",
          }
          : { whiteSpace: "nowrap", textOverflow: "ellipsis" }),
        /* The graders are a different kind of number from the system ones, and
           a single hairline says so more quietly than a second header row. */
        ...(divider && { borderLeft: "1px solid", borderColor: "divider" }),
      }}
    >
      {children}
    </Typography>
  );
}
Head.propTypes = {
  children: PropTypes.node, right: PropTypes.bool,
  divider: PropTypes.bool, last: PropTypes.bool,
  title: PropTypes.string, wrap: PropTypes.bool,
};

function Num({ children, sx, strong, tone }) {
  return (
    <Typography
      sx={{
        typography: "s2", flexShrink: 0, textAlign: "right",
        fontVariantNumeric: "tabular-nums",
        fontWeight: strong ? 700 : 500,
        color: tone || "text.secondary",
        ...sx,
      }}
    >
      {children}
    </Typography>
  );
}
Num.propTypes = { children: PropTypes.node, sx: PropTypes.object, strong: PropTypes.bool, tone: PropTypes.string };

/**
 * The grader heatmap.
 *
 * Same hues and the same bands as `interpolateColorBasedOnScore` — the scale
 * the develop and observe grids paint their eval cells with — carried at a
 * lower alpha. Those grids show one row at a time against white space; here
 * five rows of graders sit stacked, and at the shared strength the block of
 * colour competes with the numbers printed on it.
 */
const SCORE_BANDS = [
  { upTo: 20, hue: "#D92D20", strong: true },
  { upTo: 40, hue: "#D92D20", strong: false },
  { upTo: 60, hue: "#E9690C", strong: false },
  { upTo: 80, hue: "#E6B800", strong: false },
  { upTo: 99, hue: "#00A251", strong: false },
  { upTo: Infinity, hue: "#00A251", strong: true },
];

const scoreFill = (value, mode) => {
  if (value == null) return "transparent";
  const band = SCORE_BANDS.find((b) => value < b.upTo) || SCORE_BANDS[SCORE_BANDS.length - 1];
  const dark = mode === "dark";
  if (band.strong) return alpha(band.hue, dark ? 0.13 : 0.1);
  return alpha(band.hue, dark ? 0.08 : 0.06);
};

/**
 * A number, and how far it has moved from the baseline.
 *
 * The delta sits beside the value rather than replacing it, because both
 * questions are live: "is this run fast enough" and "is it faster than the one
 * we were comparing against" are asked by different people in the same meeting.
 */
function MetricCell({ deltaWidth = 0, text, anchor, quiet, tone, delta }) {
  return (
    <Box
      sx={{
        display: "grid", gridTemplateColumns: deltaWidth ? `1fr ${deltaWidth}px` : "1fr",
        alignItems: "center", columnGap: 0.75, minWidth: 0,
        alignContent: "center", px: 1.25,
      }}
    >
      <Typography
        noWrap
        sx={{
          typography: "s2", fontVariantNumeric: "tabular-nums", textAlign: "right",
          fontWeight: anchor ? 700 : 500,
          color: tone || (quiet ? "text.subtitle" : "text.primary"),
        }}
      >
        {text}
      </Typography>
      {!!deltaWidth && <Delta delta={delta} />}
    </Box>
  );
}
MetricCell.propTypes = {
  deltaWidth: PropTypes.number, text: PropTypes.node, anchor: PropTypes.bool,
  quiet: PropTypes.bool, tone: PropTypes.string, delta: PropTypes.object,
};

/**
 * Better or worse, not up or down.
 *
 * Half these metrics are lower-is-better, so colouring by direction would
 * paint a run that got cheaper and faster in the same red as one that started
 * hallucinating. The arrow says which way it moved; the colour says whether
 * that was good.
 */
/**
 * Movement, and only movement.
 *
 * This is the one thing on the row that colour is for, so it is the only thing
 * that gets any — and softened, because a column of saturated green and red is
 * read as alarm rather than as information. A metric that did not move renders
 * nothing: a dash in every unchanged cell is ink spent saying "no news".
 */
function Delta({ delta }) {
  if (!delta || delta.flat) return <Box />;
  /* Arrow direction is SEMANTIC: up = improved, down = regressed —
     regardless of whether the underlying number went up or down.
     Otherwise a metric where lower-is-better (duration, cost, tokens)
     would show a green down arrow, which reads as regression. */
  const color = delta.better ? "#16A34A" : "#DC2626";
  return (
    <Stack direction="row" alignItems="center" spacing={0.125} sx={{ minWidth: 0, opacity: 0.92 }}>
      <Iconify
        icon={delta.better ? "eva:arrow-upward-fill" : "eva:arrow-downward-fill"}
        width={10}
        sx={{ color, flexShrink: 0 }}
      />
      <Typography
        noWrap
        sx={{ typography: "s3", fontWeight: 600, color, fontVariantNumeric: "tabular-nums" }}
      >
        {delta.text}
      </Typography>
    </Stack>
  );
}
Delta.propTypes = { delta: PropTypes.object };

/**
 * A grader's result for one run.
 *
 * Tinted rather than plain, because the point of the column is to be scanned
 * down: a run where one grader collapsed should be findable without reading
 * every number.
 */
function ScoreCell({ value, deltaWidth = 0, delta, divider, last }) {
  return (
    <Box
      sx={{
        display: "grid", gridTemplateColumns: deltaWidth ? `1fr ${deltaWidth}px` : "1fr",
        alignItems: "center", alignContent: "center", columnGap: 0.75, minWidth: 0,
        px: 1.25,
        /* Filled like the observe grid's eval cells, a shade quieter. */
        backgroundColor: (t) => scoreFill(value, t.palette.mode),
        ...(last && { pr: 2.5 }),
        ...(divider && { borderLeft: "1px solid", borderColor: "divider" }),
      }}
    >
      {/*
        Plain. The red/amber/green bands these carried were invented here — the
        thresholds belong to the graders, not to this table — so every cell was
        painted by a rule nobody set, and nine coloured numbers a row is a
        traffic light with no junction. Movement is the thing worth colouring,
        and the delta beside it does that.
      */}
      <Typography
        noWrap
        sx={{
          typography: "s2", fontWeight: 600, textAlign: "right",
          fontVariantNumeric: "tabular-nums",
          color: value == null ? "text.disabled" : "text.primary",
        }}
      >
        {value == null ? "—" : `${value}%`}
      </Typography>
      {!!deltaWidth && <Delta delta={delta} />}
    </Box>
  );
}
ScoreCell.propTypes = {
  value: PropTypes.number, deltaWidth: PropTypes.number,
  delta: PropTypes.object, divider: PropTypes.bool, last: PropTypes.bool,
};

/* A run's env pairing and timestamp — the part of its name that gives way,
   with an ellipsis and the full text on hover, when the column is narrow. */
function RunDetail({ text }) {
  return (
    <Tooltip arrow title={text} placement="top-start">
      <Typography
        noWrap
        sx={{ typography: "s3", color: "text.subtitle", minWidth: 0, flex: "0 1 auto", overflow: "hidden", textOverflow: "ellipsis" }}
      >
        {text}
      </Typography>
    </Tooltip>
  );
}
RunDetail.propTypes = { text: PropTypes.string };

/** A legend key: the eval's colour as a dot. */
function LegendDot({ color }) {
  return <Box sx={{ width: 8, height: 8, borderRadius: "50%", bgcolor: color, flexShrink: 0 }} />;
}
LegendDot.propTypes = { color: PropTypes.string };

function Line({ label, value }) {
  return (
    <Stack direction="row" alignItems="center" spacing={1}>
      <Typography sx={{ typography: "s3", color: "text.subtitle", flex: 1 }}>{label}</Typography>
      <Typography sx={{ typography: "s3", fontWeight: 600 }}>{value}</Typography>
    </Stack>
  );
}
Line.propTypes = { label: PropTypes.string, value: PropTypes.string };
