import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";

// A tab's label: its name, an optional item count, and — when the tab owns a
// blocking setup gap — an amber attention dot. The count is hidden while the
// builder is streaming (the parent passes a null count), and a zero count never
// shows a badge.
export default function WorkspaceTabLabel({ label, count, gaps }) {
  const hasGaps = gaps && gaps.length > 0;

  return (
    <Stack direction="row" alignItems="center" spacing={0.75}>
      <Typography component="span" sx={{ typography: "s1", color: "inherit" }}>
        {label}
      </Typography>
      {count != null && count > 0 && (
        <Typography
          component="span"
          sx={{ typography: "s3", color: "text.subtitle", fontVariantNumeric: "tabular-nums" }}
        >
          {count}
        </Typography>
      )}
      {hasGaps && (
        // An amber attention dot, not a number — a setup gap is a "needs input"
        // signal, and rendering it as a count read as an item count next to the
        // real count badges.
        <Box
          sx={{
            width: 7,
            height: 7,
            borderRadius: "50%",
            flexShrink: 0,
            bgcolor: "status.warning.solid",
          }}
        />
      )}
    </Stack>
  );
}

WorkspaceTabLabel.propTypes = {
  label: PropTypes.string.isRequired,
  count: PropTypes.number,
  gaps: PropTypes.arrayOf(
    PropTypes.shape({ id: PropTypes.string, title: PropTypes.string }),
  ),
};
