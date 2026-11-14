import PropTypes from "prop-types";
import { keyframes, useTheme } from "@mui/material/styles";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";

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
  Three kinds, three series colours — tools, rules and data each take a colour
  from the data palette. The chip itself is a neutral tag; only the icon and the
  legend dot carry the kind, so a filling sandbox still reads as "different
  kinds of things arriving" without tinted pills.
*/
const KIND_SERIES = { tool: 2, rule: 1, data: 3 };

export const kindColor = (theme, kind) => theme.palette.data[KIND_SERIES[kind]];

export function LandedChip({ item }) {
  const theme = useTheme();
  return (
    <Stack
      direction="row" alignItems="center" spacing={0.5}
      sx={{
        px: 0.75, py: 0.25, borderRadius: "4px",
        bgcolor: "action.selected",
        animation: `${chipLand} 0.35s cubic-bezier(0.2, 0.9, 0.2, 1.2) forwards`,
      }}
    >
      <Iconify icon={item.icon} width={12} sx={{ color: kindColor(theme, item.kind) }} />
      <Typography sx={{ typography: "s3", fontWeight: 500, color: "text.secondary" }}>
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
};

export function MiniCount({ color, label }) {
  return (
    <Stack direction="row" alignItems="center" spacing={0.5}>
      <Box sx={{ width: 6, height: 6, borderRadius: "50%", bgcolor: color }} />
      <Typography sx={{ typography: "s3", color: "text.secondary", fontVariantNumeric: "tabular-nums" }}>
        {label}
      </Typography>
    </Stack>
  );
}

MiniCount.propTypes = { color: PropTypes.string, label: PropTypes.string };
