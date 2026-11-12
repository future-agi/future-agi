import { formatExactMoney } from "src/sections/common/format/money";
// W7 A2 (W7-3): a decision tree's path, one step per visited node — the
// question, check or eval, the answer, p (a decision model's probabilities),
// the reason, the judge model, cost and time — then the score reached. Used
// by the results drawer (DagScoreDetails) and the editor's Test.
import React from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { checkText } from "./dagModel";

const headOf = (s) =>
  s.kind === "check"
    ? `Check: ${checkText(s.check)}`
    : s.kind === "eval"
      ? `Eval: ${s.template ?? s.template_id}`
      : s.question;

const pText = (p) =>
  p && typeof p === "object"
    ? Object.entries(p)
        .map(([k, v]) => `${k} ${Number(v).toFixed(2)}`)
        .join(" · ")
    : null;

export default function DagPathSteps({
  path = [],
  leaf = null,
  skipLabel = null,
}) {
  return (
    <Stack gap={0.75} data-testid="dag-path-steps">
      {path.map((s) => (
        <Box
          key={`${s.node_id}-${s.n}`}
          data-testid="dag-path-step"
          data-node-id={s.node_id}
          sx={{
            pl: 1.25,
            borderLeft: "2px solid",
            borderColor: "primary.light",
          }}
        >
          <Typography typography="s2" fontWeight={600}>
            {`${s.n}. ${headOf(s)}`}
          </Typography>
          <Typography typography="s3" data-testid="dag-path-answer">
            {`→ ${s.answer}${pText(s.p) ? ` · p ${pText(s.p)}` : ""}`}
          </Typography>
          {s.reason ? (
            <Typography typography="s3" color="text.secondary">
              {s.reason}
            </Typography>
          ) : null}
          <Typography typography="s3" color="text.disabled">
            {[
              s.judge_model,
              s.cost_usd
                ? formatExactMoney(s.cost_usd)
                : s.kind === "check"
                  ? "free"
                  : null,
              s.latency_ms ? `${Math.round(s.latency_ms)} ms` : null,
            ]
              .filter(Boolean)
              .join(" · ")}
          </Typography>
        </Box>
      ))}
      {leaf ? (
        <Typography
          typography="s2"
          fontWeight={700}
          data-testid="dag-path-leaf"
        >
          {`${leaf.label} (${Number(leaf.score).toFixed(2)})`}
        </Typography>
      ) : skipLabel ? (
        <Typography
          typography="s3"
          color="text.secondary"
        >{`Skipped · ${skipLabel}`}</Typography>
      ) : null}
    </Stack>
  );
}

DagPathSteps.propTypes = {
  path: PropTypes.array,
  leaf: PropTypes.object,
  skipLabel: PropTypes.string,
};
