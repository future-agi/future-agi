import PropTypes from "prop-types";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
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
import { paths } from "src/routes/paths";
import { useDebounce } from "src/hooks/use-debounce";
import {
  useGitHubConnection,
  useSourceBranches,
  useSourceRepositories,
} from "src/api/integrations";
import { parseGitHubInput } from "src/pages/dashboard/harness/requestMapper";
import { GITHUB_INSTALL_OUTCOMES } from "src/sections/settings/integrations/constants";
import Field from "src/sections/simulate/environments/components/Field";
import ComingSoonChip from "src/sections/simulate/environments/components/ComingSoonChip";
import { CONNECT_AGENT_PATH } from "../agentsTabs";
import { GITHUB_SOURCE } from "./connectSources";
import { LogoTile, OptionCard, TextLink } from "./connectBits";
import { MONO } from "./connectStyles";

const INSTALL_PATH = `${paths.dashboard.settings.integrations}?${new URLSearchParams({
  platform: GITHUB_SOURCE,
  return_to: `${CONNECT_AGENT_PATH}?source=${GITHUB_SOURCE}`,
})}`;

const keepAll = (options) => options;

function InstallApp({ onInstall, onPaste }) {
  return (
    <Box>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{ p: 1.75, borderRadius: "8px", border: "1px solid", borderColor: "divider" }}
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
        <Button
          size="small"
          variant="outlined"
          color="inherit"
          onClick={onInstall}
          startIcon={<Iconify icon="simple-icons:github" width={14} />}
          sx={{ flexShrink: 0, borderColor: "divider" }}
        >
          Install GitHub App
        </Button>
      </Stack>
      <TextLink onClick={onPaste} sx={{ mt: 0.75 }}>
        Can&apos;t install the app? Paste the repository link instead
      </TextLink>
    </Box>
  );
}
InstallApp.propTypes = { onInstall: PropTypes.func.isRequired, onPaste: PropTypes.func.isRequired };

function PickFromGitHub({ connection, repository, onRepository, branch, onBranch, onInstall }) {
  const [query, setQuery] = useState("");
  const [changingBranch, setChangingBranch] = useState(false);
  const search = useDebounce(repository ? "" : query.trim(), 300);
  const repositories = useSourceRepositories(connection.id, search);
  const branches = useSourceBranches(connection.id, repository?.id);

  return (
    <Stack spacing={1.5}>
      <Box>
        <Stack direction="row" alignItems="center" spacing={0.75} sx={{ mb: 0.5 }}>
          <Typography sx={{ typography: "s3", fontWeight: 600 }}>Repository</Typography>
          <Typography sx={{ typography: "s3", color: "text.secondary" }}>
            {`· from GitHub (${connection.display_name})`}
          </Typography>
          <Box flex={1} />
          <TextLink onClick={onInstall}>Missing a repository or an account? Add it on GitHub</TextLink>
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
              <Box component="span" sx={{ fontFamily: MONO, typography: "s1", flex: 1, minWidth: 0 }}>
                {option.full_name}
              </Box>
              <Box component="span" sx={{ typography: "s3", color: "text.secondary" }}>
                {option.private ? "Private" : "Public"}
              </Box>
            </Box>
          )}
          renderInput={(params) => (
            <TextField
              {...params}
              placeholder="Choose a repository"
              sx={{ "& .MuiInputBase-input": { typography: "s1", fontFamily: MONO } }}
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
              renderValue={(value) => <Box component="span" sx={{ fontFamily: MONO }}>{value}</Box>}
              sx={{ typography: "s1" }}
            >
              {branches.isFetching && (
                <MenuItem disabled value="">
                  <CircularProgress size={14} sx={{ mr: 1 }} />
                  Loading branches
                </MenuItem>
              )}
              {(branches.data || []).map((item) => (
                <MenuItem key={item.name} value={item.name} sx={{ typography: "s1", gap: 1 }}>
                  <Box component="span" sx={{ fontFamily: MONO, flex: 1 }}>{item.name}</Box>
                  {item.is_default && (
                    <Box component="span" sx={{ typography: "s3", color: "text.secondary" }}>default</Box>
                  )}
                </MenuItem>
              ))}
            </Select>
          </Box>
        ) : (
          <Typography component="div" sx={{ typography: "s3", color: "text.secondary" }}>
            {"Reads "}
            <Box component="span" sx={{ fontFamily: MONO, color: "text.primary" }}>{branch}</Box>
            {". A fix merged there counts as released. "}
            <TextLink onClick={() => setChangingBranch(true)}>Change</TextLink>
          </Typography>
        ))}
      {repository && (
        <Stack
          direction="row"
          alignItems="center"
          spacing={1.25}
          sx={{ px: 1.5, py: 1.25, borderRadius: "6px", border: "1px solid", borderColor: "divider" }}
        >
          <Iconify icon="solar:magnifer-linear" width={16} sx={{ color: "text.secondary", flexShrink: 0 }} />
          <Typography sx={{ typography: "s1", flex: 1, minWidth: 0 }}>
            {`We read ${repository.full_name} at ${branch} and list the agents in it.`}
          </Typography>
          <ComingSoonChip />
        </Stack>
      )}
    </Stack>
  );
}
PickFromGitHub.propTypes = {
  connection: PropTypes.shape({ id: PropTypes.string.isRequired, display_name: PropTypes.string }).isRequired,
  repository: PropTypes.object,
  onRepository: PropTypes.func.isRequired,
  branch: PropTypes.string,
  onBranch: PropTypes.func.isRequired,
  onInstall: PropTypes.func.isRequired,
};

function PasteLink({ link, onLink, branch, onBranch, entry, onEntry, onPick }) {
  return (
    <Stack spacing={1.5}>
      <Field
        label="Repository link"
        required
        mono
        placeholder="https://github.com/owner/repo"
        value={link}
        onChange={onLink}
        error={link.trim() && !parseGitHubInput(link) ? "Enter a GitHub link or owner/repo." : ""}
        helper="Public repositories only. Private ones need the GitHub App."
      />
      <Box sx={{ display: "grid", gap: 1.5, gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" } }}>
        <Field label="Branch or tag" mono placeholder="Default branch" value={branch} onChange={onBranch} />
        <Field label="Entry path (optional)" mono placeholder="src/agent/index.ts" value={entry} onChange={onEntry} />
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
  entry: PropTypes.string.isRequired,
  onEntry: PropTypes.func.isRequired,
  onPick: PropTypes.func.isRequired,
};

export default function GitHubConnectFlow({ outcome }) {
  const navigate = useNavigate();
  const github = useGitHubConnection({ refetchOnWindowFocus: true });
  const connection = github.data;
  const [mode, setMode] = useState("app");
  const [repository, setRepository] = useState(null);
  const [branch, setBranch] = useState("");
  const [link, setLink] = useState("");
  const [linkBranch, setLinkBranch] = useState("");
  const [entry, setEntry] = useState("");
  const message = GITHUB_INSTALL_OUTCOMES[outcome];
  const install = () => navigate(INSTALL_PATH);

  const pickRepository = (picked) => {
    setRepository(picked);
    setBranch(picked?.default_branch || "");
  };

  let appBody;
  if (github.isLoading) {
    appBody = <CircularProgress size={20} />;
  } else if (github.isError) {
    appBody = <Alert severity="error">Couldn&apos;t check the GitHub connection. Try again.</Alert>;
  } else if (!connection) {
    appBody = <InstallApp onInstall={install} onPaste={() => setMode("link")} />;
  } else if (connection.status !== "active") {
    appBody = (
      <Alert
        severity="warning"
        action={
          <Button size="small" color="inherit" onClick={() => navigate(paths.dashboard.settings.integrations)}>
            Manage
          </Button>
        }
      >
        The GitHub App is suspended on GitHub. Unsuspend it to pick a repository.
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
        onInstall={install}
      />
    );
  }

  const ready = mode === "app" ? !!repository : !!parseGitHubInput(link);
  const hint = ready
    ? "Reading the agent and creating it are coming soon"
    : mode === "app"
      ? "Choose a repository"
      : "Paste the repository link";

  return (
    <Box sx={{ display: "flex", flexDirection: "column", minHeight: "100%" }}>
      <Stack spacing={1.75} sx={{ p: 2.5, flex: 1 }}>
        {message && message.severity !== "success" && (
          <Alert severity={message.severity}>{message.text}</Alert>
        )}
        <Typography sx={{ typography: "s1", color: "text.secondary" }}>
          Connect GitHub to pick from your repositories, or paste a repository link and start.
        </Typography>
        <Box sx={{ display: "grid", gap: 1, gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" } }}>
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
            entry={entry}
            onEntry={setEntry}
            onPick={() => setMode("app")}
          />
        )}
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
        <Box flex={1} />
        <Typography sx={{ typography: "s3", color: "text.secondary" }}>{hint}</Typography>
        <Button
          variant="contained"
          color="primary"
          disabled
          endIcon={<Iconify icon="solar:alt-arrow-right-linear" width={14} />}
          sx={{ flexShrink: 0 }}
        >
          Connect agent
        </Button>
      </Stack>
    </Box>
  );
}
GitHubConnectFlow.propTypes = { outcome: PropTypes.string };
