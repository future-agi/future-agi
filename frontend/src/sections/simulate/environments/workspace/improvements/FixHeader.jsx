import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Button, IconButton, Stack, Typography } from "@mui/material";
import { enqueueSnackbar } from "notistack";

import Iconify from "src/components/iconify";
import { ConfirmDialog } from "src/components/custom-dialog";
import { RouterLink } from "src/routes/components";
import { paths } from "src/routes/paths";
import {
  fixErrorOf,
  useStopFixRun,
} from "src/api/simulate-environments/fixRuns";

import FixStatusChip from "./FixStatusChip";
import { STEP_LABEL, fixName, formatCredits, identityLine } from "./fixRunView";

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

export default function FixHeader({ fix, envId, actions }) {
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
        href={paths.dashboard.simulate.environments.workspaceTab(
          envId,
          "improvements",
        )}
        aria-label="Back to epochs"
      >
        <Iconify
          icon="solar:alt-arrow-left-linear"
          width={18}
          sx={{ color: "text.subtitle" }}
        />
      </IconButton>
      <Box flex={1} minWidth={0}>
        <Stack direction="row" alignItems="center" spacing={1}>
          <Typography noWrap sx={{ typography: "m3", fontWeight: 600 }}>
            {fixName(fix)}
          </Typography>
          <FixStatusChip state={fix.state} />
          {STEP_LABEL[fix.step] && STOPPABLE.has(fix.state) && (
            <Typography
              noWrap
              sx={{ typography: "s3", color: "text.tertiary" }}
            >
              {STEP_LABEL[fix.step]}
            </Typography>
          )}
        </Stack>
        <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
          {identityLine(fix)}
        </Typography>
      </Box>
      <Stack direction="row" spacing={1} alignItems="center">
        <StopControl fix={fix} />
        {actions}
      </Stack>
    </Stack>
  );
}

FixHeader.propTypes = {
  fix: PropTypes.object.isRequired,
  envId: PropTypes.string.isRequired,
  actions: PropTypes.node,
};
