import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { DEFAULT_SCENARIOS } from "../../../panels/scenarioCountRules";
import { ROW_BOX_SX, T12 } from "../../drawerStyles";
import useDrawerForm from "../useDrawerForm";
import {
  ChipToggle,
  Field,
  FormCard,
  HintLine,
  Label,
  MONO,
  WhereOption,
  useUiOnlyBuild,
} from "../formBits";
import EnvValueRows from "../shared/EnvValueRows";
import FrameworkTuning from "../shared/FrameworkTuning";
import SecretField from "../shared/SecretField";
import TextLink from "../shared/TextLink";
import { GitHostConnect, NameSelect, RepoPicker } from "./GitHostPicker";
import RepoScan from "./RepoScan";
import {
  GIT_HOSTS,
  GIT_HOST_META,
  SAMPLE_REPOS,
  credentialKeysOf,
  dirOf,
  frameworkTuningOf,
  repoBranches,
} from "./sampleData";

/* Code that calls the model APIs itself has no framework to find the agent
   by, so the person names the function a conversation starts in. */
const DIRECT = "Direct API calls";
const ENTRY_EXAMPLE = "app/chat.py:handle_message";
const twoColumns = {
  display: "grid",
  gap: 1.5,
  gridTemplateColumns: "1fr 1fr",
};

const repoNameOf = (typed) =>
  typed
    .trim()
    .replace(/^https?:\/\/[^/]+\//, "")
    .replace(/\.git$/, "")
    .replace(/\/+$/, "");
const folderOfEntry = (entry) => (entry.trim() ? dirOf(entry) : "");

const SAMPLE_CHECKS = [
  { label: "Repository", detail: "Read at its branch · 128 source files" },
  { label: "OPENAI_API_KEY", detail: "Set and accepted by OpenAI" },
];

/**
 * A repository on GitLab or Bitbucket, or code that calls OpenAI, Anthropic
 * or Gemini directly (`direct`, any git host): connect the host, pick the
 * repository, and it's read for agents; or type the repository and the
 * folder its entry file lives in. The environment values the agent's code
 * reads are asked for on the same screen. UI only: connecting, the
 * repositories and the scan are samples, preflight is a sample and nothing
 * builds. `onBack` returns to where the form was opened from.
 */
export default function GitHostForm({
  item,
  onBusyChange,
  direct = false,
  onBack,
}) {
  const startHost = item.setup.host || "github";
  const [connected, setConnected] = useState({});
  const [adding, setAdding] = useState(false);
  const [scanStatus, setScanStatus] = useState(null);
  const [changingBranch, setChangingBranch] = useState(false);
  const build = useUiOnlyBuild(SAMPLE_CHECKS);
  const [form, set, setForm] = useDrawerForm(
    {
      host: startHost,
      mode: "picker",
      repo: "",
      manualRepo: "",
      branch: "main",
      agentId: null,
      entry: "",
      folder: "",
      visibility: "public",
      privateVia: "app",
      accessToken: "",
      envValues: [],
      credFiles: [],
      egress: "",
      scenarioCount: DEFAULT_SCENARIOS,
    },
    build,
  );
  // Several fields at once; like `set`, it drops a finished preflight.
  const update = (patch) => {
    setForm((s) => ({ ...s, ...patch }));
    build.resetPreflight();
  };
  const repoName =
    form.mode === "picker" ? form.repo : repoNameOf(form.manualRepo);

  const { host, mode, branch, agentId, entry, folder, visibility, privateVia } =
    form;
  const meta = GIT_HOST_META[host];
  const hostLabel = meta.label;
  const picker = mode === "picker";
  // A pasted GitHub link: it can start at the repository root.
  const pastedLink = host === "github" && !picker;
  const privatePasted =
    pastedLink &&
    visibility === "private" &&
    (privateVia !== "token" || !form.accessToken.trim());
  const accounts = connected[host] || [];
  const hostRepos =
    host === "github"
      ? SAMPLE_REPOS.github
      : SAMPLE_REPOS[host].filter((r) =>
          accounts.some((a) => r.name.startsWith(`${a}/`)),
        );
  const repo = picker
    ? hostRepos.find((r) => r.name === form.repo) || null
    : repoName
      ? {
          name: repoName,
          language: /\.ts|typescript|-web|-chat/i.test(form.manualRepo)
            ? "TypeScript"
            : "Python",
          agents: [],
        }
      : null;
  const pickedAgent = picker
    ? repo?.agents.find((a) => a.id === agentId) || null
    : null;
  const isDirect = pickedAgent
    ? pickedAgent.framework === DIRECT
    : !!repo && (!picker || direct || !!entry.trim());
  const scanBusy =
    picker && !!repo && (!scanStatus || scanStatus === "scanning");
  const scanFailed =
    picker &&
    !!repo &&
    (scanStatus === "error" || (scanStatus === "timeout" && !entry.trim()));
  const ready =
    !!repo &&
    (!!folder.trim() || pastedLink) &&
    !!branch.trim() &&
    !privatePasted &&
    !scanBusy &&
    !scanFailed;
  const missing = ready
    ? null
    : !repo
      ? picker
        ? host !== "github" && !accounts.length
          ? `Connect ${hostLabel} first`
          : "Choose a repository"
        : pastedLink
          ? "Paste the repository link"
          : "Add a repository"
      : privatePasted
        ? privateVia === "token"
          ? "Add the access token"
          : "Private repositories need the GitHub App or an access token"
        : scanBusy
          ? `Reading ${repo.name}…`
          : scanFailed
            ? "Retry reading the repository"
            : !branch.trim()
              ? "Add the branch"
              : scanStatus === "none"
                ? "Name the entry file"
                : picker
                  ? "Choose the agent"
                  : "Add the agent folder";
  const agentChosen =
    !!repo && (pastedLink || (!!folder.trim() && !scanBusy && !scanFailed));
  const expectedNames = agentChosen
    ? credentialKeysOf(repo, pickedAgent || { framework: DIRECT, entry })
    : [];

  const pickHost = (next) => {
    if (next === host) return;
    setAdding(false);
    setScanStatus(null);
    update({
      host: next,
      mode: "picker",
      repo: "",
      agentId: null,
      folder: "",
      entry: "",
    });
  };
  const pickRepo = (name) => {
    setScanStatus(null);
    setChangingBranch(false);
    update({
      repo: name,
      agentId: null,
      folder: "",
      entry: "",
      branch: "main",
    });
  };
  const pickAgent = (a) =>
    update({
      agentId: a.id,
      folder: dirOf(a.entry),
      entry: a.framework === DIRECT ? a.entry : "",
    });
  const nameEntryFile = (v) =>
    update({ entry: v, agentId: null, folder: folderOfEntry(v) });

  const intro = `${
    direct
      ? "Pick the git host, the repository and the agent in it."
      : `Connect ${hostLabel} to pick the repository and the agent in it.`
  } We read the code when you connect, then you can confirm or correct what we found on the environment.`;

  return (
    <FormCard
      uiOnly
      intro={intro}
      build={build}
      scenarioCount={form.scenarioCount}
      onScenarioCount={set("scenarioCount")}
      canRun={ready}
      blockedReason="Pick the repository and the agent, then preflight reads it and checks its credentials."
      onRun={build.runPreflight}
      hint={missing}
      onBusyChange={onBusyChange}
    >
      {onBack && (
        <TextLink onClick={onBack}>
          <Iconify
            icon="solar:alt-arrow-left-linear"
            width={12}
            sx={{ mr: 0.5 }}
          />
          Back to our SDK
        </TextLink>
      )}
      {direct && (
        <Box>
          <Label>Git host</Label>
          <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.75, mt: 0.75 }}>
            {GIT_HOSTS.map((h) => (
              <ChipToggle
                key={h}
                icon={GIT_HOST_META[h].icon}
                label={GIT_HOST_META[h].label}
                on={host === h}
                onClick={() => pickHost(h)}
              />
            ))}
          </Box>
        </Box>
      )}

      {host === "github" && (
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
            on={picker}
            onClick={() => update({ mode: "picker" })}
          />
          <WhereOption
            icon="solar:link-linear"
            label="Paste a repo link"
            short="Start building now · public repositories"
            hint="We check the branch daily; Sync picks up a change now. Without the app, fixes come back as a patch."
            on={!picker}
            onClick={() => update({ mode: "manual", folder: "" })}
          />
        </Box>
      )}

      {picker && host !== "github" && (!accounts.length || adding) && (
        <Box>
          <GitHostConnect
            host={host}
            except={adding ? accounts : []}
            onConnected={(account) => {
              setConnected((c) => ({
                ...c,
                [host]: [...new Set([...(c[host] || []), account])],
              }));
              setAdding(false);
            }}
            onCancel={adding ? () => setAdding(false) : undefined}
          />
          <TextLink
            onClick={() => update({ mode: "manual", folder: "" })}
            sx={{ mt: 0.75 }}
          >
            {`Enter a ${hostLabel} repository URL instead`}
          </TextLink>
        </Box>
      )}
      {picker && (host === "github" || (accounts.length > 0 && !adding)) && (
        <RepoPicker
          host={host}
          accounts={host === "github" ? [] : accounts}
          repos={hostRepos}
          repo={form.repo}
          onPick={pickRepo}
          onAddAccount={host === "github" ? undefined : () => setAdding(true)}
        />
      )}

      {!picker && (
        <Stack spacing={1.5}>
          <Field
            label={pastedLink ? "Repository link" : `${hostLabel} repository`}
            required
            mono
            value={form.manualRepo}
            onChange={(v) =>
              update({
                manualRepo: v,
                folder: pastedLink ? folderOfEntry(entry) : "",
              })
            }
            placeholder={
              pastedLink
                ? "https://github.com/owner/repo"
                : `${meta.accountPlaceholder}/repo`
            }
            helper={
              pastedLink
                ? "We read the code so scenarios stay in step with the agent's tools."
                : `Read once now. Sync and fix ${meta.review}s need the ${hostLabel} connection.`
            }
          />
          {pastedLink ? (
            <>
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
                  onChange={(v) =>
                    update({
                      entry: v,
                      folder: v.includes("/") ? v.replace(/\/[^/]*$/, "") : "",
                    })
                  }
                  placeholder="src/agent/index.ts"
                />
              </Box>
              <Box>
                <Label>Visibility</Label>
                <Box sx={{ ...twoColumns, gap: 0.75, mt: 0.75 }}>
                  <ChipToggle
                    label="Public"
                    on={visibility === "public"}
                    onClick={() => update({ visibility: "public" })}
                    fullWidth
                  />
                  <ChipToggle
                    label="Private"
                    on={visibility === "private"}
                    onClick={() => update({ visibility: "private" })}
                    fullWidth
                  />
                </Box>
              </Box>
              {visibility === "private" && (
                <Stack spacing={1}>
                  <Box sx={{ ...twoColumns, gap: 1 }}>
                    <WhereOption
                      icon="mdi:github"
                      label="Install the GitHub App"
                      short="Pick from a list · fixes as pull requests"
                      hint="Install once per account; merges are seen at once and fixes come back as pull requests."
                      on={privateVia === "app"}
                      onClick={() => update({ privateVia: "app" })}
                    />
                    <WhereOption
                      icon="solar:key-minimalistic-linear"
                      label="Use an access token"
                      short="A personal access token or deploy key · read only"
                      hint="For when the app can't be installed. We read the code with it and check the branch once a day; without the app, fixes come back as a patch."
                      on={privateVia === "token"}
                      onClick={() => update({ privateVia: "token" })}
                    />
                  </Box>
                  {privateVia === "app" ? (
                    <Stack
                      direction="row"
                      alignItems="center"
                      spacing={1.25}
                      sx={{ ...ROW_BOX_SX, px: 1.5, py: 1.25 }}
                    >
                      <Iconify
                        icon="solar:lock-keyhole-minimalistic-linear"
                        width={16}
                        sx={{ color: "text.secondary", flexShrink: 0 }}
                      />
                      <Typography sx={{ ...T12, flex: 1, minWidth: 0 }}>
                        Install the app once, then pick this repository from the
                        list.
                      </Typography>
                      <TextLink
                        onClick={() =>
                          update({ mode: "picker", visibility: "public" })
                        }
                      >
                        Connect GitHub
                      </TextLink>
                    </Stack>
                  ) : (
                    <SecretField
                      label="Access token or deploy key"
                      required
                      value={form.accessToken}
                      onChange={set("accessToken")}
                      placeholder="Paste the token"
                      helper="A fine-grained personal access token with Contents: read on this repository, or a read-only deploy key. Stored encrypted, never shown again, never sent to the improver."
                    />
                  )}
                </Stack>
              )}
            </>
          ) : (
            <Field
              label="Agent folder"
              required
              mono
              value={folder}
              onChange={set("folder")}
              placeholder="agents/returns"
              helper={
                <HintLine
                  text="The folder the agent's entry file lives in."
                  hint="A monorepo can hold several agents."
                />
              }
            />
          )}
          {!pastedLink && (
            <TextLink
              onClick={() => update({ mode: "picker" })}
            >{`Pick from ${hostLabel} instead`}</TextLink>
          )}
        </Stack>
      )}

      {repo &&
        !pastedLink &&
        (changingBranch ? (
          <Box sx={{ maxWidth: 320 }}>
            {picker ? (
              <NameSelect
                label="Branch"
                value={branch}
                options={repoBranches(repo).map((name) => ({ name }))}
                onChange={(b) => {
                  setChangingBranch(false);
                  setScanStatus(null);
                  update({ branch: b, agentId: null, folder: "", entry: "" });
                }}
                renderMeta={(o) => (o.name === "main" ? "default" : "")}
              />
            ) : (
              <Field
                label="Branch or tag"
                mono
                value={branch}
                onChange={set("branch")}
              />
            )}
          </Box>
        ) : (
          <Typography component="div" sx={{ ...T12, color: "text.secondary" }}>
            {"Reads "}
            <Box
              component="span"
              sx={{ fontFamily: MONO, color: "text.primary" }}
            >
              {branch}
            </Box>
            {". A fix merged there counts as released. "}
            <TextLink
              onClick={() => setChangingBranch(true)}
              sx={{ display: "inline-flex" }}
            >
              Change
            </TextLink>
          </Typography>
        ))}

      {picker && repo && (
        <RepoScan
          repo={repo}
          branch={branch}
          hostLabel={hostLabel}
          agentId={agentId}
          onPick={pickAgent}
          entryFile={entry}
          onEntryFile={nameEntryFile}
          onStatus={setScanStatus}
        />
      )}

      {pickedAgent && (
        <FrameworkTuning tuning={frameworkTuningOf(pickedAgent.framework)} />
      )}
      {isDirect && !pastedLink && (!picker || !!pickedAgent) && (
        <Field
          label={
            pickedAgent
              ? "Where the agent starts"
              : "Where the agent starts (when it uses no framework)"
          }
          mono
          value={entry}
          onChange={set("entry")}
          placeholder={ENTRY_EXAMPLE}
          helper={
            <HintLine
              text={
                pickedAgent
                  ? `${pickedAgent.sdk || "Model API"} calls, no framework. Check the function each conversation starts in.`
                  : "File and function each conversation starts in."
              }
              hint="With no framework, we read the prompts and tools reached from the function you name."
            />
          }
        />
      )}

      {agentChosen && (
        <EnvValueRows
          sample
          values={form.envValues}
          onValues={set("envValues")}
          files={form.credFiles}
          onFiles={set("credFiles")}
          expectedNames={expectedNames}
          egress={form.egress}
          onEgress={set("egress")}
        />
      )}
    </FormCard>
  );
}
GitHostForm.propTypes = {
  item: PropTypes.object.isRequired,
  onBusyChange: PropTypes.func,
  direct: PropTypes.bool,
  onBack: PropTypes.func,
};
