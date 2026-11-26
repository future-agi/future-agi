import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import {
  Alert,
  Autocomplete,
  Box,
  Button,
  Chip,
  MenuItem,
  Paper,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import ContentSkeleton from "src/components/content-skeleton";
import ErrorState from "src/components/error-state";
import Iconify from "src/components/iconify";
import ModalWrapper from "src/components/ModalWrapper/ModalWrapper";
import { useConnectedAgents } from "src/api/agents/connectedAgents";
import { useAgentDefinitionVersions } from "src/api/agent-definition/agent-definition-version";
import { harnessIdempotencyKey } from "src/api/harness/harness";
import {
  createHarnessAdaptation,
  createHarnessHostedAgent,
  useHarnessReleases,
} from "src/api/harness/releases";
import { paths } from "src/routes/paths";

import { errorMessage, shortDigest } from "./harnessShared";

// The agents an adaptation takes, as the agents list filters them.
const PLATFORMS = [
  {
    value: "retell_chat",
    label: "Retell chat",
    filters: { source: ["retell"], agent_type: ["text"] },
  },
  {
    value: "retell",
    label: "Retell voice",
    filters: { source: ["retell"], agent_type: ["voice"] },
  },
  {
    value: "vapi",
    label: "Vapi",
    filters: { source: ["vapi"], agent_type: ["voice"] },
  },
];
const AGENTS_LIMIT = 100;

// Gateway names: the `vertex_ai/` prefix keeps the call on Vertex (the platform's own key).
const HOSTED_AGENT_MODELS = [
  { value: "vertex_ai/gemini-3.7-flash", label: "Gemini 3.7 Flash" },
  { value: "vertex_ai/gemini-3.8-flash", label: "Gemini 3.8 Flash" },
];

const originSummary = (origins = {}) =>
  ["specialized", "custom"]
    .filter((key) => origins?.[key])
    .map((key) => `${origins[key]} ${key}`)
    .join(" · ");

function AgentVersionPicker({ platform, agent, onAgent, version, onVersion }) {
  const { data, isFetching, isPlaceholderData } = useConnectedAgents({
    page: 0,
    pageSize: AGENTS_LIMIT,
    filters: PLATFORMS.find((one) => one.value === platform).filters,
  });
  // Until this platform's agents are read, the list still holds the last platform's.
  const agents = isPlaceholderData ? [] : data?.items || [];
  const versions = useAgentDefinitionVersions({ selectedAgentId: agent?.id });
  const versionOptions = useMemo(
    () => (versions.data?.pages || []).flatMap((page) => page?.results || []),
    [versions.data],
  );
  const loadMoreOnScroll = (event) => {
    const list = event.currentTarget;
    if (
      versions.hasNextPage &&
      list.scrollTop + list.clientHeight >= list.scrollHeight - 8
    ) {
      versions.fetchNextPage();
    }
  };

  return (
    <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
      <Autocomplete
        fullWidth
        size="small"
        options={agents}
        loading={isFetching}
        value={agent}
        onChange={(_, value) => {
          onAgent(value);
          onVersion(null);
        }}
        getOptionLabel={(option) => option?.name || ""}
        isOptionEqualToValue={(option, value) => option?.id === value?.id}
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
        ListboxProps={{ onScroll: loadMoreOnScroll }}
        renderInput={(params) => (
          <TextField {...params} label="Agent version" />
        )}
      />
    </Stack>
  );
}

AgentVersionPicker.propTypes = {
  platform: PropTypes.string.isRequired,
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
  const [platform, setPlatform] = useState(PLATFORMS[0].value);
  const [agent, setAgent] = useState(null);
  const [version, setVersion] = useState(null);
  const [name, setName] = useState(release?.name || "");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  // One key per dialog, so a retried click cannot start a second adaptation. A key already spent
  // on another choice is replaced.
  const [idempotencyKey, setIdempotencyKey] = useState(harnessIdempotencyKey);

  const ready = Boolean(suiteId && version?.id && name.trim());

  const submit = async () => {
    setSubmitting(true);
    setError("");
    try {
      // The agent as connected on the Agents page: its provider, agent id and stored key are
      // read from the version on the server, so nothing is typed in again here.
      const job = await createHarnessAdaptation({
        suite_id: suiteId,
        agent_version_id: version.id,
        name: name.trim(),
        idempotency_key: idempotencyKey,
      });
      queryClient.invalidateQueries({ queryKey: ["harness-jobs"] });
      navigate(paths.dashboard.simulate.environments.detail(job.job.job_id));
    } catch (requestError) {
      if (requestError?.error === "idempotency_conflict")
        setIdempotencyKey(harnessIdempotencyKey());
      setError(errorMessage(requestError));
      setSubmitting(false);
    }
  };

  return (
    <ModalWrapper
      open={open}
      onClose={onClose}
      title={`Use ${release?.name} with my agent`}
      actionBtnTitle="Adapt to my agent"
      isValid={ready}
      isLoading={submitting}
      onSubmit={submit}
    >
      <Stack spacing={2}>
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
          value={platform}
          onChange={(event) => {
            setPlatform(event.target.value);
            setAgent(null);
            setVersion(null);
          }}
        >
          {PLATFORMS.map((item) => (
            <MenuItem key={item.value} value={item.value}>
              {item.label}
            </MenuItem>
          ))}
        </TextField>
        <AgentVersionPicker
          platform={platform}
          agent={agent}
          onAgent={setAgent}
          version={version}
          onVersion={setVersion}
        />
        <TextField
          size="small"
          label="Environment name"
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
        <Typography variant="caption" color="text.secondary">
          Adapting reads your agent as connected on the Agents page, maps its
          tools onto this environment and proves the mapping on every scenario,
          then asks you to approve it. The scenario suite stays as released.
        </Typography>
        {error && (
          <Alert severity="error" variant="outlined">
            {error}
          </Alert>
        )}
      </Stack>
    </ModalWrapper>
  );
}

UseReleaseDialog.propTypes = {
  release: PropTypes.object,
  open: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
};

// Our own agent: instructions and a model, reaching the environment's tools over MCP. The
// platform binds it directly, so the environment is ready to run as soon as it is created.
export function HostedAgentDialog({ release, open, onClose }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const suites = release?.suites || [];
  const [suiteId, setSuiteId] = useState(suites[0]?.id || "");
  const [name, setName] = useState(`${release?.name || "Agent"} (our agent)`);
  const [instructions, setInstructions] = useState("");
  const [model, setModel] = useState(HOSTED_AGENT_MODELS[0].value);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [idempotencyKey, setIdempotencyKey] = useState(harnessIdempotencyKey);
  const ready = Boolean(suiteId && name.trim() && instructions.trim());

  const submit = async () => {
    setSubmitting(true);
    setError("");
    try {
      const created = await createHarnessHostedAgent({
        suite_id: suiteId,
        name: name.trim(),
        instructions: instructions.trim(),
        model,
        idempotency_key: idempotencyKey,
      });
      queryClient.invalidateQueries({ queryKey: ["harness-jobs"] });
      navigate(paths.dashboard.simulate.environments.detail(created.id));
    } catch (requestError) {
      if (requestError?.error === "idempotency_conflict")
        setIdempotencyKey(harnessIdempotencyKey());
      setError(errorMessage(requestError));
      setSubmitting(false);
    }
  };

  return (
    <ModalWrapper
      open={open}
      onClose={onClose}
      title={`Run our agent on ${release?.name}`}
      actionBtnTitle="Create environment"
      isValid={ready}
      isLoading={submitting}
      onSubmit={submit}
    >
      <Stack spacing={2}>
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
          size="small"
          multiline
          minRows={6}
          label="Instructions"
          placeholder="You are the front desk of …. Use your tools to look things up and make changes."
          value={instructions}
          onChange={(event) => setInstructions(event.target.value)}
        />
        <TextField
          select
          size="small"
          label="Model"
          value={model}
          onChange={(event) => setModel(event.target.value)}
        >
          {HOSTED_AGENT_MODELS.map((item) => (
            <MenuItem key={item.value} value={item.value}>
              {item.label}
            </MenuItem>
          ))}
        </TextField>
        <TextField
          size="small"
          label="Environment name"
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
        <Typography variant="caption" color="text.secondary">
          Our agent runs in the harness and reaches this environment&apos;s
          tools over MCP, so there is nothing to connect or adapt. Its model
          usage counts towards the simulation.
        </Typography>
        {error && (
          <Alert severity="error" variant="outlined">
            {error}
          </Alert>
        )}
      </Stack>
    </ModalWrapper>
  );
}

HostedAgentDialog.propTypes = {
  release: PropTypes.object,
  open: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
};

function ReleaseCard({ release, onUse, onHosted }) {
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
        <Stack direction="row" spacing={1}>
          <Button
            size="small"
            variant="contained"
            disabled={!release.suites?.length}
            onClick={() => onUse(release)}
            startIcon={<Iconify icon="solar:link-circle-linear" width={16} />}
          >
            Use with my agent
          </Button>
          <Button
            size="small"
            variant="outlined"
            disabled={!release.suites?.length}
            onClick={() => onHosted(release)}
            startIcon={<Iconify icon="solar:cpu-bolt-linear" width={16} />}
          >
            Run our agent
          </Button>
        </Stack>
      </Stack>
    </Paper>
  );
}

ReleaseCard.propTypes = {
  release: PropTypes.object.isRequired,
  onUse: PropTypes.func.isRequired,
  onHosted: PropTypes.func.isRequired,
};

// Released environments: proved worlds and suites a hosted agent can run against without
// authoring a new environment. `empty` renders once the list has loaded with nothing in it.
export default function ReleaseGallery({ empty = null }) {
  const { data, error, isLoading, refetch } = useHarnessReleases();
  const releases = data || [];
  const [using, setUsing] = useState(null);
  const [hosting, setHosting] = useState(null);

  if (isLoading) {
    return (
      <ContentSkeleton variant="page" label="Loading prebuilt environments" />
    );
  }
  if (error && !data) {
    return (
      <ErrorState
        title="Couldn't load the prebuilt environments"
        detail={errorMessage(error)}
        onRetry={() => refetch()}
      />
    );
  }
  if (!releases.length) return empty;

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
          <ReleaseCard
            key={release.id}
            release={release}
            onUse={setUsing}
            onHosted={setHosting}
          />
        ))}
      </Box>
      {using && (
        <UseReleaseDialog open release={using} onClose={() => setUsing(null)} />
      )}
      {hosting && (
        <HostedAgentDialog
          open
          release={hosting}
          onClose={() => setHosting(null)}
        />
      )}
    </Box>
  );
}

ReleaseGallery.propTypes = { empty: PropTypes.node };
