import PropTypes from "prop-types";
import { Box } from "@mui/material";

// A quiet neutral tag marking a surface that isn't live yet. Kept low-emphasis
// (action.selected fill, secondary text) so it reads as state, not a call to action, and
// carries a stable aria-label whatever the visible text.
export default function ComingSoonChip({ compact = false, sx }) {
  return (
    <Box
      component="span"
      aria-label="Coming soon"
      sx={{
        display: "inline-flex",
        alignItems: "center",
        flexShrink: 0,
        px: 0.75,
        height: 18,
        borderRadius: "4px",
        typography: "s3",
        fontWeight: 500,
        whiteSpace: "nowrap",
        color: "text.secondary",
        bgcolor: "action.selected",
        ...sx,
      }}
    >
      {compact ? "Soon" : "Coming soon"}
    </Box>
  );
}
ComingSoonChip.propTypes = { compact: PropTypes.bool, sx: PropTypes.object };
