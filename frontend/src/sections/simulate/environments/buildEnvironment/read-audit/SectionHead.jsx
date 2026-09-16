import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography } from "@mui/material";

import Iconify from "src/components/iconify";

// The slim head above a read-audit column (icon tile + title + count + subtitle).
// Ported verbatim from the designer's AgentReadReceipt.jsx SectionHead.
export default function SectionHead({ icon, title, subtitle, count, accent }) {
  return (
    <Stack
      direction="row" alignItems="center" spacing={1.25}
      sx={{ py: 0.75, borderBottom: "1px solid", borderColor: "divider", mb: 0.25 }}
    >
      <Box
        sx={{
          width: 20, height: 20, borderRadius: 0.5, display: "grid", placeItems: "center",
          bgcolor: (t) => alpha(accent || t.palette.text.primary, t.palette.mode === "dark" ? 0.14 : 0.08),
          color: accent || "text.secondary",
          flexShrink: 0,
        }}
      >
        <Iconify icon={icon} width={12} />
      </Box>
      <Stack direction="row" alignItems="baseline" spacing={0.75} sx={{ flex: 1, minWidth: 0 }}>
        <Typography sx={{ typography: "s2", fontWeight: "fontWeightBold" }}>{title}</Typography>
        {count != null && (
          <Typography sx={{ typography: "s3", color: "text.subtitle", fontVariantNumeric: "tabular-nums" }}>
            {count}
          </Typography>
        )}
        {subtitle && (
          <Typography noWrap sx={{ typography: "s3", color: "text.subtitle", ml: "auto !important" }}>
            {subtitle}
          </Typography>
        )}
      </Stack>
    </Stack>
  );
}
SectionHead.propTypes = {
  icon: PropTypes.string,
  title: PropTypes.string,
  subtitle: PropTypes.string,
  count: PropTypes.number,
  accent: PropTypes.string,
};
