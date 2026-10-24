import PropTypes from "prop-types";
import { Box, Stack, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { blockerReason, unmarkedNote } from "../../_mock/releaseBlocker";

/**
 * The release-blocker triangle, with the reason it was set.
 *
 * Read-only unless `onToggle` is given. Toggleable rows show a hollow triangle
 * on hover so a scenario can be marked without opening the editor; one that
 * was un-marked keeps its hollow triangle, so the change is still visible.
 */
export default function BlockerFlag({
  row,
  onToggle,
  locked = false,
  reason,
  size = 13,
}) {
  const on = !!row.critical;
  const note = unmarkedNote(row);
  const canToggle = !!onToggle && !locked;
  if (!on && !canToggle && !note) return null;

  const why = on ? reason || blockerReason(row) : note;
  const action = canToggle
    ? on
      ? "Click to un-mark."
      : "Click to mark must-pass — an improvement that breaks it is rejected."
    : locked
      ? "Fork this environment to change it."
      : null;

  const title = (
    <Stack spacing={0.375} sx={{ py: 0.25, maxWidth: 280 }}>
      <Typography sx={{ typography: "s3", fontWeight: 600, color: "inherit" }}>
        {on ? "Must-pass" : "Not must-pass"}
      </Typography>
      {/* The PRD calls these must-pass scenarios: an improvement that breaks
          one is rejected, however much else it lifts. */}
      {on && (
        <Typography sx={{ typography: "s3", color: "inherit" }}>
          Improvements are rejected if they break it.
        </Typography>
      )}
      {why && (
        <Typography sx={{ typography: "s3", color: "inherit" }}>
          {why}
        </Typography>
      )}
      {action && (
        <Typography sx={{ typography: "s3", color: "inherit", opacity: 0.7 }}>
          {action}
        </Typography>
      )}
    </Stack>
  );

  return (
    <Tooltip arrow title={title}>
      <Box
        component={canToggle ? "button" : "span"}
        type={canToggle ? "button" : undefined}
        aria-label={
          canToggle
            ? on
              ? "Remove must-pass"
              : "Mark as must-pass"
            : "Must-pass"
        }
        aria-pressed={canToggle ? on : undefined}
        onClick={
          canToggle
            ? (e) => {
                e.stopPropagation();
                onToggle(row.id);
              }
            : undefined
        }
        sx={{
          display: "inline-flex",
          alignItems: "center",
          flexShrink: 0,
          p: 0,
          m: 0,
          border: 0,
          bgcolor: "transparent",
          font: "inherit",
          cursor: canToggle ? "pointer" : "default",
          /* A shield in the text colour — protected, not broken. Red read as an error. */
          color: on ? "text.primary" : "text.disabled",
          /* Hollow and hidden until the row is hovered — unless someone
             un-marked it, which stays on show. */
          ...(!on &&
            !note && {
              opacity: 0,
              ".MuiTableRow-root:hover &, .blocker-host:hover &, &:focus-visible":
                { opacity: 1 },
            }),
          transition: "opacity .12s ease, color .12s ease",
          "&:hover": canToggle
            ? { color: on ? "text.secondary" : "text.primary" }
            : undefined,
          "&:focus-visible": {
            outline: "2px solid",
            outlineColor: "primary.main",
            outlineOffset: 2,
            borderRadius: "4px",
          },
        }}
      >
        <Iconify
          icon={on ? "solar:shield-check-bold" : "solar:shield-check-linear"}
          width={size}
        />
      </Box>
    </Tooltip>
  );
}

BlockerFlag.propTypes = {
  row: PropTypes.object.isRequired,
  onToggle: PropTypes.func,
  locked: PropTypes.bool,
  reason: PropTypes.string,
  size: PropTypes.number,
};
