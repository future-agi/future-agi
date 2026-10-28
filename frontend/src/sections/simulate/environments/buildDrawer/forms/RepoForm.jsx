import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Stack } from "@mui/material";
import { parseGitHubInput } from "src/pages/dashboard/harness/requestMapper";
import usePanelBuild from "../../hooks/usePanelBuild";
import { DEFAULT_SCENARIOS } from "../../panels/scenarioCountRules";
import { DEFAULT_BRANCH, REPO_VISIBILITY } from "../../repoProviders";
import DrawerEnvValues from "./DrawerEnvValues";
import useDrawerForm from "./useDrawerForm";
import {
  ChipToggle,
  Field,
  FormCard,
  Label,
  ToggleRow,
  WhereOption,
  useUiOnlyBuild,
} from "./formBits";
import GithubPicker, { GITHUB_PICK_INITIAL } from "./extras/GithubPicker";
import { sampleRepoOf } from "./extras/sampleGithub";

const VIA = { GITHUB: "github", LINK: "link" };
const PRIVATE_VIA = { APP: "app", TOKEN: "token" };

const initial = {
  via: VIA.LINK,
  privateVia: PRIVATE_VIA.APP,
  accessToken: "",
  github: GITHUB_PICK_INITIAL,
  repo: "",
  branch: DEFAULT_BRANCH,
  entry: "",
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

/**
 * GitHub, by a pasted repository link: public, or private through the GitHub
 * App. Connect GitHub (the app's picker) and a private repository read with an
 * access token are Vel's design without an API yet: while either is chosen the
 * form is UI only, on sample data, and the real build never sees it.
 */
export default function RepoForm({ onBusyChange }) {
  const live = usePanelBuild();
  const [form, set, setForm] = useDrawerForm(initial, {
    resetPreflight: () => resetBoth(),
  });
  const {
    via,
    privateVia,
    accessToken,
    github,
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
  const [scanStatus, setScanStatus] = useState("scanning");
  const viaGithub = via === VIA.GITHUB;
  const isPrivate = visibility === REPO_VISIBILITY.PRIVATE;
  const viaToken = !viaGithub && isPrivate && privateVia === PRIVATE_VIA.TOKEN;
  const uiOnly = viaGithub || viaToken;
  const parsed = parseGitHubInput(repo);
  const repoError =
    repo.trim() && !parsed
      ? "Enter a repository as owner/repo or a GitHub URL."
      : null;

  const pickedAgent =
    sampleRepoOf(github.repo)?.agents.find((a) => a.id === github.agentId) ||
    null;
  const scanDone = !["scanning", "error"].includes(scanStatus);
  const githubReady =
    !!github.repo && scanDone && (!!pickedAgent || !!github.entryFile.trim());
  const sampleChecks = viaGithub
    ? [
        {
          label: `${github.repo} @ ${github.branch}`,
          detail: "Read through the GitHub App",
        },
        {
          label: pickedAgent?.name || github.entryFile.trim(),
          detail: pickedAgent
            ? `${pickedAgent.framework} · ${pickedAgent.entry}`
            : "Entry file",
        },
      ]
    : [
        {
          label: "Access token",
          detail: `Contents: read on ${parsed || "the repository"}`,
        },
      ];
  const sample = useUiOnlyBuild(sampleChecks);
  const build = uiOnly ? sample : live;
  const resetBoth = () => {
    live.resetPreflight();
    sample.resetPreflight();
  };

  const canRun = viaGithub
    ? githubReady
    : viaToken
      ? !!parsed && !!accessToken.trim()
      : !!parsed;
  const hint = canRun
    ? null
    : viaGithub
      ? !github.installed
        ? "Install the GitHub App"
        : !github.repo
          ? "Choose a repository"
          : scanStatus === "error"
            ? "Retry reading the repository"
            : !scanDone
              ? "Wait for the repository to be read"
              : "Choose the agent or name its entry file"
      : !parsed
        ? "Paste the repository link"
        : "Add the access token";
  const blockedReason = viaGithub
    ? "Pick the repository and the agent, then preflight reads it and checks its credentials."
    : viaToken && parsed
      ? "Add the access token, then preflight reads the repository with it."
      : "Paste the repository link, then preflight reads it and checks its credentials.";
  const setGithub = (patch) => set("github")((g) => ({ ...g, ...patch }));
  const switchToToken = () => {
    setForm((s) => ({
      ...s,
      via: VIA.LINK,
      visibility: REPO_VISIBILITY.PRIVATE,
      privateVia: PRIVATE_VIA.TOKEN,
    }));
    resetBoth();
  };

  const buildSource = () => ({
    kind: "repo",
    provider: "github",
    value: repo.trim(),
    ref: branch.trim() || DEFAULT_BRANCH,
    entry: entry.trim(),
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
      intro="Connect GitHub to pick from your repositories, or paste a repository link and start. We read the code when you connect, then you can confirm or correct what we found on the environment."
      uiOnly={uiOnly}
      build={build}
      scenarioCount={scenarioCount}
      onScenarioCount={set("scenarioCount")}
      canRun={canRun}
      blockedReason={blockedReason}
      onRun={() =>
        uiOnly ? sample.runPreflight() : live.runPreflight(buildSource())
      }
      hint={hint}
      onBusyChange={onBusyChange}
    >
      <Box
        role="radiogroup"
        aria-label="How to reach the repository"
        sx={{ ...twoColumns, gap: 1 }}
      >
        <WhereOption
          icon="mdi:github"
          label="Connect GitHub"
          short="Pick from a list · private repositories · fixes as pull requests"
          hint="With the GitHub App, merges are picked up at once and fixes come back as pull requests."
          on={viaGithub}
          onClick={() => set("via")(VIA.GITHUB)}
        />
        <WhereOption
          icon="solar:link-linear"
          label="Paste a repo link"
          short="Start building now · public or private repositories"
          hint="A private repository needs the GitHub App installed on it, named by its installation ID."
          on={!viaGithub}
          onClick={() => set("via")(VIA.LINK)}
        />
      </Box>
      {viaGithub ? (
        <GithubPicker
          value={github}
          onChange={setGithub}
          onScanStatus={setScanStatus}
          onUseToken={switchToToken}
        />
      ) : (
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
            <Stack spacing={1}>
              <Box sx={{ ...twoColumns, gap: 1 }}>
                <WhereOption
                  icon="mdi:github"
                  label="Install the GitHub App"
                  short="Name its installation · fixes as pull requests"
                  hint="The installation grants the runner read access to this repository."
                  on={!viaToken}
                  onClick={() => set("privateVia")(PRIVATE_VIA.APP)}
                />
                <WhereOption
                  icon="solar:key-minimalistic-linear"
                  label="Use an access token"
                  short="A personal access token or deploy key · read only"
                  hint="For when the app can't be installed. We read the code with it and check the branch once a day. Without the app, fixes come back as a patch."
                  on={viaToken}
                  onClick={() => set("privateVia")(PRIVATE_VIA.TOKEN)}
                />
              </Box>
              {viaToken ? (
                <Field
                  label="Access token or deploy key"
                  required
                  mono
                  type="password"
                  value={accessToken}
                  onChange={set("accessToken")}
                  placeholder="Paste the token"
                  helper="A fine-grained personal access token with Contents: read on this repository, or a read-only deploy key. Stored encrypted, never shown again."
                />
              ) : (
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
          )}
        </Stack>
      )}
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
