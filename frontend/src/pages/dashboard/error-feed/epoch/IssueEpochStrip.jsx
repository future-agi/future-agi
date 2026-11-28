import PropTypes from "prop-types";
import { Button, Stack, Typography } from "@mui/material";
import { enqueueSnackbar } from "notistack";

import StatusBadge from "src/components/status-badge";
import { RouterLink } from "src/routes/components";
import {
  useIssueEpoch,
  useStopIssueEpoch,
} from "src/api/errorFeed/epochFromIssue";
import { fixErrorOf } from "src/api/simulate-environments/fixRuns";
import { useCodingAgentAttemptsEnabled } from "src/api/simulate-environments/codingAgent";
import { CAPABILITY, useFeatureAllowed } from "src/hooks/useCapabilities";
import CopyForCodingAgentButton from "src/sections/simulate/environments/workspace/improvements/CopyForCodingAgentButton";

import {
  epochHref,
  epochIcon,
  epochKey,
  epochLabel,
  epochLabelLong,
  epochSentence,
  isEpochLive,
  secondaryActions,
} from "./issueEpochView";

function StopJob({ jobId }) {
  const stop = useStopIssueEpoch(jobId);
  return (
    <Button
      size="small"
      variant="text"
      disabled={stop.isPending}
      onClick={() =>
        stop.mutate(undefined, {
          onError: (e) =>
            enqueueSnackbar(
              fixErrorOf(e).message || "Could not stop the epoch",
              { variant: "error" },
            ),
        })
      }
      sx={{ color: "text.secondary" }}
    >
      Stop
    </Button>
  );
}
StopJob.propTypes = { jobId: PropTypes.string.isRequired };

// The issue's epoch in one line under the header: its state, what it means, and the
// other ways on. The header holds the main action.
export default function IssueEpochStrip({ clusterId, projectId, onRunAgain }) {
  const { allowed } = useFeatureAllowed(CAPABILITY.FIX_MY_AGENT);
  const { status } = useIssueEpoch({ clusterId, projectId });
  const codingAgent = useCodingAgentAttemptsEnabled();
  const key = epochKey(status);
  if (!allowed || !key) return null;
  // Only the epoch's own read names its run; the job's summary of it does not.
  const sourceRunId = codingAgent ? status.epoch?.source_run_id : null;
  const mark = epochIcon(status);
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={1.5}
      data-testid="issue-epoch-strip"
      sx={{
        px: 2,
        py: 1,
        borderBottom: "1px solid",
        borderColor: "divider",
        flexShrink: 0,
      }}
    >
      <StatusBadge
        status={isEpochLive(status) ? "running" : key}
        label={epochLabel(status)}
        role={mark.role}
        title={epochLabelLong(status)}
        size="sm"
      />
      <Typography
        sx={{ typography: "s1", color: "text.secondary", flex: 1, minWidth: 0 }}
      >
        {epochSentence(status)}
      </Typography>
      {sourceRunId && (
        <CopyForCodingAgentButton
          sourceRunId={sourceRunId}
          data-testid="issue-copy-for-coding-agent"
        />
      )}
      {secondaryActions(status).map((action) => {
        if (action.kind === "view") {
          return (
            <Button
              key="view"
              size="small"
              variant="outlined"
              component={RouterLink}
              href={epochHref(status.epoch)}
            >
              {action.label}
            </Button>
          );
        }
        if (action.kind === "stop") {
          return <StopJob key="stop" jobId={status.jobId} />;
        }
        return (
          <Button
            key="again"
            size="small"
            variant="outlined"
            onClick={onRunAgain}
          >
            {action.label}
          </Button>
        );
      })}
    </Stack>
  );
}

IssueEpochStrip.propTypes = {
  clusterId: PropTypes.string.isRequired,
  projectId: PropTypes.string,
  onRunAgain: PropTypes.func.isRequired,
};
