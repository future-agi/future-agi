import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";

import { useHubRules } from "../api/queries";

// "What review means" (SPEC §6.10), rendered from the rules file only.
export default function ReviewMeans({ rules: given, dense }) {
  const { data } = useHubRules();
  const rules = given || data;
  const paragraphs = Array.isArray(rules?.reviewMeans)
    ? rules.reviewMeans
    : rules?.reviewMeans
      ? [rules.reviewMeans]
      : [];
  if (!paragraphs.length) return null;
  return (
    <Box data-testid="hub-review-means" sx={{ display: "flex", flexDirection: "column", gap: dense ? 0.75 : 1 }}>
      {paragraphs.map((text) => (
        <Typography key={text.slice(0, 40)} typography="s2" color="text.secondary">
          {text}
        </Typography>
      ))}
    </Box>
  );
}

ReviewMeans.propTypes = { rules: PropTypes.object, dense: PropTypes.bool };
