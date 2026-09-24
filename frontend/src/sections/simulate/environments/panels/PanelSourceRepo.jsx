import { useReducer } from "react";
import { Box, Stack } from "@mui/material";
import { parseGitHubInput } from "src/pages/dashboard/harness/requestMapper";
import Field from "../components/Field";
import Label from "../components/Label";
import ChipCard from "../components/ChipCard";
import ProviderRow from "../components/ProviderRow";
import ContinueRow from "../components/ContinueRow";
import EnvironmentValues from "./EnvironmentValues";
import ScenarioCount from "./ScenarioCount";
import { DEFAULT_SCENARIOS, isValidScenarioCount } from "./scenarioCountRules";
import RuntimePreflight from "./RuntimePreflight";
import ParallelismField from "./ParallelismField";
import usePanelBuild from "../hooks/usePanelBuild";
import {
  REPO_PROVIDERS,
  DEFAULT_REPO_PROVIDER,
  DEFAULT_BRANCH,
  REPO_VISIBILITY,
  DEFAULT_REPO_VISIBILITY,
  REPO_VISIBILITY_LABEL,
  INSTALLATION_ID_LABEL,
  INSTALLATION_ID_HELPER,
  INSTALLATION_ID_PLACEHOLDER,
} from "../repoProviders";

const initial = {
  provider: DEFAULT_REPO_PROVIDER,
  repo: "",
  branch: DEFAULT_BRANCH,
  entry: "",
  visibility: DEFAULT_REPO_VISIBILITY,
  installationId: "",
  envText: "",
  egress: "",
  secretFiles: [],
  scenarioCount: DEFAULT_SCENARIOS,
};

function reducer(s, a) {
  if (a.type === "reset") return initial;
  const value = typeof a.value === "function" ? a.value(s[a.field]) : a.value;
  return { ...s, [a.field]: value };
}

export default function PanelSourceRepo() {
  const [form, dispatch] = useReducer(reducer, initial);
  const build = usePanelBuild();
  // Any edit invalidates a prior preflight result, so re-disable Build.
  const set = (field) => (value) => {
    dispatch({ field, value });
    build.resetPreflight();
  };
  const { provider, repo, branch, entry, visibility, installationId, envText, egress, secretFiles, scenarioCount } = form;
  const isPrivate = visibility === REPO_VISIBILITY.PRIVATE;
  // The repo must parse to owner/repo (or a GitHub URL) before we can preflight.
  // Only flag a non-empty, unparseable value so the field isn't red before typing.
  const parsedRepo = parseGitHubInput(repo);
  const repoError = repo.trim() && !parsedRepo
    ? "Enter a repository as owner/repo or a GitHub URL."
    : "";
  const canGo = !!parsedRepo;

  const buildSource = () => ({
    kind: "repo",
    provider,
    value: repo.trim(),
    ref: branch.trim() || DEFAULT_BRANCH,
    entry: entry.trim(),
    visibility,
    installationId: isPrivate ? (installationId.trim() || null) : null,
    envText: envText.trim() || null,
    egress: egress.trim() || null,
    secretFiles,
    scenarioCount: Number(scenarioCount) || undefined,
  });

  return (
    <Stack spacing={1.75} sx={{ p: 2.5 }}>
      <ProviderRow
        options={REPO_PROVIDERS}
        value={provider}
        onChange={set("provider")}
      />
      <Field
        label="Repository"
        required
        placeholder="owner/repo"
        value={repo} onChange={set("repo")}
        mono
        error={repoError}
        helper="We read the code so scenarios stay in sync with your actual tools."
      />
      <Stack direction="row" spacing={1.5}>
        <Field
          label="Branch or tag"
          value={branch} onChange={set("branch")}
          mono
          fullWidth
        />
        <Field
          label="Entry path (optional)"
          placeholder="src/agent/index.ts"
          value={entry} onChange={set("entry")}
          mono
          fullWidth
        />
      </Stack>
      <Box>
        <Label>Visibility</Label>
        <Box sx={{ display: "grid", gap: 0.75, gridTemplateColumns: "1fr 1fr", mt: 0.75 }}>
          <ChipCard
            label={REPO_VISIBILITY_LABEL[REPO_VISIBILITY.PUBLIC]}
            on={!isPrivate}
            onClick={() => set("visibility")(REPO_VISIBILITY.PUBLIC)}
          />
          <ChipCard
            label={REPO_VISIBILITY_LABEL[REPO_VISIBILITY.PRIVATE]}
            on={isPrivate}
            onClick={() => set("visibility")(REPO_VISIBILITY.PRIVATE)}
          />
        </Box>
      </Box>
      {isPrivate && (
        <Field
          label={INSTALLATION_ID_LABEL}
          placeholder={INSTALLATION_ID_PLACEHOLDER}
          value={installationId} onChange={set("installationId")}
          mono
          helper={INSTALLATION_ID_HELPER}
        />
      )}
      <EnvironmentValues
        envText={envText} onEnvText={set("envText")}
        egress={egress} onEgress={set("egress")}
        secretFiles={secretFiles} onSecretFiles={set("secretFiles")}
      />
      <ScenarioCount value={scenarioCount} onChange={set("scenarioCount")} />
      <ParallelismField
        value={build.parallelism}
        input={build.parallelismInput}
        onChange={build.setParallelism}
        enabled={build.parallelismEnabled}
        admitted={build.admittedParallelism}
      />
      <RuntimePreflight
        status={build.status}
        canRun={canGo}
        onRun={() => build.runPreflight(buildSource())}
        result={build.result}
        error={build.error}
      />
      <ContinueRow
        disabled={!build.readyToSubmit || !isValidScenarioCount(scenarioCount)}
        busy={build.committing}
        hint={build.status === "done" ? "Resolve the checks above" : "Run preflight to continue"}
        onClick={build.commitBuild}
      />
    </Stack>
  );
}
