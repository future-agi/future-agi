import React from "react";
import PropTypes from "prop-types";
import { Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";

/**
 * iterate r2 G3 (P1): the answer a re-test of production failures is for,
 * one line + one next step: `Re-test of 6 production failures: 4 now pass
 * · 2 still fail (Resolution)  [See the 2 still failing]`.
 */
export default function RetestStrip({ summary, runLabel, onShowStill }) {
  if (!summary) return null;
  // IT-R4 G3 (P7): neutral until every re-tested row is scored — never red
  // or green on a partial answer (`2 of 6 scored so far`).
  const pending = summary.notScored > 0;
  const bad = !pending && summary.stillFail > 0;
  const tone = pending ? "text.secondary" : bad ? "red.600" : "green.600";
  const scored = summary.total - summary.notScored;
  const text = pending
    ? `Re-test of ${summary.total} production ${summary.total === 1 ? "failure" : "failures"}: ${scored} of ${summary.total} scored so far (${summary.nowPass} pass, ${summary.stillFail} fail) · the answer when all are scored`
    : summary.text;
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={1}
      sx={{
        minWidth: 0,
        minHeight: 24,
        px: 1,
        borderRadius: 0.75,
        bgcolor: (t) =>
          pending
            ? t.palette.action.hover
            : bad
              ? `${t.palette.error.main}0d`
              : `${t.palette.success.main}0d`,
      }}
      data-testid="results-retest"
    >
      <Iconify
        icon={
          pending
            ? "mdi:progress-clock"
            : bad
              ? "mdi:alert-circle"
              : "mdi:check-circle"
        }
        width={16}
        sx={{ color: tone, flexShrink: 0 }}
      />
      <Typography
        typography="s2"
        fontWeight={600}
        noWrap
        title={text}
        sx={{ minWidth: 0, flex: "0 1 auto" }}
        data-testid="results-retest-text"
      >
        {`${runLabel ? `${runLabel} · ` : ""}${text}`}
      </Typography>
      {bad ? (
        <Button
          size="small"
          variant="contained"
          color="primary"
          onClick={() => onShowStill(summary.stillIds)}
          data-testid="results-retest-still"
          sx={{
            height: 24,
            px: 1,
            typography: "s2",
            flexShrink: 0,
            whiteSpace: "nowrap",
          }}
        >
          {`See the ${summary.stillFail} still failing`}
        </Button>
      ) : null}
    </Stack>
  );
}

RetestStrip.propTypes = {
  summary: PropTypes.object,
  runLabel: PropTypes.string,
  onShowStill: PropTypes.func.isRequired,
};
