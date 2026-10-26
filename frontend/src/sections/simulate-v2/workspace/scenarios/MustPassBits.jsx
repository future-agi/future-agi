import PropTypes from "prop-types";
import { Box, Stack, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";

const num = { fontVariantNumeric: "tabular-nums" };

/*
  How much of the set improvements can't break, and why — one quiet line over
  the batches. Most of a large set is protected because it passes today;
  marking is for what must never break.
*/
export function ProtectionLine({ tally }) {
  if (!tally?.total) return null;
  const held = tally.marked + tally.memory + tally.passing;
  const parts = [
    tally.marked && `${tally.marked} must-pass`,
    tally.memory && `${tally.memory} regression memory`,
    tally.passing &&
      `${tally.passing} pass every repeat${tally.latestName ? ` in ${tally.latestName}` : " today"}`,
  ].filter(Boolean);
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1}
      flexWrap="wrap"
      rowGap={0.25}
      sx={{ px: 0.25, mb: 1.25 }}
    >
      <Iconify
        icon="solar:shield-check-linear"
        width={16}
        sx={{ color: "text.tertiary", flexShrink: 0 }}
      />
      <Typography sx={{ typography: "s2", color: "text.secondary", ...num }}>
        <Box
          component="span"
          sx={{ color: "text.primary", fontWeight: 600 }}
        >{`Improvements can't break ${held} of ${tally.total}`}</Box>
        {parts.length > 0 && ` — ${parts.join(" · ")}`}
      </Typography>
      {tally.open > 0 && (
        <Tooltip
          arrow
          title="Not reliably passing yet — mark the ones that must never break as must-pass."
        >
          <Typography
            sx={{
              typography: "s2",
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
    </Stack>
  );
}
ProtectionLine.propTypes = { tally: PropTypes.object };
