import React, { useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import {
  criterionMetric,
  failingTurnOf,
  productionCellText,
  productionOf,
} from "../retestModel";
import { trialOutcome } from "../resultsSimple";

const VISIBLE = 4;

/**
 * iterate r2 G3 (P1): a row re-tested from production — the conversation
 * with the failing turn marked, then the production reply and the new
 * reply side by side, each with its result on the production criterion.
 */
export default function ProductionRetestCard({ row, run, metrics }) {
  const p = productionOf(row);
  const [all, setAll] = useState(false);
  if (!p) return null;
  const turn = failingTurnOf(row);
  const metric = criterionMetric(metrics, p.criterion);
  const outcome = trialOutcome(row?.results?.[run?.run_id], metric?.key);
  const reply = row?.results?.[run?.run_id]?.output;
  const replyText =
    typeof reply === "string"
      ? reply
      : reply?.text ?? reply?.value ?? (reply ? JSON.stringify(reply) : "");
  const failedProd = String(p.outcome ?? "").toLowerCase() === "fail";
  const turns = turn?.turns ?? [];
  // Around the failing turn (the rest one click away).
  const from = all ? 0 : Math.max(0, (turn?.index ?? 0) - (VISIBLE - 2));
  const shown = all ? turns : turns.slice(from, from + VISIBLE);
  const newTone =
    outcome.tone === "pass"
      ? "green.600"
      : outcome.tone === "none"
        ? "text.secondary"
        : "red.600";
  return (
    <Box
      data-testid="drawer-production-retest"
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        p: 1.25,
      }}
    >
      <Typography typography="s2" fontWeight={600} sx={{ mb: 0.75 }}>
        {`Production vs re-test${p.criterion ? ` · ${p.criterion}` : ""}`}
      </Typography>
      {turns.length ? (
        <Stack spacing={0.5} sx={{ mb: 1 }} data-testid="drawer-conversation">
          {!all && from > 0 ? (
            <Button
              size="small"
              color="inherit"
              onClick={() => setAll(true)}
              sx={{ alignSelf: "flex-start", typography: "s3", px: 0.5 }}
            >
              {`Show the ${turns.length} turns`}
            </Button>
          ) : null}
          {shown.map((t, i) => {
            const idx = from + i;
            const failing = idx === turn.n - 1 || idx === turn.index;
            return (
              <Stack
                key={idx}
                direction="row"
                gap={1}
                data-failing={idx === turn.n - 1 ? "true" : undefined}
                sx={(theme) => ({
                  px: 0.75,
                  py: 0.5,
                  borderRadius: 0.5,
                  borderLeft: "3px solid",
                  borderColor:
                    idx === turn.n - 1 ? "error.main" : "transparent",
                  bgcolor: failing
                    ? `${theme.palette.error.main}0f`
                    : "transparent",
                })}
              >
                <Typography
                  typography="s3"
                  color="text.secondary"
                  sx={{
                    width: 74,
                    flexShrink: 0,
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {`${idx + 1} · ${t.role}`}
                </Typography>
                <Typography typography="s2" sx={{ minWidth: 0 }}>
                  {t.text}
                </Typography>
              </Stack>
            );
          })}
        </Stack>
      ) : null}
      <Stack direction="row" gap={1} sx={{ "& > *": { flex: 1, minWidth: 0 } }}>
        <Box sx={{ borderRadius: 0.75, bgcolor: "action.hover", p: 1 }}>
          <Typography
            typography="s3"
            fontWeight={600}
            sx={{ color: failedProd ? "red.600" : "green.600", mb: 0.5 }}
          >
            {`Production · ${productionCellText(p)}`}
          </Typography>
          <Typography typography="s2" sx={{ whiteSpace: "pre-wrap" }}>
            {p.reply ?? "No reply recorded"}
          </Typography>
          {p.reason ? (
            <Typography typography="s3" color="text.secondary" sx={{ mt: 0.5 }}>
              {p.reason}
            </Typography>
          ) : null}
        </Box>
        <Box sx={{ borderRadius: 0.75, bgcolor: "action.hover", p: 1 }}>
          <Typography
            typography="s3"
            fontWeight={600}
            sx={{ color: newTone, mb: 0.5 }}
            data-testid="drawer-retest-result"
          >
            {`${run?.label ?? "Re-test"} · ${
              outcome.tone === "pass"
                ? "✓ Passed"
                : outcome.tone === "none"
                  ? "Not scored yet"
                  : "✗ Still fails"
            }${p.criterion ? ` · ${p.criterion}` : ""}${outcome.n > 1 ? ` · ${outcome.label}` : ""}`}
          </Typography>
          <Typography typography="s2" sx={{ whiteSpace: "pre-wrap" }}>
            {replyText || "No output yet"}
          </Typography>
          {row?.results?.[run?.run_id]?.scores?.[metric?.key]?.reason ? (
            <Typography typography="s3" color="text.secondary" sx={{ mt: 0.5 }}>
              {row.results[run.run_id].scores[metric.key].reason}
            </Typography>
          ) : null}
        </Box>
      </Stack>
    </Box>
  );
}

ProductionRetestCard.propTypes = {
  row: PropTypes.object,
  run: PropTypes.object,
  metrics: PropTypes.array,
};
