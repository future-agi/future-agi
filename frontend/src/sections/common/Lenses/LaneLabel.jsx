// A lens lane's label: the run's colour dot (same colour = same run), its
// label and name, `Baseline` on the baseline lane, and an optional second
// line. Shape differs from colour alone: the baseline also carries a word.
import React from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import Truncate from "src/sections/common/ux/Truncate";

export default function LaneLabel({ lane, sub, dense = false }) {
  const [code, ...rest] = String(lane.label ?? "").split(" ");
  const name = rest.join(" ");
  return (
    <Stack sx={{ minWidth: 0 }} data-lane-label={lane.runId}>
      <Stack direction="row" alignItems="center" gap={0.75} minWidth={0}>
        <Box
          aria-hidden
          sx={{
            width: 8,
            height: 8,
            borderRadius: "50%",
            bgcolor: lane.color,
            flexShrink: 0,
          }}
        />
        <Typography
          typography={dense ? "s2" : "s1"}
          fontWeight="fontWeightSemiBold"
          sx={{ flexShrink: 0, fontVariantNumeric: "tabular-nums" }}
        >
          {code}
        </Typography>
        {name ? (
          <Box sx={{ minWidth: 0, flex: "0 1 auto" }}>
            <Truncate
              text={name}
              sx={{ typography: dense ? "s2" : "s1", color: "text.primary" }}
            />
          </Box>
        ) : null}
        {lane.isBaseline ? (
          <Typography
            typography="s3"
            color="text.secondary"
            sx={{
              flexShrink: 0,
              px: 0.5,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 0.5,
            }}
          >
            Baseline
          </Typography>
        ) : null}
      </Stack>
      {sub ? (
        <Typography
          typography="s3"
          color="text.secondary"
          noWrap
          sx={{ pl: 1.75 }}
        >
          {sub}
        </Typography>
      ) : null}
    </Stack>
  );
}

LaneLabel.propTypes = {
  lane: PropTypes.shape({
    runId: PropTypes.string,
    label: PropTypes.string,
    color: PropTypes.string,
    isBaseline: PropTypes.bool,
  }).isRequired,
  sub: PropTypes.string,
  dense: PropTypes.bool,
};
