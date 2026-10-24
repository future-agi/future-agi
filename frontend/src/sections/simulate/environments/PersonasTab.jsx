import { Box, Button } from "@mui/material";
import Iconify from "src/components/iconify";
import PersonaListView from "src/sections/persona/PersonaListView";
import { PERSONAS_DOCS_URL } from "src/sections/persona/common";

// Sized like Create persona beside it (PersonaListView) so the pair reads as
// one group; only the outline marks it as the secondary action.
const docsButton = (
  <Button
    variant="outlined"
    component="a"
    href={PERSONAS_DOCS_URL}
    target="_blank"
    rel="noopener noreferrer"
    startIcon={<Iconify icon="solar:document-text-linear" width={18} />}
    sx={{
      px: 2.5,
      typography: "body2",
      textTransform: "none",
      borderColor: "divider",
      color: "text.primary",
    }}
  >
    View Docs
  </Button>
);

/**
 * Same frame as MyEnvironmentsTab: a flex column that fills the remaining
 * height, so the persona table's pagination pins to the bottom of the tab.
 */
export default function PersonasTab() {
  return (
    <Box
      sx={{
        flex: 1,
        minHeight: 0,
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        px: 2,
        pt: 2,
        pb: 2,
      }}
    >
      <PersonaListView toolbarActions={docsButton} />
    </Box>
  );
}
