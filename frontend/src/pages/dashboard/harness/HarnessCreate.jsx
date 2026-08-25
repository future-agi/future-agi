import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import {
  Alert,
  Box,
  Button,
  ButtonBase,
  Chip,
  CircularProgress,
  LinearProgress,
  MenuItem,
  Paper,
  Stack,
  TextField,
  Typography,
  alpha,
} from "@mui/material";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Helmet } from "react-helmet-async";
import { useNavigate } from "react-router-dom";

import Iconify from "src/components/iconify";
import EnvironmentSwitcher from "src/components/harness/EnvironmentSwitcher";
import StatusChip from "src/components/custom-status-chip/CustomStatusChip";
import { STATUS_TYPES } from "src/utils/statusUtils";
import {
  createHarnessJob,
  listHarnessJobs,
  preflightHarnessJob,
  uploadHarnessSource,
} from "src/api/harness/harness";
import { paths } from "src/routes/paths";

import { parseDotEnv } from "./dotenv";
import { errorMessage, readable, stages } from "./harnessShared";
import { prepareSourceFolder } from "./sourceUpload";

// Uploaded agent folders are often only a few KiB, and a fixed MiB unit rounds
// every one of those to "0.0 MiB". Scale the unit to the actual size instead.
const readableSize = (bytes = 0) => {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MiB`;
};

function Section({ title, description, children }) {
  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Typography variant="subtitle2">{title}</Typography>
      {description && (
        <Typography variant="caption" color="text.secondary">
          {description}
        </Typography>
      )}
      <Box sx={{ mt: 1.5 }}>{children}</Box>
    </Paper>
  );
}

function SourceTile({ icon, title, description, selected, onSelect }) {
  return (
    <ButtonBase
      role="radio"
      aria-checked={selected}
      onClick={onSelect}
      sx={(theme) => ({
        display: "flex",
        alignItems: "flex-start",
        // ButtonBase centres its content; these tiles read as labelled rows, not buttons.
        justifyContent: "flex-start",
        gap: 1.25,
        p: 1.75,
        borderRadius: 1,
        textAlign: "left",
        border: 1,
        borderColor: selected ? "accent.brand" : "divider",
        bgcolor: selected
          ? alpha(theme.palette.primary.main, 0.08)
          : "transparent",
        transition: theme.transitions.create([
          "border-color",
          "background-color",
        ]),
        "&:hover": {
          borderColor: selected ? "accent.brand" : "text.disabled",
          bgcolor: selected
            ? alpha(theme.palette.primary.main, 0.12)
            : "action.hover",
        },
      })}
    >
      <Iconify
        icon={icon}
        width={22}
        sx={{
          mt: 0.25,
          flexShrink: 0,
          color: selected ? "accent.brand" : "text.secondary",
        }}
      />
      <Box sx={{ minWidth: 0 }}>
        <Typography variant="subtitle2">{title}</Typography>
        <Typography variant="caption" color="text.secondary">
          {description}
        </Typography>
      </Box>
    </ButtonBase>
  );
}

SourceTile.propTypes = {
  icon: PropTypes.string.isRequired,
  title: PropTypes.string.isRequired,
  description: PropTypes.string.isRequired,
  selected: PropTypes.bool,
  onSelect: PropTypes.func.isRequired,
};

Section.propTypes = {
  title: PropTypes.string.isRequired,
  description: PropTypes.string,
  children: PropTypes.node,
};

export default function HarnessCreate() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const { data: listData } = useQuery({
    queryKey: ["harness-jobs"],
    queryFn: listHarnessJobs,
    meta: { errorHandled: true },
  });
  const jobs = useMemo(
    () => (Array.isArray(listData) ? listData : []),
    [listData],
  );

  const [sourceMode, setSourceMode] = useState("upload");
  const [uploadedSource, setUploadedSource] = useState(null);
  const [sourceUploadProgress, setSourceUploadProgress] = useState(null);
  const [githubRepository, setGithubRepository] = useState("");
  const [githubVisibility, setGithubVisibility] = useState("public");
  const [githubInstallationId, setGithubInstallationId] = useState("");
  const [scenarioCount, setScenarioCount] = useState(10);
  const [preflight, setPreflight] = useState(null);
  const [configurationValues, setConfigurationValues] = useState({});
  const [environmentValues, setEnvironmentValues] = useState({});
  const [environmentText, setEnvironmentText] = useState("");
  const [environmentError, setEnvironmentError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState("");

  // Switching source invalidates a preflight taken against the other one.
  const selectSource = (mode) => {
    setSourceMode(mode);
    setPreflight(null);
  };

  const sourcePayload = () => {
    if (sourceMode === "upload")
      return { source_id: uploadedSource?.source_id };
    return {
      github_repository: githubRepository.trim(),
      github_visibility: githubVisibility,
      github_installation_id:
        githubVisibility === "private"
          ? githubInstallationId.trim() || undefined
          : undefined,
    };
  };

  const inspect = async () => {
    setChecking(true);
    setError("");
    try {
      const value = await preflightHarnessJob({
        ...sourcePayload(),
        secret_refs: {},
        connector_config: configurationValues,
        environment_values: environmentValues,
      });
      setPreflight(value);
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setChecking(false);
    }
  };

  const run = async () => {
    setSubmitting(true);
    setError("");
    try {
      const value = await createHarnessJob({
        ...sourcePayload(),
        scenario_count: Number(scenarioCount),
        connector: "auto",
        secret_refs: {},
        connector_config: configurationValues,
        environment_values: environmentValues,
      });
      queryClient.invalidateQueries({ queryKey: ["harness-jobs"] });
      navigate(paths.dashboard.simulate.harness.detail(value.job.job_id));
    } catch (requestError) {
      setError(errorMessage(requestError));
      setSubmitting(false);
    }
  };

  const loadEnvironment = (text) => {
    try {
      const values = parseDotEnv(text);
      setEnvironmentValues(values);
      setEnvironmentText("");
      setEnvironmentError("");
      setPreflight(null);
    } catch (parseError) {
      setEnvironmentError(parseError.message);
    }
  };

  const uploadEnvironment = async (event) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    if (file.size > 262144) {
      setEnvironmentError("The .env file may not exceed 256 KiB");
      return;
    }
    loadEnvironment(await file.text());
  };

  const uploadSourceFolder = async (event) => {
    const selected = Array.from(event.target.files || []);
    event.target.value = "";
    if (!selected.length) return;
    setError("");
    setPreflight(null);
    setUploadedSource(null);
    let prepared;
    try {
      prepared = prepareSourceFolder(selected);
    } catch (uploadError) {
      setError(uploadError.message);
      return;
    }
    const formData = new FormData();
    prepared.files.forEach((file, index) => {
      formData.append("files", file, file.name);
      formData.append("paths", prepared.paths[index]);
    });
    formData.append("name", prepared.name);
    setSourceUploadProgress(0);
    try {
      const result = await uploadHarnessSource(formData, (progressEvent) => {
        if (progressEvent.total)
          setSourceUploadProgress(
            Math.round((progressEvent.loaded / progressEvent.total) * 100),
          );
      });
      setUploadedSource({ ...result, excluded_count: prepared.excludedCount });
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setSourceUploadProgress(null);
    }
  };

  const requirements = preflight?.credentials?.requirements || [];
  const credentialChoices = preflight?.credentials?.credential_choices || [];
  const choiceMembers = new Set(
    credentialChoices.flatMap((choice) => choice.options.flat()),
  );
  const missingRequirements = requirements.filter(
    (item) =>
      item.required &&
      item.status === "missing" &&
      !choiceMembers.has(item.environment_name),
  );
  const requirementConfigured = (name) => {
    const requirement = requirements.find(
      (item) => item.environment_name === name,
    );
    if (requirement?.status !== "missing") return true;
    if (requirement?.kind === "secret")
      return Boolean(String(environmentValues[name] || "").trim());
    if (Object.hasOwn(environmentValues, name)) return true;
    return Boolean(String(configurationValues[name] || "").trim());
  };
  const unsatisfiedChoices = credentialChoices.filter(
    (choice) =>
      !choice.satisfied &&
      !choice.options.some((option) =>
        option.every((name) => requirementConfigured(name)),
      ),
  );
  const requirementsConfigured =
    missingRequirements.every((item) =>
      requirementConfigured(item.environment_name),
    ) && unsatisfiedChoices.length === 0;
  const requiredInputCount =
    missingRequirements.length + unsatisfiedChoices.length;
  const hasSource =
    sourceMode === "upload"
      ? Boolean(uploadedSource?.source_id)
      : Boolean(githubRepository.trim()) &&
        (githubVisibility === "public" || Boolean(githubInstallationId.trim()));

  return (
    <>
      <Helmet>
        <title>Create RL Environment | Future AGI</title>
      </Helmet>

      <Box sx={{ height: "100vh", overflow: "auto", p: 2 }}>
        <Stack spacing={2}>
          <Box>
            <Stack
              direction="row"
              alignItems="center"
              spacing={1.5}
              sx={{ mb: 0.5 }}
            >
              <Button
                size="small"
                color="inherit"
                onClick={() => navigate(paths.dashboard.simulate.harness.root)}
                startIcon={<Iconify icon="eva:arrow-back-fill" width={18} />}
                sx={{ color: "text.secondary", px: 1, ml: -1, minWidth: 0 }}
              >
                All environments
              </Button>
              <EnvironmentSwitcher
                jobs={jobs}
                currentName="New environment"
                onSelect={(jobId) =>
                  navigate(paths.dashboard.simulate.harness.detail(jobId))
                }
                onCreate={() => {}}
                showCreate={false}
              />
            </Stack>
            <Typography typography="m2" fontWeight={600}>
              Create RL environment
            </Typography>
            <Typography typography="s1" color="text.secondary">
              Point ALK at your agent, check what it needs, then run the whole
              pipeline end to end.
            </Typography>
          </Box>

          <Box
            sx={{
              display: "grid",
              gap: 2,
              alignItems: "start",
              gridTemplateColumns: {
                xs: "minmax(0, 1fr)",
                lg: "minmax(0, 1.4fr) minmax(300px, 0.6fr)",
              },
            }}
          >
            <Stack spacing={2}>
              <Section
                title="Agent source"
                description="Where ALK should read the agent from."
              >
                <Stack spacing={2}>
                  <Box
                    role="radiogroup"
                    aria-label="Agent source"
                    sx={{
                      display: "grid",
                      gap: 1.5,
                      gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" },
                    }}
                  >
                    <SourceTile
                      icon="solar:folder-with-files-linear"
                      title="Upload folder"
                      description="Send a copy of a local folder to the isolated runner."
                      selected={sourceMode === "upload"}
                      onSelect={() => selectSource("upload")}
                    />
                    <SourceTile
                      icon="mdi:github"
                      title="GitHub repository"
                      description="Check out a branch the runner can reach."
                      selected={sourceMode === "github"}
                      onSelect={() => selectSource("github")}
                    />
                  </Box>

                  {sourceMode === "upload" ? (
                    uploadedSource ? (
                      <Stack
                        direction="row"
                        spacing={1.5}
                        alignItems="center"
                        sx={{
                          p: 1.5,
                          borderRadius: 1,
                          border: 1,
                          borderColor: "divider",
                          bgcolor: "background.neutral",
                        }}
                      >
                        <Iconify
                          icon="solar:folder-with-files-bold"
                          width={24}
                          sx={{ color: "accent.brand", flexShrink: 0 }}
                        />
                        <Box sx={{ minWidth: 0, flex: 1 }}>
                          <Typography variant="body2" fontWeight={600} noWrap>
                            {uploadedSource.name}
                          </Typography>
                          <Typography variant="caption" color="text.secondary">
                            {uploadedSource.file_count} files ·{" "}
                            {readableSize(uploadedSource.total_bytes)}
                            {uploadedSource.excluded_count
                              ? ` · ${uploadedSource.excluded_count} generated or secret files excluded`
                              : ""}
                          </Typography>
                        </Box>
                        <Button
                          component="label"
                          size="small"
                          color="inherit"
                          disabled={sourceUploadProgress !== null}
                          sx={{ flexShrink: 0 }}
                        >
                          {sourceUploadProgress !== null
                            ? `Uploading ${sourceUploadProgress}%`
                            : "Replace"}
                          <Box
                            component="input"
                            type="file"
                            multiple
                            directory=""
                            webkitdirectory=""
                            onChange={uploadSourceFolder}
                            sx={{ display: "none" }}
                          />
                        </Button>
                      </Stack>
                    ) : (
                      <Box
                        component="label"
                        sx={(theme) => ({
                          display: "flex",
                          flexDirection: "column",
                          alignItems: "center",
                          justifyContent: "center",
                          gap: 0.75,
                          px: 2,
                          py: 4,
                          borderRadius: 1,
                          border: "1px dashed",
                          borderColor: "divider",
                          cursor:
                            sourceUploadProgress === null
                              ? "pointer"
                              : "default",
                          transition: theme.transitions.create([
                            "border-color",
                            "background-color",
                          ]),
                          "&:hover": {
                            borderColor: "accent.brand",
                            bgcolor: "action.hover",
                          },
                        })}
                      >
                        <Iconify
                          icon="solar:folder-with-files-linear"
                          width={28}
                          sx={{ color: "text.secondary" }}
                        />
                        <Typography variant="subtitle2">
                          {sourceUploadProgress !== null
                            ? `Uploading ${sourceUploadProgress}%`
                            : "Choose agent folder"}
                        </Typography>
                        <Typography
                          variant="caption"
                          color="text.secondary"
                          sx={{ textAlign: "center" }}
                        >
                          .env files and generated dependency folders are left
                          behind.
                        </Typography>
                        {sourceUploadProgress !== null && (
                          <LinearProgress
                            variant="determinate"
                            value={sourceUploadProgress}
                            sx={{ width: "60%", mt: 1 }}
                          />
                        )}
                        <Box
                          component="input"
                          type="file"
                          multiple
                          directory=""
                          webkitdirectory=""
                          onChange={uploadSourceFolder}
                          sx={{ display: "none" }}
                        />
                      </Box>
                    )
                  ) : (
                    <Stack spacing={1.5}>
                      <TextField
                        fullWidth
                        size="small"
                        label="Repository URL"
                        placeholder="https://github.com/owner/repository or .../tree/branch"
                        value={githubRepository}
                        onChange={(event) => {
                          setGithubRepository(event.target.value);
                          setPreflight(null);
                        }}
                      />
                      <Box
                        sx={{
                          display: "grid",
                          gap: 1.5,
                          gridTemplateColumns: {
                            xs: "1fr",
                            sm:
                              githubVisibility === "private"
                                ? "180px minmax(0, 1fr)"
                                : "180px",
                          },
                        }}
                      >
                        <TextField
                          select
                          size="small"
                          label="Visibility"
                          value={githubVisibility}
                          onChange={(event) => {
                            setGithubVisibility(event.target.value);
                            setPreflight(null);
                          }}
                        >
                          <MenuItem value="public">Public</MenuItem>
                          <MenuItem value="private">Private</MenuItem>
                        </TextField>
                        {githubVisibility === "private" && (
                          <TextField
                            size="small"
                            label="GitHub App installation ID"
                            helperText="The installation that grants the runner access to this repository."
                            FormHelperTextProps={{ sx: { mx: 0 } }}
                            value={githubInstallationId}
                            onChange={(event) =>
                              setGithubInstallationId(event.target.value)
                            }
                          />
                        )}
                      </Box>
                    </Stack>
                  )}
                </Stack>
              </Section>

              <Section
                title="Run settings"
                description="How much of the agent to exercise in this run."
              >
                <Stack
                  direction={{ xs: "column", sm: "row" }}
                  spacing={1.5}
                  alignItems={{ sm: "center" }}
                >
                  <TextField
                    size="small"
                    label="Scenarios"
                    type="number"
                    value={scenarioCount}
                    onChange={(event) => setScenarioCount(event.target.value)}
                    inputProps={{ min: 1, max: 100 }}
                    sx={{ width: 140, flexShrink: 0 }}
                  />
                  <Typography variant="caption" color="text.secondary">
                    Each scenario is one generated conversation the agent is put
                    through, then graded. More scenarios means broader coverage
                    and a longer run.
                  </Typography>
                </Stack>
              </Section>

              <Section
                title="Environment values"
                description="Values stay in this browser session, are sent only for preflight and run execution, and are never written to jobs, logs, or artifacts."
              >
                <Stack spacing={1.5}>
                  <Stack
                    direction={{ xs: "column", md: "row" }}
                    spacing={1.5}
                    alignItems={{ md: "center" }}
                  >
                    <Button
                      component="label"
                      variant="outlined"
                      startIcon={
                        <Iconify icon="solar:upload-minimalistic-linear" />
                      }
                      sx={{ flexShrink: 0, whiteSpace: "nowrap" }}
                    >
                      Upload .env
                      <Box
                        component="input"
                        type="file"
                        accept=".env,text/plain"
                        onChange={uploadEnvironment}
                        sx={{ display: "none" }}
                      />
                    </Button>
                    <TextField
                      fullWidth
                      size="small"
                      multiline
                      maxRows={4}
                      label="Or paste .env contents"
                      placeholder="OPENAI_API_KEY=..."
                      value={environmentText}
                      onChange={(event) =>
                        setEnvironmentText(event.target.value)
                      }
                    />
                    <Button
                      variant="text"
                      disabled={!environmentText.trim()}
                      onClick={() => loadEnvironment(environmentText)}
                      sx={{ flexShrink: 0, whiteSpace: "nowrap" }}
                    >
                      Use values
                    </Button>
                    {Object.keys(environmentValues).length > 0 && (
                      <Button
                        color="inherit"
                        onClick={() => {
                          setEnvironmentValues({});
                          setPreflight(null);
                        }}
                      >
                        Clear
                      </Button>
                    )}
                  </Stack>
                  {Object.keys(environmentValues).length > 0 && (
                    <Stack
                      direction="row"
                      spacing={0.75}
                      flexWrap="wrap"
                      useFlexGap
                    >
                      {Object.keys(environmentValues).map((name) => (
                        <Chip
                          key={name}
                          size="small"
                          variant="outlined"
                          label={name}
                        />
                      ))}
                    </Stack>
                  )}
                  {environmentError && (
                    <Alert severity="error" variant="outlined">
                      {environmentError}
                    </Alert>
                  )}
                </Stack>
              </Section>

              {preflight && (
                <Section title="Preflight">
                  <Stack spacing={1.5}>
                    <Stack direction="row" spacing={1} alignItems="center">
                      <StatusChip
                        status={
                          requirementsConfigured
                            ? STATUS_TYPES.PASS
                            : STATUS_TYPES.RUNNING
                        }
                        label={
                          requirementsConfigured
                            ? "Preflight ready"
                            : `${requiredInputCount} credential choice${requiredInputCount === 1 ? "" : "s"} needed`
                        }
                      />
                      <Typography variant="caption" color="text.secondary">
                        {preflight.credentials?.scanned_files || 0} files
                        scanned ·{" "}
                        {(
                          preflight.credentials?.detected_connectors || []
                        ).join(", ") || "connector discovered after checkout"}
                      </Typography>
                    </Stack>
                    {unsatisfiedChoices.map((choice) => (
                      <Alert key={choice.id} severity="info" variant="outlined">
                        {choice.purpose}: choose{" "}
                        {choice.options
                          .map((option) => option.join(" + "))
                          .join(" or ")}
                      </Alert>
                    ))}
                    {requirements.map((item) => (
                      <Stack
                        key={item.id}
                        direction={{ xs: "column", md: "row" }}
                        spacing={1.5}
                        alignItems={{ md: "center" }}
                      >
                        <Box sx={{ minWidth: 260 }}>
                          <Typography variant="body2" fontWeight={600}>
                            {item.environment_name}
                          </Typography>
                          <Typography variant="caption" color="text.secondary">
                            {item.provider} · {item.purpose} ·{" "}
                            {readable(item.status)}
                          </Typography>
                        </Box>
                        {item.status === "missing" && (
                          <TextField
                            fullWidth
                            size="small"
                            label={
                              item.kind === "secret"
                                ? "Secret value (used for this run only)"
                                : "Configuration value"
                            }
                            type={item.kind === "secret" ? "password" : "text"}
                            value={
                              item.kind === "secret"
                                ? environmentValues[item.environment_name] || ""
                                : configurationValues[item.environment_name] ||
                                  ""
                            }
                            onChange={(event) => {
                              const setter =
                                item.kind === "secret"
                                  ? setEnvironmentValues
                                  : setConfigurationValues;
                              setter((existing) => ({
                                ...existing,
                                [item.environment_name]: event.target.value,
                              }));
                            }}
                            helperText={
                              item.kind === "secret"
                                ? "Injected ephemerally and removed when the run finishes"
                                : undefined
                            }
                            FormHelperTextProps={{ sx: { mx: 0 } }}
                          />
                        )}
                      </Stack>
                    ))}
                  </Stack>
                </Section>
              )}

              {error && (
                <Alert severity="error" variant="outlined">
                  {error}
                </Alert>
              )}

              <Stack direction="row" spacing={1.5} sx={{ pb: 4 }}>
                <Button
                  variant="outlined"
                  disabled={checking || !hasSource}
                  onClick={inspect}
                  startIcon={
                    checking ? (
                      <CircularProgress size={16} />
                    ) : (
                      <Iconify icon="solar:magnifer-linear" />
                    )
                  }
                >
                  Preflight
                </Button>
                <Button
                  variant="contained"
                  disabled={
                    submitting ||
                    !hasSource ||
                    !preflight ||
                    !requirementsConfigured
                  }
                  onClick={run}
                  startIcon={
                    submitting ? (
                      <CircularProgress size={16} />
                    ) : (
                      <Iconify icon="solar:play-bold" />
                    )
                  }
                >
                  Run end to end
                </Button>
              </Stack>
            </Stack>

            <Paper variant="outlined" sx={{ p: 2 }}>
              <Typography variant="subtitle2">What ALK will do</Typography>
              <Typography variant="caption" color="text.secondary">
                Every stage runs without operator prompts once the run starts.
              </Typography>
              <Stack spacing={1.2} sx={{ mt: 1.5 }}>
                {stages.map((stage) => (
                  <Stack
                    key={stage}
                    direction="row"
                    spacing={1.2}
                    alignItems="center"
                  >
                    <Iconify
                      icon="solar:record-circle-linear"
                      color="text.secondary"
                      sx={{ opacity: 0.6 }}
                    />
                    <Typography
                      variant="body2"
                      color="text.secondary"
                      sx={{ opacity: 0.6 }}
                    >
                      {readable(stage)}
                    </Typography>
                  </Stack>
                ))}
              </Stack>
            </Paper>
          </Box>
        </Stack>
      </Box>
    </>
  );
}
