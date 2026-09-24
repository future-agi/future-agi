import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Button, Stack, Typography } from "@mui/material";

import Iconify from "src/components/iconify";

import { BUILD_TONES } from "../buildTones";
import { READ_AUDIT_COPY } from "../readAudit.constants";

// The slim amber strip between the top bar and the doc body when the reader
// completed with gaps. Ported verbatim from the designer's AgentReadReceipt.jsx
// ReaderStatusBand, amber hex from BUILD_TONES.
export default function ReaderStatusBand({ issueCount, issues, onRetryAll, busy }) {
  const list = Object.keys(issues || {}).join(", ");
  return (
    <Stack
      direction="row" alignItems="center" spacing={1.25}
      sx={{
        px: 3, py: 1, flexShrink: 0,
        borderBottom: "1px solid",
        borderColor: alpha(BUILD_TONES.amber, 0.3),
        bgcolor: (t) => alpha(BUILD_TONES.amber, t.palette.mode === "dark" ? 0.08 : 0.05),
      }}
    >
      <Iconify icon="solar:danger-triangle-bold" width={15} sx={{ color: BUILD_TONES.amber, flexShrink: 0 }} />
      <Box flex={1} minWidth={0}>
        <Typography sx={{ typography: "s2", fontWeight: "fontWeightBold", color: "text.primary" }}>
          Reader completed with {issueCount} {issueCount === 1 ? "gap" : "gaps"}
          <Box component="span" sx={{ color: "text.subtitle", fontWeight: "fontWeightMedium" }}>
            {" "}· {list} could not be read completely
          </Box>
        </Typography>
      </Box>
      {onRetryAll && (
        <Button
          size="small" variant="text"
          onClick={onRetryAll}
          disabled={busy}
          startIcon={<Iconify icon="solar:refresh-linear" width={13} />}
          sx={{ typography: "s3", fontWeight: "fontWeightBold", color: "text.primary" }}
        >
          {busy ? READ_AUDIT_COPY.retrying : READ_AUDIT_COPY.retry}
        </Button>
      )}
    </Stack>
  );
}
ReaderStatusBand.propTypes = {
  issueCount: PropTypes.number,
  issues: PropTypes.objectOf(PropTypes.shape({
    severity: PropTypes.string,
    icon: PropTypes.string,
    message: PropTypes.string,
    hint: PropTypes.string,
    retryLabel: PropTypes.string,
  })),
  onRetryAll: PropTypes.func,
  busy: PropTypes.bool,
};
