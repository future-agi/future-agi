import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import {
  Alert,
  Autocomplete,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  MenuItem,
  Paper,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import Iconify from "src/components/iconify";
import { useAgentDefinitions } from "src/components/run-tests/common";
import { useAgentDefinitionVersions } from "src/api/agent-definition/agent-definition-version";
import {
  harnessIdempotencyKey,
  storeHarnessSecretValues,
} from "src/api/harness/harness";
import {
  createHarnessAdaptation,
  createHarnessComposition,
  useHarnessReleases,
} from "src/api/harness/releases";
import { paths } from "src/routes/paths";

import {
  errorMessage,
  providerCredentialName,
  releaseUsePayload,
  shortDigest,
} from "./harnessShared";

const CONNECTORS = [
  { value: "retell_chat", label: "Retell chat" },
  { value: "retell", label: "Retell voice" },
  { value: "vapi", label: "Vapi" },
];

const originSummary = (origins = {}) =>
  ["specialized", "custom"]
    .filter((key) => origins?.[key])
    .map((key) => `${origins[key]} ${key}`)
    .join(" · ");

function AgentVersionPicker({ connector, agent, onAgent, version, onVersion }) {
  const {
    agentDefinitions = [],
    fetchNextPage,
    hasNextPage,
    isLoading,
  } = useAgentDefinitions(connector === "retell_chat" ? "text" : "voice");
  const versions = useAgentDefinitionVersions({ selectedAgentId: agent?.id });
  const versionOptions = useMemo(
    () => (versions.data?.pages || []).flatMap((page) => page?.results || []),
    [versions.data],
  );
  const loadMoreOnScroll = (load, more) => (event) => {
    const list = event.currentTarget;
    if (more && list.scrollTop + list.clientHeight >= list.scrollHeight - 8) {
      load();
    }
  };

  return (
    <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
      <Autocomplete
        fullWidth
        size="small"
        options={(agentDefinitions || []).filter(Boolean)}
        loading={isLoading}
        value={agent}
        onChange={(_, value) => {
          onAgent(value);
          onVersion(null);
        }}
        getOptionLabel={(option) => option?.agent_name || ""}
        isOptionEqualToValue={(option, value) => option?.id === value?.id}
        ListboxProps={{
          onScroll: loadMoreOnScroll(fetchNextPage, hasNextPage),
        }}
        renderInput={(params) => <TextField {...params} label="Agent" />}
      />
      <Autocomplete
        fullWidth
        size="small"
        disabled={!agent}
        options={versionOptions}
        loading={versions.isLoading && Boolean(agent)}
        value={version}
        onChange={(_, value) => onVersion(value)}
        getOptionLabel={(option) =>
          option?.version_name_display || option?.version_name || ""
        }
        isOptionEqualToValue={(option, value) => option?.id === value?.id}
        ListboxProps={{
          onScroll: loadMoreOnScroll(
            versions.fetchNextPage,
            versions.hasNextPage,
          ),
        }}
        renderInput={(params) => (
          <TextField {...params} label="Agent version" />
        )}
      />
    </Stack>
  );
}

AgentVersionPicker.propTypes = {
  connector: PropTypes.string.isRequired,
  agent: PropTypes.object,
  onAgent: PropTypes.func.isRequired,
  version: PropTypes.object,
  onVersion: PropTypes.func.isRequired,
};

export function UseReleaseDialog({ release, open, onClose }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const suites = release?.suites || [];
  const [suiteId, setSuiteId] = useState(suites[0]?.id || "");
  const [connector, setConnector] = useState("retell_chat");
  const [agent, setAgent] = useState(null);
  const [version, setVersion] = useState(null);
  const [apiKey, setApiKey] = useState("");
  const [targetId, setTargetId] = useState("");
  const [dynamicVariables, setDynamicVariables] = useState("");
  const [name, setName] = useState(release?.name || "");
  const [submitting, setSubmitting] = useState("");
  const [error, setError] = useState("");
  // One key per dialog, so a retried click cannot start a second adaptation.
  const [idempotencyKey] = useState(harnessIdempotencyKey);

  const keyName = providerCredentialName(connector);
  const ready =
    Boolean(suiteId && version?.id && apiKey.trim() && targetId.trim()) &&
    Boolean(name.trim()) &&
    !submitting;

  const submit = async (kind) => {
    setSubmitting(kind);
    setError("");
    try {
      const stored = await storeHarnessSecretValues({ [keyName]: apiKey });
      const payload = releaseUsePayload({
        suiteId,
        agentVersionId: version.id,
        name,
        connector,
        targetId,
        dynamicVariables,
        secretRefs: stored?.secret_refs || {},
        idempotencyKey: `${idempotencyKey}-${kind}`,
      });
      if (kind === "adapt") {
        const job = await createHarnessAdaptation(payload);
        queryClient.invalidateQueries({ queryKey: ["harness-jobs"] });
        navigate(paths.dashboard.simulate.harness.detail(job.job.job_id));
      } else {
        const composition = await createHarnessComposition(payload);
        navigate(paths.dashboard.simulate.environments.detail(composition.id));
      }
    } catch (requestError) {
      setError(errorMessage(requestError));
      setSubmitting("");
    }
  };

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>Use {release?.name} with my agent</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ pt: 1 }}>
          <TextField
            select
            size="small"
            label="Scenario suite"
            value={suiteId}
            onChange={(event) => setSuiteId(event.target.value)}
          >
            {suites.map((suite) => (
              <MenuItem key={suite.id} value={suite.id}>
                {suite.name} · {suite.scenario_count} scenarios
              </MenuItem>
            ))}
          </TextField>
          <TextField
            select
            size="small"
            label="Agent platform"
            value={connector}
            onChange={(event) => {
              setConnector(event.target.value);
              setAgent(null);
              setVersion(null);
            }}
          >
            {CONNECTORS.map((item) => (
              <MenuItem key={item.value} value={item.value}>
                {item.label}
              </MenuItem>
            ))}
          </TextField>
          <AgentVersionPicker
            connector={connector}
            agent={agent}
            onAgent={setAgent}
            version={version}
            onVersion={setVersion}
          />
          <TextField
            size="small"
            type="password"
            label={keyName}
            value={apiKey}
            onChange={(event) => setApiKey(event.target.value)}
            helperText="Used to inspect and clone the agent. Stored as a run-scoped secret; never written to the job or artifacts."
          />
          <TextField
            size="small"
            label={
              connector === "vapi"
                ? "Vapi assistant ID"
                : connector === "retell_chat"
                  ? "Retell chat agent ID"
                  : "Retell voice agent ID"
            }
            value={targetId}
            onChange={(event) => setTargetId(event.target.value)}
            helperText="ALK clones this agent for each run and points its tools at the environment."
          />
          {connector === "retell_chat" && (
            <TextField
              size="small"
              multiline
              minRows={2}
              label="Dynamic variables (JSON)"
              placeholder={'{"customer_name":"Jane Doe"}'}
              value={dynamicVariables}
              onChange={(event) => setDynamicVariables(event.target.value)}
            />
          )}
          <TextField
            size="small"
            label="Environment name"
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
          <Typography variant="caption" color="text.secondary">
            Adapting maps your agent&apos;s tools onto this environment, asks
            about your policies and proposes scenario changes for you to
            approve. Composing skips that and needs an agent that already calls
            the environment&apos;s own tools.
          </Typography>
          {error && (
            <Alert severity="error" variant="outlined">
              {error}
            </Alert>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button
          color="inherit"
          onClick={onClose}
          disabled={Boolean(submitting)}
        >
          Cancel
        </Button>
        <Button
          variant="outlined"
          disabled={!ready}
          onClick={() => submit("compose")}
          startIcon={
            submitting === "compose" ? <CircularProgress size={14} /> : null
          }
        >
          Compose without adaptation
        </Button>
        <Button
          variant="contained"
          disabled={!ready}
          onClick={() => submit("adapt")}
          startIcon={
            submitting === "adapt" ? (
              <CircularProgress size={14} color="inherit" />
            ) : null
          }
        >
          Adapt to my agent
        </Button>
      </DialogActions>
    </Dialog>
  );
}

UseReleaseDialog.propTypes = {
  release: PropTypes.object,
  open: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
};

function ReleaseCard({ release, onUse }) {
  const tools = release.tools || [];
  const parameters = release.policy?.parameters || [];
  const rules = release.policy?.agent_rules || [];
  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Stack spacing={1}>
        <Stack direction="row" alignItems="center" spacing={1}>
          <Typography variant="subtitle1" sx={{ flex: 1 }} noWrap>
            {release.name}
          </Typography>
          {release.system && <Chip size="small" label="Future AGI" />}
          {release.parent && (
            <Chip size="small" variant="outlined" label="Extension" />
          )}
        </Stack>
        <Typography variant="caption" color="text.secondary">
          {tools.length} tools · {parameters.length} policy parameters ·{" "}
          {rules.length} agent rules · {shortDigest(release.digest)}
        </Typography>
        <Stack spacing={0.25}>
          {(release.suites || []).map((suite) => (
            <Typography key={suite.id} variant="body2">
              {suite.name}{" "}
              <Typography
                component="span"
                variant="caption"
                color="text.secondary"
              >
                {suite.scenario_count} scenarios
                {originSummary(suite.origins)
                  ? ` · ${originSummary(suite.origins)}`
                  : ""}
              </Typography>
            </Typography>
          ))}
        </Stack>
        <Box>
          <Button
            size="small"
            variant="contained"
            disabled={!release.suites?.length}
            onClick={() => onUse(release)}
            startIcon={<Iconify icon="solar:link-circle-linear" width={16} />}
          >
            Use with my agent
          </Button>
        </Box>
      </Stack>
    </Paper>
  );
}

ReleaseCard.propTypes = {
  release: PropTypes.object.isRequired,
  onUse: PropTypes.func.isRequired,
};

// Released environments: proved worlds and suites a hosted agent can run against without
// authoring a new environment.
export default function ReleaseGallery() {
  const { data: releases = [], error } = useHarnessReleases();
  const [using, setUsing] = useState(null);

  if (error) {
    return (
      <Alert severity="warning" variant="outlined">
        Prebuilt environments are unavailable: {errorMessage(error)}
      </Alert>
    );
  }
  if (!releases.length) return null;

  return (
    <Box>
      <Typography variant="subtitle1">Prebuilt environments</Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
        Proved worlds and scenario suites. Run one against your hosted agent
        instead of building a new environment.
      </Typography>
      <Box
        sx={{
          display: "grid",
          gap: 1.5,
          gridTemplateColumns: {
            xs: "minmax(0, 1fr)",
            md: "repeat(2, minmax(0, 1fr))",
            xl: "repeat(3, minmax(0, 1fr))",
          },
        }}
      >
        {releases.map((release) => (
          <ReleaseCard key={release.id} release={release} onUse={setUsing} />
        ))}
      </Box>
      {using && (
        <UseReleaseDialog open release={using} onClose={() => setUsing(null)} />
      )}
    </Box>
  );
}
