import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Button,
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
import useUiSample from "../../hooks/useUiSample";
import { RUN_MODE_CHIP } from "../workspace.constants";
import AgentSyncDialog from "../agent/AgentSyncDialog";
import SwitchVersionDialog from "../agent/SwitchVersionDialog";
import { CONNECTOR_LABEL } from "./overview.constants";

const providerLabel = (provider) =>
  VOICE_CHAT_PROVIDERS.find((p) => p.value === provider)?.label ||
  CONNECTOR_LABEL[provider] ||
  provider;

// The newest version when the agent has one past the active version:
// "v3" for versions_count 3 and active_version "v2".
const newerVersion = ({ active_version: active, versions_count: count }) => {
  const n = Number(/^v(\d+)$/.exec(active || "")?.[1]);
  return n && count > n ? `v${count}` : null;
};

// Vel's agent line: "Tests <name> <version> · <provider> · Live · default:
// production" with Try vN, View agent and a menu. Name, version and provider
// are real; Try vN shows only with ?uiSample=agentversion. Which version
// is the environment's default isn't in the API (soon chip), so Default to
// production shows only with ?uiSample=agentversion. Changing the default,
// Change agent and Sync agent have no API (UI only).
export default function AgentLine({
  subject,
  agentMode,
  envId,
  envName,
  onChangeAgent,
}) {
  const [menu, setMenu] = useState(null);
  const [syncOpen, setSyncOpen] = useState(false);
  const [switchOpen, setSwitchOpen] = useState(false);
  // { label, follows } for the switch dialog; kept while it closes.
  const [switchTo, setSwitchTo] = useState(null);
  const openSwitch = (to) => {
    setSwitchTo(to);
    setSwitchOpen(true);
  };
  const sampleDefault = useUiSample("agentversion");
  const live = RUN_MODE_CHIP[agentMode]?.label;
  const source = subject.provider ? providerLabel(subject.provider) : null;
  // Version numbers can have gaps or drafts, so a count can't name a real
  // newer version: Try vN is sample only until the API says which is newer.
  const newer = sampleDefault ? newerVersion(subject) : null;
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
        {[source, live]
          .filter(Boolean)
          .map((x) => ` · ${x}`)
          .join("")}
        {" · "}
      </Typography>
      <SoonChip
        label="Default" detail="Which agent version this environment runs by default when you start a run."
        what="Which agent version this environment tests by default"
        size="tiny"
      />
      <Box flex={1} />
      {newer && (
        <Button
          size="small"
          variant="outlined"
          onClick={() => openSwitch({ label: newer, follows: false })}
          sx={{ color: "text.primary", borderColor: "divider", flexShrink: 0 }}
        >
          {`Try ${newer}`}
        </Button>
      )}
      {sampleDefault && subject.active_version && (
        <>
          <UiOnlyFlag feature="agentVersionDefault" />
          <Button
            size="small"
            variant="outlined"
            onClick={() =>
              openSwitch({ label: subject.active_version, follows: true })
            }
            sx={{
              color: "text.primary",
              borderColor: "divider",
              flexShrink: 0,
            }}
          >
            Default to production
          </Button>
        </>
      )}
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
        {onChangeAgent && (
          <MenuItem
            onClick={() => {
              setMenu(null);
              onChangeAgent();
            }}
            sx={{ gap: 1 }}
          >
            <Iconify icon="solar:transfer-horizontal-linear" width={16} />
            <Typography sx={{ typography: "s2" }}>Change agent</Typography>
            <UiOnlyFlag feature="connectAgent" />
          </MenuItem>
        )}
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
      <AgentSyncDialog
        open={syncOpen}
        onClose={() => setSyncOpen(false)}
        name={subject.name}
        sourceLabel={source}
        version={subject.active_version}
      />
      <SwitchVersionDialog
        open={switchOpen}
        onClose={() => setSwitchOpen(false)}
        envName={envName}
        label={switchTo?.label}
        follows={switchTo?.follows}
      />
    </Stack>
  );
}

AgentLine.propTypes = {
  subject: PropTypes.shape({
    id: PropTypes.string,
    name: PropTypes.string,
    provider: PropTypes.string,
    active_version: PropTypes.string,
    versions_count: PropTypes.number,
  }).isRequired,
  agentMode: PropTypes.string,
  envId: PropTypes.string,
  envName: PropTypes.string,
  // Opens the Change agent drawer; the menu item shows only with it.
  onChangeAgent: PropTypes.func,
};
