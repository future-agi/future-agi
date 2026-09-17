import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";

import Iconify from "src/components/iconify";

import { AGENT_SHAPE, AGENT_TYPE_SHAPE } from "./agents.shapes";
import { AGENT_CARD_COPY, MOCK_CREDENTIALS } from "./agentCards.constants";
import { DetailBlock } from "./agentPrimitives";
import VersionsBlock from "./VersionsBlock";
import { connectionRowsFor, sourceRowsFor } from "./agentVersion.helpers";

// The card body when expanded. Same layout for source and non-source so there's
// only one styling to learn: connection block on the left, source-location on
// the right, versions timeline beneath, and — for the source — the issued env
// credentials. The Contract tab is one click away, so the tools/rules summary
// that used to sit here was dropped as noise.
export default function AgentDetail({ agent, type, onAddVersion, onSetActiveVersion }) {
  const values = agent.values || {};
  const connectionRows = connectionRowsFor(agent, type, values);
  const sourceRows = sourceRowsFor(agent);
  const showToken = agent.isSource;
  const versions = agent.versions || [];

  return (
    <Box sx={{ p: 2.5 }}>
      <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" } }}>
        <DetailBlock title={AGENT_CARD_COPY.connection} icon="solar:link-round-linear" rows={connectionRows} />
        <DetailBlock title={AGENT_CARD_COPY.source} icon="solar:code-square-linear" rows={sourceRows} />
      </Box>

      <Box sx={{ mt: 2 }}>
        <VersionsBlock
          versions={versions}
          activeVersionId={agent.activeVersionId}
          onAddVersion={onAddVersion}
          onSetActiveVersion={onSetActiveVersion}
        />
      </Box>

      {showToken && (
        <Box sx={{ mt: 2 }}>
          <DetailBlock
            title={AGENT_CARD_COPY.issuedCredentials}
            icon="solar:key-linear"
            rows={[
              { label: AGENT_CARD_COPY.testPhoneLabel, value: MOCK_CREDENTIALS.testPhoneNumber, copy: true },
              { label: AGENT_CARD_COPY.envTokenLabel, value: MOCK_CREDENTIALS.environmentToken, copy: true, mono: true },
            ]}
            subtitle={AGENT_CARD_COPY.credentialsSubtitle}
          />
        </Box>
      )}

      {!agent.isSource && (
        <Stack direction="row" spacing={1} alignItems="center"
          sx={{
            mt: 2, px: 1.5, py: 1.25, borderRadius: 1.25,
            bgcolor: "background.neutral",
            border: "1px solid", borderColor: "divider",
          }}
        >
          <Iconify icon="solar:info-circle-linear" width={13} sx={{ color: "text.subtitle", flexShrink: 0 }} />
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            {AGENT_CARD_COPY.additionalTested}
          </Typography>
        </Stack>
      )}
    </Box>
  );
}
AgentDetail.propTypes = {
  agent: AGENT_SHAPE, type: AGENT_TYPE_SHAPE,
  onAddVersion: PropTypes.func, onSetActiveVersion: PropTypes.func,
};
