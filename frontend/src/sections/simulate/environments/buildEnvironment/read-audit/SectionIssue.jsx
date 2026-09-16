import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Button, Stack, Typography } from "@mui/material";

import Iconify from "src/components/iconify";

import { BUILD_TONES } from "../buildTones";

// The inline "why this dimension is empty" block: an amber-tinted card with the
// gap message, an optional hint and an optional per-section retry. Ported
// verbatim from the designer's AgentReadReceipt.jsx SectionIssue, amber hex from
// BUILD_TONES.
export default function SectionIssue({ issue, onRetry }) {
  if (!issue) return null;
  return (
    <Stack
      direction="row" alignItems="flex-start" spacing={1.25}
      sx={{
        mt: 1, px: 1.5, py: 1.25,
        borderRadius: 1,
        border: "1px solid",
        borderColor: (t) => alpha(BUILD_TONES.amber, t.palette.mode === "dark" ? 0.35 : 0.3),
        bgcolor: (t) => alpha(BUILD_TONES.amber, t.palette.mode === "dark" ? 0.08 : 0.05),
      }}
    >
      <Iconify icon={issue.icon || "solar:danger-triangle-linear"} width={15} sx={{ color: BUILD_TONES.amber, flexShrink: 0, mt: "2px" }} />
      <Box flex={1} minWidth={0}>
        <Typography sx={{ typography: "s2", fontWeight: "fontWeightBold", color: "text.primary" }}>
          {issue.message}
        </Typography>
        {issue.hint && (
          <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}>
            {issue.hint}
          </Typography>
        )}
      </Box>
      {onRetry && issue.retryLabel && (
        <Button
          size="small" variant="outlined"
          onClick={onRetry}
          startIcon={<Iconify icon="solar:refresh-linear" width={13} />}
          sx={{
            typography: "s3", fontWeight: "fontWeightBold",
            color: "text.primary", borderColor: "divider",
            "&:hover": { borderColor: "text.primary", bgcolor: "transparent" },
          }}
        >
          {issue.retryLabel}
        </Button>
      )}
    </Stack>
  );
}
SectionIssue.propTypes = {
  issue: PropTypes.shape({
    severity: PropTypes.string,
    icon: PropTypes.string,
    message: PropTypes.string,
    hint: PropTypes.string,
    retryLabel: PropTypes.string,
  }),
  onRetry: PropTypes.func,
};
