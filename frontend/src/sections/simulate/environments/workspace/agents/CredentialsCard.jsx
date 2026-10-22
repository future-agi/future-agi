import { Box } from "@mui/material";

import { AGENT_CARD_COPY, MOCK_CREDENTIALS } from "./agentCards.constants";
import { DetailBlock } from "./agentPrimitives";

// Test phone + env token, isolated in their own card so they read as what they
// are — environment credentials, not properties of the active agent. Same shape
// the DetailBlock uses so it slots cleanly under the hero.
export default function CredentialsCard() {
  return (
    <Box sx={{
      border: "1px solid", borderColor: "divider",
      borderRadius: 1.5, overflow: "hidden",
      bgcolor: "background.paper", p: 2.5,
    }}>
      <DetailBlock
        title={AGENT_CARD_COPY.environmentCredentials}
        icon="solar:key-linear"
        rows={[
          { label: AGENT_CARD_COPY.testPhoneLabel, value: MOCK_CREDENTIALS.testPhoneNumber, copy: true },
          { label: AGENT_CARD_COPY.envTokenLabel, value: MOCK_CREDENTIALS.environmentToken, copy: true, mono: true },
        ]}
        subtitle={AGENT_CARD_COPY.credentialsSubtitle}
      />
    </Box>
  );
}
