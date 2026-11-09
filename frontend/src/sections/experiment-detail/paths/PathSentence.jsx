// Paths · the answer first (R4 lane T, D14): one sentence about the path
// that fails most (`#2B: 20% of tries (18 of 90) take …; all 18 fail. #3A
// never takes that path.`), then where node identity comes from. Every
// number comes from pathModel.pathSentence (no digit literals here).
import React from "react";
import PropTypes from "prop-types";
import { Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import Term from "src/sections/common/ux/Term";
import {
  NODE_SOURCE_TEXT,
  onePathText,
  pathSentence,
} from "src/sections/common/Trajectory/pathModel";

/** The sentence text (or the one-path line), or null without paths. */
export function sentenceFor(agg, { label, compare, compareLabel } = {}) {
  const one = onePathText(agg);
  if (one) return { text: `${label}: ${one}.`, path: agg.paths[0], one: true };
  return pathSentence(agg, { label, compare, compareLabel });
}

export default function PathSentence({
  agg,
  compareAgg,
  label,
  compareLabel,
  action,
}) {
  const s = sentenceFor(agg, {
    label,
    compare: compareAgg ?? null,
    compareLabel,
  });
  if (!s) return null;
  const failing = s.path?.failed > 0;
  return (
    <Stack gap={0.25} sx={{ minWidth: 0 }} data-testid="path-sentence">
      <Stack direction="row" alignItems="flex-start" gap={1}>
        <Iconify
          icon={failing ? "mdi:alert-circle-outline" : "mdi:source-branch"}
          width={18}
          sx={{
            mt: "2px",
            flexShrink: 0,
            color: failing ? "error.main" : "text.secondary",
          }}
        />
        <Typography
          typography="s1"
          fontWeight="fontWeightMedium"
          sx={{ minWidth: 0, fontVariantNumeric: "tabular-nums" }}
        >
          {s.text}
        </Typography>
        {action}
      </Stack>
      <Typography
        typography="s3"
        color="text.secondary"
        sx={{ pl: 3.25 }}
        data-testid="path-node-source"
      >
        <Term k="pathShare">Paths</Term>
        {` · ${NODE_SOURCE_TEXT[agg.node_source] ?? NODE_SOURCE_TEXT.span_names}`}
      </Typography>
    </Stack>
  );
}

PathSentence.propTypes = {
  agg: PropTypes.object.isRequired,
  compareAgg: PropTypes.object,
  label: PropTypes.string,
  compareLabel: PropTypes.string,
  action: PropTypes.node,
};
