import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";

import { BUILD_TONES } from "../buildTones";

// One entry in the read-audit metadata strip: a coloured dot, the count and its
// label. Ported verbatim from the designer's AgentReadReceipt.jsx StatChip, with
// the tone hexes lifted into BUILD_TONES.
export default function StatChip({ label, value, tone = "neutral" }) {
  const color = tone === "red" ? BUILD_TONES.red : tone === "amber" ? BUILD_TONES.amber : null;
  return (
    <Stack direction="row" alignItems="center" spacing={0.875}>
      <Box sx={{ width: 7, height: 7, borderRadius: "50%", flexShrink: 0, bgcolor: color || "text.disabled" }} />
      <Typography sx={{ typography: "s2", fontWeight: "fontWeightBold", color: color || "text.primary", fontVariantNumeric: "tabular-nums" }}>
        {value}
      </Typography>
      <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{label}</Typography>
    </Stack>
  );
}
StatChip.propTypes = {
  label: PropTypes.string,
  value: PropTypes.node,
  tone: PropTypes.oneOf(["neutral", "amber", "red"]),
};
