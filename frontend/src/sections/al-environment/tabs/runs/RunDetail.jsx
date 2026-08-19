import PropTypes from "prop-types";
import { Box, Button, Typography } from "@mui/material";
import Pane from "../../parts/Pane";
import MetricBars from "./MetricBars";
import ScenarioCard from "./ScenarioCard";

/**
 * One run in full. Reading a run replaces the scenario count with the scenarios themselves —
 * `read_run` overwrites `summary["scenarios"]` with the results array — so the count here comes
 * from that array's length, never from the number the list endpoint carries.
 */
const RunDetail = ({ run, onBack }) => {
  const cases = Array.isArray(run.scenarios) ? run.scenarios : [];
  const models = run.models
    ? ` · agent ${run.models.agent} · user ${run.models.user} · eval harness ${run.models.judge}`
    : "";

  return (
    <Box>
      {/* Back first, above everything. Coming out of a run is the commonest thing anybody does
          here, and it was a small chip below the title. */}
      <Box sx={{ mb: 1.6 }}>
        <Button
          size="small"
          variant="outlined"
          color="inherit"
          onClick={onBack}
          sx={{
            borderRadius: 5,
            color: "text.secondary",
            borderColor: "divider",
          }}
        >
          ‹ all runs
        </Button>
      </Box>

      <Pane
        title={run.run_id}
        meta={`${run.passed ?? 0}/${cases.length} passed in ${run.seconds}s`}
      >
        <Box
          sx={{
            px: 3.6,
            py: 3,
            mb: 2,
            borderRadius: "8px",
            bgcolor: "background.paper",
            border: "1px solid",
            borderColor: "divider",
          }}
        >
          <Typography variant="body2" color="text.secondary">
            {`${run.modality || "chat"} · concurrency ${run.concurrency} · $${run.spent_usd || 0}${models}`}
          </Typography>
          <MetricBars metrics={run.metrics} title="measured across the suite" />
        </Box>

        {cases.map((one) => (
          <ScenarioCard key={one.scenario} runId={run.run_id} result={one} />
        ))}
      </Pane>
    </Box>
  );
};

RunDetail.propTypes = {
  run: PropTypes.object.isRequired,
  onBack: PropTypes.func.isRequired,
};

export default RunDetail;
