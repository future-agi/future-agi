import PropTypes from "prop-types";
import { useNavigate } from "react-router-dom";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { SectionCard } from "../components/primitives";
import { optimizationList, optimizationVerdict } from "../_mock/optimizationRuns";
import OptimizationRunsList from "../run/fixmyagent/OptimizationRunsList";

/**
 * Every optimization this environment has seen.
 *
 * The canonical home, because an optimization is about the agent and the
 * environment rather than about the one simulation run that prompted it.
 * Filing them under runs would mean a team's tuning history disappeared the day
 * somebody tidied their runs list, and "how has this agent been tuned over
 * time" would have no screen behind it.
 *
 * Opening one goes back to the run it came from, where the diagnosis it was
 * made against is still on screen — a result read without the problem
 * statement beside it is half the story.
 */
export default function OptimizationsPanel({ env, envState }) {
  const navigate = useNavigate();
  const records = optimizationList(envState);
  const improved = records.filter((r) => r.result && r.result.heldScore > r.result.heldBase).length;
  /*
    The same test the row's warning triangle uses. Counting only gamed winners
    and rejected blockers here meant a run flagged in the list was reported as
    "none" in the tile directly above it — two definitions of the same word on
    one screen.
  */
  const flagged = records.filter((r) => {
    const v = optimizationVerdict(r);
    return v && v.tone !== "#16A34A";
  }).length;

  return (
    <Stack spacing={2}>
      {!!records.length && (
        <Stack direction="row" spacing={1.5} flexWrap="wrap" rowGap={1.5}>
          <Tile label="Runs" value={records.length} />
          <Tile label="Improved held-out" value={improved} tone="#16A34A" />
          <Tile
            label="Needed a second look" value={flagged} tone={flagged ? "#CA8A04" : undefined}
            note={flagged ? "gamed winner, rejected blocker or a regression" : "none"}
          />
        </Stack>
      )}

      <SectionCard
        title="Optimization runs"
        subtitle="Prompt searches scored by this environment. The number shown is the held-out rate — what the winner did on scenarios the search never saw."
      >
        <OptimizationRunsList
          records={records}
          scope="environment"
          onOpen={(r) => r.fromRunId && navigate(paths.dashboard.simulate.simulationRun(env.id, r.fromRunId))}
          emptyBody="Finish a run, open Fix my agent from its results, and start a search. Every optimization this environment has seen will be listed here."
        />
      </SectionCard>
    </Stack>
  );
}

OptimizationsPanel.propTypes = { env: PropTypes.object, envState: PropTypes.object };

function Tile({ label, value, tone, note }) {
  return (
    <Box
      sx={{
        px: 2, py: 1.5, borderRadius: 1.5, border: "1px solid", borderColor: "divider",
        minWidth: 150, flex: "0 1 auto",
        bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.02 : 0.01),
      }}
    >
      <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{label}</Typography>
      <Typography sx={{ typography: "m2", fontWeight: 700, color: tone || "text.primary", fontVariantNumeric: "tabular-nums" }}>
        {value}
      </Typography>
      {note && <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{note}</Typography>}
    </Box>
  );
}

Tile.propTypes = { label: PropTypes.string, value: PropTypes.number, tone: PropTypes.string, note: PropTypes.string };
