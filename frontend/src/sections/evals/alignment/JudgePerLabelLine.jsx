import React from "react";
import PropTypes from "prop-types";
import { Link as RouterLink } from "react-router-dom";
import { Link, Stack, Typography } from "@mui/material";
import { improveJudgeHref, pctText } from "./agreementModel";
import { hasAlignmentRoutes } from "./alignmentApi";

/**
 * Dataset Summary › Judge vs human: precision and recall per label, and
 * Improve this judge (only when the pair's eval template exists and the
 * Agreement tab it opens is registered, integration).
 */
const JudgePerLabelLine = ({ pair, datasetId }) => {
  const href = hasAlignmentRoutes() ? improveJudgeHref(pair, datasetId) : null;
  const perLabel = pair?.per_label ?? [];
  if (!perLabel.length && !href) return null;
  return (
    <Stack
      direction="row"
      gap={2}
      alignItems="center"
      flexWrap="wrap"
      sx={{ mt: 1.5 }}
      data-testid="w7-per-label-line"
    >
      {perLabel.length ? (
        <Typography
          typography="s2"
          color="text.secondary"
          sx={{ fontVariantNumeric: "tabular-nums" }}
        >
          {perLabel
            .map(
              (l) =>
                `${l.label}: precision ${pctText(l.precision)} · recall ${pctText(l.recall)}`,
            )
            .join("; ")}
        </Typography>
      ) : null}
      {href ? (
        <Link
          component={RouterLink}
          to={href}
          typography="s2"
          data-testid="w7-improve-link"
        >
          Improve this judge
        </Link>
      ) : null}
    </Stack>
  );
};

JudgePerLabelLine.propTypes = {
  pair: PropTypes.object,
  datasetId: PropTypes.string,
};

export default JudgePerLabelLine;
