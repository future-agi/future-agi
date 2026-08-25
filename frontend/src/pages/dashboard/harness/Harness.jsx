import { useCallback, useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
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
  Tab,
  Tabs,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import { Helmet } from "react-helmet-async";
import Iconify from "src/components/iconify";
import {
  adjustHarnessJob,
  cancelHarnessJob,
  createHarnessJob,
  getHarnessJob,
  listHarnessJobs,
  preflightHarnessJob,
  uploadHarnessSecretFile,
  uploadHarnessSource,
} from "src/api/harness/harness";
import { parseDotEnv } from "./dotenv";
import { prepareSourceFolder } from "./sourceUpload";
import {
  credentialCount,
  credentialValue,
  mergePastedCredentials,
  updateCredential,
} from "./credentialValues";

const terminalStages = new Set(["completed", "failed", "canceled"]);
const stages = [
  "queued",
  "acquiring_source",
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
  "cleaning_up",
  "completed",
];

const stageLabels = {
  queued: "Queued",
  acquiring_source: "Preparing source",
  understanding_agent: "Understanding agent",
  generating_environment: "Generating environment",
  building_environment: "Building environment",
  validating_environment: "Validating environment",
  generating_data: "Generating data",
  generating_scenarios: "Generating scenarios",
  validating_scenarios: "Validating scenarios",
  connecting_agent: "Connecting agent",
  running: "Running scenarios",
  grading: "Grading results",
  uploading_artifacts: "Uploading artifacts",
  cleaning_up: "Cleaning up",
  completed: "Completed",
  failed: "Failed",
  canceled: "Canceled",
};

const detailTabs = [
  { value: "contract", label: "Contract" },
  { value: "environment", label: "Environment" },
  { value: "scenarios", label: "Scenarios" },
  { value: "runs", label: "Runs" },
];

const stageDetailTabs = {
  queued: "contract",
  acquiring_source: "contract",
  understanding_agent: "contract",
  generating_environment: "environment",
  building_environment: "environment",
  validating_environment: "environment",
  generating_data: "environment",
  generating_scenarios: "scenarios",
  validating_scenarios: "scenarios",
  connecting_agent: "runs",
  running: "runs",
  grading: "runs",
  uploading_artifacts: "runs",
  cleaning_up: "runs",
  completed: "runs",
  failed: "runs",
  canceled: "runs",
};

const readable = (value = "") =>
  stageLabels[value] ||
  value.replaceAll("_", " ").replace(/^./, (letter) => letter.toUpperCase());

function requestErrorMessage(requestError, fallback = "Something went wrong") {
  const data = requestError?.response?.data;
  const detail = data?.detail ?? data;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map(String).join(" ");
  if (detail && typeof detail === "object")
    return Object.entries(detail)
      .map(([field, messages]) => {
        const text = Array.isArray(messages)
          ? messages.map(String).join(" ")
          : String(messages);
        return `${readable(field)}: ${text}`;
      })
      .join(" ");
  return requestError?.message || fallback;
}

function eventMessage(event) {
  const payload = event.payload || {};
  if (payload.detail) return String(payload.detail);
  if (payload.message) return String(payload.message);
  if (payload.stage) {
    if (event.type === "harness.stage.started")
      return `${readable(payload.stage)} started`;
    if (event.type === "harness.stage.completed")
      return `${readable(payload.stage)} completed`;
    if (event.type === "harness.stage.failed")
      return `${readable(payload.stage)} failed`;
    return `${readable(payload.stage)} updated`;
  }
  return readable(event.type || "Progress updated");
}

function StageOutput({ output }) {
  const data = output.data || {};
  return (
    <Accordion variant="outlined" defaultExpanded={output.kind !== "scenarios"}>
      <AccordionSummary
        expandIcon={<Iconify icon="eva:arrow-ios-downward-fill" />}
      >
        <Box sx={{ minWidth: 0 }}>
          <Typography variant="subtitle2">{output.title}</Typography>
          <Typography variant="caption" color="text.secondary">
            {output.summary}
          </Typography>
        </Box>
      </AccordionSummary>
      <AccordionDetails>
        {output.kind === "simulation" && (
          <Stack spacing={1} alignItems="flex-start">
            <Typography variant="body2" color="text.secondary">
              Open the exact simulation execution to inspect calls, transcripts,
              recordings, tool activity, and evaluations.
            </Typography>
            <Button
              component="a"
              href={data.url}
              target="_blank"
              rel="noopener noreferrer"
              variant="contained"
              endIcon={<Iconify icon="solar:arrow-right-up-linear" />}
            >
              Open simulation
            </Button>
          </Stack>
        )}
        {output.kind === "contract" && (
          <Stack spacing={1}>
            <Typography variant="body2">{data.one_liner}</Typography>
            <Typography variant="caption" color="text.secondary">
              Modality: {data.modality || "unknown"} · Runtime:{" "}
              {typeof data.runtime === "string"
                ? data.runtime
                : data.runtime?.entrypoint || "discovered"}
            </Typography>
            <Stack direction="row" gap={0.75} flexWrap="wrap">
              {(data.tools || []).map((tool) => (
                <Chip
                  key={tool.name || String(tool)}
                  size="small"
                  label={tool.name || String(tool)}
                  variant="outlined"
                />
              ))}
            </Stack>
            {(data.hard_constraints || []).map((constraint) => (
              <Typography key={String(constraint)} variant="body2">
                • {String(constraint)}
              </Typography>
            ))}
          </Stack>
        )}
        {output.kind === "environment" && (
          <Stack spacing={1}>
            <Stack direction="row" gap={0.75} flexWrap="wrap">
              {(data.services || []).map((service) => (
                <Chip
                  key={service}
                  size="small"
                  label={service}
                  color="success"
                />
              ))}
            </Stack>
            <Typography variant="body2">
              Project: {data.project || "isolated run"} ·{" "}
              {data.managed ? "ALK-managed" : "repository-provided"}
            </Typography>
            {Object.entries(data.overrides || {}).map(([name, value]) => (
              <Typography key={name} variant="caption" color="text.secondary">
                {name} → {String(value)}
              </Typography>
            ))}
          </Stack>
        )}
        {output.kind === "scenarios" && (
          <Stack spacing={1}>
            {(Array.isArray(data) ? data : []).map((scenario) => (
              <Paper key={scenario.name} variant="outlined" sx={{ p: 1.25 }}>
                <Typography variant="subtitle2">
                  {readable(scenario.name)}
                </Typography>
                <Typography variant="body2">{scenario.instruction}</Typography>
                <Typography variant="caption" color="text.secondary">
                  {scenario.use_case || "Generated test case"}
                </Typography>
              </Paper>
            ))}
          </Stack>
        )}
        {!["contract", "environment", "scenarios", "simulation"].includes(
          output.kind,
        ) && (
          <Box
            component="pre"
            sx={{ m: 0, whiteSpace: "pre-wrap", fontSize: 12 }}
          >
            {JSON.stringify(data, null, 2)}
          </Box>
        )}
      </AccordionDetails>
    </Accordion>
  );
}

StageOutput.propTypes = {
  output: PropTypes.shape({
    data: PropTypes.oneOfType([PropTypes.object, PropTypes.array]),
    kind: PropTypes.string.isRequired,
    summary: PropTypes.string,
    title: PropTypes.string.isRequired,
  }).isRequired,
};

export default function Harness() {
  const [sourceMode, setSourceMode] = useState("upload");
  const [uploadedSource, setUploadedSource] = useState(null);
  const [sourceUploadProgress, setSourceUploadProgress] = useState(null);
  const [githubRepository, setGithubRepository] = useState("");
  const [githubVisibility, setGithubVisibility] = useState("public");
  const [githubInstallationId, setGithubInstallationId] = useState("");
  const [scenarioCount, setScenarioCount] = useState(10);
  const [preflight, setPreflight] = useState(null);
  const [preflightDirty, setPreflightDirty] = useState(false);
  const [configurationValues, setConfigurationValues] = useState({});
  const [environmentValues, setEnvironmentValues] = useState({});
  const [environmentText, setEnvironmentText] = useState("");
  const [environmentError, setEnvironmentError] = useState("");
  const [secretFileRefs, setSecretFileRefs] = useState({});
  const [secretFileUploads, setSecretFileUploads] = useState({});
  const [uploadingSecretFile, setUploadingSecretFile] = useState(false);
  const [jobs, setJobs] = useState([]);
  const [current, setCurrent] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [checking, setChecking] = useState(false);
  const [adjustment, setAdjustment] = useState("");
  const [adjusting, setAdjusting] = useState(false);
  const [detailTab, setDetailTab] = useState("contract");
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
      setError(
        requestErrorMessage(requestError, "Could not load harness runs"),
      );
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
        setError(
          requestErrorMessage(requestError, "Could not refresh this run"),
        );
      }
    }, 2000);
    return () => window.clearInterval(timer);
  }, [current?.job?.job_id, current?.status?.stage]);

  useEffect(() => {
    const activeTab = stageDetailTabs[current?.status?.stage];
    if (activeTab) setDetailTab(activeTab);
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

  const preflightPayload = () => ({
    ...sourcePayload(),
    secret_refs: secretFileRefs,
    connector_config: configurationValues,
    environment_values: environmentValues,
  });

  const inspect = async () => {
    setChecking(true);
    setError("");
    try {
      const value = await preflightHarnessJob(preflightPayload());
      setPreflight(value);
      setPreflightDirty(false);
      return value;
    } catch (requestError) {
      setError(requestErrorMessage(requestError, "Preflight could not finish"));
      return null;
    } finally {
      setChecking(false);
    }
  };

  const run = async () => {
    setSubmitting(true);
    setError("");
    try {
      const checked = await preflightHarnessJob(preflightPayload());
      setPreflight(checked);
      setPreflightDirty(false);
      if (!checked.ready_to_submit) {
        setError(
          "Preflight found items that still need attention. Review the checklist below, make the changes, and check again.",
        );
        return;
      }
      const value = await createHarnessJob({
        ...sourcePayload(),
        scenario_count: Number(scenarioCount),
        connector: "auto",
        secret_refs: secretFileRefs,
        connector_config: configurationValues,
        environment_values: environmentValues,
      });
      setCurrent(value);
      setJobs((existing) => [value, ...existing]);
      setDetailTab("contract");
    } catch (requestError) {
      setError(
        requestErrorMessage(requestError, "The run could not be started"),
      );
    } finally {
      setSubmitting(false);
    }
  };

  const cancel = async () => {
    setError("");
    try {
      const value = await cancelHarnessJob(current.job.job_id);
      setCurrent(value);
    } catch (requestError) {
      setError(
        requestErrorMessage(requestError, "The run could not be canceled"),
      );
    }
  };

  const submitAdjustment = async () => {
    if (!adjustment.trim() || !current?.job?.job_id) return;
    setAdjusting(true);
    setError("");
    try {
      const value = await adjustHarnessJob(current.job.job_id, {
        instruction: adjustment.trim(),
        client_request_id: window.crypto?.randomUUID?.(),
      });
      setCurrent(value);
      setAdjustment("");
    } catch (requestError) {
      setError(
        requestErrorMessage(requestError, "The change could not be sent"),
      );
    } finally {
      setAdjusting(false);
    }
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
    setPreflightDirty(false);
    setConfigurationValues({});
    setEnvironmentValues({});
    setEnvironmentText("");
    setEnvironmentError("");
    setSecretFileRefs({});
    setSecretFileUploads({});
    setError("");
    setDetailTab("contract");
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
    if (secretFileRefs[name]) return true;
    return Boolean(
      String(
        credentialValue(environmentValues, configurationValues, name),
      ).trim(),
    );
  };
  const unsatisfiedChoices = credentialChoices.filter(
    (choice) =>
      !choice.satisfied &&
      !choice.options.some((option) =>
        option.every((name) => requirementConfigured(name)),
      ),
  );
  const actionableRequirementNames = new Set([
    ...missingRequirements.map((item) => item.environment_name),
    ...unsatisfiedChoices.flatMap((choice) => choice.options.flat()),
  ]);
  const actionableRequirements = requirements.filter(
    (item) =>
      item.status === "missing" &&
      actionableRequirementNames.has(item.environment_name),
  );
  const requirementsConfiguredLocally =
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
  const validScenarioCount =
    Number.isInteger(Number(scenarioCount)) &&
    Number(scenarioCount) >= 1 &&
    Number(scenarioCount) <= 100;
  const loadedCredentialCount =
    credentialCount(environmentValues, configurationValues) +
    Object.keys(secretFileRefs).length;
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
  const packagingFindings = (preflight?.packaging?.candidates || []).flatMap(
    (candidate) =>
      (candidate.findings || []).map((finding) => ({
        ...finding,
        path: candidate.path,
      })),
  );
  const blockingPackagingFindings = packagingFindings.filter(
    (finding) => finding.blocking,
  );
  const preflightReady = Boolean(preflight?.ready_to_submit && !preflightDirty);
  const selectedOutputs = (current?.stage_outputs || []).filter((output) =>
    detailTab === "runs"
      ? !["contract", "environment", "scenarios"].includes(output.kind)
      : output.kind === detailTab,
  );
  const outputCounts = (current?.stage_outputs || []).reduce(
    (counts, output) => ({
      ...counts,
      [output.kind]: (counts[output.kind] || 0) + 1,
    }),
    {},
  );

  const loadPastedEnvironment = () => {
    try {
      const values = parseDotEnv(environmentText);
      if (!Object.keys(values).length)
        throw new Error("No environment variables were found.");
      const merged = mergePastedCredentials(
        environmentValues,
        configurationValues,
        values,
      );
      setEnvironmentValues(merged.environmentValues);
      setConfigurationValues(merged.configurationValues);
      setEnvironmentText("");
      setEnvironmentError("");
      setPreflightDirty(Boolean(preflight));
    } catch (parseError) {
      setEnvironmentError(parseError.message);
    }
  };

  const uploadCredentialFile = async (environmentName, file) => {
    if (!file) return;
    setUploadingSecretFile(true);
    setEnvironmentError("");
    try {
      const formData = new FormData();
      formData.append("file", file);
      formData.append("environment_name", environmentName);
      const uploaded = await uploadHarnessSecretFile(formData);
      setSecretFileRefs((existing) => ({
        ...existing,
        [environmentName]: uploaded.secret_ref,
      }));
      setSecretFileUploads((existing) => ({
        ...existing,
        [environmentName]: { name: file.name, size: uploaded.size },
      }));
      // A pasted workstation path and a provider-managed file reference are mutually exclusive.
      setEnvironmentValues((existing) => {
        const next = { ...existing };
        delete next[environmentName];
        return next;
      });
      setConfigurationValues((existing) => {
        const next = { ...existing };
        delete next[environmentName];
        return next;
      });
      setPreflightDirty(true);
    } catch (requestError) {
      setEnvironmentError(
        requestErrorMessage(
          requestError,
          "Credential file could not be uploaded",
        ),
      );
    } finally {
      setUploadingSecretFile(false);
    }
  };

  const uploadSourceFolder = async (event) => {
    const selected = Array.from(event.target.files || []);
    event.target.value = "";
    if (!selected.length) return;
    setError("");
    setPreflight(null);
    setPreflightDirty(false);
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
      setError(
        requestErrorMessage(requestError, "The folder could not be uploaded"),
      );
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
          <Stack
            direction="row"
            alignItems="center"
            justifyContent="space-between"
          >
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
                onClick={() => {
                  setCurrent(item);
                  setDetailTab("contract");
                }}
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
            <Stack spacing={0.25} mb={1.5}>
              <Typography variant="subtitle1" fontWeight={700}>
                Set up a harness run
              </Typography>
              <Typography variant="body2" color="text.secondary">
                Add the agent source, provide any run-only environment values,
                then check readiness before starting.
              </Typography>
            </Stack>
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
                  setPreflightDirty(false);
                  setError("");
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
                    startIcon={
                      <Iconify icon="solar:folder-with-files-linear" />
                    }
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
                      setPreflightDirty(false);
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
                      setPreflightDirty(false);
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
                      onChange={(event) => {
                        setGithubInstallationId(event.target.value);
                        setPreflight(null);
                        setPreflightDirty(false);
                      }}
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
                error={!validScenarioCount}
                helperText={!validScenarioCount ? "Use 1–100" : undefined}
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
                {preflightDirty
                  ? "Check again"
                  : preflight
                    ? "Recheck"
                    : "Check readiness"}
              </Button>
              <Button
                variant="contained"
                disabled={
                  submitting ||
                  checking ||
                  !hasSource ||
                  !validScenarioCount ||
                  !preflightReady
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
                Start run
              </Button>
            </Stack>
            <Stack direction="row" spacing={1} mt={1} alignItems="center">
              <Chip
                size="small"
                variant={hasSource ? "filled" : "outlined"}
                color={hasSource ? "success" : "default"}
                label={hasSource ? "1 Source added" : "1 Add source"}
              />
              <Iconify
                icon="eva:arrow-ios-forward-fill"
                color="text.disabled"
              />
              <Chip
                size="small"
                variant={preflight ? "filled" : "outlined"}
                color={
                  preflightReady
                    ? "success"
                    : preflight || preflightDirty
                      ? "warning"
                      : "default"
                }
                label={
                  preflightDirty
                    ? "2 Recheck readiness"
                    : preflightReady
                      ? "2 Ready"
                      : preflight
                        ? "2 Needs attention"
                        : "2 Check readiness"
                }
              />
              <Iconify
                icon="eva:arrow-ios-forward-fill"
                color="text.disabled"
              />
              <Chip
                size="small"
                variant="outlined"
                color={preflightReady ? "primary" : "default"}
                label="3 Start run"
              />
            </Stack>
            <Paper variant="outlined" sx={{ mt: 1.5, p: 1.5 }}>
              <Stack spacing={1}>
                <Stack
                  direction="row"
                  alignItems="center"
                  justifyContent="space-between"
                >
                  <Box>
                    <Typography variant="subtitle2">
                      Environment credentials
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      Paste .env contents for bulk setup, or check readiness and
                      enter only the missing values below.
                    </Typography>
                  </Box>
                  {loadedCredentialCount > 0 && (
                    <Chip
                      size="small"
                      color="success"
                      icon={<Iconify icon="solar:shield-check-bold" />}
                      label={`${loadedCredentialCount} loaded`}
                    />
                  )}
                </Stack>
                <Stack
                  direction={{ xs: "column", md: "row" }}
                  spacing={1}
                  alignItems={{ md: "flex-start" }}
                >
                  <TextField
                    fullWidth
                    size="small"
                    multiline
                    minRows={2}
                    maxRows={5}
                    label="Paste .env contents"
                    placeholder="OPENAI_API_KEY=..."
                    value={environmentText}
                    onChange={(event) => {
                      setEnvironmentText(event.target.value);
                      setEnvironmentError("");
                    }}
                  />
                  <Button
                    variant="outlined"
                    disabled={!environmentText.trim()}
                    onClick={loadPastedEnvironment}
                    sx={{ minWidth: 120 }}
                  >
                    Use values
                  </Button>
                  {loadedCredentialCount > 0 && (
                    <Button
                      color="inherit"
                      onClick={() => {
                        setEnvironmentValues({});
                        setConfigurationValues({});
                        setSecretFileRefs({});
                        setSecretFileUploads({});
                        setEnvironmentText("");
                        setPreflightDirty(Boolean(preflight));
                      }}
                    >
                      Clear
                    </Button>
                  )}
                </Stack>
                <Stack direction="row" spacing={1} alignItems="center">
                  <Button
                    component="label"
                    variant="outlined"
                    size="small"
                    disabled={uploadingSecretFile}
                    startIcon={
                      uploadingSecretFile ? (
                        <CircularProgress size={16} />
                      ) : (
                        <Iconify icon="solar:file-upload-bold" />
                      )
                    }
                  >
                    Upload Google credential JSON
                    <Box
                      component="input"
                      type="file"
                      accept="application/json,.json"
                      hidden
                      onChange={(event) => {
                        const [file] = event.target.files || [];
                        uploadCredentialFile(
                          "GOOGLE_APPLICATION_CREDENTIALS",
                          file,
                        );
                        event.target.value = "";
                      }}
                    />
                  </Button>
                  {secretFileUploads.GOOGLE_APPLICATION_CREDENTIALS && (
                    <Chip
                      size="small"
                      color="success"
                      label={`${secretFileUploads.GOOGLE_APPLICATION_CREDENTIALS.name} · mounted per run`}
                    />
                  )}
                </Stack>
                <Typography variant="caption" color="text.secondary">
                  {loadedCredentialCount
                    ? `${loadedCredentialCount} credentials loaded. Values are hidden; uploaded files are mounted read-only and removed at the terminal run boundary.`
                    : "Pasted values and uploaded credential files are never written to jobs, logs, bundles, or artifacts."}
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
                    color={preflightReady ? "success" : "warning"}
                    label={
                      preflightDirty
                        ? "Readiness changed — check again"
                        : preflightReady
                          ? "Ready to run"
                          : requiredInputCount
                            ? `${requiredInputCount} credential input${requiredInputCount === 1 ? "" : "s"} needed`
                            : "Source setup needs attention"
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
                  {(preflight.notes || []).map((note) => (
                    <Alert key={note} severity="info">
                      {note}
                    </Alert>
                  ))}
                  {(preflight.packaging?.notes || []).map((note) => (
                    <Alert key={note} severity="warning">
                      {note}
                    </Alert>
                  ))}
                  {blockingPackagingFindings.map((finding) => (
                    <Alert
                      key={`${finding.path}-${finding.code}`}
                      severity="error"
                    >
                      <Typography variant="subtitle2">
                        {finding.path} · {readable(finding.code)}
                      </Typography>
                      {finding.message}
                    </Alert>
                  ))}
                  {preflight.packaging?.selected_path && (
                    <Alert severity="success" icon={false}>
                      Runtime packaging: {preflight.packaging.selected_path}
                    </Alert>
                  )}
                  {unsatisfiedChoices.map((choice) => (
                    <Alert key={choice.id} severity="info">
                      {choice.purpose}: choose{" "}
                      {choice.options
                        .map((option) => option.join(" + "))
                        .join(" or ")}
                    </Alert>
                  ))}
                  {actionableRequirements.length > 0 && (
                    <Box sx={{ pt: 0.5 }}>
                      <Typography variant="subtitle2">
                        Missing credentials
                      </Typography>
                      <Typography variant="caption" color="text.secondary">
                        Enter only the values requested by preflight. Secret
                        fields stay masked and are used for this run only.
                      </Typography>
                    </Box>
                  )}
                  {actionableRequirements.map((item) => (
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
                          value={credentialValue(
                            environmentValues,
                            configurationValues,
                            item.environment_name,
                          )}
                          onChange={(event) => {
                            const updated = updateCredential(
                              environmentValues,
                              configurationValues,
                              {
                                name: item.environment_name,
                                value: event.target.value,
                                kind: item.kind,
                              },
                            );
                            setEnvironmentValues(updated.environmentValues);
                            setConfigurationValues(updated.configurationValues);
                            setPreflightDirty(true);
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
                  {preflightDirty && requirementsConfiguredLocally && (
                    <Alert severity="info">
                      Values are loaded. Select <strong>Check again</strong> to
                      confirm credentials and packaging before starting.
                    </Alert>
                  )}
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
                  overflow: "hidden",
                  display: "flex",
                  flexDirection: "column",
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
                <Box sx={{ flex: 1, minHeight: 0, overflow: "auto", pr: 0.5 }}>
                  <Divider sx={{ my: 2 }} />
                  <Stack spacing={1.2}>
                    {stages.map((stage, index) => {
                      const completed =
                        stageIndex > index ||
                        current.status.stage === "completed";
                      const active = stageIndex === index;
                      return (
                        <Stack
                          key={stage}
                          direction="row"
                          spacing={1.2}
                          alignItems="center"
                        >
                          <Iconify
                            icon={
                              completed
                                ? "solar:check-circle-bold"
                                : active
                                  ? "solar:play-circle-bold"
                                  : "solar:record-circle-linear"
                            }
                            color={
                              completed
                                ? "success.main"
                                : active
                                  ? "primary.main"
                                  : "text.disabled"
                            }
                          />
                          <Typography
                            color={
                              completed || active
                                ? "text.primary"
                                : "text.disabled"
                            }
                          >
                            {readable(stage)}
                          </Typography>
                        </Stack>
                      );
                    })}
                  </Stack>
                </Box>
                {!terminalStages.has(current.status.stage) && (
                  <Paper
                    variant="outlined"
                    sx={{ mt: 2.5, p: 1.5, bgcolor: "action.hover" }}
                  >
                    <Stack spacing={1}>
                      <Box>
                        <Typography variant="subtitle2">
                          Change this run
                        </Typography>
                        <Typography variant="caption" color="text.secondary">
                          Tell the harness what to adjust. The request is queued
                          and applied at the next safe stage boundary.
                        </Typography>
                      </Box>
                      <TextField
                        fullWidth
                        size="small"
                        multiline
                        minRows={2}
                        maxRows={5}
                        placeholder='For example: "Add scenarios covering payment failures"'
                        value={adjustment}
                        onChange={(event) => setAdjustment(event.target.value)}
                        onKeyDown={(event) => {
                          if (
                            (event.metaKey || event.ctrlKey) &&
                            event.key === "Enter"
                          )
                            submitAdjustment();
                        }}
                      />
                      <Button
                        fullWidth
                        variant="contained"
                        disabled={adjusting || !adjustment.trim()}
                        onClick={submitAdjustment}
                        startIcon={
                          adjusting ? (
                            <CircularProgress size={16} />
                          ) : (
                            <Iconify icon="solar:plain-2-bold" />
                          )
                        }
                      >
                        Send change
                      </Button>
                    </Stack>
                  </Paper>
                )}
                {!terminalStages.has(current.status.stage) && (
                  <Button
                    color="error"
                    variant="outlined"
                    onClick={cancel}
                    sx={{ mt: 1.5 }}
                  >
                    Cancel run
                  </Button>
                )}
              </Box>

              <Box
                sx={{
                  minWidth: 0,
                  minHeight: 0,
                  display: "flex",
                  flexDirection: "column",
                  overflow: "hidden",
                }}
              >
                <Tabs
                  value={detailTab}
                  onChange={(_event, value) => setDetailTab(value)}
                  variant="fullWidth"
                  sx={{ px: 1, borderBottom: 1, borderColor: "divider" }}
                >
                  {detailTabs.map((tab) => (
                    <Tab
                      key={tab.value}
                      value={tab.value}
                      label={
                        <Stack
                          direction="row"
                          spacing={0.75}
                          alignItems="center"
                        >
                          <span>{tab.label}</span>
                          {stageDetailTabs[current.status.stage] ===
                            tab.value &&
                            !terminalStages.has(current.status.stage) && (
                              <CircularProgress size={14} />
                            )}
                          {tab.value !== "runs" &&
                            outputCounts[tab.value] > 0 && (
                              <Iconify
                                icon="solar:check-circle-bold"
                                color="success.main"
                                width={16}
                              />
                            )}
                        </Stack>
                      }
                    />
                  ))}
                </Tabs>

                <Box sx={{ p: 2, overflow: "auto", flex: 1 }}>
                  {detailTab !== "runs" ? (
                    <Stack spacing={1.5}>
                      <Box>
                        <Typography variant="subtitle1" fontWeight={700}>
                          {
                            detailTabs.find((tab) => tab.value === detailTab)
                              ?.label
                          }
                        </Typography>
                        <Typography variant="body2" color="text.secondary">
                          {detailTab === "contract" &&
                            "What the harness understood about the agent and its capabilities."}
                          {detailTab === "environment" &&
                            "The isolated services and runtime assembled for this run."}
                          {detailTab === "scenarios" &&
                            "The grounded test cases generated for the agent."}
                        </Typography>
                      </Box>
                      {selectedOutputs.map((output) => (
                        <StageOutput key={output.id} output={output} />
                      ))}
                      {!selectedOutputs.length && (
                        <Paper
                          variant="outlined"
                          sx={{
                            p: 4,
                            textAlign: "center",
                            bgcolor: "action.hover",
                          }}
                        >
                          <Iconify
                            icon="solar:hourglass-line-linear"
                            width={32}
                            color="text.disabled"
                          />
                          <Typography variant="subtitle2" mt={1}>
                            Not available yet
                          </Typography>
                          <Typography variant="body2" color="text.secondary">
                            This view fills in automatically when the related
                            stage completes.
                          </Typography>
                        </Paper>
                      )}
                    </Stack>
                  ) : (
                    <Stack spacing={1.5}>
                      <Paper variant="outlined" sx={{ p: 1.5 }}>
                        <Stack
                          direction="row"
                          spacing={1.25}
                          alignItems="center"
                        >
                          {!terminalStages.has(current.status.stage) ? (
                            <CircularProgress size={20} />
                          ) : (
                            <Iconify
                              icon={
                                current.status.stage === "completed"
                                  ? "solar:check-circle-bold"
                                  : "solar:danger-circle-bold"
                              }
                              color={
                                current.status.stage === "completed"
                                  ? "success.main"
                                  : "error.main"
                              }
                              width={22}
                            />
                          )}
                          <Box>
                            <Typography variant="subtitle2">
                              {!terminalStages.has(current.status.stage)
                                ? `Working on ${readable(current.status.stage).toLowerCase()}`
                                : readable(current.status.stage)}
                            </Typography>
                            <Typography variant="body2" color="text.secondary">
                              {terminalStages.has(current.status.stage)
                                ? "The final status and generated artifacts are shown here."
                                : `Updated ${secondsSinceUpdate ?? 0}s ago. Progress refreshes automatically every 2 seconds.`}
                            </Typography>
                          </Box>
                        </Stack>
                      </Paper>

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

                      {(current.adjustments || []).length > 0 && (
                        <Stack spacing={1}>
                          <Typography variant="subtitle2">
                            Requested changes
                          </Typography>
                          {(current.adjustments || []).map((item) => (
                            <Alert
                              key={item.adjustment_id}
                              severity={
                                item.status === "applied" ? "success" : "info"
                              }
                              icon={
                                item.status === "pending" ||
                                item.status === "applying" ? (
                                  <CircularProgress size={16} />
                                ) : undefined
                              }
                            >
                              <Typography variant="subtitle2">
                                {readable(item.status)} ·{" "}
                                {readable(item.target_stage)}
                              </Typography>
                              {item.instruction}
                            </Alert>
                          ))}
                        </Stack>
                      )}

                      {selectedOutputs.map((output) => (
                        <StageOutput key={output.id} output={output} />
                      ))}

                      {current.credentials && (
                        <Paper variant="outlined" sx={{ p: 1.5 }}>
                          <Typography variant="subtitle2">
                            Runtime readiness
                          </Typography>
                          <Typography variant="body2" color="text.secondary">
                            {current.credentials.scanned_files} source files
                            inspected ·{" "}
                            {(
                              current.credentials.detected_connectors || []
                            ).join(", ") || "generic connector"}{" "}
                            · {current.credentials.requirements?.length || 0}{" "}
                            configuration requirements
                          </Typography>
                        </Paper>
                      )}

                      <Box>
                        <Typography variant="subtitle2" mb={1}>
                          Activity
                        </Typography>
                        <Stack spacing={0}>
                          {messages
                            .slice(-20)
                            .reverse()
                            .map((event, index) => (
                              <Stack
                                key={event.event_id}
                                direction="row"
                                spacing={1.25}
                                sx={{ pb: 1.25 }}
                              >
                                <Stack alignItems="center">
                                  <Box
                                    sx={{
                                      mt: 0.5,
                                      width: 8,
                                      height: 8,
                                      borderRadius: "50%",
                                      bgcolor:
                                        index === 0
                                          ? "primary.main"
                                          : "divider",
                                    }}
                                  />
                                  {index <
                                    Math.min(messages.length, 20) - 1 && (
                                    <Box
                                      sx={{
                                        width: 1,
                                        flex: 1,
                                        bgcolor: "divider",
                                      }}
                                    />
                                  )}
                                </Stack>
                                <Box sx={{ flex: 1, minWidth: 0 }}>
                                  <Stack
                                    direction="row"
                                    justifyContent="space-between"
                                    spacing={1}
                                  >
                                    <Typography variant="body2">
                                      {eventMessage(event)}
                                    </Typography>
                                    <Typography
                                      variant="caption"
                                      color="text.secondary"
                                      sx={{ whiteSpace: "nowrap" }}
                                    >
                                      {event.wall_time
                                        ? new Date(
                                            event.wall_time,
                                          ).toLocaleTimeString()
                                        : ""}
                                    </Typography>
                                  </Stack>
                                </Box>
                              </Stack>
                            ))}
                          {!messages.length && (
                            <Typography variant="body2" color="text.secondary">
                              Activity will appear when the runner starts.
                            </Typography>
                          )}
                        </Stack>
                      </Box>
                    </Stack>
                  )}
                </Box>
              </Box>
            </Box>
          )}
        </Box>
      </Box>
    </>
  );
}
