// W7 A1: a composite cell's children (integration S7 drawer, S8 grid tooltip).
// Compact: `Correctness ×2 Pass 0.82 · Tone ×1 Fail 0.31 · majority → Pass`
// and `Judges disagree (2–1)`. Full: one line per child with its judge,
// level, value, reason and steps, and a link to the jury report.
import React from "react";
import PropTypes from "prop-types";
import { Box, Chip, Link, Stack, Typography } from "@mui/material";
import { Link as RouterLink } from "react-router-dom";
import { endpoints } from "src/utils/axios";
import {
  aggregationWord,
  compactLine,
  isJuryOf,
  levelText,
  splitLine,
  voteShareLine,
} from "./compositeModel";

const verdictOf = (k) => (k.skipped ? "Skipped" : k.passed ? "Pass" : "Fail");
const colorOf = (k) =>
  k.skipped ? "text.disabled" : k.passed ? "success.main" : "error.main";

function ChildLine({ k }) {
  return (
    <Box
      data-testid="composite-child"
      sx={{
        py: 0.75,
        borderBottom: "1px solid",
        borderColor: "divider",
        "&:last-of-type": { borderBottom: 0 },
      }}
    >
      <Stack direction="row" alignItems="center" gap={1} flexWrap="wrap">
        <Typography typography="s2" fontWeight={600}>
          {k.name}
        </Typography>
        <Typography typography="s3" color="text.secondary">
          ×{k.weight}
        </Typography>
        {k.judge_model ? (
          <Chip size="small" variant="outlined" label={k.judge_model} />
        ) : null}
        <Typography typography="s3" color="text.secondary">
          {levelText(k)}
        </Typography>
        <Box sx={{ flex: 1 }} />
        <Typography typography="s2" fontWeight={600} color={colorOf(k)}>
          {verdictOf(k)}
          {k.skipped || k.value == null ? "" : ` ${Number(k.value).toFixed(2)}`}
        </Typography>
      </Stack>
      {k.reason ? (
        <Typography typography="s3" color="text.secondary" sx={{ mt: 0.25 }}>
          {k.reason}
        </Typography>
      ) : null}
      {k.steps?.length ? (
        <Stack direction="row" gap={0.5} flexWrap="wrap" sx={{ mt: 0.5 }}>
          {k.steps.map((s) => (
            <Chip
              key={s.n}
              size="small"
              variant="outlined"
              color={
                s.passed == null ? "default" : s.passed ? "success" : "error"
              }
              label={`Step ${s.n} · ${s.name}${s.value == null ? "" : ` ${Number(s.value).toFixed(2)}`}`}
            />
          ))}
        </Stack>
      ) : null}
    </Box>
  );
}

ChildLine.propTypes = { k: PropTypes.object.isRequired };

export default function CompositeScoreDetails({ cell, compact = false }) {
  const t = cell?.typed;
  if (t?.kind !== "composite") return null;
  const split = splitLine(t);
  if (compact)
    return (
      <Box data-testid="composite-compact">
        <Typography typography="s3">{compactLine(t, cell.passed)}</Typography>
        {split ? (
          <Typography typography="s3" fontWeight={600}>
            {split}
          </Typography>
        ) : null}
      </Box>
    );
  return (
    <Box
      data-testid="composite-details"
      sx={{
        mb: 1.25,
        p: 1.25,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
      }}
    >
      <Stack direction="row" alignItems="center" gap={1} sx={{ mb: 0.5 }}>
        <Typography typography="s2" fontWeight={600}>
          {["Children", aggregationWord(t.aggregation), voteShareLine(t)]
            .filter(Boolean)
            .join(" · ")}
        </Typography>
        {split ? <Chip size="small" color="warning" label={split} /> : null}
        <Box sx={{ flex: 1 }} />
        {t.template_id && endpoints.develop?.w7?.compositeJury ? (
          <Link
            typography="s3"
            component={RouterLink}
            to={`/dashboard/evaluations/${t.template_id}?tab=details${t.run_id ? `&run=${t.run_id}` : ""}&view=jury`}
          >
            {`${isJuryOf(t) ? "Judge" : "Child"} agreement${t.run_label ? ` on ${t.run_label}` : ""}`}
          </Link>
        ) : null}
      </Stack>
      {t.children.map((k) => (
        <ChildLine key={k.id} k={k} />
      ))}
    </Box>
  );
}

CompositeScoreDetails.ready = true;

CompositeScoreDetails.propTypes = {
  cell: PropTypes.object,
  compact: PropTypes.bool,
};
