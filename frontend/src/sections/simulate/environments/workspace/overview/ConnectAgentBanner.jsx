import PropTypes from "prop-types";
import { Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";

// Vel's no-agent banner on Overview. Connect agent opens the Change agent
// drawer, as Vel's does; attaching the agent there is UI only.
export default function ConnectAgentBanner({ onConnect }) {
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
        onClick={onConnect}
        size="small"
        variant="contained"
        color="primary"
      >
        Connect agent
      </Button>
    </Stack>
  );
}

ConnectAgentBanner.propTypes = { onConnect: PropTypes.func.isRequired };
