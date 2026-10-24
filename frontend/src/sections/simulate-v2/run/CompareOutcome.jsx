import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Tooltip, Typography } from "@mui/material";
import { PAIR_CLASSES } from "../_mock/pairs";

/* Red only for a regression — an exact pair that passed and fails now. */

/**
 * A scenario row's tag: its pair class against the baseline and, when the
 * verdict moved, the flip — "Exact · passed → failed". Classes outside the
 * delta read as themselves (N/A, New, Changed …); a sub-goal changed as
 * policy rides alongside as "criteria changed".
 */
export default function OutcomeChip({ pair }) {
  if (!pair) return null;
  const meta = PAIR_CLASSES[pair.cls];
  if (!meta) return null;
  const moved = meta.inDelta && pair.flip && pair.flip.kind !== "same";
  const bad = pair.cls === "exact" && pair.flip?.kind === "regressed";
  const tip = [
    meta.tip,
    ...(pair.reasons || []).filter((r) => !/^Only in the/.test(r)),
    moved ? `Baseline ${pair.flip.a}, now ${pair.flip.b}.` : null,
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <Box
      component="span"
      sx={{
        display: "inline-flex",
        alignItems: "center",
        gap: 0.5,
        flexShrink: 0,
      }}
    >
      <Tooltip arrow title={tip}>
        <Typography
          component="span"
          noWrap
          sx={{
            px: 0.75,
            py: 0.125,
            borderRadius: "4px",
            flexShrink: 0,
            typography: "s3",
            fontWeight: 600,
            color: bad
              ? "status.danger.fg"
              : meta.inDelta
                ? "text.secondary"
                : "text.tertiary",
            bgcolor: (t) =>
              bad
                ? t.palette.status.danger.bg
                : alpha(t.palette.text.primary, 0.06),
          }}
        >
          {meta.short}
          {moved && (
            <Box
              component="span"
              sx={{ fontWeight: 600 }}
            >{` · ${pair.flip.text}`}</Box>
          )}
        </Typography>
      </Tooltip>
      {(pair.criteria || []).length > 0 && (
        <Tooltip
          arrow
          title={`Changed as policy: ${pair.criteria.map((c) => c.name).join(", ")}. Out of the delta — the scenario still pairs on its other sub-goals.`}
        >
          <Typography
            component="span"
            noWrap
            sx={{
              px: 0.75,
              py: 0.125,
              borderRadius: "4px",
              flexShrink: 0,
              typography: "s3",
              fontWeight: 600,
              color: "text.tertiary",
              border: "1px dashed",
              borderColor: "divider",
            }}
          >
            criteria changed
          </Typography>
        </Tooltip>
      )}
    </Box>
  );
}
OutcomeChip.propTypes = { pair: PropTypes.object };
