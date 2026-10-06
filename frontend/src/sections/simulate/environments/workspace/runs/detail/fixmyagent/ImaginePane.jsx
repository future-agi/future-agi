import { Box, Stack } from "@mui/material";

import ComingSoonChip from "../../../../components/ComingSoonChip";
import EmptyState from "../../../../components/EmptyState";

/**
 * Imagine — GATED, coming soon.
 *
 * The designer's Imagine tab is an exploratory chat-to-widgets canvas driven
 * entirely by a local mock resolver (`resolvePrompt` over fabricated task data).
 * There is no backend for it — no analytics endpoint on the run answers freeform
 * questions — so rather than fabricate widgets, the tab is present but inert with
 * a coming-soon marker, keeping the drawer's shape while being honest about the
 * gap.
 */
export default function ImaginePane() {
  return (
    <Stack sx={{ flex: 1 }} alignItems="center" justifyContent="center">
      <EmptyState
        icon="solar:magic-stick-3-linear"
        title="Imagine"
        body="Ask freeform questions about this run and get charts back. This exploratory canvas isn't wired to a backend yet."
      />
      <Box sx={{ mt: -1.5 }}>
        <ComingSoonChip />
      </Box>
    </Stack>
  );
}
