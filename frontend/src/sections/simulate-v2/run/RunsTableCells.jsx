import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";

/*
  The runs table's cells — a header, a number with its change beside it, and a
  grader's score. Shared by the environment's Runs tab and an improvement's
  runs, so the two read the same.
*/

export function Head({ children, right, divider, last, title, wrap, gap }) {
  return (
    <Typography
      title={title}
      sx={{
        /* Sentence case, not shouted uppercase — the ALL-CAPS pass on
           long labels ("PROFESSION | ALISM") broke mid-word and read
           heavy alongside the values below. Small-caps weight + a
           lighter tint gives the same "this is a header" signal
           without the noise. */
        typography: "s3",
        fontWeight: 600,
        color: "text.tertiary",
        letterSpacing: 0.2,
        lineHeight: 1.3,
        textAlign: right ? "right" : "left",
        minWidth: 0,
        px: right || gap ? 1.25 : 0,
        ...(gap && { pl: 2.5 }),
        ...(last && { pr: 2.5 }),
        /* Metric headers stay on one line (their columns are sized to fit);
           grader names may wrap on word boundaries, never mid-word. */
        overflow: "hidden",
        ...(wrap
          ? {
              whiteSpace: "normal",
              overflowWrap: "normal",
              wordBreak: "normal",
              /* Two lines at most; a longer name ends in an ellipsis and the
               full name is in the tooltip. */
              display: "-webkit-box",
              WebkitLineClamp: 2,
              WebkitBoxOrient: "vertical",
            }
          : { whiteSpace: "nowrap", textOverflow: "ellipsis" }),
        /* The graders are a different kind of number from the system ones, and
           a single hairline says so more quietly than a second header row. */
        ...(divider && { borderLeft: "1px solid", borderColor: "divider" }),
      }}
    >
      {children}
    </Typography>
  );
}
Head.propTypes = {
  children: PropTypes.node,
  right: PropTypes.bool,
  divider: PropTypes.bool,
  last: PropTypes.bool,
  title: PropTypes.string,
  wrap: PropTypes.bool,
  gap: PropTypes.bool,
};

/**
 * The grader heatmap.
 *
 * Same hues and the same bands as `interpolateColorBasedOnScore` — the scale
 * the develop and observe grids paint their eval cells with — carried at a
 * lower alpha. Those grids show one row at a time against white space; here
 * five rows of graders sit stacked, and at the shared strength the block of
 * colour competes with the numbers printed on it.
 */
const SCORE_BANDS = [
  /* eslint-disable no-restricted-syntax -- ds: data colour */
  { upTo: 20, hue: "#D92D20", strong: true },
  { upTo: 40, hue: "#D92D20", strong: false },
  { upTo: 60, hue: "#E9690C", strong: false },
  { upTo: 80, hue: "#E6B800", strong: false },
  { upTo: 99, hue: "#00A251", strong: false },
  { upTo: Infinity, hue: "#00A251", strong: true },
  /* eslint-enable no-restricted-syntax */
];

const scoreFill = (value, mode) => {
  if (value == null) return "transparent";
  const band =
    SCORE_BANDS.find((b) => value < b.upTo) ||
    SCORE_BANDS[SCORE_BANDS.length - 1];
  const dark = mode === "dark";
  if (band.strong) return alpha(band.hue, dark ? 0.13 : 0.1);
  return alpha(band.hue, dark ? 0.08 : 0.06);
};

/**
 * A number, and how far it has moved from the baseline.
 *
 * The delta sits beside the value rather than replacing it, because both
 * questions are live: "is this run fast enough" and "is it faster than the one
 * we were comparing against" are asked by different people in the same meeting.
 */
export function MetricCell({
  deltaWidth = 0,
  text,
  anchor,
  quiet,
  tone,
  delta,
}) {
  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: deltaWidth ? `1fr ${deltaWidth}px` : "1fr",
        alignItems: "center",
        columnGap: 0.75,
        minWidth: 0,
        alignContent: "center",
        px: 1.25,
      }}
    >
      <Typography
        noWrap
        sx={{
          typography: "s2",
          fontVariantNumeric: "tabular-nums",
          textAlign: "right",
          fontWeight: anchor ? 600 : 500,
          color: tone || (quiet ? "text.tertiary" : "text.primary"),
        }}
      >
        {text}
      </Typography>
      {!!deltaWidth && <Delta delta={delta} />}
    </Box>
  );
}
MetricCell.propTypes = {
  deltaWidth: PropTypes.number,
  text: PropTypes.node,
  anchor: PropTypes.bool,
  quiet: PropTypes.bool,
  tone: PropTypes.string,
  delta: PropTypes.object,
};

/**
 * Better or worse, not up or down.
 *
 * Half these metrics are lower-is-better, so colouring by direction would
 * paint a run that got cheaper and faster in the same red as one that started
 * hallucinating. The arrow says which way it moved; the colour says whether
 * that was good.
 */
/**
 * Movement, and only movement.
 *
 * This is the one thing on the row that colour is for, so it is the only thing
 * that gets any — and softened, because a column of saturated green and red is
 * read as alarm rather than as information. A metric that did not move renders
 * nothing: a dash in every unchanged cell is ink spent saying "no news".
 */
function Delta({ delta }) {
  if (!delta || delta.flat) return <Box />;
  /* The arrow says which way the number moved; the colour says whether that
     was good — so a cheaper run shows a green down arrow. A change inside
     the noise two runs carry is grey: it moved, but it says nothing. */
  const up = delta.up ?? delta.better;
  const color = delta.muted
    ? "text.tertiary"
    : delta.better
      ? "status.success.fg"
      : "status.danger.fg";
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={0.125}
      sx={{ minWidth: 0 }}
    >
      <Iconify
        icon={up ? "solar:arrow-up-linear" : "solar:arrow-down-linear"}
        width={12}
        sx={{ color, flexShrink: 0 }}
      />
      <Typography
        noWrap
        sx={{
          typography: "s3",
          fontWeight: 600,
          color,
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {delta.text}
      </Typography>
    </Stack>
  );
}
Delta.propTypes = { delta: PropTypes.object };

/**
 * A grader's result for one run.
 *
 * Tinted rather than plain, because the point of the column is to be scanned
 * down: a run where one grader collapsed should be findable without reading
 * every number.
 */
export function ScoreCell({ value, deltaWidth = 0, delta, divider, last }) {
  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: deltaWidth ? `1fr ${deltaWidth}px` : "1fr",
        alignItems: "center",
        alignContent: "center",
        columnGap: 0.75,
        minWidth: 0,
        px: 1.25,
        /* Filled like the observe grid's eval cells, a shade quieter. */
        backgroundColor: (t) => scoreFill(value, t.palette.mode),
        ...(last && { pr: 2.5 }),
        ...(divider && { borderLeft: "1px solid", borderColor: "divider" }),
      }}
    >
      {/*
        Plain. The red/amber/green bands these carried were invented here — the
        thresholds belong to the graders, not to this table — so every cell was
        painted by a rule nobody set, and nine coloured numbers a row is a
        traffic light with no junction. Movement is the thing worth colouring,
        and the delta beside it does that.
      */}
      <Typography
        noWrap
        sx={{
          typography: "s2",
          fontWeight: 600,
          textAlign: "right",
          fontVariantNumeric: "tabular-nums",
          color: value == null ? "text.disabled" : "text.primary",
        }}
      >
        {value == null ? "—" : `${value}%`}
      </Typography>
      {!!deltaWidth && <Delta delta={delta} />}
    </Box>
  );
}
ScoreCell.propTypes = {
  value: PropTypes.number,
  deltaWidth: PropTypes.number,
  delta: PropTypes.object,
  divider: PropTypes.bool,
  last: PropTypes.bool,
};
