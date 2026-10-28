import PropTypes from "prop-types";
import { LinearProgress, Stack, Typography } from "@mui/material";

import { formatSpan, plural, runSpanMs } from "./runHeaderFormat";

/**
 * How far a live run has got: a bar over its calls and "3/12 calls done · 2
 * passed · 1 failed · 4m 12s elapsed". Done is every call no longer queued or
 * running. The header refetches while the run is live, which moves the clock.
 */
export default function RunProgressBar({ stats, startedAt, unit }) {
  const total = stats?.total ?? 0;
  const done = Math.max(
    0,
    total - (stats?.queued ?? 0) - (stats?.inProgress ?? 0),
  );
  const value = total ? Math.round((done / total) * 100) : 0;
  const elapsed = formatSpan(runSpanMs(startedAt, null));
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1.5}
      sx={{ px: 3, py: 1, borderBottom: "1px solid", borderColor: "divider" }}
    >
      <LinearProgress
        variant="determinate"
        value={value}
        aria-label="Run progress"
        sx={{ flex: 1, maxWidth: 320, height: 6, borderRadius: 3 }}
      />
      <Typography
        data-testid="run-progress"
        noWrap
        sx={{
          typography: "s2",
          color: "text.subtitle",
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {`${done}/${plural(total, unit)} done · ${stats?.passed ?? 0} passed · ${stats?.failed ?? 0} failed`}
        {elapsed ? ` · ${elapsed} elapsed` : ""}
      </Typography>
    </Stack>
  );
}
RunProgressBar.propTypes = {
  stats: PropTypes.shape({
    total: PropTypes.number,
    passed: PropTypes.number,
    failed: PropTypes.number,
    queued: PropTypes.number,
    inProgress: PropTypes.number,
  }),
  startedAt: PropTypes.string,
  unit: PropTypes.string.isRequired,
};
