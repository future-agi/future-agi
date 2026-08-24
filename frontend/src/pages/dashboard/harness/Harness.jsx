import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Divider,
  IconButton,
  LinearProgress,
  MenuItem,
  Paper,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import { Helmet } from "react-helmet-async";
import Iconify from "src/components/iconify";
import {
  cancelHarnessJob,
  createHarnessJob,
  getHarnessJob,
  listHarnessJobs,
  preflightHarnessJob,
  uploadHarnessSource,
} from "src/api/harness/harness";
import { parseDotEnv } from "./dotenv";
import { prepareSourceFolder } from "./sourceUpload";

const terminalStages = new Set(["completed", "failed", "canceled"]);
const stages = [
  "understanding_agent",
  "generating_environment",
  "building_environment",
  "validating_environment",
  "generating_data",
  "generating_scenarios",
  "validating_scenarios",
  "connecting_agent",
  "running",
  "grading",
  "uploading_artifacts",
  "completed",
];

const readable = (value = "") =>
  value.replaceAll("_", " ").replace(/^./, (letter) => letter.toUpperCase());

function eventMessage(event) {
  const payload = event.payload || {};
  if (payload.detail) return String(payload.detail);
  if (payload.message) return String(payload.message);
  if (payload.stage)
    return `${readable(payload.stage)} ${readable(event.type)}`;
  return readable(event.type || "Progress updated");
}

export default function Harness() {
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
  const [jobs, setJobs] = useState([]);
  const [current, setCurrent] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [checking, setChecking] = useState(false);
  const [clock, setClock] = useState(Date.now());

  useEffect(() => {
    const timer = window.setInterval(() => setClock(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  const refreshList = useCallback(async () => {
    try {
      const value = await listHarnessJobs();
      setJobs(Array.isArray(value) ? value : []);
      if (!current && value?.length) setCurrent(value[0]);
    } catch (requestError) {
      setError(requestError?.response?.data?.detail || requestError.message);
    }
  }, [current]);

  useEffect(() => {
    refreshList();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const id = current?.job?.job_id;
    if (!id || terminalStages.has(current.status.stage)) return undefined;
    const timer = window.setInterval(async () => {
      try {
        const value = await getHarnessJob(id);
        setCurrent(value);
        setJobs((existing) =>
          existing.map((job) => (job.job?.job_id === id ? value : job)),
        );
      } catch (requestError) {
        setError(requestError?.response?.data?.detail || requestError.message);
      }
    }, 2000);
    return () => window.clearInterval(timer);
  }, [current?.job?.job_id, current?.status?.stage]);

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
      setError(requestError?.response?.data?.detail || requestError.message);
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
      setCurrent(value);
      setJobs((existing) => [value, ...existing]);
    } catch (requestError) {
      setError(requestError?.response?.data?.detail || requestError.message);
    } finally {
      setSubmitting(false);
    }
  };

  const cancel = async () => {
    const value = await cancelHarnessJob(current.job.job_id);
    setCurrent(value);
  };

  const startNewEnvironment = () => {
    setCurrent(null);
    setSourceMode("upload");
    setUploadedSource(null);
    setSourceUploadProgress(null);
    setGithubRepository("");
    setGithubVisibility("public");
    setGithubInstallationId("");
    setScenarioCount(10);
    setPreflight(null);
    setConfigurationValues({});
    setEnvironmentValues({});
    setEnvironmentText("");
    setEnvironmentError("");
    setError("");
  };

  const stageIndex = stages.indexOf(current?.status?.stage);
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
  const progress = current
    ? current.status.stage === "completed"
      ? 100
      : Math.max(2, ((Math.max(stageIndex, 0) + 0.5) / stages.length) * 100)
    : 0;
  const updatedAt = current?.status?.updated_at
    ? new Date(current.status.updated_at)
    : null;
  const secondsSinceUpdate = updatedAt
    ? Math.max(0, Math.floor((clock - updatedAt.getTime()) / 1000))
    : null;
  const messages = useMemo(() => {
    const seen = new Set();
    return (current?.events || []).filter((event) => {
      const key = event.event_id || JSON.stringify(event);
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }, [current?.events]);

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
      setUploadedSource({
        ...result,
        excluded_count: prepared.excludedCount,
      });
    } catch (requestError) {
      setError(requestError?.response?.data?.detail || requestError.message);
    } finally {
      setSourceUploadProgress(null);
    }
  };

  return (
    <>
      <Helmet>
        <title>RL Environment | Future AGI</title>
      </Helmet>
      <Box
        sx={{
          height: "100%",
          display: "grid",
          gridTemplateColumns: "280px minmax(0, 1fr)",
          bgcolor: "background.default",
        }}
      >
        <Paper square variant="outlined" sx={{ p: 2, overflow: "auto" }}>
          <Stack direction="row" alignItems="center" justifyContent="space-between">
            <Typography variant="overline" color="text.secondary">
              RL environments
            </Typography>
            <Tooltip title="New RL environment">
              <IconButton
                size="small"
                color="primary"
                aria-label="Create a new RL environment"
                onClick={startNewEnvironment}
              >
                <Iconify icon="mingcute:add-line" width={20} />
              </IconButton>
            </Tooltip>
          </Stack>
          <Stack spacing={1} mt={1}>
            {jobs.map((item) => (
              <Button
                key={item.job.job_id}
                variant={
                  current?.job?.job_id === item.job.job_id
                    ? "contained"
                    : "text"
                }
                color="inherit"
                onClick={() => setCurrent(item)}
                sx={{
                  justifyContent: "flex-start",
                  textAlign: "left",
                  textTransform: "none",
                }}
              >
                <Box>
                  <Typography variant="body2" fontWeight={600} noWrap>
                    {item.job.metadata?.agent_name}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    {readable(item.status.stage)}
                  </Typography>
                </Box>
              </Button>
            ))}
          </Stack>
        </Paper>

        <Box
          sx={{
            minWidth: 0,
            display: "flex",
            flexDirection: "column",
            overflow: "hidden",
          }}
        >
          <Paper square variant="outlined" sx={{ p: 2 }}>
            <Stack
              direction={{ xs: "column", md: "row" }}
              spacing={1.5}
              alignItems="center"
            >
              <TextField
                select
                size="small"
                label="Source"
                value={sourceMode}
                onChange={(event) => {
                  setSourceMode(event.target.value);
                  setPreflight(null);
                }}
                sx={{ minWidth: 130 }}
              >
                <MenuItem value="upload">Upload folder</MenuItem>
                <MenuItem value="github">GitHub</MenuItem>
              </TextField>
              {sourceMode === "upload" ? (
                <Stack sx={{ minWidth: 280 }} spacing={0.5}>
                  <Button
                    component="label"
                    variant="outlined"
                    disabled={sourceUploadProgress !== null}
                    startIcon={<Iconify icon="solar:folder-with-files-linear" />}
                  >
                    {sourceUploadProgress !== null
                      ? `Uploading ${sourceUploadProgress}%`
                      : uploadedSource
                        ? "Replace folder"
                        : "Choose agent folder"}
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
                  <Typography variant="caption" color="text.secondary">
                    {uploadedSource
                      ? `${uploadedSource.name}: ${uploadedSource.file_count} files (${(uploadedSource.total_bytes / 1024 / 1024).toFixed(1)} MiB)${uploadedSource.excluded_count ? `; ${uploadedSource.excluded_count} generated or secret files excluded` : ""}`
                      : "The folder is uploaded to the isolated runner. .env and generated dependency folders are excluded."}
                  </Typography>
                </Stack>
              ) : (
                <>
                  <TextField
                    fullWidth
                    size="small"
                    label="GitHub repository URL"
                    placeholder="https://github.com/owner/repository or .../tree/branch"
                    value={githubRepository}
                    onChange={(event) => {
                      setGithubRepository(event.target.value);
                      setPreflight(null);
                    }}
                  />
                  <TextField
                    select
                    size="small"
                    label="Visibility"
                    value={githubVisibility}
                    onChange={(event) => {
                      setGithubVisibility(event.target.value);
                      setPreflight(null);
                    }}
                    sx={{ minWidth: 120 }}
                  >
                    <MenuItem value="public">Public</MenuItem>
                    <MenuItem value="private">Private</MenuItem>
                  </TextField>
                  {githubVisibility === "private" && (
                    <TextField
                      size="small"
                      label="GitHub App installation ID"
                      value={githubInstallationId}
                      onChange={(event) =>
                        setGithubInstallationId(event.target.value)
                      }
                      sx={{ minWidth: 230 }}
                    />
                  )}
                </>
              )}
              <TextField
                size="small"
                label="Scenarios"
                type="number"
                value={scenarioCount}
                onChange={(event) => setScenarioCount(event.target.value)}
                inputProps={{ min: 1, max: 100 }}
                sx={{ width: 120 }}
              />
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
            <Paper variant="outlined" sx={{ mt: 1.5, p: 1.5 }}>
              <Stack spacing={1}>
                <Stack
                  direction={{ xs: "column", md: "row" }}
                  spacing={1}
                  alignItems={{ md: "center" }}
                >
                  <Button
                    component="label"
                    variant="outlined"
                    startIcon={<Iconify icon="solar:upload-minimalistic-linear" />}
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
                    onChange={(event) => setEnvironmentText(event.target.value)}
                  />
                  <Button
                    variant="text"
                    disabled={!environmentText.trim()}
                    onClick={() => loadEnvironment(environmentText)}
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
                <Typography variant="caption" color="text.secondary">
                  {Object.keys(environmentValues).length
                    ? `${Object.keys(environmentValues).length} variables loaded: ${Object.keys(environmentValues).join(", ")}`
                    : "Values stay in this browser session, are sent only for preflight/run execution, and are never written to jobs, logs, or artifacts."}
                </Typography>
                {environmentError && (
                  <Alert severity="error">{environmentError}</Alert>
                )}
              </Stack>
            </Paper>
            {preflight && (
              <Paper variant="outlined" sx={{ mt: 1.5, p: 1.5 }}>
                <Stack
                  direction="row"
                  spacing={1}
                  alignItems="center"
                  mb={requirements.length ? 1 : 0}
                >
                  <Chip
                    size="small"
                    color={requirementsConfigured ? "success" : "warning"}
                    label={
                      requirementsConfigured
                        ? "Preflight ready"
                        : `${requiredInputCount} credential choice${requiredInputCount === 1 ? "" : "s"} needed`
                    }
                  />
                  <Typography variant="caption" color="text.secondary">
                    {preflight.credentials?.scanned_files || 0} files scanned ·{" "}
                    {(preflight.credentials?.detected_connectors || []).join(
                      ", ",
                    ) || "connector discovered after checkout"}
                  </Typography>
                </Stack>
                <Stack spacing={1}>
                  {unsatisfiedChoices.map((choice) => (
                    <Alert key={choice.id} severity="info">
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
                      spacing={1}
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
                              : configurationValues[item.environment_name] || ""
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
                        />
                      )}
                    </Stack>
                  ))}
                </Stack>
              </Paper>
            )}
            {error && (
              <Alert severity="error" sx={{ mt: 1.5 }}>
                {error}
              </Alert>
            )}
          </Paper>

          {!current ? (
            <Stack
              flex={1}
              alignItems="center"
              justifyContent="center"
              spacing={1}
            >
              <Iconify icon="solar:server-square-cloud-linear" width={54} />
              <Typography variant="h6">
                Give us the agent folder; ALK does the rest.
              </Typography>
              <Typography color="text.secondary">
                Environment discovery, services, realistic data, scenarios,
                calls and grading run without operator prompts.
              </Typography>
            </Stack>
          ) : (
            <Box
              sx={{
                flex: 1,
                minHeight: 0,
                display: "grid",
                gridTemplateColumns:
                  "minmax(380px, 0.9fr) minmax(480px, 1.4fr)",
              }}
            >
              <Box
                sx={{
                  p: 2,
                  overflow: "auto",
                  borderRight: 1,
                  borderColor: "divider",
                }}
              >
                <Stack
                  direction="row"
                  justifyContent="space-between"
                  alignItems="center"
                >
                  <Box>
                    <Typography variant="h6">
                      {current.job.metadata?.agent_name}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      {current.job.run_id}
                    </Typography>
                  </Box>
                  <Chip
                    label={readable(current.status.stage)}
                    color={
                      current.status.stage === "failed"
                        ? "error"
                        : current.status.stage === "completed"
                          ? "success"
                          : "primary"
                    }
                  />
                </Stack>
                <LinearProgress
                  variant="determinate"
                  value={progress}
                  color={
                    current.status.stage === "failed" ? "error" : "primary"
                  }
                  sx={{ mt: 1.5 }}
                />
                <Stack direction="row" justifyContent="space-between" mt={0.5}>
                  <Typography variant="caption" color="text.secondary">
                    {current.status.completed_scenarios || 0} /{" "}
                    {current.status.total_scenarios ||
                      current.job.scenario_count}{" "}
                    scenarios complete
                  </Typography>
                  <Typography
                    variant="caption"
                    color={
                      secondsSinceUpdate > 60 &&
                      !terminalStages.has(current.status.stage)
                        ? "warning.main"
                        : "text.secondary"
                    }
                  >
                    Updated {secondsSinceUpdate ?? 0}s ago · attempt{" "}
                    {current.status.attempt || 1}
                  </Typography>
                </Stack>
                <Divider sx={{ my: 2 }} />
                <Stack spacing={1.2}>
                  {stages.map((stage, index) => {
                    const reached =
                      stageIndex >= index ||
                      current.status.stage === "completed";
                    return (
                      <Stack
                        key={stage}
                        direction="row"
                        spacing={1.2}
                        alignItems="center"
                      >
                        <Iconify
                          icon={
                            reached
                              ? "solar:check-circle-bold"
                              : "solar:record-circle-linear"
                          }
                          color={reached ? "success.main" : "text.disabled"}
                        />
                        <Typography
                          color={reached ? "text.primary" : "text.disabled"}
                        >
                          {readable(stage)}
                        </Typography>
                      </Stack>
                    );
                  })}
                </Stack>
                {!terminalStages.has(current.status.stage) && (
                  <Button
                    color="error"
                    variant="outlined"
                    onClick={cancel}
                    sx={{ mt: 3 }}
                  >
                    Cancel run
                  </Button>
                )}
              </Box>

              <Box sx={{ p: 2, overflow: "auto" }}>
                <Typography variant="overline" color="text.secondary">
                  Live harness activity
                </Typography>
                <Stack spacing={1.5} mt={1.5}>
                  {current.credentials && (
                    <Paper variant="outlined" sx={{ p: 1.5 }}>
                      <Typography variant="subtitle2">
                        Runtime preflight
                      </Typography>
                      <Typography variant="body2" color="text.secondary">
                        {current.credentials.scanned_files} source files
                        inspected ·{" "}
                        {(current.credentials.detected_connectors || []).join(
                          ", ",
                        ) || "generic connector"}{" "}
                        · {current.credentials.requirements?.length || 0}{" "}
                        configuration requirements
                      </Typography>
                    </Paper>
                  )}
                  {messages.map((event) => (
                    <Paper
                      key={event.event_id}
                      variant="outlined"
                      sx={{ p: 1.5 }}
                    >
                      <Stack direction="row" justifyContent="space-between">
                        <Typography variant="caption" color="primary.main">
                          {readable(event.payload?.stage || event.type)}
                        </Typography>
                        <Typography variant="caption" color="text.secondary">
                          {event.wall_time
                            ? new Date(event.wall_time).toLocaleTimeString()
                            : ""}
                        </Typography>
                      </Stack>
                      <Typography variant="body2">
                        {eventMessage(event)}
                      </Typography>
                    </Paper>
                  ))}
                  {current.status.failure && (
                    <Alert severity="error">
                      <Typography variant="subtitle2">
                        {readable(current.status.failure.domain)} ·{" "}
                        {current.status.failure.code}
                      </Typography>
                      {current.status.failure.message}
                    </Alert>
                  )}
                  {current.status.detail && (
                    <Alert
                      severity={
                        current.status.stage === "failed" ? "error" : "info"
                      }
                    >
                      {current.status.detail}
                    </Alert>
                  )}
                  {!terminalStages.has(current.status.stage) && (
                    <Stack direction="row" spacing={1} alignItems="center">
                      <CircularProgress size={16} />
                      <Typography variant="body2" color="text.secondary">
                        ALK is working autonomously…
                      </Typography>
                    </Stack>
                  )}
                </Stack>
              </Box>
            </Box>
          )}
        </Box>
      </Box>
    </>
  );
}
