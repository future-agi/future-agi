import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { useEnvironmentRuns } from "src/api/simulate-environments/runs";
import SideDrawer from "../../components/SideDrawer";
import SoonChip from "../../components/SoonChip";
import StatusChip from "../runs/StatusChip";

const DAY = 24 * 3600 * 1000;

// "Today, 17:00" · "Yesterday, 09:00" · "22 Sept, 21:13": how a history reads.
const whenLabel = (iso) => {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const time = d.toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
  });
  const startOfToday = new Date();
  startOfToday.setHours(0, 0, 0, 0);
  if (d >= startOfToday) return `Today, ${time}`;
  if (d >= new Date(startOfToday.getTime() - DAY)) return `Yesterday, ${time}`;
  return `${d.toLocaleDateString(undefined, { day: "numeric", month: "short" })}, ${time}`;
};

// The executions payload has no pass rate, so it is derived from the run's
// own passed/total, and only once there is something to divide.
const passRateOf = (run) =>
  Number.isFinite(run?.passed) && run?.total > 0
    ? Math.round((run.passed / run.total) * 100)
    : null;

// A run still going (or stopped) has no rate worth showing yet.
const FINISHED = new Set(["passed", "failed"]);

const scenarioCountOf = (run) =>
  run?.scenarioCount ?? (run?.scenarioIds?.length || null);

// Every history row: inset and rounded. The line down to the next entry's dot
// is drawn per row, so it starts and ends exactly on a dot.
const historyRowSx = (connect) => ({
  position: "relative",
  mx: 1,
  px: 1.5,
  py: 1.25,
  borderRadius: 1,
  ...(connect && {
    "&::after": {
      content: '""',
      position: "absolute",
      left: 17,
      top: 20,
      height: "100%",
      width: "1px",
      bgcolor: "divider",
    },
  }),
});

// A run's point on the history line: square, as in Vel's timeline.
function RunDot() {
  return (
    <Box
      sx={{
        width: 11,
        height: 11,
        borderRadius: 0.5,
        flexShrink: 0,
        mt: "4px",
        border: "2px solid",
        borderColor: "text.disabled",
        bgcolor: "background.paper",
        position: "relative",
        zIndex: 1,
      }}
    />
  );
}

function RunEntry({ run, connect }) {
  const when = whenLabel(run.finishedAt || run.startedAt);
  const rate = passRateOf(run);
  const n = scenarioCountOf(run);
  const label = run.label || (run.ordinal ? `Run ${run.ordinal}` : "Run");
  return (
    <Stack
      direction="row"
      spacing={1.25}
      data-testid="history-run"
      sx={historyRowSx(connect)}
    >
      <RunDot />
      <Box minWidth={0} flex={1}>
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {when || "Not started yet"}
        </Typography>
        <Stack direction="row" alignItems="center" spacing={0.75}>
          <Iconify
            icon="solar:play-linear"
            width={12}
            sx={{ color: "text.secondary", flexShrink: 0 }}
          />
          <Typography
            noWrap
            sx={{ typography: "s2", fontWeight: 600, flex: 1 }}
          >
            {rate != null && FINISHED.has(run.status)
              ? `${label} · ${rate}% passed`
              : label}
          </Typography>
          {run.runState && <StatusChip status={run.runState} />}
        </Stack>
        <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
          {n ? `${n} ${n === 1 ? "scenario" : "scenarios"}` : "All scenarios"}
        </Typography>
      </Box>
    </Stack>
  );
}
RunEntry.propTypes = {
  run: PropTypes.object.isRequired,
  connect: PropTypes.bool,
};

// Vel's scenario history drawer. Runs are real: the newest page of the
// environment's executions. Batches (who added which scenarios, and when) are
// not in the API, so that half of his timeline waits behind a soon chip.
function HistoryBody({ env, envState }) {
  const { runs = [], count, isLoading } = useEnvironmentRuns(env, envState);
  const total = count ?? runs.length;
  return (
    <Box
      sx={{
        height: "100%",
        display: "flex",
        flexDirection: "column",
        minHeight: 0,
      }}
    >
      <Box
        sx={{
          pl: 2.5,
          pr: 6,
          py: 1.5,
          borderBottom: "1px solid",
          borderColor: "divider",
          flexShrink: 0,
        }}
      >
        <Typography sx={{ typography: "s1", fontWeight: 600 }}>
          Scenario history
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}>
          {isLoading
            ? "Loading runs"
            : `${total} ${total === 1 ? "run" : "runs"} · newest first`}
        </Typography>
        <Stack
          direction="row"
          alignItems="center"
          spacing={0.75}
          sx={{ mt: 1 }}
        >
          <Typography sx={{ typography: "s3", color: "text.secondary" }}>
            {"Batches aren't in the API yet."}
          </Typography>
          <SoonChip
            label="Batches"
            what="Who added which scenarios, and when"
            plural
            size="tiny"
          />
        </Stack>
      </Box>

      <Box sx={{ flex: 1, minHeight: 0, overflowY: "auto", py: 1 }}>
        {isLoading ? null : runs.length === 0 ? (
          <Typography
            sx={{ px: 2.5, py: 1.5, typography: "s2", color: "text.secondary" }}
          >
            No runs yet.
          </Typography>
        ) : (
          <Box sx={{ position: "relative" }}>
            {runs.map((run, i) => (
              <RunEntry
                key={run.id ?? i}
                run={run}
                connect={i < runs.length - 1}
              />
            ))}
          </Box>
        )}
      </Box>
    </Box>
  );
}
HistoryBody.propTypes = {
  env: PropTypes.object,
  envState: PropTypes.object,
};

export default function HistoryDrawer({ open, onClose, env, envState }) {
  return (
    <SideDrawer open={open} onClose={onClose} width={480}>
      {open && <HistoryBody env={env} envState={envState} />}
    </SideDrawer>
  );
}
HistoryDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  // useEnvironmentRuns reads the real executions off env.platform.runTestId,
  // and falls back to envState.runs for an environment with no run-test yet.
  env: PropTypes.object,
  envState: PropTypes.object,
};
