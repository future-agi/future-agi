import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import {
  Alert,
  Box,
  Button,
  ButtonBase,
  Chip,
  CircularProgress,
  Collapse,
  IconButton,
  InputAdornment,
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
  uploadHarnessSecretFile,
  uploadHarnessSource,
} from "src/api/harness/harness";
import { paths } from "src/routes/paths";

import { parseDotEnv } from "./dotenv";
import {
  credentialValue,
  mergePastedCredentials,
  updateCredential,
} from "./credentialValues";
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
  // A changed input does not invalidate what preflight already told us — it just means the
  // answer may be out of date. Hiding the panel loses the findings the user was reading.
  const [preflightDirty, setPreflightDirty] = useState(false);
  const [showDetected, setShowDetected] = useState(false);
  const [revealedSecrets, setRevealedSecrets] = useState(() => new Set());
  // A credential FILE cannot travel as an environment value. It is uploaded on its own and
  // referenced by an opaque handle, so the contents never reach a job body, log or artifact.
  const [secretFileRefs, setSecretFileRefs] = useState({});
  const [secretFileUploads, setSecretFileUploads] = useState({});
  const [uploadingSecretFile, setUploadingSecretFile] = useState(false);
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
    setPreflightDirty(Boolean(preflight));
  };

  // The contract wants "org/repo" with the branch in a separate ref, but the
  // field invites a pasted GitHub URL — accept both, lift the branch out of a
  // /tree/ (or /blob/, /commit/) link, and validate against mirrors of the
  // backend patterns so bad input fails HERE with a usable message instead of
  // an opaque server 400.
  const REPOSITORY_PATTERN = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;
  const REF_PATTERN = /^[A-Za-z0-9._/-]+$/;

  const parseGithubSource = (raw) => {
    const value = raw
      .trim()
      .split(/[?#]/)[0]
      .replace(/\/+$/, "")
      .replace(/\.git$/, "");
    const match = value.match(
      /^(?:https?:\/\/)?(?:www\.)?github\.com\/([^/]+\/[^/]+)(?:\/(?:tree|blob|commit)\/(.+))?$/i,
    );
    const repository = (match ? match[1] : value).replace(/\.git$/, "");
    const ref = match ? match[2] || undefined : undefined;
    if (!REPOSITORY_PATTERN.test(repository) || (ref && !REF_PATTERN.test(ref))) {
      return {
        error:
          "Enter the repository as org/repo, or paste a github.com URL (optionally with /tree/<branch>).",
      };
    }
    return { repository, ref };
  };

  const sourcePayload = () => {
    if (sourceMode === "upload")
      return { kind: "archive", archive_artifact_id: uploadedSource?.source_id };
    const parsed = parseGithubSource(githubRepository);
    if (parsed.error) throw new Error(parsed.error);
    return {
      kind: "github",
      repository: parsed.repository,
      ref: parsed.ref,
      visibility: githubVisibility,
      installation_id:
        githubVisibility === "private"
          ? githubInstallationId.trim() || undefined
          : undefined,
    };
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
      // A pasted path and an uploaded file are mutually exclusive for the same variable.
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
      setPreflightDirty(Boolean(preflight));
    } catch (requestError) {
      setEnvironmentError(errorMessage(requestError));
    } finally {
      setUploadingSecretFile(false);
    }
  };

  // The backend speaks the nested futureagi.harness-job.v1 contract: flat
  // fields are rejected as unknown. Pasted env values travel in the write-only
  // agent.environment_values channel (stripped into encrypted credential
  // storage server-side, never persisted on the job) — config carries only
  // the non-secret connector settings.
  const checkPayload = () => ({
    schema_version: "futureagi.harness-job.v1",
    source: sourcePayload(),
    agent: {
      // The real-voice call runner activates only on the explicit livekit
      // connector; "auto" keeps the typed not-wired fallback.
      connector: "livekit",
      config: { ...configurationValues },
      secret_refs: secretFileRefs,
      environment_values: environmentValues,
    },
    artifacts: { level: "traces" },
  });

  const inspect = async () => {
    setChecking(true);
    setError("");
    try {
      const value = await preflightHarnessJob(checkPayload());
      setPreflight(value);
      setPreflightDirty(false);
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
      const checked = await preflightHarnessJob(checkPayload());
      setPreflight(checked);
      setPreflightDirty(false);
      if (!checked?.ready_to_submit) {
        setError(
          "The runner is not ready to start this run. Review the readiness panel below.",
        );
        setSubmitting(false);
        return;
      }
      const value = await createHarnessJob({
        ...checkPayload(),
        scenario_count: Number(scenarioCount),
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
      // Merge, never replace: a paste should not silently discard values typed by hand.
      const merged = mergePastedCredentials(
        environmentValues,
        configurationValues,
        values,
      );
      setEnvironmentValues(merged.environmentValues);
      setConfigurationValues(merged.configurationValues);
      setEnvironmentError("");
      setPreflightDirty(Boolean(preflight));
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
    setPreflightDirty(Boolean(preflight));
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
    if (secretFileRefs[name]) return true;
    return Boolean(
      String(
        credentialValue(environmentValues, configurationValues, name) || "",
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
  const requirementsConfigured =
    missingRequirements.every((item) =>
      requirementConfigured(item.environment_name),
    ) && unsatisfiedChoices.length === 0;
  const requiredInputCount =
    missingRequirements.length + unsatisfiedChoices.length;
  // Only "missing" rows take a value; everything else is read-only detail that
  // would otherwise bury them at equal visual weight.
  const requirementsNeedingValue = requirements.filter(
    (item) => item.status === "missing",
  );
  const detectedRequirements = requirements.filter(
    (item) => item.status !== "missing",
  );
  const toggleSecret = (name) =>
    setRevealedSecrets((current) => {
      const next = new Set(current);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });

  // Blocking and optional variables get the same row, so an optional one can be
  // set without hunting for somewhere else to put it.
  const renderCredentialRow = (item, index) => {
    const isSecret = item.kind === "secret";
    const revealed = revealedSecrets.has(item.environment_name);
    return (
      <Stack
        key={item.id}
        direction={{ xs: "column", md: "row" }}
        spacing={{ xs: 1, md: 3 }}
        alignItems={{ md: "center" }}
        sx={{
          p: 1.5,
          borderTop: index === 0 ? 0 : "1px solid",
          borderColor: "divider",
        }}
      >
        <Box sx={{ width: { md: 280 }, flexShrink: 0 }}>
          <Typography
            variant="body2"
            fontWeight={600}
            sx={{ fontFamily: "monospace", wordBreak: "break-all" }}
          >
            {item.environment_name}
          </Typography>
          <Typography variant="caption" color="text.secondary">
            {item.provider} · {item.purpose}
          </Typography>
        </Box>
        <TextField
          fullWidth
          size="small"
          placeholder={
            item.status === "missing"
              ? isSecret
                ? "Paste secret"
                : "Enter value"
              : readable(item.status)
          }
          type={isSecret && !revealed ? "password" : "text"}
          // One logical value per variable. Reading and writing through the
          // helpers keeps it out of both maps at once, which is what left a
          // stale entry showing after a paste.
          value={credentialValue(
            environmentValues,
            configurationValues,
            item.environment_name,
          )}
          onChange={(event) => {
            const next = updateCredential(
              environmentValues,
              configurationValues,
              {
                name: item.environment_name,
                value: event.target.value,
                kind: item.kind,
              },
            );
            setEnvironmentValues(next.environmentValues);
            setConfigurationValues(next.configurationValues);
            setPreflightDirty(Boolean(preflight));
          }}
          InputProps={
            isSecret
              ? {
                  endAdornment: (
                    <InputAdornment position="end">
                      <IconButton
                        size="small"
                        edge="end"
                        onClick={() => toggleSecret(item.environment_name)}
                        aria-label={`${revealed ? "Hide" : "Show"} ${item.environment_name}`}
                      >
                        <Iconify
                          icon={
                            revealed
                              ? "solar:eye-closed-linear"
                              : "solar:eye-linear"
                          }
                          width={16}
                        />
                      </IconButton>
                    </InputAdornment>
                  ),
                }
              : undefined
          }
        />
      </Stack>
    );
  };

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
                          setPreflightDirty(Boolean(preflight));
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
                            setPreflightDirty(Boolean(preflight));
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

              <Paper variant="outlined" sx={{ p: 2 }}>
                <Stack
                  direction="row"
                  spacing={1.5}
                  alignItems="center"
                  flexWrap="wrap"
                  useFlexGap
                >
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
                    sx={{ flexShrink: 0 }}
                  >
                    {preflight ? "Check again" : "Preflight"}
                  </Button>
                  <Typography variant="caption" color="text.secondary">
                    {hasSource
                      ? "Check the agent's requirements before entering values."
                      : "Select an agent source to check its requirements."}
                  </Typography>
                </Stack>

                {preflight && (
                  <Stack spacing={1.5} sx={{ mt: 2 }}>
                    <Stack direction="row" spacing={1} alignItems="center">
                      <StatusChip
                        status={
                          // Stale beats ready: a result from before the last edit should not
                          // claim the run is good to go.
                          preflightDirty
                            ? null
                            : requirementsConfigured
                              ? STATUS_TYPES.PASS
                              : STATUS_TYPES.RUNNING
                        }
                        label={
                          preflightDirty
                            ? "Something changed — check again"
                            : requirementsConfigured
                              ? "Ready to run"
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
                    {(preflight.packaging?.notes || []).map((note) => (
                      <Alert key={note} severity="warning" variant="outlined">
                        {note}
                      </Alert>
                    ))}
                    {(preflight.packaging?.candidates || [])
                      .flatMap((candidate) =>
                        (candidate.findings || [])
                          .filter((finding) => finding.blocking)
                          .map((finding) => ({
                            ...finding,
                            path: candidate.path,
                          })),
                      )
                      .map((finding) => (
                        <Alert
                          key={`${finding.path}-${finding.code}`}
                          severity="error"
                          variant="outlined"
                        >
                          {finding.path}: {finding.message}
                        </Alert>
                      ))}
                    {preflight.packaging?.selected_path && (
                      <Alert severity="success" variant="outlined">
                        Will package {preflight.packaging.selected_path}
                      </Alert>
                    )}
                    {unsatisfiedChoices.map((choice) => (
                      <Alert key={choice.id} severity="info" variant="outlined">
                        {choice.purpose}: choose{" "}
                        {choice.options
                          .map((option) => option.join(" + "))
                          .join(" or ")}
                      </Alert>
                    ))}
                    {requirementsNeedingValue.length > 0 && (
                      <Box>
                        <Stack
                          direction="row"
                          spacing={1}
                          alignItems="baseline"
                          flexWrap="wrap"
                          useFlexGap
                          sx={{ mb: 1 }}
                        >
                          <Typography variant="subtitle2">
                            Needs a value
                          </Typography>
                          <Typography variant="caption" color="text.secondary">
                            Used for this run only, then discarded.
                          </Typography>
                        </Stack>

                        <Paper variant="outlined">
                          {requirementsNeedingValue.map(renderCredentialRow)}
                        </Paper>
                      </Box>
                    )}

                    {detectedRequirements.length > 0 && (
                      <Box>
                        <Button
                          size="small"
                          onClick={() => setShowDetected((open) => !open)}
                          startIcon={
                            <Iconify
                              icon={
                                showDetected
                                  ? "solar:alt-arrow-down-linear"
                                  : "solar:alt-arrow-right-linear"
                              }
                              width={16}
                            />
                          }
                          sx={{
                            px: 0.5,
                            fontWeight: 400,
                            color: "text.secondary",
                          }}
                        >
                          {detectedRequirements.length} optional or provided —
                          set them if you want to override a default
                        </Button>
                        <Collapse in={showDetected}>
                          <Paper variant="outlined" sx={{ mt: 1 }}>
                            {detectedRequirements.map(renderCredentialRow)}
                          </Paper>
                        </Collapse>
                      </Box>
                    )}
                  </Stack>
                )}
              </Paper>

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
                    <Button
                      component="label"
                      variant="outlined"
                      disabled={uploadingSecretFile}
                      startIcon={
                        uploadingSecretFile ? (
                          <CircularProgress size={16} />
                        ) : (
                          <Iconify icon="solar:upload-minimalistic-linear" />
                        )
                      }
                      sx={{ flexShrink: 0, whiteSpace: "nowrap" }}
                    >
                      Upload credential file
                      <Box
                        component="input"
                        type="file"
                        accept="application/json,.json"
                        onChange={(event) =>
                          uploadCredentialFile(
                            "GOOGLE_APPLICATION_CREDENTIALS",
                            event.target.files?.[0],
                          )
                        }
                        sx={{ display: "none" }}
                      />
                    </Button>
                    {Object.keys(environmentValues).length > 0 && (
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
                  {Object.entries(secretFileUploads).map(([name, file]) => (
                    <Alert key={name} severity="success" variant="outlined">
                      {name}: {file.name} uploaded · mounted per run, never
                      written to the job
                    </Alert>
                  ))}
                  {environmentError && (
                    <Alert severity="error" variant="outlined">
                      {environmentError}
                    </Alert>
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

              {error && (
                <Alert severity="error" variant="outlined">
                  {error}
                </Alert>
              )}

              <Stack direction="row" spacing={1.5} sx={{ pb: 4 }}>
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
