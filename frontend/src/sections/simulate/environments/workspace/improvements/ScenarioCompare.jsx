import PropTypes from "prop-types";
import { useLocation } from "react-router-dom";
import { Box, Link, Stack, Typography } from "@mui/material";

import StatusBadge from "src/components/status-badge";
import { RouterLink } from "src/routes/components";
import { paths } from "src/routes/paths";

import SideDrawer from "../../components/SideDrawer";

// A call as the verdict read it: the judge's result, or not measured.
const callStatus = (call) =>
  call.harnessOutcomeStatus === "passed" ||
  call.harnessOutcomeStatus === "failed"
    ? call.harnessOutcomeStatus
    : "unmeasured";

function Calls({ title, calls, envId }) {
  const location = useLocation();
  const sorted = [...calls].sort(
    (a, b) => (a.trialIndex ?? 0) - (b.trialIndex ?? 0),
  );
  return (
    <Box sx={{ minWidth: 0 }}>
      <Typography sx={{ typography: "s2", fontWeight: 600, mb: 1 }}>
        {title}
      </Typography>
      <Stack
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "8px",
        }}
      >
        {sorted.map((call, i) => (
          <Box
            key={call.id}
            data-testid={`compare-call-${call.id}`}
            sx={{
              px: 1.5,
              py: 1,
              ...(i > 0 && { borderTop: "1px solid", borderColor: "divider" }),
            }}
          >
            <Stack direction="row" alignItems="center" spacing={1}>
              <Typography sx={{ typography: "s2", flex: 1 }}>
                {call.trialIndex ? `Trial ${call.trialIndex}` : "Call"}
              </Typography>
              <StatusBadge status={callStatus(call)} size="sm" />
              {call.checkRun && (
                <Link
                  component={RouterLink}
                  href={`${paths.dashboard.simulate.environments.execution(
                    envId,
                    call.checkRun.run_id,
                    call.checkRun.test_execution_id,
                  )}?rowId=${encodeURIComponent(call.id)}`}
                  state={{ backTo: location.pathname }}
                  sx={{ typography: "s3", flexShrink: 0 }}
                >
                  Open call
                </Link>
              )}
            </Stack>
            {call.outcomeReason && (
              <Typography
                sx={{
                  typography: "s3",
                  color: "text.subtitle",
                  mt: 0.5,
                  wordBreak: "break-word",
                }}
              >
                {call.outcomeReason}
              </Typography>
            )}
          </Box>
        ))}
      </Stack>
    </Box>
  );
}
Calls.propTypes = {
  title: PropTypes.string.isRequired,
  calls: PropTypes.array.isRequired,
  envId: PropTypes.string.isRequired,
};

// The design's before-and-after compare (CallCompare) over the check runs'
// calls: one scenario's calls on the current version beside the candidate's,
// each opening on its run.
export default function ScenarioCompare({
  row,
  heading,
  afterTitle,
  envId,
  onClose,
}) {
  return (
    <SideDrawer open={!!row} onClose={onClose} width={760}>
      {row && (
        <Box data-testid="scenario-compare" sx={{ p: 2.5, pr: 5 }}>
          <Typography sx={{ typography: "s1", fontWeight: 600 }}>
            {heading}
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle", mb: 2 }}>
            {row.name}
          </Typography>
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: {
                xs: "1fr",
                md: "repeat(2, minmax(0, 1fr))",
              },
              gap: 2,
            }}
          >
            <Calls
              title="Before the change"
              calls={row.beforeCalls}
              envId={envId}
            />
            <Calls title={afterTitle} calls={row.afterCalls} envId={envId} />
          </Box>
        </Box>
      )}
    </SideDrawer>
  );
}

ScenarioCompare.propTypes = {
  row: PropTypes.object,
  heading: PropTypes.string,
  afterTitle: PropTypes.string.isRequired,
  envId: PropTypes.string.isRequired,
  onClose: PropTypes.func.isRequired,
};
