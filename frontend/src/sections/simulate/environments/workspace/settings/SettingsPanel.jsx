import PropTypes from "prop-types";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Box, Stack, Typography, Button, TextField, Chip } from "@mui/material";
import { harnessEnvironmentQuery } from "src/api/simulate-environments/environment";
import { useRenameEnvironment } from "src/api/simulate-environments/environments";
import SectionCard from "../../components/SectionCard";
import CopyField from "../../components/CopyField";
import { validateEnvName, MAX_ENV_NAME } from "../renameEnvironment";
import { envVarGroups } from "./envVarGroups";

const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

/**
 * Environment settings.
 *
 * On a built (backend-backed) environment the contract makes this tab almost
 * entirely read-only: §6 `settings` records how the environment was built, and
 * the name is the only editable field (§8). So this shows two things — the
 * rename field and a read-only view of the environment variables (§11) — both
 * read straight from §6 via the same cache the Evals tab uses (the flagged
 * `useEnvironment` detail merge stays dormant until HARNESS_DETAIL_ENABLED
 * flips, so reading the query here is what makes the tab work today).
 *
 * Run defaults, Build arguments and Versions used to live here too, but had no
 * backend field behind them — they were sample UI. They are preserved,
 * commented, at the foot of this file so they can be revived field by field if
 * the contract ever grows them.
 */
export default function SettingsPanel({ env, backed = false, locked = false }) {
  // §6 detail is the source of truth for a backed env's settings. Shares the
  // ["harness-environment", id] cache the Evals tab already populates.
  const detailQuery = useQuery(harnessEnvironmentQuery(env.id, { enabled: backed }));
  const detail = detailQuery.data;
  const groups = envVarGroups(detail?.settings?.agent);

  // §8 rename. Baseline is the §6 name — after a successful PATCH the mutation
  // writes the fresh detail back into this cache, so the field reflects the new
  // name without a manual refetch. `nameDraft` is null until the user edits, so
  // the input follows the detail until then (§6 may resolve after first render).
  const currentName = detail?.overview?.name ?? env?.name ?? "";
  const rename = useRenameEnvironment();
  const [nameDraft, setNameDraft] = useState(null);
  const [nameError, setNameError] = useState(null);
  const displayName = nameDraft ?? currentName;
  const canRename = backed && !locked;
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
        onError: (e) => setNameError(e?.message || "Couldn't rename the environment."),
      },
    );
  };

  return (
    <Box sx={{ p: 2 }}>
      <Box sx={{ mb: 3 }}>
        <Typography sx={{ typography: "m2", fontWeight: 600 }}>Settings</Typography>
        <Typography sx={{ typography: "s1", color: "text.secondary", maxWidth: 760 }}>
          How this environment was built. Everything here is fixed at build time.
          Only the name can be changed.
        </Typography>
      </Box>

      {!backed ? (
        <SectionCard title="Settings">
          <Box sx={{ p: 2.5 }}>
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              Settings appear once this environment has been built.
            </Typography>
          </Box>
        </SectionCard>
      ) : (
        <Stack spacing={2}>
          {/* Environment info — mirrors Vel's design: rename (the one editable
              field, §8) plus the read-only Environment ID. */}
          <SectionCard
            title="Environment info"
            subtitle="The name is the only field you can change on a built environment"
          >
            <Stack spacing={2.25} sx={{ p: 2.5 }}>
              <Box>
                <Typography sx={{ typography: "s2", fontWeight: 600, mb: 0.625 }}>
                  Environment name
                </Typography>
                {canRename ? (
                  <>
                    <Stack
                      direction={{ xs: "column", sm: "row" }}
                      spacing={1}
                      alignItems={{ sm: "flex-start" }}
                    >
                      <TextField
                        size="small"
                        aria-label="Environment name"
                        value={displayName}
                        onChange={(e) => setNameDraft(e.target.value)}
                        error={Boolean(nameError)}
                        helperText={nameError || `1–${MAX_ENV_NAME} characters.`}
                        inputProps={{ maxLength: MAX_ENV_NAME + 1 }}
                        sx={{ flex: 1, maxWidth: 460, "& .MuiInputBase-root": { typography: "s2" } }}
                      />
                      <Button
                        variant="outlined"
                        size="small"
                        onClick={saveName}
                        disabled={!nameChanged || rename.isPending}
                        sx={{
                          color: "text.primary", borderColor: "divider",
                          typography: "s2", fontWeight: 600, mt: { sm: 0.25 },
                        }}
                      >
                        {rename.isPending ? "Renaming…" : "Rename"}
                      </Button>
                    </Stack>
                    <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.625 }}>
                      Renaming changes the identifier used by runs and SDK clients.
                    </Typography>
                  </>
                ) : (
                  <Typography sx={{ typography: "s2" }}>{currentName || "-"}</Typography>
                )}
              </Box>
              <Box sx={{ maxWidth: 460 }}>
                <Typography sx={{ typography: "s3", color: "text.subtitle", mb: 0.5 }}>
                  Environment ID
                </Typography>
                <CopyField value={env.id} />
              </Box>
            </Stack>
          </SectionCard>

          <SectionCard
            title="Environment variables"
            subtitle="Set when the environment was built. Read-only. Secret values are never shown."
          >
            <EnvVarsBody loading={detailQuery.isLoading} groups={groups} />
          </SectionCard>

          {/* Credential files are mounted files, not env variables — a separate
              section (§11). Shown only when the environment has any. */}
          {groups.credentialFiles.length > 0 && (
            <SectionCard
              title="Credential files"
              subtitle="Mounted into the environment by name. Contents and the original filename are not stored."
            >
              <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
                {groups.credentialFiles.map((f) => (
                  <Stack
                    key={f.environment_name}
                    direction="row"
                    alignItems="center"
                    spacing={1.5}
                    sx={{ px: 2.5, py: 1.375 }}
                  >
                    <Typography
                      sx={{
                        flex: 1,
                        minWidth: 0,
                        typography: "s2",
                        fontWeight: 600,
                        fontFamily: MONO,
                        overflowWrap: "anywhere",
                      }}
                    >
                      {f.environment_name}
                    </Typography>
                    <Chip
                      size="small"
                      label="File"
                      sx={{
                        height: 19,
                        borderRadius: 0.5,
                        color: "text.secondary",
                        border: "1px solid",
                        borderColor: "divider",
                        bgcolor: "transparent",
                        "& .MuiChip-label": { px: 0.75, typography: "s3", fontWeight: 600 },
                      }}
                    />
                  </Stack>
                ))}
              </Stack>
            </SectionCard>
          )}

          {/*
            Run defaults, Build arguments and Versions were here. They are sample
            UI — §6 `settings` is read-only and carries no run-default, build-arg
            or version data — so they are preserved commented at the foot of this
            file rather than shown as if they worked.
          */}
        </Stack>
      )}
    </Box>
  );
}

SettingsPanel.propTypes = {
  env: PropTypes.object.isRequired,
  backed: PropTypes.bool,
  locked: PropTypes.bool,
};

const MASK = "••••••••••••";

/**
 * Flatten the two variable groups into the single key · value · kind list Vel's
 * design uses. Secrets have no value returned (encrypted, addressed by name), so
 * they render masked; config carries real values. Credential files are NOT env
 * variables — they are mounted files, so they get their own section, not a row
 * here. `kind` fills the chip slot the design has (its mock `usedBy` field is not
 * in the §6 payload, so the honest signal is which group the variable came from).
 */
function envVarRows(groups) {
  return [
    ...groups.secrets.map((name) => ({ key: name, kind: "Secret", masked: true })),
    ...Object.entries(groups.config).map(([key, value]) => ({
      key,
      value: String(value),
      kind: "Config",
      masked: false,
    })),
  ];
}

/**
 * The §11 environment-variables view (Secrets + Config only). Matches the
 * design's flat list (key · value · kind chip) but read-only: no add row, no
 * delete, and no eye — a secret's value is never returned, so there is nothing
 * a reveal could show.
 */
function EnvVarsBody({ loading, groups }) {
  const rows = envVarRows(groups);
  if (loading) {
    return (
      <Box sx={{ p: 2.5 }}>
        <Typography sx={{ typography: "s2", color: "text.secondary" }}>Loading…</Typography>
      </Box>
    );
  }
  if (!rows.length) {
    return (
      <Box sx={{ p: 2.5 }}>
        <Typography sx={{ typography: "s2", color: "text.secondary" }}>
          No variables were recorded for this environment.
        </Typography>
      </Box>
    );
  }
  return (
    <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
      {rows.map((v) => (
        <Stack
          key={`${v.kind}:${v.key}`}
          direction="row"
          alignItems="center"
          spacing={1.5}
          sx={{ px: 2.5, py: 1.375 }}
        >
          <Typography
            sx={{
              typography: "s2",
              fontWeight: 600,
              fontFamily: MONO,
              width: 220,
              flexShrink: 0,
              pr: 1,
              // A long unbroken identifier (GOOGLE_APPLICATION_CREDENTIALS_JSON)
              // has no space to wrap on, so let it break within the column
              // instead of overflowing into the value.
              overflowWrap: "anywhere",
            }}
          >
            {v.key}
          </Typography>
          <Typography
            noWrap
            sx={{ flex: 1, minWidth: 0, typography: "s2", fontFamily: MONO, color: "text.subtitle" }}
          >
            {v.masked ? MASK : v.value}
          </Typography>
          <Chip
            size="small"
            label={v.kind}
            sx={{
              height: 19,
              borderRadius: 0.5,
              color: "text.secondary",
              border: "1px solid",
              borderColor: "divider",
              bgcolor: "transparent",
              "& .MuiChip-label": { px: 0.75, typography: "s3", fontWeight: 600 },
            }}
          />
        </Stack>
      ))}
    </Stack>
  );
}
EnvVarsBody.propTypes = {
  loading: PropTypes.bool,
  groups: PropTypes.object,
};

/*
  ────────────────────────────────────────────────────────────────────────────
  SAMPLE SECTIONS — preserved, not shown.

  Run defaults, Build arguments and Versions had no backend behind them: §6
  `settings` is read-only and carries no run-default, build-arg or version data.
  Reviving any of them means adding the field to the contract first, then
  restoring the matching block below plus the props/state/imports it needs
  (`envState`, `patch`; the version/restore state + handlers; `useState`,
  `alpha`, `Slider`, `Switch`, `IconButton`, `Tooltip`, `MenuItem`, `Iconify`,
  `ConfirmDialog`, `MockBadge`, and the `envConfig` / `versions` fixtures). Full
  original wiring is in git history for this file.

  --- Run defaults (fixture: DEFAULT_RUNTIME / ISOLATION_OPTIONS, REGION_OPTIONS)
  <SectionCard title="Run defaults"
    subtitle="Every run inherits these unless it overrides them" action={<MockBadge />}>
    ... isolation picker, concurrency / task-timeout / step-budget / retries
    sliders, region select, file-tracking + record-video toggles ...
  </SectionCard>

  --- Build arguments (fixture: DEFAULT_BUILD_ARGS)
  <SectionCard title="Build arguments"
    subtitle="Passed at image build time, not at runtime. One per line: KEY=value"
    action={<MockBadge />}>
    ... multiline KEY=value TextField ...
  </SectionCard>

  --- Versions (fixture: environmentVersions / nextEnvVersion)
  <SectionCard title="Versions"
    subtitle="Every run pins a version, so a result can always be traced to the
    world that produced it" action={<New version button>}>
    ... "What changed in the world?" change-kind picker (ENV_CHANGES) that mints
    v(N+1) via patch(); per-version rows with Switch / Restore; ConfirmDialog
    restore-preflight that forks the environment ...
  </SectionCard>
  ────────────────────────────────────────────────────────────────────────────
*/
