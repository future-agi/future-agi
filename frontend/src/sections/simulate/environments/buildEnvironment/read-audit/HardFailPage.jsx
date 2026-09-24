import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Button, IconButton, Link, Stack, Typography } from "@mui/material";

import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";

import { BUILD_TONES } from "../buildTones";
import { READ_AUDIT_COPY } from "../readAudit.constants";

// Takes over the whole panel when the reader returned nothing: the agent ref,
// the failure reason, retry/change-source actions and a "continue with defaults"
// escape hatch. Ported verbatim from the designer's AgentReadReceipt.jsx
// HardFailPage — red hexes from BUILD_TONES, copy from READ_AUDIT_COPY, and the
// icon-only back button given an explicit tooltip + aria-label.
export default function HardFailPage({ agentRef, reason, onRetry, onChangeSource, onContinueWithDefaults, busy }) {
  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0, bgcolor: "background.paper" }}>
      <Stack
        direction="row" alignItems="center" spacing={2}
        sx={{ px: 3, py: 1.75, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}
      >
        <CustomTooltip show size="small" title={READ_AUDIT_COPY.back} arrow>
          <IconButton size="small" aria-label={READ_AUDIT_COPY.back} onClick={onChangeSource}>
            <Iconify icon="solar:alt-arrow-left-linear" width={17} />
          </IconButton>
        </CustomTooltip>
        <Typography sx={{ typography: "s2", color: "text.subtitle", fontFamily: "ui-monospace, Menlo, monospace" }}>
          {agentRef}
        </Typography>
      </Stack>

      <Stack alignItems="center" justifyContent="center" sx={{ flex: 1, px: 3, textAlign: "center" }}>
        <Box
          sx={{
            width: 56, height: 56, borderRadius: 2, display: "grid", placeItems: "center", mb: 2,
            bgcolor: (t) => alpha(BUILD_TONES.red, t.palette.mode === "dark" ? 0.18 : 0.1),
            color: BUILD_TONES.red,
          }}
        >
          <Iconify icon="solar:danger-triangle-bold" width={28} />
        </Box>
        <Typography sx={{ typography: "m2", fontWeight: "fontWeightBold" }}>
          {READ_AUDIT_COPY.hardfailTitle}
        </Typography>
        <Typography sx={{ typography: "s2", color: "text.secondary", mt: 1, maxWidth: 560 }}>
          {reason}
        </Typography>
        <Stack direction="row" spacing={1.5} sx={{ mt: 3 }}>
          <Button
            variant="contained" color="primary"
            onClick={onRetry}
            disabled={busy}
            startIcon={<Iconify icon="solar:refresh-linear" width={15} />}
            sx={{ typography: "s2", fontWeight: "fontWeightBold" }}
          >
            {busy ? READ_AUDIT_COPY.retrying : READ_AUDIT_COPY.retry}
          </Button>
          <Button
            variant="outlined"
            onClick={onChangeSource}
            sx={{ typography: "s2", fontWeight: "fontWeightBold", color: "text.primary", borderColor: "divider" }}
          >
            {READ_AUDIT_COPY.changeSource}
          </Button>
        </Stack>
        <Link
          component="button"
          onClick={onContinueWithDefaults}
          underline="hover"
          sx={{ typography: "s3", color: "text.subtitle", mt: 2, fontWeight: "fontWeightSemiBold" }}
        >
          {READ_AUDIT_COPY.continueDefaults}
        </Link>
      </Stack>
    </Box>
  );
}
HardFailPage.propTypes = {
  agentRef: PropTypes.string,
  reason: PropTypes.string,
  onRetry: PropTypes.func,
  onChangeSource: PropTypes.func,
  onContinueWithDefaults: PropTypes.func,
  busy: PropTypes.bool,
};
