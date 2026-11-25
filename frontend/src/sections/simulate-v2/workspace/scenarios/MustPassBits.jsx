import PropTypes from "prop-types";
import { Box, Stack, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";

const num = { fontVariantNumeric: "tabular-nums" };

/*
  How much of the set improvements can't break, and why — one quiet line over
  the batches. Most of a large set is protected because it passes today;
  marking is for what must never break.
*/
export function ProtectionLine({ tally, trailing = null, actions = null }) {
  if (!tally?.total) return trailing;
  const held = tally.marked + tally.memory + tally.passing;
  const parts = [
    tally.marked && `${tally.marked} must pass`,
    tally.memory && `${tally.memory} regression memory`,
    tally.passing &&
      `${tally.passing}${tally.marked || tally.memory ? " more" : ""} passed every trial${tally.latestName ? ` in ${tally.latestName}` : " today"}`,
  ].filter(Boolean);
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1}
      sx={{ px: 0.25, mb: 1, minWidth: 0 }}
    >
      <Iconify
        icon="solar:shield-check-linear"
        width={16}
        sx={{ color: "text.tertiary", flexShrink: 0 }}
      />
      {/* One short line; why each is protected is on hover. */}
      <Tooltip arrow title={`Protected ${held} · ${parts.join(" · ")}`}>
        <Typography
          noWrap
          sx={{
            typography: "s3",
            color: "text.primary",
            fontWeight: 600,
            cursor: "default",
            ...num,
          }}
        >{`Must-pass ${tally.marked || 0} · of ${tally.total} scenarios`}</Typography>
      </Tooltip>
      {tally.open > 0 && (
        <Tooltip
          arrow
          title="Not reliably passing yet — mark the ones that must never break as must-pass."
        >
          <Typography
            noWrap
            sx={{
              typography: "s3",
              color: "text.tertiary",
              cursor: "help",
              borderBottom: "1px dotted",
              borderColor: "text.disabled",
              ...num,
            }}
          >
            {`${tally.open} not protected`}
          </Typography>
        </Tooltip>
      )}
      {tally.markedFailing > 0 && (
        <Tooltip
          arrow
          title="Marked must-pass, but failed or flaky in the latest run, so not protected until it passes."
        >
          <Typography
            noWrap
            sx={{
              typography: "s3",
              color: "text.tertiary",
              cursor: "help",
              borderBottom: "1px dotted",
              borderColor: "text.disabled",
              ...num,
            }}
          >
            {`${tally.markedFailing} must-pass failing now`}
          </Typography>
        </Tooltip>
      )}
      {/* Right of the line: the proof counts, moved out of the toolbar. */}
      {trailing}
      <Box sx={{ flex: 1 }} />
      {actions}
    </Stack>
  );
}
ProtectionLine.propTypes = {
  tally: PropTypes.object,
  trailing: PropTypes.node,
  actions: PropTypes.node,
};
