import PropTypes from "prop-types";
import { useState } from "react";
import { useLocation } from "react-router-dom";
import {
  Box,
  Button,
  IconButton,
  Link,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";

import Iconify from "src/components/iconify";
import { ConfirmDialog } from "src/components/custom-dialog";
import { RouterLink } from "src/routes/components";
import { paths } from "src/routes/paths";
import {
  fixErrorOf,
  useStopFixRun,
} from "src/api/simulate-environments/fixRuns";

import EpochMoreMenu from "./EpochMoreMenu";
import FixStatusChip from "./FixStatusChip";
import {
  fixName,
  formatCredits,
  identityLine,
  originOf,
  stageLabel,
} from "./fixRunView";

const STOPPABLE = new Set(["queued", "running"]);

function stopSummary(fix) {
  const n = fix.candidates?.length ?? 0;
  const max = fix.plan?.max_candidates;
  const tried = max
    ? `${n} of up to ${max} candidates`
    : `${n} ${n === 1 ? "candidate" : "candidates"}`;
  return `${tried} ${n === 1 ? "has" : "have"} run · ${formatCredits(fix.spent_credits)} spent so far.`;
}

function StopControl({ fix }) {
  const [asking, setAsking] = useState(false);
  const stop = useStopFixRun(fix.id);
  if (fix.state === "stopping" || stop.isPending) {
    return (
      <Button size="small" variant="outlined" disabled>
        Stopping
      </Button>
    );
  }
  if (!STOPPABLE.has(fix.state)) return null;
  return (
    <>
      <Button
        size="small"
        variant="outlined"
        onClick={() => setAsking(true)}
        startIcon={<Iconify icon="solar:stop-circle-linear" width={16} />}
      >
        Stop
      </Button>
      <ConfirmDialog
        open={asking}
        onClose={() => setAsking(false)}
        title={`Stop ${fixName(fix)}?`}
        content={
          <Stack spacing={1}>
            <Typography sx={{ typography: "s2" }}>
              {stopSummary(fix)}
            </Typography>
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              Stopping ends the search without picking a candidate. The current
              version stays. The candidates that ran stay on this page; nothing
              more is spent.
            </Typography>
          </Stack>
        }
        action={
          <Button
            size="small"
            variant="contained"
            color="primary"
            onClick={() => {
              setAsking(false);
              stop.mutate(undefined, {
                onError: (e) =>
                  enqueueSnackbar(
                    fixErrorOf(e).message || "Could not stop the epoch",
                    { variant: "error" },
                  ),
              });
            }}
          >
            Stop
          </Button>
        }
      />
    </>
  );
}
StopControl.propTypes = { fix: PropTypes.object.isRequired };

// Where the origin line links: the Error Feed issue, or the run the epoch started from.
function originHref(fix, kind, envId, runTestId) {
  if (kind === "issue")
    return paths.dashboard.errorFeed.detail(fix.issue.cluster_id);
  if (kind === "run" && runTestId)
    return paths.dashboard.simulate.environments.execution(
      envId,
      runTestId,
      fix.source_run_id,
    );
  return null;
}

// The design's epoch header (ImprovementRunsView): the environment, then the epoch,
// and below it the versions and where it came from, in words. Everything comes from
// the epoch's own read, so a reload or a shared link keeps it.
export default function FixHeader({ fix, envId, runTestId, actions }) {
  const location = useLocation();
  const { issue } = fix;
  const issueHref = issue
    ? paths.dashboard.errorFeed.detail(issue.cluster_id)
    : null;
  const epochsHref = paths.dashboard.simulate.environments.workspaceTab(
    envId,
    "improvements",
  );
  const origin = originOf(fix);
  const originLink = originHref(fix, origin.kind, envId, runTestId);
  const quiet = { typography: "s3", color: "text.subtitle" };
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={2}
      sx={{
        px: 3,
        py: 2,
        borderBottom: "1px solid",
        borderColor: "divider",
        flexShrink: 0,
      }}
    >
      <IconButton
        size="small"
        component={RouterLink}
        href={issueHref || epochsHref}
        aria-label={issue ? "Back to the issue" : "Back to epochs"}
      >
        <Iconify
          icon="solar:alt-arrow-left-linear"
          width={18}
          sx={{ color: "text.subtitle" }}
        />
      </IconButton>
      <Box flex={1} minWidth={0}>
        <Stack direction="row" alignItems="center" spacing={1}>
          {fix.environment?.name && (
            <Typography
              noWrap
              sx={{ typography: "m3", color: "text.secondary", minWidth: 0 }}
            >
              <Link
                component={RouterLink}
                href={epochsHref}
                color="inherit"
                underline="hover"
                data-testid="epoch-environment-crumb"
              >
                {fix.environment.name}
              </Link>
              {" /"}
            </Typography>
          )}
          <Typography
            noWrap
            sx={{ typography: "m3", fontWeight: 600, flexShrink: 0 }}
          >
            {fixName(fix)}
          </Typography>
          <FixStatusChip state={fix.state} />
          {/* Queued and Stopping are the status itself; a running epoch says its stage. */}
          {fix.state === "running" && stageLabel(fix) && (
            <Typography
              noWrap
              data-testid="epoch-stage"
              sx={{ typography: "s3", color: "text.tertiary" }}
            >
              {stageLabel(fix)}
            </Typography>
          )}
        </Stack>
        <Typography noWrap sx={quiet}>
          {`${identityLine(fix)} · `}
          {originLink ? (
            <Tooltip arrow describeChild title={origin.label}>
              <Link
                component={RouterLink}
                href={originLink}
                state={{ backTo: location.pathname }}
                color="inherit"
                underline="hover"
                data-testid="epoch-origin"
              >
                {origin.label}
              </Link>
            </Tooltip>
          ) : (
            <Box component="span" data-testid="epoch-origin">
              {origin.label}
            </Box>
          )}
        </Typography>
      </Box>
      <Stack direction="row" spacing={1} alignItems="center">
        <StopControl fix={fix} />
        <EpochMoreMenu fix={fix} />
        {actions}
      </Stack>
    </Stack>
  );
}

FixHeader.propTypes = {
  fix: PropTypes.object.isRequired,
  envId: PropTypes.string.isRequired,
  // The environment's run list id: a run's page needs it.
  runTestId: PropTypes.string,
  actions: PropTypes.node,
};
