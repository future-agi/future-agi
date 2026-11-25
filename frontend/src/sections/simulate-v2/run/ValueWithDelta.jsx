import PropTypes from "prop-types";
import { Stack, Tooltip, Typography } from "@mui/material";

/* A number and how far it moved from the baseline: the arrow is the
   direction, green or red whether that's better. No change, no mark. */
export default function ValueWithDelta({
  text,
  diff,
  diffText,
  lowerIsBetter,
  strong,
  tone,
  against = "the baseline",
  wasText = null,
}) {
  const moved =
    diff != null && diff !== 0 && (!diffText || diffText(diff) !== "0");
  const better = moved && (lowerIsBetter ? diff < 0 : diff > 0);
  return (
    <Stack
      direction="row"
      alignItems="baseline"
      justifyContent="flex-end"
      spacing={0.5}
      sx={{ minWidth: 0, whiteSpace: "nowrap" }}
    >
      <Typography
        component="span"
        sx={{
          typography: "s2",
          fontWeight: strong ? 600 : 400,
          color: tone || "text.primary",
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {text}
      </Typography>
      {moved && (
        <Tooltip
          arrow
          title={`${better ? "Better" : "Worse"} than ${against}${wasText ? ` (${wasText})` : ""}`}
        >
          <Typography
            component="span"
            sx={{
              typography: "s3",
              fontWeight: 600,
              fontVariantNumeric: "tabular-nums",
              color: better ? "status.success.fg" : "status.danger.fg",
              cursor: "default",
            }}
          >
            {`${diff > 0 ? "▲" : "▼"}${diffText ? diffText(diff) : Math.abs(diff)}`}
          </Typography>
        </Tooltip>
      )}
    </Stack>
  );
}
ValueWithDelta.propTypes = {
  text: PropTypes.string,
  diff: PropTypes.number,
  diffText: PropTypes.func,
  lowerIsBetter: PropTypes.bool,
  strong: PropTypes.bool,
  tone: PropTypes.string,
  /* Who the change is against, and its value there — for the hover. */
  against: PropTypes.string,
  wasText: PropTypes.string,
};
