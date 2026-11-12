import React from "react";
import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { firstWrongStepText } from "src/sections/common/Align/milestones";
import { fmtCount } from "src/sections/common/format/fmt";
import { causeMarker } from "./matrixModel";

/**
 * `#2B fails 6 rows that every other run passes. All 6 mention an order id
 * like "A-7781". First wrong step: #2B step 1 · classify_intent.` with `Show 6 rows` ·
 * `Compare a trace` · `Test a fix →` and `Rule-based` (R5
 * `pattern` + its root cause). One line above the Matrix grid.
 */
export function patternSentence(pattern, cause, runLabel, noun = "row") {
  if (!pattern?.rows?.length) return null;
  const n = pattern.rows.length;
  const parts = [
    `${runLabel} fails ${fmtCount(n, noun)} that every other run passes.`,
  ];
  if (pattern.common_feature) parts.push(`All ${n} ${pattern.common_feature}.`);
  const marker = causeMarker(cause, runLabel);
  if (marker) parts.push(`${firstWrongStepText(marker)}.`);
  return parts.join(" ");
}

/**
 * IT-R1 G2 P4: the lead line of the `Why?` panel, from the cause the
 * Summary names: `#2B: Order id loses its "A-" prefix on 4 rows · first
 * wrong step 1 · classify_intent`. Falls back to patternSentence.
 */
export function patternLead(pattern, cause, runLabel, noun = "row") {
  if (!pattern?.rows?.length) return null;
  const title = cause?.title;
  if (!title) return patternSentence(pattern, cause, runLabel, noun);
  const marker = causeMarker(cause, runLabel);
  return [
    `${runLabel}: ${title} on ${fmtCount(pattern.rows.length, noun)}`,
    marker ? `first wrong step ${marker.stepN} · ${marker.stepName}` : null,
  ]
    .filter(Boolean)
    .join(" · ");
}

const PatternCallout = ({
  pattern,
  cause,
  runLabel,
  active,
  onShowRows,
  onCompare,
  onTestFix,
  testFixDisabledReason,
  wrap = false,
  noun = "row",
}) => {
  const sentence = patternSentence(pattern, cause, runLabel, noun);
  if (!sentence) return null;
  const n = pattern.rows.length;
  const lead = wrap ? patternLead(pattern, cause, runLabel, noun) : null;
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={1}
      data-testid="pattern-callout"
      id="matrix-pattern-callout"
      tabIndex={-1}
      sx={(t) => ({
        mx: 1,
        px: 1.25,
        py: 0.25,
        minHeight: 34,
        borderRadius: 0.5,
        border: "1px solid",
        borderColor: alpha(t.palette.primary.main, 0.3),
        bgcolor: alpha(t.palette.primary.main, 0.06),
        flexWrap: wrap ? "wrap" : "nowrap",
        ...(wrap ? { py: 0.75, rowGap: 0.5 } : null),
      })}
    >
      <Iconify
        icon="mdi:lightbulb-on-outline"
        width={18}
        sx={{ color: "primary.main", flexShrink: 0 }}
      />
      {wrap ? (
        // IT-R1 G2 P4: in the Why? panel the reason is read in full.
        <Box sx={{ minWidth: 0, flex: "1 1 100%" }}>
          <Typography typography="s2" fontWeight="fontWeightSemiBold">
            {lead}
          </Typography>
          {lead !== sentence ? (
            <Typography typography="s2" color="text.secondary">
              {sentence}
            </Typography>
          ) : null}
        </Box>
      ) : (
        /* R4 implementation (8c): one line; the whole sentence in the tooltip. */
        <CustomTooltip show size="small" placement="top" title={sentence}>
          <Typography
            typography="s2"
            noWrap
            sx={{ minWidth: 0, flex: "1 1 240px" }}
          >
            {sentence}
          </Typography>
        </CustomTooltip>
      )}
      <Stack
        direction="row"
        alignItems="center"
        gap={0.5}
        sx={{ flexShrink: 0 }}
      >
        <Button
          size="small"
          color={active ? "primary" : "inherit"}
          variant={active ? "outlined" : "text"}
          onClick={onShowRows}
          sx={{ typography: "s2", whiteSpace: "nowrap" }}
        >
          {active ? "Show all rows" : `Show ${fmtCount(n, "row")}`}
        </Button>
        <CustomTooltip
          show
          size="small"
          placement="top"
          arrow
          title={`Step through these ${fmtCount(n, "row")} with ${runLabel} next to a run that passed`}
        >
          <Button
            size="small"
            color="inherit"
            startIcon={<Iconify icon="mdi:compare-horizontal" width={16} />}
            onClick={onCompare}
            sx={{ typography: "s2", whiteSpace: "nowrap" }}
          >
            Open side by side
          </Button>
        </CustomTooltip>
        {/* dataset integration FE-0: no Test a fix for a run it does not apply to. */}
        {onTestFix ? (
          <CustomTooltip
            show={Boolean(testFixDisabledReason)}
            size="small"
            placement="top"
            arrow
            title={testFixDisabledReason ?? ""}
          >
            <span>
              <Button
                size="small"
                variant="contained"
                color="primary"
                disabled={Boolean(testFixDisabledReason)}
                onClick={onTestFix}
                sx={{ typography: "s2", whiteSpace: "nowrap" }}
              >
                Test a fix →
              </Button>
            </span>
          </CustomTooltip>
        ) : null}
        <CustomTooltip
          show
          size="small"
          placement="top"
          arrow
          title="Rule-based: these failing rows share this pattern in the results. Nothing changes until you run a fix."
        >
          {/* One view: the provenance badge is an icon (the words are in
              its tooltip and its accessible name). */}
          <Box
            component="span"
            role="img"
            aria-label="Rule-based pattern"
            sx={{
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              width: 22,
              height: 22,
              borderRadius: 0.5,
              bgcolor: "action.hover",
              color: "text.secondary",
            }}
          >
            <Iconify icon="mdi:creation-outline" width={14} />
          </Box>
        </CustomTooltip>
      </Stack>
    </Stack>
  );
};

PatternCallout.propTypes = {
  wrap: PropTypes.bool,
  noun: PropTypes.string,
  pattern: PropTypes.object,
  cause: PropTypes.object,
  runLabel: PropTypes.string,
  active: PropTypes.bool,
  onShowRows: PropTypes.func,
  onCompare: PropTypes.func,
  onTestFix: PropTypes.func,
  testFixDisabledReason: PropTypes.string,
};

export default PatternCallout;
