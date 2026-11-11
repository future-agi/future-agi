import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useSnackbar } from "notistack";
import {
  Alert,
  Autocomplete,
  Box,
  Button,
  CircularProgress,
  MenuItem,
  Select,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { paths } from "src/routes/paths";
import { useDebounce } from "src/hooks/use-debounce";
import {
  useGitHubConnection,
  useSourceBranches,
  useSourceRepositories,
} from "src/api/integrations";
import {
  useAgentScan,
  useConnectAgent,
  useStartScan,
} from "src/api/agents/agentConnect";
import { errorMessage } from "src/pages/dashboard/harness/harnessShared";
import { parseGitHubInput } from "src/pages/dashboard/harness/requestMapper";
import { GITHUB_INSTALL_OUTCOMES } from "src/sections/settings/integrations/constants";
import Field from "src/sections/simulate/environments/components/Field";
import { CONNECT_AGENT_PATH, CONNECTED_AGENTS_PATH } from "../agentsTabs";
import { GITHUB_SOURCE } from "./connectSources";
import { AgentDetails, FoundAgents, ManualAgent } from "./ConnectAgentSteps";
import { githubSource } from "./githubSource";
import {
  clearConnectDraft,
  draftHasProgress,
  readConnectDraft,
  saveConnectDraft,
} from "./connectDraft";
import { LogoTile, OptionCard, TextLink } from "./connectBits";
import { MONO } from "./connectStyles";

const INSTALL_PATH = `${paths.dashboard.settings.integrations}?${new URLSearchParams(
  {
    platform: GITHUB_SOURCE,
    return_to: `${CONNECT_AGENT_PATH}?source=${GITHUB_SOURCE}`,
  },
)}`;

const keepAll = (options) => options;

function InstallApp({ onInstall, onPaste, canInstall }) {
  return (
    <Box>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{
          p: 1.75,
          borderRadius: "8px",
          border: "1px solid",
          borderColor: "divider",
        }}
      >
        <LogoTile icon="simple-icons:github" size={32} />
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s1", fontWeight: 600 }}>
            Install the GitHub App to pick the agent
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.secondary" }}>
            Read only. We find the agents in the repositories you choose.
          </Typography>
        </Box>
        <CustomTooltip
          show={!canInstall}
          title="You don't have permission to connect GitHub."
          placement="top"
          arrow
          size="small"
        >
          <span>
            <Button
              size="small"
              variant="outlined"
              color="inherit"
              disabled={!canInstall}
              onClick={onInstall}
              startIcon={<Iconify icon="simple-icons:github" width={14} />}
              sx={{ flexShrink: 0, borderColor: "divider" }}
            >
              Install GitHub App
            </Button>
          </span>
        </CustomTooltip>
      </Stack>
      <TextLink onClick={onPaste} sx={{ mt: 0.75 }}>
        Can&apos;t install the app? Paste the repository link instead
      </TextLink>
    </Box>
  );
}
InstallApp.propTypes = {
  onInstall: PropTypes.func.isRequired,
  onPaste: PropTypes.func.isRequired,
  canInstall: PropTypes.bool.isRequired,
};

function PickFromGitHub({
  connection,
  repository,
  onRepository,
  branch,
  onBranch,
  onInstall,
}) {
  const [query, setQuery] = useState("");
  const [changingBranch, setChangingBranch] = useState(false);
  const search = useDebounce(repository ? "" : query.trim(), 300);
  const repositories = useSourceRepositories(connection.id, search);
  const branches = useSourceBranches(connection.id, repository?.id);

  return (
    <Stack spacing={1.5}>
      <Box>
        <Stack
          direction="row"
          alignItems="center"
          spacing={0.75}
          sx={{ mb: 0.5 }}
        >
          <Typography sx={{ typography: "s3", fontWeight: 600 }}>
            Repository
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.secondary" }}>
            {`· from GitHub (${connection.display_name})`}
          </Typography>
          <Box flex={1} />
          {onInstall && (
            <TextLink onClick={onInstall}>
              Missing a repository or an account? Add it on GitHub
            </TextLink>
          )}
        </Stack>
        <Autocomplete
          size="small"
          options={repositories.data || []}
          value={repository}
          onChange={(_event, picked) => {
            onRepository(picked);
            setChangingBranch(false);
          }}
          inputValue={query}
          onInputChange={(_event, next) => setQuery(next)}
          filterOptions={keepAll}
          getOptionLabel={(option) => option.full_name}
          isOptionEqualToValue={(option, value) => option.id === value.id}
          loading={repositories.isFetching}
          noOptionsText={
            repositories.isError
              ? "Couldn't load repositories from GitHub. Try again."
              : "No repository the GitHub App can read matches."
          }
          renderOption={(props, option) => (
            <Box component="li" {...props} key={option.id} sx={{ gap: 1 }}>
              <Box
                component="span"
                sx={{
                  fontFamily: MONO,
                  typography: "s1",
                  flex: 1,
                  minWidth: 0,
                }}
              >
                {option.full_name}
              </Box>
              <Box
                component="span"
                sx={{ typography: "s3", color: "text.secondary" }}
              >
                {option.private ? "Private" : "Public"}
              </Box>
            </Box>
          )}
          renderInput={(params) => (
            <TextField
              {...params}
              placeholder="Choose a repository"
              sx={{
                "& .MuiInputBase-input": { typography: "s1", fontFamily: MONO },
              }}
            />
          )}
        />
      </Box>
      {repository &&
        (changingBranch ? (
          <Box sx={{ maxWidth: 320 }}>
            <Select
              size="small"
              fullWidth
              value={branch}
              onChange={(event) => {
                onBranch(event.target.value);
                setChangingBranch(false);
              }}
              renderValue={(value) => (
                <Box component="span" sx={{ fontFamily: MONO }}>
                  {value}
                </Box>
              )}
              sx={{ typography: "s1" }}
            >
              {branches.isFetching && (
                <MenuItem disabled value="">
                  <CircularProgress size={14} sx={{ mr: 1 }} />
                  Loading branches
                </MenuItem>
              )}
              {(branches.data || []).map((item) => (
                <MenuItem
                  key={item.name}
                  value={item.name}
                  sx={{ typography: "s1", gap: 1 }}
                >
                  <Box component="span" sx={{ fontFamily: MONO, flex: 1 }}>
                    {item.name}
                  </Box>
                  {item.is_default && (
                    <Box
                      component="span"
                      sx={{ typography: "s3", color: "text.secondary" }}
                    >
                      default
                    </Box>
                  )}
                </MenuItem>
              ))}
            </Select>
          </Box>
        ) : (
          <Typography
            component="div"
            sx={{ typography: "s3", color: "text.secondary" }}
          >
            {"Reads "}
            <Box
              component="span"
              sx={{ fontFamily: MONO, color: "text.primary" }}
            >
              {branch}
            </Box>
            {". A fix merged there counts as released. "}
            <TextLink onClick={() => setChangingBranch(true)}>Change</TextLink>
          </Typography>
        ))}
    </Stack>
  );
}
PickFromGitHub.propTypes = {
  connection: PropTypes.shape({
    id: PropTypes.string.isRequired,
    display_name: PropTypes.string,
  }).isRequired,
  repository: PropTypes.object,
  onRepository: PropTypes.func.isRequired,
  branch: PropTypes.string,
  onBranch: PropTypes.func.isRequired,
  onInstall: PropTypes.func,
};

function PasteLink({ link, onLink, branch, onBranch, onPick }) {
  return (
    <Stack spacing={1.5}>
      <Field
        label="Repository link"
        required
        mono
        placeholder="https://github.com/owner/repo"
        value={link}
        onChange={onLink}
        error={
          link.trim() && !parseGitHubInput(link)
            ? "Enter a GitHub link or owner/repo."
            : ""
        }
        helper="Public repositories only. Private ones need the GitHub App."
      />
      <Box sx={{ maxWidth: 320 }}>
        <Field
          label="Branch or tag"
          mono
          placeholder="Default branch"
          value={branch}
          onChange={onBranch}
        />
      </Box>
      <TextLink onClick={onPick}>Pick from GitHub instead</TextLink>
    </Stack>
  );
}
PasteLink.propTypes = {
  link: PropTypes.string.isRequired,
  onLink: PropTypes.func.isRequired,
  branch: PropTypes.string.isRequired,
  onBranch: PropTypes.func.isRequired,
  onPick: PropTypes.func.isRequired,
};

const defaultName = (picked, source) =>
  picked?.name ||
  (source?.repository ? source.repository.split("/").pop() : "") ||
  "";

/**
 * Connect a GitHub agent: pick the repository and branch (or paste a link),
 * find the agents in it, pick one, give it a name and its values, connect. The
 * agent page then shows it being read.
 */
export default function GitHubConnectFlow({ outcome }) {
  const navigate = useNavigate();
  const { enqueueSnackbar } = useSnackbar();
  const github = useGitHubConnection({ refetchOnWindowFocus: true });
  const connection = github.data;
  const [draft] = useState(() => readConnectDraft() || {});
  const [mode, setMode] = useState(draft.mode || "app");
  const [repository, setRepository] = useState(draft.repository || null);
  const [branch, setBranch] = useState(draft.branch || "");
  const [link, setLink] = useState(draft.link || "");
  const [linkBranch, setLinkBranch] = useState(draft.linkBranch || "");
  // "source": pick where the code is. "agents": the scan's list. "details":
  // name and values of the one picked.
  const [stage, setStage] = useState(
    draft.scanId ? draft.stage || "source" : "source",
  );
  const [scanId, setScanId] = useState(draft.scanId || null);
  const [picked, setPicked] = useState(draft.picked || null);
  const [manual, setManual] = useState(!!draft.manual);
  const [subfolder, setSubfolder] = useState(draft.subfolder || "");
  const [name, setName] = useState(draft.name || "");
  const [values, setValues] = useState({});

  useEffect(() => {
    const next = {
      mode,
      repository,
      branch,
      link,
      linkBranch,
      stage,
      scanId,
      picked,
      manual,
      subfolder,
      name,
    };
    if (draftHasProgress(next)) {
      saveConnectDraft(next);
    } else {
      clearConnectDraft();
    }
  }, [
    mode,
    repository,
    branch,
    link,
    linkBranch,
    stage,
    scanId,
    picked,
    manual,
    subfolder,
    name,
  ]);
  const startScan = useStartScan();
  const scan = useAgentScan(scanId);
  const connect = useConnectAgent();
  const message = GITHUB_INSTALL_OUTCOMES[outcome];
  const { role } = useAuthContext();
  const canInstall =
    !!RolePermission.SIMULATION_AGENT[PERMISSIONS.CREATE][role];
  const canConnect = canInstall;
  const install = () => navigate(INSTALL_PATH);

  const pickRepository = (next) => {
    setRepository(next);
    setBranch(next?.default_branch || "");
  };
  const source = githubSource({
    mode,
    connection,
    repository,
    branch,
    link,
    linkBranch,
  });

  const findAgents = () =>
    startScan.mutate(source, {
      onSuccess: (data) => {
        setScanId(data.id);
        setPicked(null);
        setManual(false);
        setStage("agents");
      },
      onError: (e) => enqueueSnackbar(errorMessage(e), { variant: "error" }),
    });

  const toDetails = () => {
    const chosen = manual
      ? { name: "", subfolder: subfolder.trim(), envNames: [] }
      : picked;
    setName((current) => current || defaultName(chosen, source));
    setStage("details");
  };

  const chosenFolder = manual ? subfolder.trim() : picked?.subfolder || "";
  const envNames = manual ? [] : picked?.envNames || [];

  const connectAgent = () =>
    connect.mutate(
      {
        ...source,
        ...(chosenFolder ? { subfolder: chosenFolder } : {}),
        ...(scan.data?.commitSha ? { commit_sha: scan.data.commitSha } : {}),
        name: name.trim(),
        env_values: Object.fromEntries(
          Object.entries(values).filter(([, value]) => value),
        ),
      },
      {
        onSuccess: (data) => {
          clearConnectDraft();
          enqueueSnackbar(
            data.exists
              ? "This agent is already connected"
              : `${name.trim()} connected; we're reading it now`,
            { variant: data.exists ? "info" : "success" },
          );
          navigate(`${CONNECTED_AGENTS_PATH}/${data.agent_id}/overview`);
        },
        onError: (e) => enqueueSnackbar(errorMessage(e), { variant: "error" }),
      },
    );

  let appBody;
  if (github.isLoading) {
    appBody = <CircularProgress size={20} />;
  } else if (github.isError) {
    appBody = (
      <Alert severity="error">
        Couldn&apos;t check the GitHub connection. Try again.
      </Alert>
    );
  } else if (!connection) {
    appBody = (
      <InstallApp
        onInstall={install}
        onPaste={() => setMode("link")}
        canInstall={canInstall}
      />
    );
  } else if (connection.status !== "active") {
    appBody = (
      <Alert
        severity="warning"
        action={
          <Button
            size="small"
            color="inherit"
            onClick={() => navigate(paths.dashboard.settings.integrations)}
          >
            Manage
          </Button>
        }
      >
        The GitHub App is suspended on GitHub. Unsuspend it to pick a
        repository.
      </Alert>
    );
  } else {
    appBody = (
      <PickFromGitHub
        connection={connection}
        repository={repository}
        onRepository={pickRepository}
        branch={branch}
        onBranch={setBranch}
        onInstall={canInstall ? install : undefined}
      />
    );
  }

  let body;
  let action;
  let hint;
  if (stage === "source") {
    body = (
      <>
        <Typography sx={{ typography: "s1", color: "text.secondary" }}>
          Connect GitHub to pick from your repositories, or paste a repository
          link and start.
        </Typography>
        <Box
          sx={{
            display: "grid",
            gap: 1,
            gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" },
          }}
        >
          <OptionCard
            icon="simple-icons:github"
            label="Connect GitHub"
            short="Pick from a list · private repositories · fixes as pull requests"
            selected={mode === "app"}
            onSelect={() => setMode("app")}
          />
          <OptionCard
            icon="solar:link-linear"
            label="Paste a repo link"
            short="Start building now · public repositories"
            selected={mode === "link"}
            onSelect={() => setMode("link")}
          />
        </Box>
        {mode === "app" ? (
          appBody
        ) : (
          <PasteLink
            link={link}
            onLink={setLink}
            branch={linkBranch}
            onBranch={setLinkBranch}
            onPick={() => setMode("app")}
          />
        )}
      </>
    );
    hint = source
      ? `We read ${source.repository}${source.ref ? ` at ${source.ref}` : ""} and list the agents in it.`
      : mode === "app"
        ? "Choose a repository"
        : "Paste the repository link";
    action = (
      <Button
        variant="contained"
        color="primary"
        disabled={!source || !canConnect || startScan.isPending}
        onClick={findAgents}
        endIcon={<Iconify icon="solar:magnifer-linear" width={14} />}
        sx={{ flexShrink: 0 }}
      >
        Find agents
      </Button>
    );
  } else if (stage === "agents") {
    body = (
      <>
        <Typography
          component="div"
          sx={{ typography: "s3", color: "text.secondary" }}
        >
          <Box
            component="span"
            sx={{ fontFamily: MONO, color: "text.primary" }}
          >
            {source?.repository}
          </Box>
          {source?.ref ? ` at ${source.ref}` : ""}
          {" · "}
          <TextLink onClick={() => setStage("source")}>Change</TextLink>
        </Typography>
        {manual ? (
          <ManualAgent
            subfolder={subfolder}
            onSubfolder={setSubfolder}
            onBack={() => setManual(false)}
          />
        ) : (
          <FoundAgents
            scan={scan.data}
            picked={picked}
            onPick={setPicked}
            onManual={() => setManual(true)}
          />
        )}
      </>
    );
    const ready = manual || !!picked;
    hint = ready
      ? "Next: name it and add its values"
      : "Pick the agent to connect";
    action = (
      <Button
        variant="contained"
        color="primary"
        disabled={!ready}
        onClick={toDetails}
        endIcon={<Iconify icon="solar:alt-arrow-right-linear" width={14} />}
        sx={{ flexShrink: 0 }}
      >
        Next
      </Button>
    );
  } else {
    body = (
      <>
        <Typography
          component="div"
          sx={{ typography: "s3", color: "text.secondary" }}
        >
          <Box
            component="span"
            sx={{ fontFamily: MONO, color: "text.primary" }}
          >
            {source?.repository}
          </Box>
          {chosenFolder ? ` · ${chosenFolder}/` : " · repository root"}
          {" · "}
          <TextLink onClick={() => setStage("agents")}>Change</TextLink>
        </Typography>
        <AgentDetails
          name={name}
          onName={setName}
          envNames={envNames}
          values={values}
          onValue={(envName, value) =>
            setValues((prev) => ({ ...prev, [envName]: value }))
          }
        />
      </>
    );
    hint = name.trim()
      ? "We read the agent once it's connected"
      : "Name the agent";
    action = (
      <Button
        variant="contained"
        color="primary"
        disabled={!name.trim() || !canConnect || connect.isPending}
        onClick={connectAgent}
        endIcon={<Iconify icon="solar:alt-arrow-right-linear" width={14} />}
        sx={{ flexShrink: 0 }}
      >
        Connect agent
      </Button>
    );
  }

  return (
    <Box sx={{ display: "flex", flexDirection: "column", minHeight: "100%" }}>
      <Stack spacing={1.75} sx={{ p: 2.5, flex: 1 }}>
        {message && message.severity !== "success" && (
          <Alert severity={message.severity}>{message.text}</Alert>
        )}
        {body}
      </Stack>
      <Stack
        direction="row"
        alignItems="center"
        spacing={2}
        sx={{
          px: 2.5,
          py: 1.5,
          borderTop: "1px solid",
          borderColor: "divider",
          position: "sticky",
          bottom: 0,
          bgcolor: "background.paper",
        }}
      >
        {stage !== "source" && (
          <Button
            color="inherit"
            onClick={() => setStage(stage === "details" ? "agents" : "source")}
            startIcon={
              <Iconify icon="solar:alt-arrow-left-linear" width={14} />
            }
            sx={{ flexShrink: 0, color: "text.secondary" }}
          >
            Back
          </Button>
        )}
        <Box flex={1} />
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {hint}
        </Typography>
        {action}
      </Stack>
    </Box>
  );
}
GitHubConnectFlow.propTypes = { outcome: PropTypes.string };
