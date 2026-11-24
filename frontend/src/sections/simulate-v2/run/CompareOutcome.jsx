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

/* How a run went for one scenario, in words: "all 3 passed",
   "2 of 3 passed", "none of 3 passed"; the "now" side is just "2 of 3". */
const trialsWord = (c, { now = false } = {}) => {
  if (!c || c.status === "missing") return "not run";
  if (c.status === "unmeasured" || c.measured === false) return "not measured";
  const n = c.repeats || 1;
  const k = c.passes ?? (c.status === "passed" ? n : 0);
  if (n <= 1) return k ? "passed" : "failed";
  if (now) return k === n ? `all ${n}` : `${k} of ${n}`;
  if (k === n) return `all ${n} passed`;
  if (k === 0) return `none of ${n} passed`;
  return `${k} of ${n} passed`;
};

/**
 * One quiet line under the scenario's title in the compare table, in plain
 * counts instead of labels: "All 3 passed before · now 2 of 3".
 * The "now" part is red when it got worse, green when it got better. The
 * pair class shows only when it isn't the usual exact match, with its
 * meaning on hover.
 */
export function OutcomeLine({ pair, cells = [], extra = null }) {
  const meta = pair ? PAIR_CLASSES[pair.cls] : null;
  const base = cells[0];
  const cur = cells[cells.length - 1];
  const kind = pair?.flip?.kind;
  const changed =
    !!base && !!cur && cells.length > 1 && kind && kind !== "same";
  const tone =
    kind === "regressed"
      ? "status.danger.fg"
      : kind === "improved"
        ? "status.success.fg"
        : "text.secondary";
  const first = trialsWord(base);
  const lead = first.charAt(0).toUpperCase() + first.slice(1);
  const unusual = meta && pair.cls !== "exact";
  return (
    <Typography sx={{ typography: "s3", color: "text.tertiary", mt: 0.25 }}>
      {changed ? (
        <>
          {`${lead} before · `}
          <Box component="span" sx={{ color: tone, fontWeight: 500 }}>
            {`now ${trialsWord(cur, { now: true })}`}
          </Box>
        </>
      ) : base && cur ? (
        cells.length > 1 ? (
          `${lead} in both runs`
        ) : (
          lead
        )
      ) : null}
      {unusual && (
        <Tooltip arrow title={meta.tip || ""}>
          <Box component="span" sx={{ cursor: "help" }}>
            {` · ${meta.short.toLowerCase()}`}
          </Box>
        </Tooltip>
      )}
      {(pair?.criteria || []).length > 0 && " · criteria changed"}
      {extra && ` · ${extra}`}
    </Typography>
  );
}
OutcomeLine.propTypes = {
  pair: PropTypes.object,
  cells: PropTypes.array,
  extra: PropTypes.string,
};
