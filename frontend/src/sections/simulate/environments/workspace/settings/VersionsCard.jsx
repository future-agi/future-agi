import PropTypes from "prop-types";
import { Box, Button, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { VERSIONS_COPY } from "../workspace.constants";
import SettingsCard from "./SettingsCard";

// Where this environment's versions live. While versioning is on, the
// Versions tab holds the history, what changed and which version runs use, so
// the card opens it. With versioning off it says so.
export default function VersionsCard({ enabled = false, onOpen }) {
  return (
    <SettingsCard
      label="Versions"
      title="Versions"
      subtitle="A version covers the world, scenarios, evaluations, caller and credentials. Every run pins one."
      action={
        enabled && onOpen ? (
          <Button
            size="small"
            variant="outlined"
            onClick={onOpen}
            startIcon={<Iconify icon="solar:history-linear" width={14} />}
            sx={{ color: "text.primary", borderColor: "divider" }}
          >
            Open versions
          </Button>
        ) : null
      }
    >
      {!enabled && (
        <Box sx={{ px: 2.5, py: 1.5 }}>
          <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
            {VERSIONS_COPY.disabled}
          </Typography>
        </Box>
      )}
    </SettingsCard>
  );
}
VersionsCard.propTypes = { enabled: PropTypes.bool, onOpen: PropTypes.func };
