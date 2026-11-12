import { useEffect, useReducer } from "react";
import { Box, Stack, Typography } from "@mui/material";
import { parseGitHubInput } from "src/pages/dashboard/harness/requestMapper";
import Field from "../components/Field";
import Label from "../components/Label";
import ChipCard from "../components/ChipCard";
import ProviderRow from "../components/ProviderRow";
import ContinueRow from "../components/ContinueRow";
import EnvironmentValues from "./EnvironmentValues";
import ScenarioCount from "./ScenarioCount";
import ObservabilityChoice from "./ObservabilityChoice";
import { DEFAULT_SCENARIOS, isValidScenarioCount } from "./scenarioCountRules";
import RuntimePreflight from "./RuntimePreflight";
import { CreditExhaustionBanner } from "src/components/CreditExhaustionBanner";
import ParallelismField from "./ParallelismField";
import usePanelBuild from "../hooks/usePanelBuild";
import usePrivateGithubAvailable from "../hooks/usePrivateGithubAvailable";
import { useEnvironmentsStore } from "../store/useEnvironmentsStore";
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
  PRIVATE_GITHUB_UNAVAILABLE_MESSAGE,
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
  observabilityEnabled: false,
};

function reducer(s, a) {
  if (a.type === "reset") return initial;
  const value = typeof a.value === "function" ? a.value(s[a.field]) : a.value;
  return { ...s, [a.field]: value };
}

// Start from the kept repo draft when there is one (EnvironmentsHome keeps it
// only for "Start a new build" on the environment it built), else blank. Only
// non-secret fields come back: pasted environment values are never stored and
// credential files are uploaded again, so both start empty.
function fromDraft(draft) {
  if (draft?.kind !== "repo") return initial;
  return {
    ...initial,
    provider: draft.provider || initial.provider,
    repo: draft.value || "",
    branch: draft.ref || initial.branch,
    entry: draft.entry || "",
    visibility: draft.visibility || initial.visibility,
    installationId: draft.installationId || "",
    egress: draft.egress || "",
    // The field holds text; the draft keeps the parsed number.
    scenarioCount: draft.scenarioCount
      ? String(draft.scenarioCount)
      : initial.scenarioCount,
    observabilityEnabled: !!draft.observabilityEnabled,
  };
}

export default function PanelSourceRepo() {
  const [form, dispatch] = useReducer(reducer, undefined, () =>
    fromDraft(useEnvironmentsStore.getState().draft),
  );
  const build = usePanelBuild();
  // Any edit invalidates a prior preflight result, so re-disable Build.
  const set = (field) => (value) => {
    dispatch({ field, value });
    build.resetPreflight();
  };
  const {
    provider,
    repo,
    branch,
    entry,
    visibility,
    installationId,
    envText,
    egress,
    secretFiles,
    scenarioCount,
    observabilityEnabled,
  } = form;
  // Only a definite `false` from the backend withdraws Private; while the answer
  // is loading or failed, the form stays as it was (self-hosted is never blocked).
  const privateUnavailable = usePrivateGithubAvailable() === false;
  const isPrivate =
    !privateUnavailable && visibility === REPO_VISIBILITY.PRIVATE;
  // A kept draft that was Private becomes Public here, so neither the form nor
  // the request carries a private source this deployment would refuse.
  useEffect(() => {
    if (privateUnavailable && visibility === REPO_VISIBILITY.PRIVATE) {
      dispatch({ field: "visibility", value: REPO_VISIBILITY.PUBLIC });
    }
  }, [privateUnavailable, visibility]);
  // The repo must parse to owner/repo (or a GitHub URL) before we can preflight.
  // Only flag a non-empty, unparseable value so the field isn't red before typing.
  const parsedRepo = parseGitHubInput(repo);
  const repoError =
    repo.trim() && !parsedRepo
      ? "Enter a repository as owner/repo or a GitHub URL."
      : "";
  const canGo = !!parsedRepo;

  const buildSource = () => ({
    kind: "repo",
    provider,
    value: repo.trim(),
    ref: branch.trim() || DEFAULT_BRANCH,
    entry: entry.trim(),
    visibility: privateUnavailable ? REPO_VISIBILITY.PUBLIC : visibility,
    installationId: isPrivate ? installationId.trim() || null : null,
    envText: envText.trim() || null,
    egress: egress.trim() || null,
    secretFiles,
    scenarioCount: Number(scenarioCount) || undefined,
    observabilityEnabled,
  });

  return (
    <Stack spacing={1.5} sx={{ p: 1.5 }}>
      <ProviderRow
        options={REPO_PROVIDERS}
        value={provider}
        onChange={set("provider")}
      />
      <Field
        label="Repository"
        required
        placeholder="owner/repo"
        value={repo}
        onChange={set("repo")}
        mono
        error={repoError}
        helper="We read the code so scenarios stay in sync with your actual tools."
      />
      <Stack direction="row" spacing={1.5}>
        <Field
          label="Branch or tag"
          value={branch}
          onChange={set("branch")}
          mono
          fullWidth
        />
        <Field
          label="Entry path (optional)"
          placeholder="src/agent/index.ts"
          value={entry}
          onChange={set("entry")}
          mono
          fullWidth
        />
      </Stack>
      {/* TODO: hide Private once creation knows versioning is on: it then refuses
          private repositories (github_private_unsupported, shown on Build). */}
      <Box>
        <Label>Visibility</Label>
        <Box
          sx={{
            display: "grid",
            gap: 0.75,
            gridTemplateColumns: "1fr 1fr",
            mt: 0.75,
          }}
        >
          <ChipCard
            label={REPO_VISIBILITY_LABEL[REPO_VISIBILITY.PUBLIC]}
            on={!isPrivate}
            onClick={() => set("visibility")(REPO_VISIBILITY.PUBLIC)}
          />
          {/* ChipCard has no disabled state, so the wrapper withholds the click. */}
          <Box
            aria-disabled={privateUnavailable || undefined}
            sx={
              privateUnavailable
                ? { opacity: 0.48, pointerEvents: "none" }
                : undefined
            }
          >
            <ChipCard
              label={REPO_VISIBILITY_LABEL[REPO_VISIBILITY.PRIVATE]}
              on={isPrivate}
              onClick={
                privateUnavailable
                  ? undefined
                  : () => set("visibility")(REPO_VISIBILITY.PRIVATE)
              }
            />
          </Box>
        </Box>
        {privateUnavailable && (
          <Typography
            sx={{ typography: "s3", color: "text.subtitle", mt: 0.5 }}
          >
            {PRIVATE_GITHUB_UNAVAILABLE_MESSAGE}
          </Typography>
        )}
      </Box>
      {isPrivate && (
        <Field
          label={INSTALLATION_ID_LABEL}
          placeholder={INSTALLATION_ID_PLACEHOLDER}
          value={installationId}
          onChange={set("installationId")}
          mono
          helper={INSTALLATION_ID_HELPER}
        />
      )}
      <EnvironmentValues
        envText={envText}
        onEnvText={set("envText")}
        egress={egress}
        onEgress={set("egress")}
        secretFiles={secretFiles}
        onSecretFiles={set("secretFiles")}
      />
      <ScenarioCount value={scenarioCount} onChange={set("scenarioCount")} />
      <ObservabilityChoice
        checked={observabilityEnabled}
        onChange={set("observabilityEnabled")}
      />
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
      <CreditExhaustionBanner
        error={build.creditExhaustion.error}
        onUpgrade={build.creditExhaustion.onUpgrade}
        onDismiss={build.creditExhaustion.onDismiss}
      />
      <ContinueRow
        disabled={!build.readyToSubmit || !isValidScenarioCount(scenarioCount)}
        busy={build.committing}
        hint={
          build.status === "done"
            ? "Resolve the failed preflight checks above to continue"
            : "Run preflight to check your setup before building"
        }
        onClick={build.commitBuild}
      />
    </Stack>
  );
}
