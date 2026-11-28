import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { PreflightBadge, PreflightReport } from "../../agents/PreflightStatus";
import { activationText, named } from "../../agents/agentVersionStatus";
import { AGENT_VERSIONS_COPY } from "../../workspace.constants";

// The version new credential values were saved as, and how its check went.
export default function SavedCredentials({ version }) {
  return (
    <Box data-testid="credentials-saved">
      <Stack direction="row" spacing={0.75} alignItems="center">
        <Typography sx={{ typography: "s3", color: "text.secondary" }}>
          {AGENT_VERSIONS_COPY.credentials.saved(named(version))}
        </Typography>
        <PreflightBadge status={version.preflightStatus} />
      </Stack>
      {version.preflightStatus === "failed" && (
        <PreflightReport report={version.preflightReport} />
      )}
      {activationText(version) && (
        <Typography
          sx={{ typography: "s3", color: "status.warning.fg", mt: 0.5 }}
        >
          {activationText(version)}
        </Typography>
      )}
    </Box>
  );
}

SavedCredentials.propTypes = { version: PropTypes.object.isRequired };
