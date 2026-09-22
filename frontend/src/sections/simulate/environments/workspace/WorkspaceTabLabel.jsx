import PropTypes from "prop-types";
import { Box, Stack, Typography, Tooltip } from "@mui/material";
import { BUILD_TONES } from "../buildEnvironment/buildTones";
import { WORKSPACE_COPY } from "./workspace.constants";

// A tab's label: its name, an optional item count, and — when the tab owns a
// blocking setup gap — a soft-tinted count badge whose tooltip lists what is
// still missing. The count is hidden while the builder is streaming (the parent
// passes a null count), and a zero count never shows a badge.
export default function WorkspaceTabLabel({ label, count, gaps }) {
  const hasGaps = gaps && gaps.length > 0;

  const content = (
    <Stack direction="row" alignItems="center" spacing={0.75}>
      <Typography component="span" sx={{ typography: "s2", color: "inherit" }}>
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
        // real count badges. The tooltip (below) lists what's missing.
        <Box
          sx={{
            width: 7,
            height: 7,
            borderRadius: "50%",
            flexShrink: 0,
            bgcolor: BUILD_TONES.amber,
          }}
        />
      )}
    </Stack>
  );

  if (!hasGaps) return content;

  return (
    <Tooltip
      arrow
      title={
        <Box>
          <Typography sx={{ typography: "s3", fontWeight: "fontWeightBold", mb: 0.375 }}>
            {WORKSPACE_COPY.gaps.tooltip}
          </Typography>
          {gaps.map((gap) => (
            <Typography key={gap.id} sx={{ typography: "s3", opacity: 0.9 }}>
              · {gap.title}
            </Typography>
          ))}
        </Box>
      }
    >
      {content}
    </Tooltip>
  );
}

WorkspaceTabLabel.propTypes = {
  label: PropTypes.string.isRequired,
  count: PropTypes.number,
  gaps: PropTypes.arrayOf(
    PropTypes.shape({ id: PropTypes.string, title: PropTypes.string }),
  ),
};
