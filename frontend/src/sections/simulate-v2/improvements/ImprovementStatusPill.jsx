import PropTypes from "prop-types";
import { Stack, Tooltip, Typography } from "@mui/material";
import StatusBadge from "src/components/status-badge/StatusBadge";

/* One colour per ending that earns one — in progress, ready to review, or
   broken. The endings that just stopped (no safe change, budget, not
   reproduced, stopped) stay neutral: nothing to celebrate, nothing on fire. */
const STATUS_TONE = {
  running: { status: "running", role: "info" },
  ready: { status: "ready", role: "success" },
  /* Ready, but its proof is on an older version — a person re-checks it. */
  recheck: { status: "ready", role: "warning" },
  /* Shipped, and the issue came back. */
  reopened: { status: "failed", role: "danger" },
};

/**
 * An epoch's status as a StatusBadge — a tinted badge for the endings that
 * earn a colour, dot + label for the neutral ones — and, once it shipped,
 * where it went ("PR #12", "27 days left") beside it. Shared by both
 * Epochs levels.
 */
export default function StatusPill({ ending, headline }) {
  if (!ending) {
    return (
      <Typography sx={{ typography: "s3", color: "text.disabled" }}>
        —
      </Typography>
    );
  }
  const tone = STATUS_TONE[ending.key];
  return (
    <Tooltip arrow title={headline || ""} disableHoverListener={!headline}>
      <Stack
        component="span"
        direction="row"
        alignItems="center"
        spacing={0.75}
        sx={{ minWidth: 0 }}
      >
        <StatusBadge
          status={tone ? tone.status : "unset"}
          role={tone ? tone.role : "neutral"}
          label={ending.label}
        />
        {ending.detail && (
          <Typography
            component="span"
            noWrap
            sx={{ typography: "s3", color: "text.tertiary", minWidth: 0 }}
          >
            {ending.detail}
          </Typography>
        )}
      </Stack>
    </Tooltip>
  );
}
StatusPill.propTypes = { ending: PropTypes.object, headline: PropTypes.string };
