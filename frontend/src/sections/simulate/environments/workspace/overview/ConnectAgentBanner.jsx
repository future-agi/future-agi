import { Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { RouterLink } from "src/routes/components";
import { CONNECT_AGENT_PATH } from "src/sections/agent-playground/agentsTabs";

// Vel's no-agent banner on Overview. Attaching an agent to an existing
// environment has no API yet, so Connect agent opens the agent connect flow.
export default function ConnectAgentBanner() {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1.5}
      sx={{
        my: 2,
        px: 2,
        py: 1.5,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1.5,
        bgcolor: "background.neutral",
      }}
    >
      <Iconify
        icon="solar:plug-circle-linear"
        width={18}
        sx={{ color: "text.secondary", flexShrink: 0 }}
      />
      <Typography sx={{ typography: "s2", fontWeight: 600, flex: 1 }}>
        Connect your agent to start testing
      </Typography>
      <Button
        component={RouterLink}
        href={CONNECT_AGENT_PATH}
        size="small"
        variant="contained"
        color="primary"
      >
        Connect agent
      </Button>
    </Stack>
  );
}
