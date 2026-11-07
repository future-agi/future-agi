import { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Checkbox,
  MenuItem,
  Pagination,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from "@mui/material";

import {
  isActiveSkillJob,
  useCancelSkillTestRun,
  useShareSkillTestRun,
  useSkillTestResults,
  useSkillTestRun,
} from "src/api/skills/testing";
import ContentSkeleton from "src/components/content-skeleton";
import EmptyState from "src/components/empty-state";
import ErrorState from "src/components/error-state";
import ListToolbar from "src/components/list-toolbar";
import StatusBadge from "src/components/status-badge";
import { ConfirmDialog } from "src/components/custom-dialog";
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from "src/theme/sizes";
import { fDateTime } from "src/utils/format-time";
import TestAttemptDrawer from "./TestAttemptDrawer";

const FILTERS = {
  all: "All examples",
  regressions: "Regressions (including mixed)",
  wrong_selection: "Wrong selection",
  inconsistent: "Inconsistent",
  errors: "Errors",
  not_graded: "Not graded",
};
const TITLES = {
  works_on_examples: "Expectations met on these examples.",
  needs_changes: "Needs changes. Review the failed candidate expectations.",
  inconclusive:
    "Comparison incomplete. Some expectations could not be verified.",
  routing_only: "Selection matched these examples. Answers were not graded.",
  diagnostic:
    "Diagnostic check: these results are not independent improvement evidence.",
};
const variantTitle = (variant, run) => {
  if (variant === "candidate")
    return run.candidate.draftRevision === null
      ? `Candidate v${run.candidate.version}`
      : `Draft r${run.candidate.draftRevision}`;
  if (variant === "production") return `Production v${run.production?.version}`;
  if (variant === "without_target")
    return run.competitors.length ? "Without this skill" : "Without any skills";
  return variant;
};

export function TestExecutionConfiguration({ configuration }) {
  return (
    <Stack spacing={0.5} sx={{ overflowWrap: "anywhere" }}>
      <Typography variant="s3" color="text.secondary">
        Task model: {configuration.model.modelId} · temperature{" "}
        {configuration.model.temperature} · output allowance{" "}
        {configuration.model.maxOutputTokens} tokens/call
      </Typography>
      <Typography variant="s3" color="text.secondary">
        {configuration.judge
          ? `Judge: ${configuration.judge.modelId} · temperature ${configuration.judge.temperature} · output allowance ${configuration.judge.maxOutputTokens} tokens/call`
          : "No AI judge"}
      </Typography>
      <Typography variant="s3" color="text.secondary">
        Harness {configuration.harnessVersion} · adapter{" "}
        {configuration.adapterVersion} · evaluator{" "}
        {configuration.evaluatorVersion}
      </Typography>
    </Stack>
  );
}
TestExecutionConfiguration.propTypes = {
  configuration: PropTypes.object.isRequired,
};

function VariantCell({ value }) {
  if (!value)
    return <Typography variant="s3">Not started / unmatched</Typography>;
  return (
    <Stack spacing={0.5} sx={{ fontVariantNumeric: "tabular-nums" }}>
      <Typography variant="s3">
        {value.variant === "without_target"
          ? "Selection: Not applicable"
          : `Loaded ${value.loaded}/${value.planned}`}
      </Typography>
      <Typography variant="s3">
        Answer passed {value.answerPassed}/{value.planned} · graded{" "}
        {value.answerGraded}/{value.planned}
      </Typography>
      <Typography variant="s3">
        {value.variant === "without_target"
          ? "Delivery: Not applicable"
          : `Delivery passed ${value.deliveryPassed}/${value.planned}`}
      </Typography>
      {value.inconsistent && (
        <StatusBadge status="flaky" label="Inconsistent" />
      )}
      <Typography variant="s3" color="text.secondary">
        {value.completed}/{value.planned} completed
      </Typography>
    </Stack>
  );
}
VariantCell.propTypes = { value: PropTypes.object };

export default function TestRunResults({
  skillId,
  runId,
  canWrite = false,
  initialCaseId,
  initialAttemptId,
  onSelectionChange,
  onOpenFile,
  onImproveSelection,
  onRetryCases,
}) {
  const query = useSkillTestRun(skillId, runId);
  const [chosenFilter, setFilter] = useState(
    initialCaseId || initialAttemptId ? "all" : null,
  );
  const [page, setPage] = useState(1);
  const [localSelection, setSelection] = useState(() =>
    initialCaseId || initialAttemptId
      ? { caseId: initialCaseId, attemptId: initialAttemptId }
      : null,
  );
  const selection = onSelectionChange
    ? initialCaseId || initialAttemptId
      ? { caseId: initialCaseId, attemptId: initialAttemptId }
      : null
    : localSelection;
  const [retryIds, setRetryIds] = useState([]);
  const [shareOpen, setShareOpen] = useState(false);
  const [actionError, setActionError] = useState("");
  const run = query.data;
  const filter =
    chosenFilter ?? (run?.decision.regressedCases > 0 ? "regressions" : "all");
  const results = useSkillTestResults(
    skillId,
    run && !run.payloadExpired ? runId : undefined,
    { filter, page, pageSize: 25 },
  );
  const cancel = useCancelSkillTestRun(skillId, runId);
  const share = useShareSkillTestRun(skillId, runId);
  const rows = results.data?.results ?? [];
  const observedProgress = useRef(null);
  const progress = run
    ? `${run.status}:${run.counts.finished}:${run.counts.graded}`
    : null;
  const refetchResults = results.refetch;
  useEffect(() => {
    if (
      progress &&
      observedProgress.current &&
      observedProgress.current !== progress &&
      !run?.payloadExpired
    )
      refetchResults();
    observedProgress.current = progress;
  }, [progress, refetchResults, run?.payloadExpired]);
  const selectedIndex = rows.findIndex(
    (item) => item.caseId === selection?.caseId,
  );
  const select = (row, attemptId = null) => {
    const next = row ? { caseId: row.caseId, attemptId } : null;
    setSelection(next);
    onSelectionChange?.(next);
  };
  if (query.isLoading)
    return (
      <ContentSkeleton variant="rows" rows={6} label="Loading run summary" />
    );
  if (query.isError)
    return (
      <ErrorState
        title="Could not load this run"
        detail="Private runs are visible only to their owner. Shared runs remain workspace-scoped."
        onRetry={query.refetch}
      />
    );
  if (!run)
    return (
      <EmptyState
        title="Choose a run"
        description="Open a run from History to inspect its evidence."
      />
    );
  const active = isActiveSkillJob(run.status);
  const variants = ["candidate", "production", "without_target"].filter(
    (variant) =>
      variant === "candidate" ||
      run.answerComparisons.some(
        (comparison) => comparison.baseline === variant,
      ) ||
      rows.some((row) => row.variants.some((item) => item.variant === variant)),
  );
  const summary = active
    ? "Comparison in progress."
    : TITLES[run.decision.state] ?? "Review this run’s evidence.";
  const shareRun = async () => {
    try {
      setActionError("");
      await share.mutateAsync({ acknowledgeSnapshotSharing: true });
      setShareOpen(false);
    } catch {
      setActionError(
        "Could not share this run. No publication or evidence attachment was changed.",
      );
    }
  };
  return (
    <Stack spacing={2} sx={{ minWidth: 0 }}>
      <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap">
        <Typography variant="m1" component="h3">
          {summary}
        </Typography>
        <StatusBadge status={run.status} />
        <StatusBadge
          status={run.visibility === "private" ? "draft" : "completed"}
          label={
            run.visibility === "private"
              ? "Private run"
              : "Shared with workspace"
          }
        />
      </Stack>
      <Typography role="status" aria-live="polite" variant="s1">
        {run.counts.finished} of {run.counts.planned} attempts finished ·{" "}
        {run.counts.graded} graded · {run.counts.answered} answered ·{" "}
        {run.counts.errors} errors · {run.counts.incomplete} incomplete ·{" "}
        {run.counts.notStarted} not started
      </Typography>
      <Typography variant="s3" color="text.secondary">
        {run.selectedCaseIds.length} unique examples · {run.repetitions}{" "}
        attempt(s) per arm · {run.competitors.length} selected competitors · In
        this text-only test, no scripts or external actions
      </Typography>
      {run.profile === "quick" && (
        <Typography variant="s3" color="text.secondary">
          One attempt per example; not a reliability estimate.
        </Typography>
      )}
      {run.parentRunId && (
        <Alert severity="info">
          Diagnostic child run. The original run’s coverage and conclusions are
          unchanged.
        </Alert>
      )}
      {(run.validity === "diagnostic" ||
        run.decision.state === "diagnostic") && (
        <Alert severity="warning">
          Diagnostic evidence: no answer-uplift or independent-improvement
          claim. {run.validityReasons.join(" · ")}
        </Alert>
      )}
      {!run.currentness.content && (
        <Alert severity="warning">
          For earlier content. The draft changed after this check.
        </Alert>
      )}
      {run.currentness.baseline === false && (
        <Alert severity="info">
          Compared with historical production v{run.production?.version}.
          Production has moved since this run.
        </Alert>
      )}
      {run.currentness.suite === false && (
        <Alert severity="info">
          The saved examples changed after this run. These results use the
          frozen cases.
        </Alert>
      )}
      {run.currentness.competitors === false && (
        <Alert severity="info">
          Selected competitors have changed. The run retains their original
          immutable versions.
        </Alert>
      )}
      {run.error && (
        <Alert severity="error">
          {run.error.code}: {run.error.message}
        </Alert>
      )}
      {actionError && <Alert severity="error">{actionError}</Alert>}
      <Box component="details">
        <Typography component="summary" variant="s3">
          Frozen model and execution details ·{" "}
          {run.executionConfiguration.model.modelId}
        </Typography>
        <Stack spacing={1} sx={{ mt: 1 }}>
          <TestExecutionConfiguration
            configuration={run.executionConfiguration}
          />
          <Typography variant="s3" color="text.secondary">
            {fDateTime(run.createdAt)} ·{" "}
            {run.fullSuite ? "Full saved suite" : "Selected examples only"} ·
            detailed evidence expires {fDateTime(run.payloadExpiresAt)}
          </Typography>
        </Stack>
      </Box>
      <Stack direction="row" spacing={1} flexWrap="wrap">
        {active && canWrite && (
          <Button
            variant="outlined"
            disabled={cancel.isPending || run.status === "cancelling"}
            onClick={async () => {
              try {
                setActionError("");
                await cancel.mutateAsync();
              } catch {
                setActionError(
                  "Could not request stop. Keep this run open to inspect its durable status.",
                );
              }
            }}
          >
            {run.status === "cancelling" || cancel.isPending
              ? "Stopping…"
              : "Stop"}
          </Button>
        )}
        {run.visibility === "private" && canWrite && (
          <Button
            size="small"
            variant="outlined"
            disabled={active || run.payloadExpired}
            onClick={() => setShareOpen(true)}
          >
            Share evidence
          </Button>
        )}
        {onRetryCases && (
          <Button
            size="small"
            variant="outlined"
            disabled={active || !retryIds.length || run.payloadExpired}
            onClick={() => onRetryCases(retryIds, run.id)}
          >
            Retry selected examples as diagnostic
          </Button>
        )}
      </Stack>
      {active && (
        <Typography variant="s3" color="text.secondary">
          Stop prevents new calls. An in-flight request may finish and incur
          provider charges. Leaving this page does not cancel.
        </Typography>
      )}
      <Stack spacing={0.5}>
        <Typography variant="s1">
          {run.decision.improvedCases} improved · {run.decision.regressedCases}{" "}
          regressed or mixed · {run.decision.unchangedCases} unchanged ·{" "}
          {run.decision.unmatchedCases} unmatched
        </Typography>
        <Typography variant="s3" color="text.secondary">
          A mixed case improves against one baseline and regresses against
          another. It is included in regressions.
        </Typography>
        {run.answerComparisons.map((comparison) => (
          <Typography
            key={comparison.baseline}
            variant="s3"
            color="text.secondary"
          >
            Against {variantTitle(comparison.baseline, run)}:{" "}
            {comparison.matchedCases}/{comparison.expectedCases} matched
            examples ·{" "}
            {run.validity === "diagnostic" ||
            comparison.deltaPercentagePoints === null
              ? "No answer delta reported"
              : `${comparison.deltaPercentagePoints > 0 ? "+" : ""}${comparison.deltaPercentagePoints} percentage points (descriptive answer difference)`}
          </Typography>
        ))}
      </Stack>
      <Typography variant="s3" color="text.secondary">
        Task estimate{" "}
        {run.metrics.taskCost.knownEstimateUsd === null
          ? "unknown"
          : `USD ${run.metrics.taskCost.knownEstimateUsd}`}{" "}
        · judge estimate{" "}
        {run.metrics.judgeCost.knownEstimateUsd === null
          ? "unknown"
          : `USD ${run.metrics.judgeCost.knownEstimateUsd}`}{" "}
        ·{" "}
        {run.metrics.taskCost.unknownCalls + run.metrics.judgeCost.unknownCalls}{" "}
        unknown calls ·{" "}
        {run.metrics.taskCost.complete && run.metrics.judgeCost.complete
          ? "Complete observed estimates"
          : "Partial costs; unknown calls are not free"}
      </Typography>
      {run.payloadExpired ? (
        <Alert severity="info">
          Detailed evidence expired. Summary counts and configuration remain,
          but answers and file receipts are no longer inspectable.
        </Alert>
      ) : (
        <>
          <ListToolbar>
            <TextField
              select
              size="small"
              label="Results filter"
              value={filter}
              onChange={(event) => {
                setFilter(event.target.value);
                setPage(1);
                setRetryIds([]);
              }}
            >
              {Object.entries(FILTERS).map(([value, label]) => (
                <MenuItem key={value} value={value}>
                  {label}
                </MenuItem>
              ))}
            </TextField>
          </ListToolbar>
          {results.isLoading ? (
            <ContentSkeleton
              variant="rows"
              rows={5}
              label="Loading example results"
            />
          ) : results.isError ? (
            <ErrorState
              title="Could not load example results"
              onRetry={results.refetch}
            />
          ) : !rows.length ? (
            <EmptyState
              title={
                active
                  ? "Waiting for the first results"
                  : "No examples match this filter"
              }
              description={
                active
                  ? "Completed evidence appears as the run settles."
                  : "Choose All examples to inspect the rest of the comparison."
              }
            />
          ) : (
            <>
              <TableContainer sx={{ maxWidth: "100%" }}>
                <Table size="small" aria-label="Example comparisons">
                  <TableHead>
                    <TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>
                      {onRetryCases && <TableCell>Select</TableCell>}
                      <TableCell>Example</TableCell>
                      <TableCell>Expected</TableCell>
                      {variants.map((variant) => (
                        <TableCell key={variant}>
                          {variantTitle(variant, run)}
                        </TableCell>
                      ))}
                      <TableCell>Change</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {rows.map((row, index) => (
                      <TableRow
                        key={row.caseId}
                        hover
                        tabIndex={0}
                        aria-label={`Inspect ${row.title}`}
                        sx={{ height: ROW_HEIGHT.default, cursor: "pointer" }}
                        onClick={() => select(row)}
                        onKeyDown={(event) => {
                          if (event.target !== event.currentTarget) return;
                          if (event.key === "Enter" || event.key === " ") {
                            event.preventDefault();
                            select(row);
                          }
                          if (event.key === "ArrowDown") {
                            event.preventDefault();
                            event.currentTarget.nextElementSibling?.focus();
                          }
                          if (event.key === "ArrowUp") {
                            event.preventDefault();
                            event.currentTarget.previousElementSibling?.focus();
                          }
                        }}
                      >
                        {onRetryCases && (
                          <TableCell padding="checkbox">
                            <Checkbox
                              disabled={active}
                              checked={retryIds.includes(row.caseId)}
                              inputProps={{
                                "aria-label": `Retry ${row.title}`,
                              }}
                              onClick={(event) => event.stopPropagation()}
                              onChange={(event) =>
                                setRetryIds((previous) =>
                                  event.target.checked
                                    ? [...previous, row.caseId]
                                    : previous.filter(
                                        (id) => id !== row.caseId,
                                      ),
                                )
                              }
                            />
                          </TableCell>
                        )}
                        <TableCell sx={{ maxWidth: 240 }}>
                          <Typography
                            noWrap
                            title={row.title}
                            variant="s1"
                            fontWeight={500}
                          >
                            {index + 1}. {row.title}
                          </Typography>
                        </TableCell>
                        <TableCell>
                          <Typography variant="s3">
                            {row.expectedTrigger === null
                              ? "Either"
                              : row.expectedTrigger
                                ? "Use skill"
                                : "Do not use"}
                          </Typography>
                        </TableCell>
                        {variants.map((variant) => (
                          <TableCell key={variant}>
                            <VariantCell
                              value={row.variants.find(
                                (item) => item.variant === variant,
                              )}
                            />
                          </TableCell>
                        ))}
                        <TableCell>
                          <StatusBadge status={row.change} />
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
              {results.data.totalPages > 1 && (
                <Pagination
                  size="small"
                  page={page}
                  count={results.data.totalPages}
                  onChange={(_, next) => setPage(next)}
                />
              )}
            </>
          )}
          {selection && (
            <TestAttemptDrawer
              key={`${run.id}:${selection.caseId}`}
              open
              onClose={() => select(null)}
              skillId={skillId}
              runId={run.id}
              caseResult={rows[selectedIndex]}
              refreshToken={progress}
              attemptId={selection.attemptId}
              onSelectAttempt={(attemptId) => {
                const next = { ...selection, attemptId };
                setSelection(next);
                onSelectionChange?.(next);
              }}
              onPrevious={
                selectedIndex > 0
                  ? () => select(rows[selectedIndex - 1])
                  : undefined
              }
              onNext={
                selectedIndex >= 0 && selectedIndex < rows.length - 1
                  ? () => select(rows[selectedIndex + 1])
                  : undefined
              }
              onOpenFile={onOpenFile}
              onImproveSelection={onImproveSelection}
            />
          )}
        </>
      )}
      <ConfirmDialog
        open={shareOpen}
        onClose={() => {
          if (!share.isPending) setShareOpen(false);
        }}
        closeDisabled={share.isPending}
        title="Share test evidence?"
        content="Share the frozen draft, example inputs, model answers and file receipts with workspace members. This cannot be made private again."
        action={
          <Button
            variant="contained"
            disabled={share.isPending}
            onClick={shareRun}
          >
            Share with workspace
          </Button>
        }
      />
    </Stack>
  );
}
TestRunResults.propTypes = {
  skillId: PropTypes.string.isRequired,
  runId: PropTypes.string.isRequired,
  canWrite: PropTypes.bool,
  initialCaseId: PropTypes.string,
  initialAttemptId: PropTypes.string,
  onSelectionChange: PropTypes.func,
  onOpenFile: PropTypes.func,
  onImproveSelection: PropTypes.func,
  onRetryCases: PropTypes.func,
};
