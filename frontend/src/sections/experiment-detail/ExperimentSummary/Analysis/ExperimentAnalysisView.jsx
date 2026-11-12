import React, { useId, useState } from "react";
import PropTypes from "prop-types";
import { Link, useLocation, useParams, useSearchParams } from "react-router-dom";
import {
  Alert, Box, Button, LinearProgress, Link as MuiLink, Stack, Table, TableBody, TableCell,
  TableContainer, TableHead, TableRow, Typography,
} from "@mui/material";
import { useRunAnalysis } from "src/api/develop/analysis";
import { useExperimentStats } from "src/api/develop/experiment-stats";
import ChooseWinnerDrawer from "src/sections/experiment-detail/ExperimentSummary/ChooseWinnerDrawer";
import { HIDE_EMPTY_LABEL, hidesEmpty, withHideEmpty } from "src/sections/common/filters/hideEmpty";
import { summaryEvaluationName, summaryRunName } from "./summaryNames";

// One calm line in place of the two scope paragraphs (W6:
// at most twelve words, no internal feature list).
const RELEASE_NOTE = "Some Summary views are not available in this release.";
const LOADING_EVALS = "Loading the evaluations to rank…";
const HIDE_EMPTY_NOTE = `The ${HIDE_EMPTY_LABEL} switch applies only to the Data tab. It is not applied here.`;
const fmt = (value) => value === null ? "Unavailable" : new Intl.NumberFormat(undefined, { maximumFractionDigits: 6 }).format(value);
const isOff = (availability) => availability?.state !== "available";

function readFilters(raw) {
  if (!raw) return { filters: [], error: null };
  try {
    const filters = JSON.parse(raw);
    if (!Array.isArray(filters)) throw new Error("Expected an array");
    return { filters, error: null };
  } catch {
    return { filters: [], error: "The filters in this link could not be read, so no results are shown. Fix or remove the filters in the link." };
  }
}

// The shared API client rejects with the HTTP status as `statusCode` and,
// when no response arrived, the network failure as `transportCode`; it does
// not keep `response`. A malformed answer is a plain Error with neither.
function failureMessage(error) {
  const status = error?.statusCode ?? error?.response?.status;
  if (status === 404) return "This dataset or one of the selected runs is unavailable in this workspace.";
  if (status === 403) return "You do not have access to this analysis.";
  if (status === 409) return "The recorded results for these runs are incomplete or cannot be compared.";
  if (status === 503) return "The analysis is too large or temporarily unavailable. Retry, or compare fewer runs.";
  if (status === 400) return "The baseline, candidate, metric or filters in this link are not supported. Check the link.";
  if (status == null && error?.transportCode) return "The analysis could not be reached. Check your connection, then retry.";
  return "The analysis could not be loaded.";
}

// The analysis read words a missing paired difference in its own internal
// terms. Show the same fact in plain words, relative to the baseline; an
// unknown reason falls back by its state.
const PAIR_REASONS = new Map([
  ["Runs use different immutable dataset versions.", "Uses a different dataset version than the baseline."],
  ["Runs do not share the exact example/trial plan.", "Uses a different set of examples or trials than the baseline."],
  ["The evaluator is not pinned in both runs.", "This evaluator did not score both this run and the baseline."],
  ["Evaluator version or configuration snapshots differ.", "Uses a different evaluator version or settings than the baseline."],
]);
function pairReason(availability) {
  if (!isOff(availability)) return null;
  return PAIR_REASONS.get(availability.reason) ?? (availability.state === "incompatible"
    ? "Cannot be compared with the baseline on this evaluator."
    : "No trial was scored in both this run and the baseline.");
}

// What a metric's values are, and whether the runs were scored with the same
// evaluator version, in words instead of version ids.
function metricCaption(metric) {
  const versions = metric.per_run.map((item) => item.evaluator_version_id);
  const recorded = versions.filter(Boolean);
  const note = recorded.length === 0 ? " The evaluator version was not recorded."
    : new Set(recorded).size > 1 ? " Runs were scored with different evaluator versions, so part of a difference can come from the evaluator."
      : recorded.length < versions.length ? " Some runs have no recorded evaluator version."
        : versions.length > 1 ? " All runs were scored with the same evaluator version." : "";
  return `Average score over scored trials.${note}`;
}

// The ranks Choose winner saved, from the experiment stats read (rank 1 wins).
function savedRanking(stats) {
  const metadata = stats?.metadata;
  if (!(metadata?.is_winner_chosen || metadata?.isWinnerChosen)) return [];
  return (stats?.table_data || stats?.tableData || [])
    .filter((row) => row?.rank != null && Number.isFinite(Number(row.rank)))
    .map((row, index) => ({
      key: String(row.dataset_id ?? row.datasetId ?? index),
      rank: Number(row.rank),
      name: row.experiment_dataset_name ?? row.experimentDatasetName ?? "Unnamed run",
    }))
    .sort((a, b) => a.rank - b.rank);
}

/** Summary read surface. Unsupported components are not imported or mounted. */
export default function ExperimentAnalysisView({ datasetId, runIds = [], baselineId, mode = "compare" }) {
  const location = useLocation();
  const { experimentId } = useParams();
  const [params, setParams] = useSearchParams();
  const parsed = readFilters(params.get("filters"));
  // The Data tab's Hide rows switch is applied on the page, and the analysis
  // endpoint refuses its expression, so the Summary reads without it.
  const hidingEmpty = hidesEmpty(parsed.filters);
  const effectiveBaselineId = runIds.length < 2
    ? "none"
    : mode === "experiment" ? baselineId : params.get("baseline") ?? baselineId;
  // The experiment host validates the baseline against its history group.
  // Do not reintroduce a URL baseline/candidate from a different immutable plan.
  const requestedCandidate = params.get("cand");
  const candidateId = effectiveBaselineId === "none"
    || (mode === "experiment" && !runIds.includes(requestedCandidate))
    ? undefined : requestedCandidate || undefined;
  const query = useRunAnalysis(datasetId, {
    runIds, baselineId: effectiveBaselineId,
    filters: withHideEmpty(parsed.filters, false), groupBy: params.get("group_by") || undefined,
    bmetric: params.get("bmetric") || undefined,
    cand: candidateId, wmetric: params.get("wmetric") || undefined,
    include: "root_causes", enabled: !parsed.error, errorHandled: true,
  });
  const analysis = query.isError ? null : query.data;
  const setSelection = (key, value) => setParams((previous) => {
    const next = new URLSearchParams(previous);
    next.set(key, value);
    if (key === "baseline") next.delete("cand");
    return next;
  });
  // Both accepted hosts use .../summary and .../data. Never use classic /data
  // for new Results example IDs or filtered drilldowns.
  const gridHref = `${location.pathname.replace(/\/summary\/?$/, "/data")}${location.search}`;
  // Experiment pages rank their own runs with the existing Choose winner
  // drawer; Compare keeps its disabled action. No control links to this page.
  const [winnerOpen, setWinnerOpen] = useState(false);
  const winnerReasonId = useId();
  const winnerErrorId = useId();
  const winnerDoor = mode === "experiment" && Boolean(experimentId);
  const canRank = winnerDoor && runIds.length >= 2;
  const stats = useExperimentStats(experimentId, { enabled: canRank });
  const statsData = canRank ? stats.data : undefined;
  const evalsList = statsData?.column_config || statsData?.columnConfig || [];
  // A disabled Choose winner always says why: too few runs, the evaluations
  // still loading, or (below) the load error with its Retry.
  let winnerReason = null;
  if (winnerDoor && !canRank) {
    winnerReason = runIds.length === 1
      ? "Choose winner ranks two or more runs. This experiment has one run."
      : "Choose winner ranks two or more runs.";
  } else if (canRank && !statsData && !stats.isError) {
    winnerReason = stats.isPending ? LOADING_EVALS : "The evaluations for Choose winner are unavailable.";
  }
  const winnerErrorShown = canRank && stats.isError;
  const winnerDescribedBy = winnerReason ? winnerReasonId : winnerErrorShown ? winnerErrorId : undefined;
  // Name ranked runs as the comparison tables do: the run label (#1, #2)
  // joins the variant name the ranking stores.
  const rankedName = (name) => {
    const labels = (analysis?.runs ?? [])
      .filter((run) => run.name === name && run.label !== run.run_id)
      .map((run) => run.label);
    return labels.length ? `${name} (${labels.join(", ")})` : name;
  };
  const ranking = savedRanking(statsData);
  const winners = ranking.filter((row) => row.rank === 1).map((row) => rankedName(row.name));
  // Runs (summaryRunName) and evaluations go by the names the Data tab
  // shows, never by an id.
  const metricName = (key) => summaryEvaluationName(analysis?.metrics.find((metric) => metric.key === key));

  return (
    <Stack spacing={2} sx={{ p: { xs: 2, md: 3 }, minWidth: 0 }}>
      <Typography variant="h5" component="h1">Summary</Typography>
      <Typography variant="body2" color="text.secondary" data-testid="summary-release-note">{RELEASE_NOTE}</Typography>
      {hidingEmpty && (
        <Typography variant="body2" color="text.secondary" data-testid="summary-hide-empty-note">{HIDE_EMPTY_NOTE}</Typography>
      )}
      <Stack direction="row" spacing={1} flexWrap="wrap" alignItems="center" useFlexGap>
        <Button component={Link} to={gridHref}>Open Results data</Button>
        {winnerDoor && (
          <Button
            variant="contained"
            disabled={!statsData}
            aria-describedby={winnerDescribedBy}
            onClick={() => setWinnerOpen(true)}
          >
            Choose winner
          </Button>
        )}
        {winnerReason && (
          <Typography id={winnerReasonId} variant="body2" color="text.secondary">
            {winnerReason}
          </Typography>
        )}
      </Stack>
      {winnerErrorShown && (
        <Alert id={winnerErrorId} severity="error" action={<Button color="inherit" onClick={() => stats.refetch()}>Retry</Button>}>
          The evaluations for Choose winner could not be loaded. Retry to rank the runs.
        </Alert>
      )}
      {ranking.length > 0 && (
        <Box component="section" aria-label="Saved ranking">
          <Typography variant="h6" component="h2">Saved ranking</Typography>
          {winners.length > 0 && <Typography>{`${winners.length > 1 ? "Winners" : "Winner"}: ${winners.join(", ")}`}</Typography>}
          <Typography variant="body2">Ranked by the weights last saved in Choose winner.</Typography>
          <TableContainer>
            <Table size="small" aria-label="Ranks by saved weights">
              <TableHead><TableRow><TableCell>Rank</TableCell><TableCell>Run</TableCell></TableRow></TableHead>
              <TableBody>{ranking.map((row) => (
                <TableRow key={row.key}><TableCell>{row.rank}</TableCell><TableCell>{rankedName(row.name)}</TableCell></TableRow>
              ))}</TableBody>
            </Table>
          </TableContainer>
        </Box>
      )}
      {winnerOpen && (
        <ChooseWinnerDrawer
          open
          experimentId={experimentId}
          evalsList={evalsList}
          onClose={() => setWinnerOpen(false)}
        />
      )}
      {parsed.error && <Alert severity="error">{parsed.error}</Alert>}
      {!parsed.error && runIds.length === 0 && (
        <Alert severity="info">This experiment has no runs to summarize.</Alert>
      )}
      {!parsed.error && runIds.length > 0 && query.isLoading && <LinearProgress aria-label="Loading analysis" />}
      {query.isError && (
        <Alert severity="error" action={<Button color="inherit" onClick={() => query.refetch()}>Retry</Button>}>
          {failureMessage(query.error)}
        </Alert>
      )}
      {analysis && !parsed.error && (
        <>
          {query.isFetching && <Typography role="status">Refreshing analysis…</Typography>}
          {analysis.running && <Alert severity="info">Runs are still in progress. Counts include only the results recorded so far.</Alert>}
          <Typography>
            Showing {analysis.examples_in_view} of {analysis.examples_total} examples.
            {" "}Averages use scored trials only. Missing, errored and unscored trials are left out, not counted as zero.
          </Typography>
          <Box component="label">
            Compare against{" "}
            <select aria-label="Analysis baseline" value={analysis.baseline_run_id ?? "none"} onChange={(event) => setSelection("baseline", event.target.value)}>
              <option value="none">None — absolute values</option>
              {analysis.runs.map((run) => <option key={run.run_id} value={run.run_id}>{summaryRunName(run)}</option>)}
            </select>
          </Box>
          {analysis.baseline_run_id && (
            <Box component="label">
              Candidate{" "}
              <select aria-label="Analysis candidate" value={analysis.candidate_run_id ?? ""} onChange={(event) => setSelection("cand", event.target.value)}>
                {analysis.runs.filter((run) => run.run_id !== analysis.baseline_run_id).map((run) => <option key={run.run_id} value={run.run_id}>{summaryRunName(run)}</option>)}
              </select>
            </Box>
          )}
          {analysis.examples_in_view === 0 && <Alert severity="info">No examples match this selection. An empty selection is not a passing result.</Alert>}
          {analysis.metrics.length === 0 && <Alert severity="info">No evaluations were recorded for these runs.</Alert>}
          {analysis.metrics.map((metric) => (
            <Box component="section" key={metric.key} aria-label={`Metric ${summaryEvaluationName(metric)}`}>
              <Typography variant="h6" component="h2">{summaryEvaluationName(metric)}</Typography>
              <Typography variant="body2">{metricCaption(metric)}</Typography>
              <TableContainer>
                <Table size="small" aria-label={`Values for ${summaryEvaluationName(metric)}`}>
                  <TableHead><TableRow>
                    <TableCell>Run</TableCell><TableCell>Mean</TableCell>
                    <TableCell>Scored / planned trials</TableCell>
                    <TableCell>Difference vs baseline</TableCell>
                  </TableRow></TableHead>
                  <TableBody>{analysis.runs.map((run) => {
                    const value = metric.per_run.find((item) => item.run_id === run.run_id);
                    const pair = metric.vs_baseline.find((item) => item.run_id === run.run_id);
                    return <TableRow key={run.run_id} selected={run.run_id === analysis.candidate_run_id}>
                      <TableCell>{summaryRunName(run)}<Typography variant="caption" display="block">{run.status}</Typography></TableCell>
                      <TableCell>{fmt(value.value)}{value.value === null && <Typography variant="caption" display="block">No scored trials.</Typography>}</TableCell>
                      <TableCell>{value.n} / {value.expected_n}; {value.missing_n} not recorded</TableCell>
                      <TableCell>{pair ? (pair.delta === null ? <>
                        {fmt(null)}
                        <Typography variant="caption" display="block">{pairReason(pair.availability)}</Typography>
                      </> : `${fmt(pair.delta)} · ${pair.n} trials compared`) : analysis.baseline_run_id === run.run_id ? "Baseline" : "No baseline"}</TableCell>
                    </TableRow>;
                  })}</TableBody>
                </Table>
              </TableContainer>
            </Box>
          ))}
          <Alert severity="info">{`${isOff(analysis.availability.uncertainty) ? "Confidence intervals and significance are not computed here. " : ""}A difference describes these results; it does not pick a winner.${canRank ? " Choose winner ranks the runs by the weights you set." : ""}`}</Alert>
          <Box component="section">
            <Typography variant="h6" component="h2">Observed failures</Typography>
            {isOff(analysis.availability.root_causes) && analysis.observed_failures?.groups.length > 0 && (
              <Typography>These are failed evaluations as recorded. They show what failed, not why.</Typography>
            )}
            {analysis.observed_failures && isOff(analysis.observed_failures.availability) && (
              <Typography>No failures to show. Only pass/fail evaluators and evaluators with a pass threshold report failures.</Typography>
            )}
            {analysis.observed_failures?.groups.map((group) => {
              const failing = analysis.runs.find((run) => run.run_id === group.run_id);
              const label = failing ? summaryRunName(failing) : null;
              return (
                <Typography key={`${group.run_id}:${group.metric}`}>
                  {`${label ? `${label}: ` : ""}${group.failed_trials} of ${group.assessed_trials} scored trials failed ${metricName(group.metric)}.`}
                </Typography>
              );
            })}
            {analysis.observed_failures?.has_more && <Typography>Showing the first 50 of {analysis.observed_failures.total} failure groups.</Typography>}
          </Box>
          {/* Cost, latency, Re-score and Download are on the Data tab: say where. */}
          <Alert severity="info">
            {`${isOff(analysis.availability.ops) ? "Cost and latency are not compared here. " : ""}${winnerDoor ? "Re-score and Download are not available on this page." : "Choose winner, Re-score and Download are not available on this page."} Open the `}
            <MuiLink component={Link} to={gridHref}>Data tab</MuiLink>
            {isOff(analysis.availability.ops) ? " to see cost and latency, re-score or download." : " to re-score or download."}
          </Alert>
          <Stack direction="row" spacing={1} flexWrap="wrap">
            {!winnerDoor && <Button disabled>Choose winner</Button>}<Button disabled>Re-score</Button><Button disabled>Download</Button>
          </Stack>
        </>
      )}
    </Stack>
  );
}

ExperimentAnalysisView.propTypes = {
  datasetId: PropTypes.string,
  runIds: PropTypes.arrayOf(PropTypes.string),
  baselineId: PropTypes.string,
  mode: PropTypes.oneOf(["experiment", "compare"]),
};
