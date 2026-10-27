import PropTypes from "prop-types";
import { useState } from "react";
import { enqueueSnackbar } from "notistack";
import {
  Box,
  Button,
  Dialog,
  IconButton,
  Menu,
  MenuItem,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { RouterLink } from "src/routes/components";
import { VOICE_CHAT_PROVIDERS } from "src/sections/agents/constants";
import { CONNECTED_AGENTS_PATH } from "src/sections/agent-playground/agentsTabs";
import SoonChip from "../../components/SoonChip";
import UiOnlyFlag from "../../components/UiOnlyFlag";
import { RUN_MODE_CHIP } from "../workspace.constants";
import { UI_ONLY_SAVED } from "../gaps/NeedsInputButton";
import { CONNECTOR_LABEL } from "./overview.constants";

const providerLabel = (provider) =>
  VOICE_CHAT_PROVIDERS.find((p) => p.value === provider)?.label ||
  CONNECTOR_LABEL[provider] ||
  provider;

// Vel's agent line: "Tests <name> <version> · <provider> · Live · default:
// production" with View agent and a menu. Name, version and provider are real;
// which version is the environment's default isn't in the API (soon chip), and
// Sync agent has no API (UI only).
export default function AgentLine({ subject, agentMode, envId }) {
  const [menu, setMenu] = useState(null);
  const [syncOpen, setSyncOpen] = useState(false);
  const live = RUN_MODE_CHIP[agentMode]?.label;
  const strong = (t) => (
    <Box component="span" sx={{ color: "text.primary", fontWeight: 600 }}>
      {t}
    </Box>
  );
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1.25}
      sx={{
        my: 2,
        px: 2,
        py: 1.25,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1.5,
      }}
    >
      <Iconify
        icon="solar:cloud-linear"
        width={16}
        sx={{ color: "text.subtitle", flexShrink: 0 }}
      />
      <Typography
        noWrap
        sx={{ typography: "s2", color: "text.subtitle", minWidth: 0 }}
      >
        Tests{" "}
        {strong(
          `${subject.name || "Agent"} ${subject.active_version || ""}`.trim(),
        )}
        {[subject.provider && providerLabel(subject.provider), live]
          .filter(Boolean)
          .map((x) => ` · ${x}`)
          .join("")}
        {" · "}
      </Typography>
      <SoonChip
        label="Default"
        what="Which agent version this environment tests by default"
        size="tiny"
      />
      <Box flex={1} />
      <Button
        component={RouterLink}
        href={`${CONNECTED_AGENTS_PATH}/${subject.id}?tab=environments&env=${envId || ""}`}
        size="small"
        endIcon={<Iconify icon="solar:arrow-right-linear" width={14} />}
        sx={{ color: "text.primary", flexShrink: 0 }}
      >
        View agent
      </Button>
      <IconButton
        size="small"
        aria-label="Agent actions"
        onClick={(e) => setMenu(e.currentTarget)}
        sx={{ color: "text.subtitle" }}
      >
        <Iconify icon="solar:menu-dots-bold" width={18} />
      </IconButton>
      <Menu
        anchorEl={menu}
        open={!!menu}
        onClose={() => setMenu(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
      >
        <MenuItem
          onClick={() => {
            setMenu(null);
            setSyncOpen(true);
          }}
          sx={{ gap: 1 }}
        >
          <Iconify icon="solar:refresh-linear" width={16} />
          <Typography sx={{ typography: "s2" }}>Sync agent</Typography>
          <UiOnlyFlag feature="syncAgent" />
        </MenuItem>
      </Menu>
      <Dialog
        open={syncOpen}
        onClose={() => setSyncOpen(false)}
        PaperProps={{ sx: { width: 460, p: 2.5 } }}
      >
        <Stack spacing={1.25}>
          <Stack direction="row" alignItems="center" spacing={1}>
            <Typography
              sx={{ typography: "m3", fontWeight: 600 }}
            >{`Sync ${subject.name || "the agent"}`}</Typography>
            <UiOnlyFlag feature="syncAgent" />
          </Stack>
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            Reads the agent&apos;s source again and shows what changed since{" "}
            {subject.active_version || "this version"}. A change becomes the
            agent&apos;s next version; this environment keeps testing the one it
            tests now until you switch.
          </Typography>
          <Stack direction="row" spacing={1} justifyContent="flex-end">
            <Button
              size="small"
              onClick={() => setSyncOpen(false)}
              sx={{ color: "text.secondary" }}
            >
              Cancel
            </Button>
            <Button
              size="small"
              variant="contained"
              color="primary"
              onClick={() => {
                setSyncOpen(false);
                enqueueSnackbar(UI_ONLY_SAVED, { variant: "info" });
              }}
            >
              Sync now
            </Button>
          </Stack>
        </Stack>
      </Dialog>
    </Stack>
  );
}

AgentLine.propTypes = {
  subject: PropTypes.shape({
    id: PropTypes.string,
    name: PropTypes.string,
    provider: PropTypes.string,
    active_version: PropTypes.string,
  }).isRequired,
  agentMode: PropTypes.string,
  envId: PropTypes.string,
};
