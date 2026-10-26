import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { RouterLink } from "src/routes/components";
import { BUILD_TONES } from "../../buildEnvironment/buildTones";
import { VOICE_CHAT_PROVIDERS } from "src/sections/agents/constants";
import { AGENT_SUMMARY_COPY, CONNECTOR_LABEL } from "./overview.constants";
import { CONNECTED_AGENTS_PATH } from "src/sections/agent-playground/agentsTabs";

// The agent definition's provider, named the way the Agents pages name it;
// harness connector names cover the rest.
const providerLabel = (provider) =>
  VOICE_CHAT_PROVIDERS.find((p) => p.value === provider)?.label ||
  CONNECTOR_LABEL[provider] ||
  provider;

export default function AgentSummarySection({ subject }) {
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
        p: 2,
        borderRadius: 1.5,
        border: "1px solid",
        borderColor: "divider",
      }}
    >
      <Stack direction="row" spacing={2} alignItems="center">
        <Box
          sx={{
            width: 34,
            height: 34,
            borderRadius: 1,
            display: "grid",
            placeItems: "center",
            bgcolor: (t) =>
              alpha(
                t.palette.text.primary,
                t.palette.mode === "dark" ? 0.06 : 0.04,
              ),
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
                fontWeight: "fontWeightBold",
                textTransform: "uppercase",
                letterSpacing: 0.4,
              }}
            >
              {AGENT_SUMMARY_COPY.label}
            </Typography>
            {subject && (
              <Box
                sx={{
                  height: 18,
                  px: 0.75,
                  borderRadius: 0.75,
                  display: "inline-flex",
                  alignItems: "center",
                  bgcolor: (t) =>
                    alpha(
                      BUILD_TONES.green,
                      t.palette.mode === "dark" ? 0.16 : 0.1,
                    ),
                  color: BUILD_TONES.green,
                }}
              >
                <Typography
                  sx={{ typography: "s3", fontWeight: "fontWeightBold" }}
                >
                  {AGENT_SUMMARY_COPY.connected}
                </Typography>
              </Box>
            )}
          </Stack>
          <Typography
            sx={{ typography: "s1", fontWeight: "fontWeightBold", mt: 0.25 }}
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
};
