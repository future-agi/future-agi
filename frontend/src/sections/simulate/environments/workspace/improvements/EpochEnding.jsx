import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";

import StatusBadge from "src/components/status-badge";

// The design's EpochEnding: how an epoch that shipped nothing ended, in one
// layout for every ending. The status word (neutral; red only when a must-pass
// scenario broke), one sentence on what happened, what it spent, and its one
// way on. Anything the ending needs below goes in as children.
export default function EpochEnding({
  label,
  danger = false,
  sentence,
  spent,
  primary,
  children,
}) {
  return (
    <Box
      data-testid="epoch-ending"
      sx={{
        mb: 2,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        bgcolor: "background.paper",
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        gap={1.5}
        sx={{ px: 2, py: 1.25 }}
      >
        <Box sx={{ flexShrink: 0, alignSelf: "flex-start", pt: "1px" }}>
          <StatusBadge
            status={danger ? "failed" : "stopped"}
            role={danger ? "danger" : "neutral"}
            variant="badge"
            label={label}
          />
        </Box>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography sx={{ typography: "s2", color: "text.primary" }}>
            {sentence}
          </Typography>
          {spent && (
            <Typography
              sx={{ typography: "s3", color: "text.tertiary", mt: 0.25 }}
            >
              {spent}
            </Typography>
          )}
        </Box>
        {primary && <Box sx={{ flexShrink: 0 }}>{primary}</Box>}
      </Stack>
      {children && (
        <Box
          sx={{
            px: 2,
            py: 1.25,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          {children}
        </Box>
      )}
    </Box>
  );
}

EpochEnding.propTypes = {
  // "Epoch 5 · Stopped": the badge.
  label: PropTypes.string.isRequired,
  // Red only when a must-pass scenario broke.
  danger: PropTypes.bool,
  sentence: PropTypes.node.isRequired,
  // "Spent 1,234 credits before it stopped."
  spent: PropTypes.string,
  // The ending's one primary button, or nothing when there is no way on.
  primary: PropTypes.node,
  children: PropTypes.node,
};
