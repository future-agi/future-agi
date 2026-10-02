import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box } from "@mui/material";

// A quiet status pill marking a surface that isn't live yet. Kept low-emphasis
// (muted fill, secondary text) so it reads as state, not a call to action, and
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
        py: 0.125,
        borderRadius: 10,
        typography: "s3",
        fontWeight: "fontWeightSemiBold",
        letterSpacing: 0.2,
        lineHeight: 1.5,
        whiteSpace: "nowrap",
        color: "text.secondary",
        bgcolor: (th) => alpha(th.palette.text.primary, 0.08),
        border: "1px solid",
        borderColor: "divider",
        ...sx,
      }}
    >
      {compact ? "Soon" : "Coming soon"}
    </Box>
  );
}
ComingSoonChip.propTypes = { compact: PropTypes.bool, sx: PropTypes.object };
