import PropTypes from "prop-types";
import { Typography } from "@mui/material";

/*
  The Evals tab holds two kinds of result (glossary: sub-goal checks decide
  the trial; a platform evaluation only scores it — "keep the locked scorer's
  result separate from an extra evaluation"). Each block says which it is.
*/
const headSx = { typography: "s3", fontWeight: 600, color: "text.secondary" };
const noteSx = { typography: "s3", color: "text.tertiary" };

export function VerdictHeading() {
  return (
    <div>
      <Typography sx={headSx}>Verdict · sub-goal checks</Typography>
      <Typography sx={noteSx}>
        These decide whether this trial passed.
      </Typography>
    </div>
  );
}

export function ExtraEvalsHeading({ sx }) {
  return (
    <div style={{ paddingTop: 4 }}>
      <Typography sx={{ ...headSx, ...sx }}>Extra evaluations</Typography>
      <Typography sx={noteSx}>
        They score this trial and never change its verdict.
      </Typography>
    </div>
  );
}
ExtraEvalsHeading.propTypes = { sx: PropTypes.object };
