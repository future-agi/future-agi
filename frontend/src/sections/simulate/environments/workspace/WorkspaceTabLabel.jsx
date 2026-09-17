import PropTypes from "prop-types";
import { Box, Stack, Typography, Tooltip } from "@mui/material";
import { alpha } from "@mui/material/styles";
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
        <Box
          sx={{
            display: "grid",
            placeItems: "center",
            flexShrink: 0,
            minWidth: 16,
            height: 16,
            px: "5px",
            borderRadius: "8px",
            bgcolor: (th) => alpha(BUILD_TONES.red, th.palette.mode === "dark" ? 0.2 : 0.12),
            color: BUILD_TONES.red,
            typography: "s3",
            fontWeight: "fontWeightBold",
            lineHeight: 1,
            fontVariantNumeric: "tabular-nums",
            fontSize: 10,
          }}
        >
          {gaps.length}
        </Box>
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
