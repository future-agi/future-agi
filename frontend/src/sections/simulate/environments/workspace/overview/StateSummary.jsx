import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import MetricStrip from "src/components/metric-strip";

/**
 * StateSummary — the state-of-the-env answer that Overview leads with.
 *
 * Four tiles (Scenarios, Evaluations, Runs, Hard rules) each clickable to jump
 * to the relevant tab, plus a latest-run card when there is one. Every number
 * comes from the same state the Runs/Scenarios/Evaluations tabs read from, so
 * the summary and the detail can't drift.
 */
export default function StateSummary({ env, envState, counts, latestRun, onGo }) {
  // A backed env passes real §6 counts; otherwise derive from the client store.
  // `??` (not `||`) so a real 0 is respected rather than falling back.
  const scenarioCount = counts?.scenarios ?? (envState?.scenarios?.length || 0);
  const evalCount = counts?.evaluations ?? (envState?.evals?.length || 0);
  const runs = envState?.runs || [];
  const runCount = counts?.runs ?? runs.length;
  const ruleCount = counts?.hardRules ?? (env?.rules?.length || 0);
  // A backed env's runs come from the executions list, newest first; the
  // client store only holds runs for a non-backed env.
  const latest = latestRun || runs[0] || null;

  const tiles = [
    { id: "scenarios", label: "Scenarios", value: scenarioCount, to: "scenarios" },
    { id: "evals", label: "Evaluations", value: evalCount, to: "evals" },
    { id: "runs", label: "Runs", value: runCount, to: "runs", disabled: !runCount },
    { id: "rules", label: "Hard rules", value: ruleCount, to: "contract" },
  ];

  return (
    <Box sx={{ mb: 3 }}>
      <MetricStrip
        sx={{ mb: latest ? 1.5 : 0 }}
        items={tiles.map((t) => ({
          key: t.id,
          label: t.label,
          value: t.value,
          onClick: t.disabled ? undefined : () => onGo?.(t.to),
        }))}
      />

      {latest && (
        <Box
          onClick={() => onGo?.("runs")}
          sx={{
            p: 1.5, borderRadius: "8px", cursor: "pointer",
            border: "1px solid", borderColor: "divider",
            "&:hover": { bgcolor: "action.hover" },
          }}
        >
          <Stack direction="row" alignItems="center" spacing={1.5}>
            <Box
              sx={{
                width: 28, height: 28, borderRadius: 1, display: "grid", placeItems: "center",
                flexShrink: 0,
                bgcolor: "action.selected",
                color: "text.secondary",
              }}
            >
              <Iconify icon="solar:play-circle-linear" width={16} />
            </Box>
            <Box flex={1} minWidth={0}>
              <Typography sx={{ typography: "s3", color: "text.subtitle", fontWeight: "fontWeightMedium" }}>
                Latest run
              </Typography>
              <Typography noWrap sx={{ typography: "s1", fontWeight: "fontWeightMedium" }}>
                {latest.label || latest.name || "Run"}
                {latest.agentVersion ? ` · ${latest.agentVersion}` : ""}
              </Typography>
            </Box>
            <Iconify icon="solar:alt-arrow-right-linear" width={14} sx={{ color: "text.disabled", flexShrink: 0 }} />
          </Stack>
        </Box>
      )}
    </Box>
  );
}

StateSummary.propTypes = {
  env: PropTypes.object,
  envState: PropTypes.object,
  counts: PropTypes.shape({
    scenarios: PropTypes.number,
    evaluations: PropTypes.number,
    runs: PropTypes.number,
    hardRules: PropTypes.number,
  }),
  // The newest run from the executions list (backed env); see runs.js.
  latestRun: PropTypes.shape({
    label: PropTypes.string,
    agentVersion: PropTypes.string,
  }),
  onGo: PropTypes.func,
};
