import PropTypes from "prop-types";
import { alpha, keyframes } from "@mui/material/styles";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";

import { BUILD_TONES } from "../buildTones";

const chipLand = keyframes`
  0%   { opacity: 0; transform: translateY(6px) scale(0.9); }
  60%  { opacity: 1; transform: translateY(-1px) scale(1.04); }
  100% { opacity: 1; transform: translateY(0)  scale(1); }
`;

// The tokens the derivation engine "discovers" — a fixed, ordered set the emit
// loop replays so the sandbox fills up in a believable rhythm.
export const TOKENS = [
  { kind: "tool", icon: "solar:code-scan-bold", label: "verify_identity" },
  { kind: "rule", icon: "solar:shield-check-bold", label: "return-window rule" },
  { kind: "tool", icon: "solar:code-scan-bold", label: "lookup_order" },
  { kind: "data", icon: "solar:database-bold", label: "customers × 240" },
  { kind: "tool", icon: "solar:code-scan-bold", label: "issue_refund" },
  { kind: "rule", icon: "solar:shield-check-bold", label: "OTP read-aloud rule" },
  { kind: "data", icon: "solar:database-bold", label: "orders × 610" },
  { kind: "tool", icon: "solar:code-scan-bold", label: "escalate_to_human" },
  { kind: "rule", icon: "solar:shield-check-bold", label: "goodwill cap" },
  { kind: "data", icon: "solar:database-bold", label: "payments × 480" },
  { kind: "tool", icon: "solar:code-scan-bold", label: "send_replacement" },
  { kind: "tool", icon: "solar:code-scan-bold", label: "get_refund_quote" },
];

/*
  Three semantic colours — teal for tools, green for rules, amber for data.
  The neutral single-tone version read as too muted; at a glance the sandbox
  filling up with distinct colours signals "different kinds of things
  arriving", which is the story worth telling here.
*/
export const KIND_COLOR = {
  tool: BUILD_TONES.teal,
  rule: BUILD_TONES.green,
  data: BUILD_TONES.amberDeep,
};

export function LandedChip({ item, dark }) {
  return (
    <Stack
      direction="row" alignItems="center" spacing={0.5}
      sx={{
        px: 0.75, py: 0.375, borderRadius: 0.75,
        border: "1px solid",
        borderColor: alpha(KIND_COLOR[item.kind], 0.35),
        bgcolor: alpha(KIND_COLOR[item.kind], dark ? 0.14 : 0.08),
        animation: `${chipLand} 0.35s cubic-bezier(0.2, 0.9, 0.2, 1.2) forwards`,
      }}
    >
      <Iconify icon={item.icon} width={10} sx={{ color: KIND_COLOR[item.kind] }} />
      <Typography sx={{ typography: "s3", fontSize: 10, fontWeight: "fontWeightSemiBold", color: "text.primary" }}>
        {item.label}
      </Typography>
    </Stack>
  );
}

LandedChip.propTypes = {
  item: PropTypes.shape({
    kind: PropTypes.string,
    icon: PropTypes.string,
    label: PropTypes.string,
  }),
  dark: PropTypes.bool,
};

export function MiniCount({ color, label }) {
  return (
    <Stack direction="row" alignItems="center" spacing={0.5}>
      <Box sx={{ width: 6, height: 6, borderRadius: "50%", bgcolor: color }} />
      <Typography sx={{ typography: "s3", fontSize: 10, color: "text.subtitle", fontVariantNumeric: "tabular-nums" }}>
        {label}
      </Typography>
    </Stack>
  );
}

MiniCount.propTypes = { color: PropTypes.string, label: PropTypes.string };
