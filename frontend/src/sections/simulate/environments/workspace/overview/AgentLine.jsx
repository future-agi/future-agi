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
import {
  isVersioningDisabled,
  useEnvironmentVersions,
} from "src/api/simulate-environments/versions";
import UiOnlyFlag from "../../components/UiOnlyFlag";
import { RUN_MODE_CHIP, VERSIONS_COPY } from "../workspace.constants";
import AgentSyncDialog from "../agent/AgentSyncDialog";
import { AGENT_SUMMARY_COPY, CONNECTOR_LABEL } from "./overview.constants";

const providerLabel = (provider) =>
  VOICE_CHAT_PROVIDERS.find((p) => p.value === provider)?.label ||
  CONNECTOR_LABEL[provider] ||
  provider;

// " · default v2": the environment's default agent version, once the versions
// have answered. Mounted only while versioning is on.
function DefaultAgentVersion({ envId }) {
  const { data, error } = useEnvironmentVersions(envId);
  const agent = isVersioningDisabled(error) ? null : data?.defaults?.agent;
  if (!agent) return null;
  return (
    <>
      {" · default "}
      <Box component="span" sx={{ color: "text.primary", fontWeight: 600 }}>
        {agent.label || VERSIONS_COPY.unlabelled}
      </Box>
    </>
  );
}

DefaultAgentVersion.propTypes = { envId: PropTypes.string };

// Vel's agent line: "Tests <name> <version> · <provider> · Live · default
// <version>" with View agent and a menu. Name, version and provider are real.
// While versioning is on, the default agent version is the environment's real
// default, and Manage versions opens the agent versions (where the default
// moves and a changed source becomes the next version), so Sync agent opens
// them too. With versioning off there is no default to state, and Change
// agent and Sync agent have no API (UI only).
export default function AgentLine({
  subject,
  agentMode,
  envId,
  versioningEnabled = false,
  onChangeAgent,
  onManageVersions,
}) {
  const [menu, setMenu] = useState(null);
  const [syncOpen, setSyncOpen] = useState(false);
  const live = RUN_MODE_CHIP[agentMode]?.label;
  const source = subject.provider ? providerLabel(subject.provider) : null;
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
        {versioningEnabled && <DefaultAgentVersion envId={envId} />}
      </Typography>
      <Box flex={1} />
      {onManageVersions && (
        <Button
          size="small"
          variant="outlined"
          onClick={onManageVersions}
          sx={{ color: "text.primary", borderColor: "divider", flexShrink: 0 }}
        >
          {AGENT_SUMMARY_COPY.manage}
        </Button>
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
            if (onManageVersions) onManageVersions();
            else setSyncOpen(true);
          }}
          sx={{ gap: 1 }}
        >
          <Iconify icon="solar:refresh-linear" width={16} />
          <Typography sx={{ typography: "s2" }}>Sync agent</Typography>
          {!onManageVersions && <UiOnlyFlag feature="syncAgent" />}
        </MenuItem>
      </Menu>
      <AgentSyncDialog
        open={syncOpen}
        onClose={() => setSyncOpen(false)}
        name={subject.name}
        sourceLabel={source}
        version={subject.active_version}
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
  // Whether the environment has versioning on, so its default agent shows.
  versioningEnabled: PropTypes.bool,
  // Opens the Change agent drawer; the menu item shows only with it.
  onChangeAgent: PropTypes.func,
  // Opens the agent versions; Manage versions shows only with it.
  onManageVersions: PropTypes.func,
};
