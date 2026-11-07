import { useState } from "react";
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
import EmptyState from "src/components/empty-state";
import ErrorState from "src/components/error-state";
import Section from "src/components/section";
import StatusBadge from "src/components/status-badge";
import { ConfirmDialog } from "src/components/custom-dialog";
import { paths } from "src/routes/paths";
import { fDateTime } from "src/utils/format-time";
import { TestExecutionConfiguration } from "./TestRunResults";

function EvidenceContent({ skillId, candidate, onSelectRun, selectedRunId }) {
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
  const attachable =
    row?.canAttach &&
    row.visibility === "workspace" &&
    row.currentness.content &&
    row.currentness.payloadAvailable &&
    !isActiveSkillJob(row.status);
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
  return (
    <Section
      title="Test evidence"
      description="Model tests are advisory. Exact content is an identity match, not an approval."
    >
      <Stack spacing={1.5}>
        {error && <Alert severity="error">{error}</Alert>}
        {selectedRunId &&
          !runs.some((item) => item.runId === selectedRunId) && (
            <Alert severity="warning">
              The selected evidence no longer matches this candidate or is
              unavailable. Review the conflict or explicitly remove the
              attachment.
            </Alert>
          )}
        {!runs.length ? (
          <EmptyState
            variant="no-results"
            title="No test evidence for these files"
            description="Model tests are advisory. Publishing remains subject to static checks and release permissions."
          />
        ) : (
          <>
            <TextField
              select
              size="small"
              label="Inspect test evidence"
              value={row?.runId ?? ""}
              onChange={(event) => {
                setInspectId(event.target.value);
                setError("");
              }}
            >
              {runs.map((run) => (
                <MenuItem key={run.runId} value={run.runId}>
                  {fDateTime(run.createdAt)} · {run.selectedCases} examples ·{" "}
                  {run.status} · {run.visibility}
                </MenuItem>
              ))}
            </TextField>
            {row && (
              <>
                <Stack direction="row" spacing={1} flexWrap="wrap">
                  <StatusBadge status={row.status} />
                  <Typography variant="s1">
                    {row.currentness.content
                      ? `Checked this exact content · ${row.suiteCases === null ? `${row.selectedCases} ad hoc examples` : `${row.selectedCases} of ${row.suiteCases} saved examples`}`
                      : "The draft changed after this check."}
                  </Typography>
                </Stack>
                <Typography variant="s3" color="text.secondary">
                  {row.repetitions} attempt(s) per arm ·{" "}
                  {fDateTime(row.createdAt)} ·{" "}
                  {row.visibility === "private"
                    ? "Private evidence"
                    : "Shared with workspace"}
                </Typography>
                {row.validity === "diagnostic" && (
                  <Alert severity="warning">
                    Diagnostic evidence. Reused or bundled examples may
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
                    Saved examples changed after this run.
                  </Typography>
                )}
                {row.currentness.competitors === false && (
                  <Typography variant="s3">
                    Competitor targets changed after this run.
                  </Typography>
                )}
                <Typography variant="s1">
                  {row.answerRegressions} answer regressions (including mixed) ·{" "}
                  {row.routingFailures} routing failures ·{" "}
                  {row.deliveryFailures} delivery failures · {row.errorAttempts}{" "}
                  error attempts
                </Typography>
                {row.answerRegressions > 0 && (
                  <Alert severity="warning">
                    {row.answerRegressions === 1
                      ? "One regression needs review."
                      : "Answer regressions need review."}
                  </Alert>
                )}
                {detail.isLoading ? (
                  <ContentSkeleton
                    variant="rows"
                    rows={2}
                    label="Loading frozen execution configuration"
                  />
                ) : detail.isError ? (
                  <ErrorState
                    title="Could not load frozen execution configuration"
                    onRetry={detail.refetch}
                  />
                ) : (
                  detail.data && (
                    <>
                      <Typography variant="s3" color="text.secondary">
                        {detail.data.counts.graded}/{detail.data.counts.planned}{" "}
                        attempts graded · {detail.data.counts.incomplete}{" "}
                        incomplete · {detail.data.counts.notStarted} not started
                      </Typography>
                      <TestExecutionConfiguration
                        configuration={detail.data.executionConfiguration}
                      />
                    </>
                  )
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
                  <Button
                    size="small"
                    variant="outlined"
                    disabled={
                      !attachable || !canWrite || row.runId === selectedRunId
                    }
                    onClick={() => onSelectRun(row.runId)}
                  >
                    Attach this evidence
                  </Button>
                </Stack>
              </>
            )}
          </>
        )}
        <Stack direction="row" spacing={1}>
          <Button size="small" component={RouterLink} to={link()}>
            Run tests
          </Button>
          {selectedRunId && (
            <Button size="small" onClick={() => onSelectRun(null)}>
              Continue without attachment
            </Button>
          )}
        </Stack>
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
