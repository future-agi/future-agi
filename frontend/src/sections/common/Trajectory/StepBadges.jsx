// W1-9 B3 · a trajectory row's verdict badges and token split (TraceTreeV2
// `rowExtras.renderExtra`). Failing first; the reason on hover.
import React from "react";
import PropTypes from "prop-types";
import { Typography } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import { stepBadgesOf, tokenSplitText } from "./stepBadges";

const COLOR = {
  fail: "accent.fail",
  error: "error.main",
  na: "info.main",
  skipped: "text.disabled",
  not_reached: "text.disabled",
  pass: "accent.pass",
};

export default function StepBadges({ entry, isRoot = false }) {
  const badges = stepBadgesOf(entry);
  const tokens = isRoot ? null : tokenSplitText(entry?.observation_span);
  if (!badges.length && !tokens) return null;
  return (
    <>
      {badges.map((b, i) => (
        <CustomTooltip
          key={`${b.label}-${i}`}
          show={Boolean(b.reason)}
          title={
            <span style={{ whiteSpace: "pre-line" }}>{b.reason ?? ""}</span>
          }
          size="small"
          type="black"
          placement="top"
        >
          <Typography
            component="span"
            data-testid="step-badge"
            data-kind={b.kind}
            sx={{
              fontSize: 9.5,
              fontWeight: 600,
              color: COLOR[b.kind],
              whiteSpace: "nowrap",
            }}
          >
            {b.label}
          </Typography>
        </CustomTooltip>
      ))}
      {tokens ? (
        <Typography
          component="span"
          data-testid="step-tokens"
          sx={{
            fontSize: 9.5,
            color: "text.disabled",
            whiteSpace: "nowrap",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {tokens}
        </Typography>
      ) : null}
    </>
  );
}

StepBadges.propTypes = {
  entry: PropTypes.object,
  isRoot: PropTypes.bool,
};
