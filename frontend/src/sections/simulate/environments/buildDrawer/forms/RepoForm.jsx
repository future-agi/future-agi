import PropTypes from "prop-types";
import { Box, Stack } from "@mui/material";
import { parseGitHubInput } from "src/pages/dashboard/harness/requestMapper";
import {
  isValidSubfolder,
  normalizeSubfolder,
} from "src/api/simulate-environments/preflightPayload";
import usePanelBuild from "../../hooks/usePanelBuild";
import { DEFAULT_SCENARIOS } from "../../panels/scenarioCountRules";
import { DEFAULT_BRANCH, REPO_VISIBILITY } from "../../repoProviders";
import DrawerEnvValues from "./DrawerEnvValues";
import useDrawerForm from "./useDrawerForm";
import { ChipToggle, Field, FormCard, Label, ToggleRow } from "./formBits";

const initial = {
  repo: "",
  branch: DEFAULT_BRANCH,
  entry: "",
  subfolder: "",
  visibility: REPO_VISIBILITY.PUBLIC,
  installationId: "",
  envText: "",
  egress: "",
  secretFiles: [],
  scenarioCount: DEFAULT_SCENARIOS,
  observabilityEnabled: false,
};

const twoColumns = {
  display: "grid",
  gap: 1.5,
  gridTemplateColumns: "1fr 1fr",
};

export default function RepoForm({ onBusyChange }) {
  const build = usePanelBuild();
  const [form, set] = useDrawerForm(initial, build);
  const {
    repo,
    branch,
    entry,
    subfolder,
    visibility,
    installationId,
    envText,
    egress,
    secretFiles,
    scenarioCount,
    observabilityEnabled,
  } = form;
  const isPrivate = visibility === REPO_VISIBILITY.PRIVATE;
  const parsed = parseGitHubInput(repo);
  const repoError =
    repo.trim() && !parsed
      ? "Enter a repository as owner/repo or a GitHub URL."
      : null;
  const subfolderError = !isValidSubfolder(subfolder)
    ? "Enter a path inside the repository, like agents/support."
    : null;

  const buildSource = () => ({
    kind: "repo",
    provider: "github",
    value: repo.trim(),
    ref: branch.trim() || DEFAULT_BRANCH,
    entry: entry.trim(),
    subfolder: normalizeSubfolder(subfolder),
    visibility,
    installationId: isPrivate ? installationId.trim() || null : null,
    envText: envText.trim() || null,
    egress: egress.trim() || null,
    secretFiles,
    scenarioCount: Number(scenarioCount) || undefined,
    observabilityEnabled,
  });

  return (
    <FormCard
      intro="Paste the GitHub repository link. For a private repository, use the GitHub App's installation ID."
      build={build}
      scenarioCount={scenarioCount}
      onScenarioCount={set("scenarioCount")}
      canRun={!!parsed && !subfolderError}
      blockedReason="Paste the repository link to run preflight."
      onRun={() => build.runPreflight(buildSource())}
      hint={parsed ? subfolderError : "Paste the repository link"}
      onBusyChange={onBusyChange}
    >
      <Stack spacing={1.5}>
        <Field
          label="Repository link"
          required
          mono
          value={repo}
          onChange={set("repo")}
          placeholder="https://github.com/owner/repo"
          error={repoError}
          helper="We read the code so scenarios stay in step with the agent's tools."
        />
        <Box sx={twoColumns}>
          <Field
            label="Branch or tag"
            mono
            value={branch}
            onChange={set("branch")}
            placeholder="main"
          />
          <Field
            label="Entry path (optional)"
            mono
            value={entry}
            onChange={set("entry")}
            placeholder="src/agent/index.ts"
          />
        </Box>
        <Field
          label="Agent folder (optional)"
          mono
          value={subfolder}
          onChange={set("subfolder")}
          placeholder="agents/support"
          error={subfolderError}
        />
        <Box>
          <Label>Visibility</Label>
          <Box sx={{ ...twoColumns, gap: 0.75, mt: 0.75 }}>
            <ChipToggle
              label="Public"
              on={!isPrivate}
              onClick={() => set("visibility")(REPO_VISIBILITY.PUBLIC)}
              fullWidth
            />
            <ChipToggle
              label="Private"
              on={isPrivate}
              onClick={() => set("visibility")(REPO_VISIBILITY.PRIVATE)}
              fullWidth
            />
          </Box>
        </Box>
        {isPrivate && (
          <Field
            label="GitHub App installation ID"
            mono
            value={installationId}
            onChange={set("installationId")}
            placeholder="12345678"
            helper="The installation that grants the runner access to this repository."
          />
        )}
      </Stack>
      <DrawerEnvValues
        envText={envText}
        onEnvText={set("envText")}
        egress={egress}
        onEgress={set("egress")}
        secretFiles={secretFiles}
        onSecretFiles={set("secretFiles")}
      />
      <ToggleRow
        title="Enable observability"
        body={
          observabilityEnabled
            ? "Its traces come into Observe once the code sends them."
            : "Off. Its traces stay out of Observe."
        }
        hint="We can observe the agent only if its code already sends telemetry (OpenTelemetry or traceAI). Without it the environment still builds."
        checked={observabilityEnabled}
        onChange={set("observabilityEnabled")}
      />
    </FormCard>
  );
}
RepoForm.propTypes = { onBusyChange: PropTypes.func };
