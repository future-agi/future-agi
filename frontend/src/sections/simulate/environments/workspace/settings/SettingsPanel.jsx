import PropTypes from "prop-types";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Box, Stack, Typography, Button, TextField } from "@mui/material";
import { harnessEnvironmentQuery } from "src/api/simulate-environments/environment";
import { useRenameEnvironment } from "src/api/simulate-environments/environments";
import {
  SAMPLE_EGRESS_DOMAINS,
  SAMPLE_ENV_VARS,
} from "src/api/simulate-environments/_fixtures/detail/settings";
import CopyField from "../../components/CopyField";
import useUiSample from "../../hooks/useUiSample";
import { validateEnvName, MAX_ENV_NAME } from "../renameEnvironment";
import BuildDetailsCard from "./BuildDetailsCard";
import DangerZone from "./DangerZone";
import EgressCard from "./EgressCard";
import EnvVarsCard, { CredentialFilesCard } from "./EnvVarsCard";
import ProductionScenariosCard from "./ProductionScenariosCard";
import ReplayFlagCard from "./ReplayFlagCard";
import SettingsCard from "./SettingsCard";
import SimulationDefaults from "./SimulationDefaults";
import VersionsCard from "./VersionsCard";
import { envVarGroups, envVarRows } from "./envVarGroups";

/**
 * Environment settings, in Vel's order: identity, how it was built, what runs
 * start from, workspace and replay switches, variables, egress, versions and
 * the danger zone.
 *
 * Rename and Delete are real, as are the build facts, end conditions, runtime
 * parallelism, egress list and variables (all from the §6 detail). Editing
 * the rest has no API yet: those cards carry the UI-only flag and save
 * nothing. Before the build (or on a template) every control shows disabled.
 */
export default function SettingsPanel({
  env,
  envState,
  patch,
  backed = false,
  locked = false,
  onGo,
}) {
  const sample = useUiSample("settings");
  // §6 detail is the source of truth for the variables. Shares the
  // ["harness-environment", id] cache the Evals tab already populates.
  const detailQuery = useQuery(
    harnessEnvironmentQuery(env.id, { enabled: backed }),
  );
  const detail = detailQuery.data;
  const groups = envVarGroups(detail?.settings?.agent);
  const disabled = !backed || locked;

  // §8 rename. After a successful PATCH the mutation writes the fresh detail
  // back into this cache, so the field follows the new name. `nameDraft` is
  // null until the user edits.
  const currentName = detail?.overview?.name ?? env?.name ?? "";
  const rename = useRenameEnvironment();
  const [nameDraft, setNameDraft] = useState(null);
  const [nameError, setNameError] = useState(null);
  const displayName = nameDraft ?? currentName;
  const nameChanged = displayName.trim() !== currentName;

  const saveName = () => {
    const check = validateEnvName(displayName);
    if (!check.ok) {
      setNameError(check.error);
      return;
    }
    setNameError(null);
    rename.mutate(
      { id: env.id, name: check.value },
      {
        onSuccess: () => setNameDraft(null),
        onError: (e) =>
          setNameError(e?.message || "Couldn't rename the environment."),
      },
    );
  };

  let egress = null;
  if (sample) egress = SAMPLE_EGRESS_DOMAINS;
  else if (env?.security) egress = env.security.allowed_egress_domains || [];

  return (
    <Box sx={{ p: 2 }}>
      <Stack spacing={2}>
        <SettingsCard
          label="Environment info"
          title="Environment info"
          subtitle="The name is the only field you can change on a built environment"
        >
          <Stack spacing={2.25} sx={{ p: 2.5 }}>
            <Box>
              <Typography sx={{ typography: "s2", fontWeight: 600, mb: 0.625 }}>
                Environment name
              </Typography>
              <Stack
                direction={{ xs: "column", sm: "row" }}
                spacing={1}
                alignItems={{ sm: "flex-start" }}
              >
                <TextField
                  size="small"
                  value={displayName}
                  disabled={disabled}
                  onChange={(e) => setNameDraft(e.target.value)}
                  error={Boolean(nameError)}
                  helperText={nameError || `1–${MAX_ENV_NAME} characters.`}
                  inputProps={{
                    maxLength: MAX_ENV_NAME + 1,
                    "aria-label": "Environment name",
                  }}
                  sx={{
                    flex: 1,
                    maxWidth: 460,
                    "& .MuiInputBase-root": { typography: "s2" },
                  }}
                />
                <Button
                  variant="outlined"
                  size="small"
                  onClick={saveName}
                  disabled={disabled || !nameChanged || rename.isPending}
                  sx={{
                    color: "text.primary",
                    borderColor: "divider",
                    typography: "s2",
                    fontWeight: 600,
                    mt: { sm: 0.25 },
                  }}
                >
                  {rename.isPending ? "Renaming…" : "Rename"}
                </Button>
              </Stack>
              <Typography
                sx={{ typography: "s3", color: "text.subtitle", mt: 0.625 }}
              >
                The name only. The environment ID and every run stay as they
                are.
              </Typography>
            </Box>
            <Box sx={{ maxWidth: 460 }}>
              <Typography
                sx={{ typography: "s3", color: "text.subtitle", mb: 0.5 }}
              >
                Environment ID
              </Typography>
              <CopyField value={env.id} />
            </Box>
          </Stack>
        </SettingsCard>

        <BuildDetailsCard env={env} connector={envState?.agent?.typeId} />

        <SimulationDefaults
          env={env}
          envState={envState}
          patch={patch}
          disabled={disabled}
          sample={sample}
        />

        <ProductionScenariosCard disabled={disabled} sample={sample} />

        <ReplayFlagCard
          tools={env?.tools || []}
          disabled={disabled}
          sample={sample}
        />

        <EnvVarsCard
          rows={sample ? SAMPLE_ENV_VARS : envVarRows(groups)}
          loading={backed && detailQuery.isLoading}
          disabled={disabled}
        />

        <CredentialFilesCard files={sample ? [] : groups.credentialFiles} />

        <EgressCard domains={egress} disabled={disabled} />

        <VersionsCard
          enabled={Boolean(env?.versioning?.enabled)}
          onOpen={onGo ? () => onGo("versions") : undefined}
        />

        <DangerZone env={env} name={currentName} canDelete={!disabled} />
      </Stack>
    </Box>
  );
}

SettingsPanel.propTypes = {
  envState: PropTypes.object,
  env: PropTypes.object.isRequired,
  patch: PropTypes.func,
  backed: PropTypes.bool,
  locked: PropTypes.bool,
  // Switches the workspace tab; Versions opens the Versions tab with it.
  onGo: PropTypes.func,
};
