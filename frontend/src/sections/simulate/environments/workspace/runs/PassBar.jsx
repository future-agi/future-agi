import PropTypes from "prop-types";
import { Box } from "@mui/material";
import { PASS_BAR_COLORS } from "./runs.constants";

// A two-segment bar: the passed share in green, the rest in red. Rendered next
// to a run's pass percentage so the split is legible at a glance.
export default function PassBar({ passed, total }) {
  const pct = total ? (passed / total) * 100 : 0;
  return (
    <Box
      sx={{
        display: "flex",
        height: 6,
        borderRadius: 3,
        overflow: "hidden",
        bgcolor: "background.neutral",
      }}
    >
      <Box sx={{ width: `${pct}%`, bgcolor: PASS_BAR_COLORS.passed }} />
      <Box sx={{ width: `${100 - pct}%`, bgcolor: PASS_BAR_COLORS.failed }} />
    </Box>
  );
}

PassBar.propTypes = { passed: PropTypes.number, total: PropTypes.number };
