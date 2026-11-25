import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { RouterLink } from "src/routes/components";
import StatusBadge from "src/components/status-badge/StatusBadge";
import { VOICE_CHAT_PROVIDERS } from "src/sections/agents/constants";
import { AGENT_SUMMARY_COPY, CONNECTOR_LABEL } from "./overview.constants";
import { CONNECTED_AGENTS_PATH } from "src/sections/agent-playground/agentsTabs";

// The agent definition's provider, named the way the Agents pages name it;
// harness connector names cover the rest.
const providerLabel = (provider) =>
  VOICE_CHAT_PROVIDERS.find((p) => p.value === provider)?.label ||
  CONNECTOR_LABEL[provider] ||
  provider;

// With `onManageVersions`, a "Manage versions" action opens the agent versions.
export default function AgentSummarySection({ subject, onManageVersions }) {
  const title = subject
    ? AGENT_SUMMARY_COPY.agentTitle(subject.name, subject.active_version)
    : AGENT_SUMMARY_COPY.noAgentTitle;
  const body = subject ? null : AGENT_SUMMARY_COPY.portableBody;
  // Where the agent runs and how many versions it has, from the agent
  // definition the environment tests.
  const facts = subject
    ? [
        subject.provider && providerLabel(subject.provider),
        subject.versions_count != null &&
          AGENT_SUMMARY_COPY.versions(subject.versions_count),
      ]
        .filter(Boolean)
        .join(" · ")
    : "";

  return (
    <Box
      sx={{
        my: 2,
        p: 1.5,
        borderRadius: "8px",
        border: "1px solid",
        borderColor: "divider",
      }}
    >
      <Stack direction="row" spacing={2} alignItems="center">
        <Box
          sx={{
            width: 28,
            height: 28,
            borderRadius: 1,
            display: "grid",
            placeItems: "center",
            bgcolor: "action.selected",
            color: "text.secondary",
            flexShrink: 0,
          }}
        >
          <Iconify icon="solar:cpu-bolt-linear" width={18} />
        </Box>
        <Box flex={1} minWidth={0}>
          <Stack direction="row" alignItems="center" spacing={1}>
            <Typography
              sx={{
                typography: "s3",
                color: "text.subtitle",
                fontWeight: "fontWeightMedium",
              }}
            >
              {AGENT_SUMMARY_COPY.label}
            </Typography>
            {subject && (
              <StatusBadge
                status="connected"
                label={AGENT_SUMMARY_COPY.connected}
                size="sm"
              />
            )}
          </Stack>
          <Typography
            sx={{ typography: "s1", fontWeight: "fontWeightMedium", mt: 0.25 }}
          >
            {title}
          </Typography>
          {facts && (
            <Typography
              sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}
            >
              {facts}
            </Typography>
          )}
          {body && (
            <Typography
              sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}
            >
              {body}
            </Typography>
          )}
        </Box>
        {subject?.id && (
          <Button
            component={RouterLink}
            href={`${CONNECTED_AGENTS_PATH}/${subject.id}`}
            size="small"
            variant="outlined"
            color="inherit"
            endIcon={<Iconify icon="solar:arrow-right-linear" width={14} />}
            sx={{ flexShrink: 0 }}
          >
            {AGENT_SUMMARY_COPY.viewAgent}
          </Button>
        )}
        {onManageVersions && (
          <Button
            variant="outlined"
            size="small"
            color="inherit"
            onClick={onManageVersions}
            sx={{ flexShrink: 0 }}
          >
            {AGENT_SUMMARY_COPY.manage}
          </Button>
        )}
      </Stack>
    </Box>
  );
}
AgentSummarySection.propTypes = {
  subject: PropTypes.shape({
    id: PropTypes.string,
    name: PropTypes.string,
    provider: PropTypes.string,
    versions_count: PropTypes.number,
    active_version: PropTypes.string,
  }),
  onManageVersions: PropTypes.func,
};
