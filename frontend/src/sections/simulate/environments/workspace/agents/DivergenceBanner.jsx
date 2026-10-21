import PropTypes from "prop-types";
import { Box, Stack, Typography, Button } from "@mui/material";

import Iconify from "src/components/iconify";

import { SOURCE_ACCENT, AGENT_CARD_COPY, DIVERGENCE_COPY } from "./agentCards.constants";

// Purely presentational — it carries no show-condition. The panel decides when
// to mount it: under the single-source model the divergence to flag is that the
// active version is no longer the latest one (a rollback), so the panel gates on
// `activeVersion.id !== latestVersion.id` and passes the active label here.
// Deliberately neutral: a heads-up that runs and the current derivation have
// different provenance, plus one-click paths to either revert or commit.
export default function DivergenceBanner({ activeLabel, onPromote, onRestore }) {
  return (
    <Stack
      direction={{ xs: "column", sm: "row" }}
      alignItems={{ sm: "center" }} spacing={1.25}
      sx={{
        mb: 2, px: 1.75, py: 1.25, borderRadius: 1.25,
        bgcolor: "background.neutral",
        border: "1px solid", borderColor: "divider",
      }}
    >
      <Iconify icon="solar:info-circle-linear" width={15} sx={{ color: "text.subtitle", flexShrink: 0 }} />
      <Box flex={1} minWidth={0}>
        <Typography sx={{ typography: "s2", color: "text.primary" }}>
          {DIVERGENCE_COPY.lead}{" "}
          <Box component="span" sx={{ fontFamily: "ui-monospace, Menlo, monospace", fontWeight: "fontWeightBold" }}>
            {activeLabel}
          </Box>
          {DIVERGENCE_COPY.trail}
        </Typography>
      </Box>
      <Stack direction="row" spacing={0.5} sx={{ flexShrink: 0 }}>
        <Button
          size="small" onClick={onRestore}
          sx={{ typography: "s3", fontWeight: "fontWeightSemiBold", color: "text.subtitle" }}
        >
          {AGENT_CARD_COPY.restoreToSource}
        </Button>
        <Button
          size="small" onClick={onPromote}
          sx={{ typography: "s3", fontWeight: "fontWeightBold", color: SOURCE_ACCENT }}
        >
          {AGENT_CARD_COPY.promoteToSource}
        </Button>
      </Stack>
    </Stack>
  );
}
DivergenceBanner.propTypes = {
  activeLabel: PropTypes.string, onPromote: PropTypes.func, onRestore: PropTypes.func,
};
