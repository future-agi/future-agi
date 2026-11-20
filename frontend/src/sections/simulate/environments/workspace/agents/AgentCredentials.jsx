import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Button, Stack, Tooltip, Typography } from "@mui/material";
import { checkRunning } from "src/api/simulate-environments/versions";
import { VersionBadge } from "../EnvVersionPin";
import { AGENT_VERSIONS_COPY } from "../workspace.constants";
import { PreflightBadge, PreflightReport } from "./PreflightStatus";
import { activationText, missingRequired, named } from "./agentVersionStatus";
import CredentialsForm from "./CredentialsForm";

const COPY = AGENT_VERSIONS_COPY.credentials;
const MONO = "ui-monospace, Menlo, monospace";

// The default version's credential names (values are never shown) and a form
// that saves new values as the agent's next version, then follows its check.
export default function AgentCredentials({ envId, defaultVersion, versions }) {
  const [open, setOpen] = useState(false);
  const [savedId, setSavedId] = useState(null);

  const names = defaultVersion?.credentialNames || [];
  const requiredMissing = missingRequired(defaultVersion);
  const saved = savedId && versions.find((one) => one.id === savedId);
  // Only this environment's own changes hold it back.
  const checking = versions.some(checkRunning);

  return (
    <Box
      data-testid="agent-credentials"
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        p: 2,
      }}
    >
      <Stack direction="row" alignItems="flex-start" spacing={1}>
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s1", fontWeight: 500 }}>
            {COPY.title}
          </Typography>
          {defaultVersion && (
            <Typography
              sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}
            >
              {COPY.subtitle(named(defaultVersion))}
            </Typography>
          )}
        </Box>
        {defaultVersion && !open && (
          <Tooltip arrow title={checking ? COPY.checkRunning : ""}>
            <Box
              component="span"
              sx={{ display: "inline-flex", flexShrink: 0 }}
            >
              <Button
                size="small"
                variant="outlined"
                color="inherit"
                disabled={checking}
                onClick={() => setOpen(true)}
              >
                {COPY.change}
              </Button>
            </Box>
          </Tooltip>
        )}
      </Stack>

      {!defaultVersion && (
        <Typography sx={{ typography: "s2", color: "text.secondary", mt: 1 }}>
          {COPY.noDefault}
        </Typography>
      )}
      {defaultVersion && !names.length && !requiredMissing.length && (
        <Typography sx={{ typography: "s2", color: "text.secondary", mt: 1 }}>
          {COPY.none}
        </Typography>
      )}
      {(names.length > 0 || requiredMissing.length > 0) && (
        <Stack direction="row" flexWrap="wrap" gap={0.75} sx={{ mt: 1 }}>
          {names.map((name) => (
            <Typography
              key={name}
              data-testid="credential-name"
              sx={{ typography: "s3", fontFamily: MONO }}
            >
              {name}
            </Typography>
          ))}
          {requiredMissing.map((name) => (
            <Stack key={name} direction="row" spacing={0.5} alignItems="center">
              <Typography
                data-testid="credential-name"
                sx={{ typography: "s3", fontFamily: MONO, color: "status.danger.fg" }}
              >
                {name}
              </Typography>
              <VersionBadge label={COPY.requiredMissing} />
            </Stack>
          ))}
        </Stack>
      )}

      {saved && !open && (
        <Box sx={{ mt: 1.5 }} data-testid="credentials-saved">
          <Stack direction="row" spacing={0.75} alignItems="center">
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              {COPY.saved(named(saved))}
            </Typography>
            <PreflightBadge status={saved.preflightStatus} />
          </Stack>
          {saved.preflightStatus === "failed" && (
            <PreflightReport report={saved.preflightReport} />
          )}
          {activationText(saved) && (
            <Typography
              sx={{ typography: "s3", color: "status.warning.fg", mt: 0.5 }}
            >
              {activationText(saved)}
            </Typography>
          )}
        </Box>
      )}

      {open && (
        <Box sx={{ mt: 2 }}>
          <CredentialsForm
            envId={envId}
            version={defaultVersion}
            onSaved={(data) => {
              setSavedId(data?.agent_version?.id || null);
              setOpen(false);
            }}
            onCancel={() => setOpen(false)}
          />
        </Box>
      )}
    </Box>
  );
}

AgentCredentials.propTypes = {
  envId: PropTypes.string.isRequired,
  // The agent's default version (versionFromApi); none before the first.
  defaultVersion: PropTypes.object,
  // Every agent version, to follow the one a save made.
  versions: PropTypes.arrayOf(PropTypes.object).isRequired,
};
