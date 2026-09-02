import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { alpha, useTheme } from "@mui/material/styles";
import ReactApexChart from "react-apexcharts";
import {
  Box, Stack, Typography, Button, Checkbox, Popover, Tooltip, IconButton,
  TextField, MenuItem, ListItemText,
} from "@mui/material";
import { ConfirmDialog } from "src/components/custom-dialog";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { runSummaries, evalSeries } from "../_mock/comparison";
import { currentEnvVersion, currentAgentVersion } from "../_mock/versions";
import { staleScenarios } from "../_mock/proofs";
import WinnerDrawer from "./WinnerDrawer";
import { allMetrics, deltaAgainst } from "../_mock/winner";
import { useEnvState } from "../store";

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
  /*
    Null means "all of them" rather than a copied list of ids: the evals arrive
    after the store hydrates, so seeding this with what exists on the first
    render would lock the chart to an empty set.
  */
  const [shownEvalIds, setShownEvalIds] = useState(null);

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
  const series = useMemo(() => evalSeries(summaries, envState), [summaries, envState]);
  /* Newest first in the table — the run someone just finished is the one they
     came here to look at. The chart keeps chronological order. */
  const rows = useMemo(() => [...summaries].reverse(), [summaries]);
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

  /* Four graders on one axis is already a lot; eight would be a scribble. The
     chart draws the ones asked for, and the table keeps all of them — the
     question "how is this moving" is narrower than "what are the numbers". */
  const shown = useMemo(
    () => (shownEvalIds ? evals.filter((e) => shownEvalIds.includes(e.id)) : evals),
    [evals, shownEvalIds],
  );
  const shownSeries = useMemo(
    () => series.filter((x) => shown.some((e) => e.id === x.id)),
    [series, shown],
  );


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
    const chronological = summaries
      .filter((r) => selected.includes(r.id))
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

  /* Deleting runs takes the winner and the baseline with them when they point
     at something that no longer exists — a badge on a deleted run, or deltas
     against one, would be worse than losing the choice. */
  const removeSelected = () => {
    const gone = new Set(selected);
    patch({
      runs: envState.runs.filter((r) => !gone.has(r.id)),
      ...(gone.has(baselineId) ? { baselineRunId: null } : {}),
      ...(winner && gone.has(winner.runId) ? { winner: null } : {}),
    });
    setSelected([]);
    setDeleting(false);
  };

  const baseline = summaries.find((r) => r.id === baselineId) || null;

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
    const num = baseline ? 104 : 76;
    /* Graders get less room once there are several: four at full width push the
       last one past the card, and a clipped column reads as a broken table
       rather than as more table. */
    const score = evals.length >= 4
      ? (baseline ? 92 : 74)
      : (baseline ? 118 : 92);
    const columns = [num, num, num, num, num, num, ...evals.map(() => score)];
    return {
      template: `26px minmax(210px, 300px) ${columns.map((c) => `minmax(${c}px, 1fr)`).join(" ")}`,
      /* Below this the table scrolls rather than crushing the run names. */
      /* Plus the width of the fade, so the rightmost grader is never underneath
         it at the end of a scroll. */
      min: 26 + 210 + columns.reduce((a, c) => a + c, 0) + 12 * (columns.length + 1) + 28,
      deltaWidth: baseline ? 52 : 0,
      /* Where the system numbers end and the graders begin. */
      firstEval: 6,
    };
  }, [baseline, evals]);

  return (
    <Box sx={{ p: 2 }}>
      <Stack direction={{ xs: "column", md: "row" }} alignItems={{ md: "center" }} spacing={1.5} sx={{ mb: 2 }}>
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "m2", fontWeight: 600 }}>Simulations summary</Typography>
          {/* Only claim the runs are comparable when they are. A partial re-run
              is a normal thing to do and a normal thing to say — what is not
              acceptable is a subtitle that keeps promising "the same scenarios"
              while one of the rows below covered three of them. */}
          <Typography sx={{ typography: "s1", color: "text.secondary" }}>
            {summaries.length} runs · {full.length === summaries.length
              ? `the same ${envState.scenarios.length} scenarios every time, so what changed is the agent`
              : `${full.length} over the full ${envState.scenarios.length} scenarios, ${summaries.length - full.length} over a subset`}
            . 3 samples per scenario.
          </Typography>
        </Box>
        <Stack direction="row" spacing={1} flexShrink={0}>
          <Button
            variant="outlined" color="inherit" size="small"
            onClick={(e) => setAddAnchor(e.currentTarget)}
            startIcon={<Iconify icon="solar:test-tube-linear" width={15} />}
            sx={{ typography: "s2", fontWeight: 700, borderColor: "divider" }}
          >
            Add more runs
          </Button>
          <Button
            variant="outlined" color="inherit" size="small"
            onClick={() => onGo("evals")}
            startIcon={<Iconify icon="solar:shield-check-linear" width={15} />}
            sx={{ typography: "s2", fontWeight: 700, borderColor: "divider" }}
          >
            Edit evals
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
        }}
      >
        {/*
          Its own row rather than the card's action slot. Seven graders make
          both the selector's summary and the legend long, and in the header
          they grow leftward into the title until the two collide.
        */}
        <Stack
          direction="row" alignItems="flex-start" spacing={2}
          sx={{ px: 2.5, pt: 1.5, pb: 0.25 }}
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
              /* Summarised, not listed: four names are already wider than the
                 control and seven are a paragraph. */
              renderValue: (ids) => {
                if (ids.length === evals.length) return `All ${evals.length} evals`;
                const names = evals.filter((x) => ids.includes(x.id)).map((x) => x.name);
                return names.length > 2 ? `${names[0]} +${names.length - 1} more` : names.join(", ");
              },
              MenuProps: { PaperProps: { sx: { maxHeight: 320 } } },
            }}
            sx={{
              width: 220, flexShrink: 0,
              "& .MuiInputBase-input": {
                typography: "s2", py: 0.75,
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
                    sx={{ p: 0.5, mr: 0.75 }}
                  />
                  <Box sx={{ width: 8, height: 8, borderRadius: "50%", bgcolor: e.color, mr: 1, flexShrink: 0 }} />
                  <ListItemText primaryTypographyProps={{ typography: "s2" }} primary={e.name} />
                </MenuItem>
              );
            })}
          </TextField>

          {/* Wraps within its own half of the row instead of pushing anything. */}
          <Stack
            direction="row" spacing={1.5} flexWrap="wrap" rowGap={0.75}
            sx={{ flex: 1, minWidth: 0, justifyContent: "flex-end", pt: 0.75 }}
          >
            {shown.map((e) => (
              <Stack key={e.id} direction="row" alignItems="center" spacing={0.625}>
                <Box sx={{ width: 8, height: 8, borderRadius: "50%", bgcolor: e.color, flexShrink: 0 }} />
                <Typography noWrap sx={{ typography: "s3", color: "text.secondary" }}>{e.name}</Typography>
              </Stack>
            ))}
          </Stack>
        </Stack>

        <Box sx={{ px: 1.5, pt: 0.5, pb: 0.5 }}>
          <ReactApexChart
            type="line"
            height={150}
            series={shownSeries.map((x) => ({ name: x.name, data: x.data }))}
            options={{
              /* Animation off. A re-render while the lines are still growing
                 leaves ApexCharts holding the collapsed path — three of four
                 series sat flat at zero while their markers were in the right
                 places, which reads as "every eval scored nothing". */
              chart: {
                toolbar: { show: false },
                zoom: { enabled: false },
                animations: { enabled: false },
                fontFamily: theme.typography.fontFamily,
                background: "transparent",
              },
              theme: { mode: theme.palette.mode },
              colors: shownSeries.map((x) => x.color),
              stroke: { width: 2, curve: "straight" },
              markers: { size: 2.5, strokeWidth: 0 },
              legend: { show: false },
              dataLabels: { enabled: false },
              grid: {
                borderColor: theme.palette.divider,
                strokeDashArray: 4,
                xaxis: { lines: { show: false } },
                padding: { left: 8, right: 8 },
              },
              xaxis: {
                /*
                  A subset run belongs on the line — it happened — but a point
                  from four scenarios sitting at 100% next to one from
                  seventeen invites exactly the wrong read. It cannot be hidden
                  without the history lying by omission, so it is labelled.
                */
                categories: summaries.map((s, i) => {
                  const partial = s.total < envState.scenarios.length;
                  const base = i === summaries.length - 1 ? "latest" : `Run ${s.ordinal}`;
                  return partial ? `${base} · subset` : base;
                }),
                axisBorder: { show: false },
                axisTicks: { show: false },
                labels: { style: { colors: theme.palette.text.secondary, fontSize: "11px" } },
                tooltip: { enabled: false },
              },
              yaxis: {
                min: 0, max: 100, tickAmount: 2,
                labels: { style: { colors: theme.palette.text.secondary, fontSize: "11px" }, formatter: (v) => `${Math.round(v)}` },
              },
              tooltip: {
                shared: true,
                intersect: false,
                y: {
                  formatter: (v, opts) => {
                    if (v == null) return "\u2014";
                    const run = summaries[opts?.dataPointIndex ?? -1];
                    const partial = run && run.total < envState.scenarios.length;
                    return `${v}%${partial ? ` · ${run.total} of ${envState.scenarios.length} scenarios` : ""}`;
                  },
                },
              },
            }}
          />
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
            <Typography sx={{ typography: "s1", fontWeight: 600 }}>Runs ({rows.length})</Typography>
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
                  selection is even eligible for. */}
              {selected.length === 1 ? (
                <Button
                  variant="contained" color="primary" size="small"
                  onClick={setBaseline}
                  startIcon={<Iconify icon="solar:transfer-vertical-linear" width={15} />}
                  sx={{ typography: "s2", fontWeight: 700 }}
                >
                  {selected[0] === baselineId ? "Clear baseline" : "Set as baseline"}
                </Button>
              ) : (
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
            overflowX: "auto",
            position: "relative",
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
              <Head right>Pass</Head>
              <Head right>Avg duration</Head>
              <Head right>Tokens</Head>
              <Head right>Cost</Head>
              <Head right>Said, not done</Head>
              <Head right>Mean return</Head>
              {evals.map((e, i) => (
                <Head key={e.id} right divider={i === 0} last={i === evals.length - 1}>
                  {e.name}
                </Head>
              ))}
            </Box>

            <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
              {rows.map((r, i) => {
                const picked = selected.includes(r.id);
                const won = winner?.runId === r.id;
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
                        sx={{ p: 0.5, pointerEvents: "none" }}
                      />
                    </Box>

                    <Stack direction="row" alignItems="center" spacing={1.25} sx={{ minWidth: 0, py: 0.875, pr: 1.5 }}>
                      {/* The run's own letter and colour, not its position in
                          the list or the order it was ticked — the same badge
                          identifies it in a comparison and in the drawer. */}
                      <Box
                        sx={{
                          width: 22, height: 22, borderRadius: 0.75, flexShrink: 0,
                          display: "grid", placeItems: "center",
                          typography: "s3", fontWeight: 700,
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
                      <Box minWidth={0} sx={{ display: "flex", alignItems: "center", minWidth: 0 }}>
                        <Stack direction="row" alignItems="baseline" spacing={0.75} sx={{ minWidth: 0 }}>
                          <Typography noWrap sx={{ typography: "s2", fontWeight: 600, flexShrink: 0 }}>
                            Run {r.ordinal} · agent {r.agentVersion}
                            {/* Env version stamped on the run — pinned
                                at start, not inferred later. Only shown
                                if the run recorded one; older runs
                                without the field omit it silently rather
                                than reading as "env unknown". */}
                            {r.envVersion && (
                              <Box
                                component="span"
                                sx={{ color: "text.subtitle", fontWeight: 500 }}
                              >
                                {" "}× env {r.envVersion}
                              </Box>
                            )}
                          </Typography>
                          {/* On one line. Two lines per row doubled the height
                              of the table for a timestamp nobody scans. */}
                          {/* Short form, and no task count: every run of this
                              environment runs the same scenarios, so printing
                              "7 tasks" on all five rows says nothing. */}
                          {/* Fixed rather than shrinkable: the chips beside it
                              are flex-shrink-0, so a row with enough of them
                              squeezed the timestamp to zero width and the run
                              silently lost its date. */}
                          <Typography noWrap sx={{ typography: "s3", color: "text.subtitle", flexShrink: 0 }}>
                            {new Date(r.finishedAt).toLocaleString(undefined, {
                              day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
                            })}
                          </Typography>
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

                    <MetricCell
                      anchor deltaWidth={grid.deltaWidth} text={`${r.passRate}%`}
                      delta={deltaAgainst(metrics.passRate, r, baseline)}
                    />
                    <MetricCell
                      quiet deltaWidth={grid.deltaWidth} text={`${(r.avgDurationMs / 1000).toFixed(1)}s`}
                      delta={deltaAgainst(metrics.duration, r, baseline)}
                    />
                    <MetricCell
                      quiet deltaWidth={grid.deltaWidth} text={r.tokens.toLocaleString()}
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
                      tone={r.saidNotDone ? "#C2603F" : undefined}
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
        title={`Delete ${selected.length} run${selected.length > 1 ? "s" : ""}?`}
        content="Their results go with them, and anything measured against them — a baseline, a winner — is cleared. The scenarios and the environment are untouched."
        action={
          <Button variant="contained" color="error" onClick={removeSelected} sx={{ typography: "s2", fontWeight: 700 }}>
            Delete
          </Button>
        }
      />

      <WinnerDrawer
        open={pickingWinner}
        onClose={() => setPickingWinner(false)}
        summaries={summaries}
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
function Head({ children, right, divider, last }) {
  return (
    <Typography
      sx={{
        typography: "s3", fontWeight: 700, color: "text.subtitle",
        textTransform: "uppercase", letterSpacing: 0.4, lineHeight: 1.3,
        textAlign: right ? "right" : "left", minWidth: 0,
        px: right ? 1.25 : 0,
        ...(last && { pr: 2.5 }),
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
  const color = delta.better ? "#5AA47B" : "#C2603F";
  return (
    <Stack direction="row" alignItems="center" spacing={0.125} sx={{ minWidth: 0, opacity: 0.92 }}>
      <Iconify
        icon={delta.up ? "eva:arrow-upward-fill" : "eva:arrow-downward-fill"}
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

function Line({ label, value }) {
  return (
    <Stack direction="row" alignItems="center" spacing={1}>
      <Typography sx={{ typography: "s3", color: "text.subtitle", flex: 1 }}>{label}</Typography>
      <Typography sx={{ typography: "s3", fontWeight: 600 }}>{value}</Typography>
    </Stack>
  );
}
Line.propTypes = { label: PropTypes.string, value: PropTypes.string };
