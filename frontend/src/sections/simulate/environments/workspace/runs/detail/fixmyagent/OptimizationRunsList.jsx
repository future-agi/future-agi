import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";

import Iconify from "src/components/iconify";
import { fToNow } from "src/utils/format-time";
import { ROW_HEIGHT } from "src/theme/sizes";
import StatusBadge from "src/components/status-badge/StatusBadge";
import EmptyState from "../../../../components/EmptyState";
import { LoadingScreen } from "src/components/loading-screen";

/**
 * Past self-improvement (optimization) runs for this execution — the Trials tab.
 *
 * Sourced from the REAL `getOptimizationRuns` list (`useOptimizationRuns`),
 * scoped by `test_execution_id`. Every field shown here is one the endpoint
 * returns: name, status, optimiser type, trial count, start time. The designer's
 * held-out / training scores and per-run verdicts are NOT shown — the list
 * payload doesn't carry them, and a fabricated leaderboard number is exactly the
 * kind of thing the gap policy forbids.
 *
 * Opening a row hands off to the product's optimization run detail (Phase-3
 * precedent: view existing runs through the product, don't port the 1200-line
 * mock run view).
 */

// The list statuses the endpoint returns, mapped to a label and a status role.
const STATUS_TONE = {
  pending: { label: "Queued", role: "neutral" },
  running: { label: "Running", role: "info" },
  completed: { label: "Completed", role: "success" },
  failed: { label: "Failed", role: "danger" },
};

export default function OptimizationRunsList({ runs = [], isLoading, onOpen }) {
  if (isLoading) {
    return <LoadingScreen compact />;
  }

  if (!runs.length) {
    return (
      <EmptyState
        icon="solar:magic-stick-3-linear"
        title="No self-improvement runs yet"
        body="Optimizations you launch for this run will appear here."
      />
    );
  }

  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        overflow: "hidden",
      }}
    >
      <Stack
        divider={
          <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
        }
      >
        {runs.map((r) => {
          const st = STATUS_TONE[r.status];
          return (
            <Stack
              key={r.id}
              direction="row"
              alignItems="center"
              spacing={2}
              onClick={() => onOpen?.(r)}
              sx={{
                px: 2,
                py: 0.5,
                minHeight: ROW_HEIGHT.twoLine,
                cursor: "pointer",
                "&:hover": { bgcolor: "action.hover" },
              }}
            >
              <Box flex={1} minWidth={0}>
                <Stack
                  direction="row"
                  alignItems="center"
                  spacing={0.875}
                  flexWrap="wrap"
                  rowGap={0.5}
                >
                  <Typography noWrap sx={{ typography: "s2", fontWeight: 500 }}>
                    {r.name}
                  </Typography>
                  {st && (
                    <StatusBadge
                      status={r.status}
                      label={st.label}
                      role={st.role}
                    />
                  )}
                </Stack>
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                  {r.optimiserLabel} · {r.trials} trial
                  {r.trials === 1 ? "" : "s"}
                  {r.startedAt ? ` · started ${fToNow(r.startedAt)}` : ""}
                </Typography>
              </Box>
              <Iconify
                icon="solar:alt-arrow-right-linear"
                width={15}
                sx={{ color: "text.disabled", flexShrink: 0 }}
              />
            </Stack>
          );
        })}
      </Stack>
    </Box>
  );
}

OptimizationRunsList.propTypes = {
  runs: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.string,
      name: PropTypes.string,
      status: PropTypes.string,
      optimiserLabel: PropTypes.string,
      trials: PropTypes.number,
      startedAt: PropTypes.string,
    }),
  ),
  isLoading: PropTypes.bool,
  onOpen: PropTypes.func,
};
