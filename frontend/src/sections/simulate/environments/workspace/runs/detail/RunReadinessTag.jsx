import { useState } from "react";
import PropTypes from "prop-types";
import { Box, ButtonBase, Popover, Stack, Typography } from "@mui/material";
import { VersionBadge } from "../../EnvVersionPin";

const STATUS_WORDS = {
  supported: "Supported",
  partial: "Partial",
  blocked: "Blocked",
  unavailable: "Unavailable",
  untested: "Untested",
  stale: "Stale",
};

// The rows a run started under that were not Supported. Unmeasured rows hold no
// case back, and a Stop not seen yet is no fault of the pairing, so neither counts.
function readinessLimits(snapshot) {
  return (snapshot?.rows ?? []).filter(
    (row) =>
      row.measured &&
      row.status !== "supported" &&
      !(row.id === "cancel" && row.status === "untested"),
  );
}

function readinessTagLabel(snapshot, selection) {
  const limits = readinessLimits(snapshot);
  const held = selection?.held_back?.count ?? 0;
  const count = limits.length;
  // A Partial or Unavailable row holds no case back, so the answer can read ready
  // with one: the tag still counts it, as the popover lists it.
  if (snapshot?.answer?.kind === "ready" && held === 0 && count === 0) {
    return "Ready when started";
  }
  if (count === 0 && held > 0) {
    // Held back for a reason no measured row names, such as a dropped case.
    return `Started with ${held} ${held === 1 ? "case" : "cases"} held back`;
  }
  return `Started with ${count} ${count === 1 ? "limit" : "limits"}`;
}

// What the run's readiness was when it started: its rows not Supported and the
// cases held back from it.
export default function RunReadinessTag({ snapshot, selection }) {
  const [anchor, setAnchor] = useState(null);
  if (!snapshot) return null;
  const limits = readinessLimits(snapshot);
  const held = selection?.held_back?.count ?? 0;
  const label = readinessTagLabel(snapshot, selection);
  const ready = label === "Ready when started";
  const reasons = Object.entries(selection?.reasons ?? {});
  return (
    <>
      <ButtonBase
        onClick={(event) => setAnchor(event.currentTarget)}
        aria-label={label}
        sx={{ borderRadius: "4px" }}
      >
        <VersionBadge label={label} role={ready ? "success" : "warning"} />
      </ButtonBase>
      <Popover
        open={Boolean(anchor)}
        anchorEl={anchor}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
      >
        <Box sx={{ p: 1.5, maxWidth: 360 }}>
          <Typography sx={{ typography: "m3", mb: 1 }}>
            Readiness when this run started
          </Typography>
          <Stack spacing={0.5}>
            {limits.length === 0 && (
              <Typography sx={{ typography: "s1", color: "text.secondary" }}>
                Every check this run needed was Supported.
              </Typography>
            )}
            {limits.map((row) => (
              <Typography key={row.id} sx={{ typography: "s1" }}>
                {`${row.label}: ${STATUS_WORDS[row.status] ?? row.status}`}
                {row.reason ? `, ${row.reason}` : ""}
              </Typography>
            ))}
            {held > 0 && (
              <>
                <Typography sx={{ typography: "s1", mt: 1 }}>
                  {`${held} of ${selection.requested} cases held back`}
                </Typography>
                {reasons.map(([row, reason]) => (
                  <Typography
                    key={row}
                    sx={{ typography: "s3", color: "text.secondary" }}
                  >
                    {reason}
                  </Typography>
                ))}
              </>
            )}
          </Stack>
        </Box>
      </Popover>
    </>
  );
}

const rowShape = PropTypes.shape({
  id: PropTypes.string,
  label: PropTypes.string,
  status: PropTypes.string,
  measured: PropTypes.bool,
  reason: PropTypes.string,
});

RunReadinessTag.propTypes = {
  snapshot: PropTypes.shape({
    answer: PropTypes.shape({ kind: PropTypes.string }),
    rows: PropTypes.arrayOf(rowShape),
  }),
  selection: PropTypes.shape({
    requested: PropTypes.number,
    held_back: PropTypes.shape({ count: PropTypes.number }),
    reasons: PropTypes.objectOf(PropTypes.string),
  }),
};
