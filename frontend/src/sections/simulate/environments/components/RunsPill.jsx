import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";

// §1 `runs_count` is 0 or 1 only: a job carries at most one test execution, so
// the number answers "has this environment ever been run", not "how many times"
// (a rerun replaces the execution rather than appending). So this renders a
// never-run / has-run state, not a lifetime total. null means the field is not
// available yet (still building, or an older row) — render a dash.
export default function RunsPill({ total }) {
  const label = total == null ? "—" : total > 0 ? "Ran" : "Never";
  const hasRun = total != null && total > 0;
  return (
    <Box
      sx={{
        display: "inline-flex",
        alignItems: "center",
        px: 0.75,
        py: 0.25,
        borderRadius: 0.75,
        border: "1px solid",
        borderColor: "divider",
      }}
    >
      <Typography
        sx={{
          typography: "s3",
          fontWeight: "fontWeightBold",
          color: hasRun ? "text.secondary" : "text.disabled",
        }}
      >
        {label}
      </Typography>
    </Box>
  );
}

RunsPill.propTypes = { total: PropTypes.number };
