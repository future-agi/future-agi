import PropTypes from "prop-types";
import { Tooltip, Typography } from "@mui/material";
import StatusBadge from "src/components/status-badge/StatusBadge";

/* One colour per ending that earns one — in progress, ready to review, or
   broken. The endings that just stopped (no safe change, budget, not
   reproduced) stay neutral: nothing to celebrate, nothing on fire. */
const STATUS_TONE = {
  running: { status: "running", role: "info" },
  ready: { status: "ready", role: "success" },
  /* Ready, but its proof is on an older version — a person re-checks it. */
  recheck: { status: "ready", role: "warning" },
  failed: { status: "failed", role: "danger" },
};

/**
 * An improvement run's status as a StatusBadge — a tinted badge for the
 * endings that earn a colour, dot + label for the neutral ones. Shared by
 * both Improvements levels.
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
      <span>
        <StatusBadge
          status={tone ? tone.status : "unset"}
          role={tone ? tone.role : "neutral"}
          label={ending.label}
        />
      </span>
    </Tooltip>
  );
}
StatusPill.propTypes = { ending: PropTypes.object, headline: PropTypes.string };
