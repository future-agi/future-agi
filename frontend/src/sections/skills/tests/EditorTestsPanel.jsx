import { useRef, useState } from "react";
import PropTypes from "prop-types";
import { Link as RouterLink } from "react-router-dom";
import {
  Alert,
  Button,
  DialogContent,
  DialogTitle,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";

import {
  useSkillTestRun,
  useSkillTestSuite,
  useSkillV11Scope,
} from "src/api/skills/testing";
import ContentSkeleton from "src/components/content-skeleton";
import EmptyState from "src/components/empty-state";
import ErrorState from "src/components/error-state";
import StatusBadge from "src/components/status-badge";
import { ClosableDialog } from "src/components/custom-dialog";
import { paths } from "src/routes/paths";
import TestRunSetup from "./TestRunSetup";
import TestRunResults from "./TestRunResults";

function EditorTestsContent({
  skill,
  draft,
  isDirty,
  onSave,
  onOpenFile,
  onImproveSelection,
}) {
  const suite = useSkillTestSuite(skill.id);
  const [caseId, setCaseId] = useState("");
  const [dialog, setDialog] = useState(null);
  const [lastRun, setLastRun] = useState(null);
  const acknowledged = useRef(null);
  const run = useSkillTestRun(skill.id, lastRun?.id);
  const canWrite = skill.permissions?.canRunTests === true;
  const stale =
    lastRun && (isDirty || lastRun.files !== JSON.stringify(draft.files));
  const flush = async () => {
    // This callback is the integration-owned session flush, never a second save implementation.
    const saved = await onSave();
    acknowledged.current = saved;
    return saved;
  };
  if (suite.isLoading)
    return (
      <ContentSkeleton
        variant="rows"
        rows={3}
        label="Loading editor examples"
      />
    );
  if (suite.isError)
    return (
      <ErrorState title="Could not load examples" onRetry={suite.refetch} />
    );
  if (!suite.data)
    return (
      <Alert severity="info">
        Select an authenticated workspace to run an example.
      </Alert>
    );
  return (
    <Stack spacing={1.5} sx={{ minWidth: 0 }}>
      <Typography variant="m3">Tests</Typography>
      <Typography variant="s3" color="text.secondary">
        In this text-only test · no scripts or external actions
      </Typography>
      {!suite.data.cases.length ? (
        <EmptyState
          title="Add a real request"
          description="Save a reviewed example in Tests before comparing your draft."
          action={
            <Button
              size="small"
              variant="outlined"
              component={RouterLink}
              to={`${paths.dashboard.skills.detail(skill.id)}?tab=tests`}
            >
              Add examples
            </Button>
          }
        />
      ) : (
        <>
          <TextField
            select
            size="small"
            label="Example to run"
            value={caseId}
            onChange={(event) => setCaseId(event.target.value)}
          >
            {suite.data.cases.map((item) => (
              <MenuItem key={item.id} value={item.id} disabled={!item.reviewed}>
                {item.title}
                {item.reviewed ? "" : " · needs review"}
              </MenuItem>
            ))}
          </TextField>
          <Button
            variant="outlined"
            disabled={
              !canWrite ||
              !suite.data.cases.some(
                (item) => item.id === caseId && item.reviewed,
              )
            }
            onClick={() => setDialog("setup")}
          >
            Run example
          </Button>
          <Typography variant="s3" color="text.secondary">
            Pending edits are saved before preview. A save conflict blocks the
            run; your editor content stays intact.
          </Typography>
        </>
      )}
      {lastRun && (
        <>
          <Typography variant="s1">
            Testing saved draft r{lastRun.revision}.
          </Typography>
          {stale && (
            <Alert severity="warning">
              For earlier content. Edit, save and run again to check the current
              draft.
            </Alert>
          )}
          {run.isError ? (
            <ErrorState
              title="Could not load the last run"
              onRetry={run.refetch}
            />
          ) : run.data ? (
            <>
              <StatusBadge status={run.data.status} />
              <Typography variant="s3" role="status" aria-live="polite">
                {run.data.counts.finished}/{run.data.counts.planned} attempts
                finished · {run.data.counts.graded} graded
              </Typography>
            </>
          ) : (
            <ContentSkeleton
              variant="rows"
              rows={1}
              label="Loading run progress"
            />
          )}
          <Button
            size="small"
            variant="outlined"
            onClick={() => setDialog("results")}
          >
            Inspect results and receipts
          </Button>
        </>
      )}
      <ClosableDialog
        open={Boolean(dialog)}
        onClose={() => setDialog(null)}
        fullWidth
        maxWidth="lg"
        aria-labelledby="editor-tests-title"
      >
        <DialogTitle id="editor-tests-title">
          <Typography component="span" variant="m1">
            {dialog === "results"
              ? "Draft test evidence"
              : "Review example comparison"}
          </Typography>
        </DialogTitle>
        <DialogContent>
          {dialog === "setup" && (
            <TestRunSetup
              skill={skill}
              draft={draft}
              suite={suite.data}
              caseIds={[caseId]}
              onBeforePreview={flush}
              compact
              onStarted={(job, configuration) => {
                setLastRun({
                  id: job.id,
                  revision: configuration.candidate.revision,
                  files: JSON.stringify(acknowledged.current.files),
                });
                setDialog("results");
              }}
            />
          )}
          {dialog === "results" && lastRun && (
            <Stack spacing={2}>
              {stale && (
                <Alert severity="warning">
                  For earlier content. These results do not describe the current
                  editor contents.
                </Alert>
              )}
              <TestRunResults
                key={lastRun.id}
                skillId={skill.id}
                runId={lastRun.id}
                canWrite={canWrite}
                onOpenFile={(path) => {
                  setDialog(null);
                  onOpenFile(path);
                }}
                onImproveSelection={
                  onImproveSelection
                    ? (selectedCaseId) => {
                        setDialog(null);
                        onImproveSelection({
                          sourceRunId: lastRun.id,
                          caseId: selectedCaseId,
                        });
                      }
                    : undefined
                }
              />
            </Stack>
          )}
        </DialogContent>
      </ClosableDialog>
    </Stack>
  );
}
EditorTestsContent.propTypes = {
  skill: PropTypes.object.isRequired,
  draft: PropTypes.object.isRequired,
  isDirty: PropTypes.bool.isRequired,
  onSave: PropTypes.func.isRequired,
  onOpenFile: PropTypes.func.isRequired,
  onImproveSelection: PropTypes.func,
};

export default function EditorTestsPanel(props) {
  const { identity, enabled } = useSkillV11Scope();
  if (!enabled)
    return (
      <Alert severity="info">
        Select an authenticated workspace to view editor tests.
      </Alert>
    );
  return (
    <EditorTestsContent key={`${identity}:${props.skill.id}`} {...props} />
  );
}
EditorTestsPanel.propTypes = EditorTestsContent.propTypes;
