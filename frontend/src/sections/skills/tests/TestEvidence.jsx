import { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Link as RouterLink } from "react-router-dom";
import {
  Alert,
  Button,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";

import { useSkill } from "src/api/skills";
import {
  isActiveSkillJob,
  useShareSkillTestRun,
  useSkillTestEvidence,
  useSkillTestRun,
  useSkillV11Scope,
} from "src/api/skills/testing";
import ContentSkeleton from "src/components/content-skeleton";
import ErrorState from "src/components/error-state";
import Section from "src/components/section";
import StatusBadge from "src/components/status-badge";
import { ConfirmDialog } from "src/components/custom-dialog";
import { paths } from "src/routes/paths";
import { fDate, fDateTime } from "src/utils/format-time";
import { TestExecutionConfiguration } from "./TestRunResults";
import TechnicalDetails from "../components/TechnicalDetails";
import { canAttachRun } from "../utils/evidence";

// Plain counts: "1 test case", "2 test cases" (UXF-11: no "(s)").
// eslint-disable-next-line react-refresh/only-export-components -- shared wording helper for this module's tests.
export const plural = (count, one, many = `${one}s`) => `${count} ${count === 1 ? one : many}`;
const STATUS_WORDS = {
  queued: "Queued",
  pending: "Queued",
  running: "Running",
  cancelling: "Stopping",
  cancelled: "Stopped",
  failed: "Failed",
  completed: "Completed",
  expired: "Expired",
};
const statusWord = (status) =>
  STATUS_WORDS[status] ||
  (status ? `${status.charAt(0).toUpperCase()}${status.slice(1).replaceAll("_", " ")}` : "Not reported");
const visibilityWord = (visibility) =>
  visibility === "private" ? "Private" : "Shared with workspace";

/**
 * The one-line conclusion of an evidence row (UXF-11), for example
 * "Passed: 1 of 1 test case, no regressions. Shared with workspace, 16 Nov."
 * It only restates what the server reported. "Passed" is said only when the
 * run's own decision is "works on examples" with no regressed case; while that
 * decision is not loaded the line states the counts it has.
 */
// eslint-disable-next-line react-refresh/only-export-components -- the summary sentence is tested on its own.
export function evidenceSummary(row, decision) {
  const cases = row.selectedCases ?? 0;
  const testCases = plural(cases, "test case");
  const regressed = decision?.regressedCases ?? row.answerRegressions ?? 0;
  let outcome;
  if (isActiveSkillJob(row.status)) outcome = `${statusWord(row.status)}: ${testCases}.`;
  else if (row.status !== "completed") outcome = `${statusWord(row.status)}: ${testCases}.`;
  else if (decision?.state === "works_on_examples" && regressed === 0)
    outcome = `Passed: ${cases} of ${testCases}, no regressions.`;
  else if (decision?.state === "needs_changes")
    outcome = regressed > 0
      ? `Needs changes: ${regressed} of ${testCases} regressed.`
      : `Needs changes: some expectations failed in ${testCases}.`;
  else if (decision?.state === "inconclusive")
    outcome = `Incomplete: some expectations in ${testCases} couldn't be checked.`;
  else if (decision?.state === "routing_only")
    outcome = `Skill selection matched in ${testCases}; answers weren't graded.`;
  else if (decision?.state === "diagnostic")
    outcome = `Diagnostic check of ${testCases}; not independent evidence.`;
  else
    outcome = regressed > 0
      ? `Completed: ${regressed} of ${testCases} regressed.`
      : `Completed: ${testCases}, no regressions.`;
  const when = row.createdAt ? fDate(row.createdAt, "d MMM") : "";
  return `${outcome} ${visibilityWord(row.visibility)}${when ? `, ${when}` : ""}.`;
}

const runOption = (run) =>
  `${fDateTime(run.createdAt)} · ${plural(run.selectedCases ?? 0, "test case")} · ${statusWord(run.status)} · ${visibilityWord(run.visibility)}`;

// variant "full" (default): the evidence picker of the Tests and publish flows.
// variant "summary": one summary line with counts, model, temperature and token
// limit under "Technical details" (Move label, UXF-11). Both keep the warnings,
// the attach control and the attached state visible.
function EvidenceContent({ skillId, candidate, onSelectRun, selectedRunId, recordedWith = "It is saved when you confirm.", variant = "full" }) {
  const summaryVariant = variant === "summary";
  const evidence = useSkillTestEvidence(skillId, candidate);
  const skill = useSkill(skillId);
  const [inspectId, setInspectId] = useState(null);
  const [shareOpen, setShareOpen] = useState(false);
  const [error, setError] = useState("");
  const runs = evidence.data?.runs ?? [];
  const inspectedId = inspectId ?? selectedRunId ?? runs[0]?.runId;
  const row = runs.find((item) => item.runId === inspectedId);
  const detail = useSkillTestRun(skillId, row?.runId);
  const share = useShareSkillTestRun(skillId, row?.runId);
  const canWrite = skill.data?.permissions?.canRunTests === true;
  const production = skill.data?.labels.find(
    (label) => label.name === "production",
  );
  const link = (runId) =>
    `${paths.dashboard.skills.detail(skillId)}?${new URLSearchParams({ tab: "tests", ...(runId ? { run: runId } : {}) })}`;
  const attachable = canAttachRun(row);
  // "Attach" only selects the run here; the move or publication sends it and the server checks it again.
  const selected = selectedRunId ? runs.find((item) => item.runId === selectedRunId) : undefined;
  const attached = canAttachRun(selected);
  // The Attach button gives way to the attached row, so focus follows it there.
  const attachedRef = useRef(null);
  const [focusAttached, setFocusAttached] = useState(false);
  useEffect(() => {
    if (focusAttached && attached) {
      attachedRef.current?.focus();
      setFocusAttached(false);
    }
  }, [focusAttached, attached]);
  if (evidence.isLoading)
    return (
      <ContentSkeleton variant="rows" rows={3} label="Loading test evidence" />
    );
  if (evidence.isError)
    return (
      <ErrorState
        title="Could not load test evidence"
        detail="No model call was made. Your release choices are unchanged."
        onRetry={evidence.refetch}
      />
    );
  const coverage = row
    ? row.currentness.content
      ? `Checked this content · ${row.suiteCases === null ? `${plural(row.selectedCases, "ad hoc test case")}` : `${row.selectedCases} of ${plural(row.suiteCases, "saved test case")}`}`
      : "The draft changed after this check."
    : "";
  const counts = row && (
    <Typography variant={summaryVariant ? "s3" : "s1"} color={summaryVariant ? "text.secondary" : undefined}>
      {plural(row.answerRegressions, "answer regression")} (including mixed) ·{" "}
      {plural(row.routingFailures, "skill-selection failure")} ·{" "}
      {plural(row.deliveryFailures, "file-delivery failure")} ·{" "}
      {plural(row.errorAttempts, "attempt with an error", "attempts with an error")}
    </Typography>
  );
  const execution = detail.isLoading ? (
    <ContentSkeleton
      variant="rows"
      rows={2}
      label="Loading the test settings"
    />
  ) : detail.isError ? (
    <ErrorState
      title="Could not load the test settings"
      onRetry={detail.refetch}
    />
  ) : (
    detail.data && (
      <>
        <Typography variant="s3" color="text.secondary">
          {detail.data.counts.graded} of {plural(detail.data.counts.planned, "attempt")} graded ·{" "}
          {detail.data.counts.incomplete} incomplete · {detail.data.counts.notStarted} not started
        </Typography>
        <TestExecutionConfiguration
          configuration={detail.data.executionConfiguration}
        />
      </>
    )
  );
  return (
    <Section
      title={<Typography component="span" variant="m3">Test evidence</Typography>}
      description={runs.length ? "Tests help you review this version; they do not approve it for you." : undefined}
    >
      <Stack spacing={1.5}>
        {error && <Alert severity="error">{error}</Alert>}
        {selectedRunId && !attached && (
          <Alert
            severity="warning"
            action={
              <Button color="inherit" size="small" onClick={() => onSelectRun(null)}>
                Continue without attachment
              </Button>
            }
          >
            The selected evidence no longer matches this candidate or is
            unavailable. Review the conflict or explicitly remove the
            attachment.
          </Alert>
        )}
        {!runs.length ? (
          <Stack direction="row" spacing={1} alignItems="center" useFlexGap flexWrap="wrap">
            <Typography variant="s1">No tests run for {candidate.kind === 'draft' ? 'this draft' : 'these files'}. Not tested is not a pass.</Typography>
            <Button size="small" component={RouterLink} to={`${paths.dashboard.skills.edit(skillId)}?tab=tests`}>Run tests</Button>
          </Stack>
        ) : (
          <>
            <TextField
              select
              size="small"
              label={summaryVariant ? "Test run" : "Inspect test evidence"}
              value={row?.runId ?? ""}
              onChange={(event) => {
                setInspectId(event.target.value);
                setError("");
              }}
            >
              {runs.map((run) => (
                <MenuItem key={run.runId} value={run.runId}>
                  {runOption(run)}
                </MenuItem>
              ))}
            </TextField>
            {row && (
              <>
                {summaryVariant ? (
                  <Typography variant="s1" data-testid="evidence-summary">
                    {evidenceSummary(row, detail.data?.decision)}
                  </Typography>
                ) : (
                  <>
                    <Stack direction="row" spacing={1} flexWrap="wrap">
                      <StatusBadge status={row.status} />
                      <Typography variant="s1">{coverage}</Typography>
                    </Stack>
                    <Typography variant="s3" color="text.secondary">
                      {plural(row.repetitions, "attempt")} per version ·{" "}
                      {fDateTime(row.createdAt)} ·{" "}
                      {row.visibility === "private"
                        ? "Private evidence"
                        : "Shared with workspace"}
                    </Typography>
                  </>
                )}
                {summaryVariant && !row.currentness.content && (
                  <Alert severity="warning">The draft changed after this check.</Alert>
                )}
                {row.validity === "diagnostic" && (
                  <Alert severity="warning">
                    Diagnostic evidence. Reused or bundled test cases may
                    influence these results.
                  </Alert>
                )}
                {!row.currentness.payloadAvailable && (
                  <Alert severity="info">
                    Detailed evidence expired. It is no longer inspectable or
                    attachable.
                  </Alert>
                )}
                {row.currentness.baseline === false && (
                  <Alert severity="info">
                    Compared with production v
                    {detail.data?.production?.version ?? "(historical)"};
                    production now points to{" "}
                    {production ? `v${production.version}` : "no version"}.
                  </Alert>
                )}
                {row.currentness.suite === false && (
                  <Typography variant="s3">
                    Saved test cases changed after this run.
                  </Typography>
                )}
                {row.currentness.competitors === false && (
                  <Typography variant="s3">
                    Competitor targets changed after this run.
                  </Typography>
                )}
                {!summaryVariant && counts}
                {row.answerRegressions > 0 && (
                  <Alert severity="warning">
                    {row.answerRegressions === 1
                      ? "One regression needs a look before you continue."
                      : `${row.answerRegressions} regressions need a look before you continue.`}
                  </Alert>
                )}
                {summaryVariant ? (
                  <TechnicalDetails title="Technical details">
                    <Stack spacing={1}>
                      <Typography variant="s3" color="text.secondary">{coverage}</Typography>
                      <Typography variant="s3" color="text.secondary">
                        {plural(row.repetitions, "attempt")} per version · {fDateTime(row.createdAt)}
                      </Typography>
                      {counts}
                      {execution}
                    </Stack>
                  </TechnicalDetails>
                ) : (
                  execution
                )}
                <Stack direction="row" spacing={1} flexWrap="wrap">
                  <Button
                    size="small"
                    component={RouterLink}
                    to={link(row.runId)}
                  >
                    View results
                  </Button>
                  {row.visibility === "private" && (
                    <Button
                      size="small"
                      variant="outlined"
                      disabled={
                        !canWrite ||
                        isActiveSkillJob(row.status) ||
                        !row.currentness.payloadAvailable
                      }
                      onClick={() => setShareOpen(true)}
                    >
                      Share evidence
                    </Button>
                  )}
                  {!(attached && row.runId === selectedRunId) && (
                    <Button
                      size="small"
                      variant="outlined"
                      disabled={
                        !attachable || !canWrite || row.runId === selectedRunId
                      }
                      onClick={() => {
                        onSelectRun(row.runId);
                        setFocusAttached(true);
                      }}
                    >
                      Attach this evidence
                    </Button>
                  )}
                </Stack>
              </>
            )}
          </>
        )}
        {attached && (
          <Alert ref={attachedRef} tabIndex={-1} role="status" severity="success">
            <Typography variant="s1" fontWeight={500}>
              Evidence attached: test run from {fDateTime(selected.createdAt)}
            </Typography>
            <Typography variant="s3" component="p">
              {selected.selectedCases} test case{selected.selectedCases === 1 ? "" : "s"} ·{" "}
              {selected.answerRegressions} answer regression{selected.answerRegressions === 1 ? "" : "s"} ·{" "}
              shared with workspace.{recordedWith ? ` ${recordedWith}` : ""}
            </Typography>
            <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap" sx={{ mt: 0.5, ml: -1 }}>
              <Button color="inherit" size="small" component={RouterLink} to={link(selected.runId)}>
                View attached results
              </Button>
              <Button color="inherit" size="small" onClick={() => onSelectRun(null)}>
                Remove attachment
              </Button>
            </Stack>
          </Alert>
        )}
        {runs.length > 0 && (
          <Stack direction="row" spacing={1}>
            <Button size="small" component={RouterLink} to={link()}>
              Run tests
            </Button>
          </Stack>
        )}
      </Stack>
      <ConfirmDialog
        open={shareOpen}
        onClose={() => {
          if (!share.isPending) setShareOpen(false);
        }}
        closeDisabled={share.isPending}
        title="Share test evidence?"
        content="Share the frozen draft, example inputs, model answers and file receipts with workspace members. This cannot be made private again. Sharing does not attach evidence or publish."
        action={
          <Button
            variant="contained"
            disabled={share.isPending}
            onClick={async () => {
              try {
                setError("");
                await share.mutateAsync({ acknowledgeSnapshotSharing: true });
                setShareOpen(false);
              } catch {
                setError(
                  "Could not share evidence. Your publication and attachment choices are unchanged.",
                );
              }
            }}
          >
            Share with workspace
          </Button>
        }
      />
    </Section>
  );
}
EvidenceContent.propTypes = {
  skillId: PropTypes.string.isRequired,
  candidate: PropTypes.object.isRequired,
  onSelectRun: PropTypes.func.isRequired,
  selectedRunId: PropTypes.string,
  // Says when the attachment is saved; empty when the dialog says it beside its action.
  recordedWith: PropTypes.string,
  /** "summary": one summary line, details under "Technical details" (Move label). */
  variant: PropTypes.oneOf(["full", "summary"]),
};

export default function TestEvidence(props) {
  const { identity, enabled } = useSkillV11Scope();
  if (!enabled)
    return (
      <Alert severity="info">
        Select an authenticated workspace to inspect evidence.
      </Alert>
    );
  return <EvidenceContent key={`${identity}:${props.skillId}`} {...props} />;
}
TestEvidence.propTypes = EvidenceContent.propTypes;
