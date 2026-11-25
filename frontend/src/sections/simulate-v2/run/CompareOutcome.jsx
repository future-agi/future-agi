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
  /* Red is for a scenario that now fails; one that fails only some trials
     (flaky) is never red. */
  const bad =
    pair.cls === "exact" &&
    pair.flip?.kind === "regressed" &&
    pair.flip?.b !== "flaky";
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
          title={`Changed as policy: ${pair.criteria.map((c) => c.name).join(", ")}. Out of the delta.`}
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

/**
 * What moved, and only that, beside the scenario's name: "passed → failed"
 * when the baseline and the latest run disagree (two runs only — with more,
 * each run's own row says it), the pair class when it isn't the usual exact
 * match (its meaning on hover), and "criteria changed". Each run's result is
 * on its own row, so it is not said again here.
 */
export function OutcomeLine({ pair, cells = [], extra = null }) {
  const meta = pair ? PAIR_CLASSES[pair.cls] : null;
  const kind = pair?.flip?.kind;
  const moved = cells.length === 2 && !!kind && kind !== "same";
  /* A scenario that now fails only some trials is amber, never red. */
  const becameFlaky = kind === "regressed" && pair?.flip?.b === "flaky";
  const tone =
    kind === "regressed"
      ? becameFlaky
        ? "status.warning.fg"
        : "status.danger.fg"
      : kind === "improved"
        ? "status.success.fg"
        : "text.secondary";
  const unusual = meta && pair.cls !== "exact";
  const parts = [];
  if (moved)
    parts.push(
      <Box key="flip" component="span" sx={{ color: tone, fontWeight: 600 }}>
        {pair.flip.text}
      </Box>,
    );
  if (unusual)
    parts.push(
      <Tooltip key="cls" arrow title={meta.tip || ""}>
        <Box component="span" sx={{ cursor: "help" }}>
          {meta.short.toLowerCase()}
        </Box>
      </Tooltip>,
    );
  if ((pair?.criteria || []).length > 0)
    parts.push(<span key="crit">criteria changed</span>);
  if (extra) parts.push(<span key="extra">{extra}</span>);
  if (!parts.length) return null;
  return (
    <Typography
      noWrap
      sx={{ typography: "s3", color: "text.tertiary", flexShrink: 0 }}
    >
      {parts.map((p, i) => (
        <Box key={i} component="span">
          {i > 0 && " · "}
          {p}
        </Box>
      ))}
    </Typography>
  );
}
OutcomeLine.propTypes = {
  pair: PropTypes.object,
  cells: PropTypes.array,
  extra: PropTypes.string,
};
