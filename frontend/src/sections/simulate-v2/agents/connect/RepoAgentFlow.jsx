import PropTypes from "prop-types";
import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  AlertTitle,
  Box,
  MenuItem,
  Select,
  Stack,
  Typography,
} from "@mui/material";
import { useNavigate } from "react-router-dom";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import {
  FRAMEWORK_META,
  agentIdOf,
  agentRecordFromSource,
} from "../../_mock/agentRegistry";
import {
  GIT_HOST_META,
  gitHostOfUrl,
  gitHostRepos,
  hostRepoNameOf,
  hostRepoUrlOf,
  installedRepos,
  reviewNounOf,
  scannedAgentSource,
  useIntegrations,
} from "../../_mock/integrations";
import { useConnectedAgents } from "../../store";
import {
  entryForTraces,
  folderForTraces,
  frameworkTuningOf,
  observeProjectById,
  readAgentFolder,
  repoBranches,
  repoChecksOf,
  repoDetectionOf,
  repoFolders,
  repoForTraces,
  traceMatchOf,
} from "../../_mock/connectAgent";
import { isSecretRef, keyRefOf } from "../../_mock/secretRefs";
import GithubRepoPicker from "../../integrations/GithubRepoPicker";
import { ChipCard, Field, Label } from "../../environments/StartEnvironment";
import ConnectSteps from "./ConnectSteps";
import EnvironmentValues from "./EnvironmentValues";
import { envValuesSourceOf } from "../../_mock/envValues";
import { FrameworkTuning, TextLink, WhereOption } from "./connectBits";
import GitHostRepoPicker from "./GitHostRepoPicker";
import RepoAgentScan from "./RepoAgentScan";
import SecretRefField from "./SecretRefField";
import { envValuesFromRepo, prefillFor } from "./repoScan";
import { HintLine } from "./replicationBits";
import { recommendedRunLocation } from "../../_mock/replication";
import { useConnectSubmitLabel } from "./connectSubmit";
import {
  credentialCheck,
  egressCheck,
  factCheck,
  parseEgress,
  usePreflight,
} from "./preflight";

const MONO = "ui-monospace, Menlo, monospace";
/* One screen: which agent, then Connect. What reading the code finds is
   applied as it stands — Finish setting up on the agent's Overview lists it
   to confirm or correct ("Check what we found"), with the Observe project. */
const STEPS = ["Connect"];

/* The git host is one setting on the agent — each is a connection. */
const HOSTS = [
  { id: "github", label: "GitHub", icon: "mdi:github" },
  { id: "gitlab", label: "GitLab", icon: "mdi:gitlab" },
  { id: "bitbucket", label: "Bitbucket", icon: "mdi:bitbucket" },
];
const HOST_LABEL = {
  github: "GitHub",
  gitlab: "GitLab",
  bitbucket: "Bitbucket",
};
/* Code that calls the model APIs itself has no framework to find the agent
   by, so the person names the function a conversation starts in. */
const DIRECT = "Direct API calls";
const ENTRY_EXAMPLE = "app/chat.py:handle_message";

/**
 * A repository, in one step: the git host (GitHub through its App, GitLab or
 * Bitbucket through the workspace's connection), the repository — read for
 * agents when it's picked — and the agent found in it, or the entry file
 * when none is; or, typed by hand, the folder its entry file lives in. The
 * environment values that agent's code reads are asked for on the same
 * screen — prefilled from any agent already connected from the repository. Connect reads that folder at the branch and saves the agent with
 * what it found; side-effect tools start stubbed, constraints start at the
 * defaults, and everything is reviewed afterwards on the agent's page.
 */
export default function RepoAgentFlow({
  title,
  preset,
  onSave,
  prefill,
  initial = null,
}) {
  const submitLabel = useConnectSubmitLabel();
  const integrations = useIntegrations();
  const navigate = useNavigate();
  const { agents: connectedAgents } = useConnectedAgents();
  /*
    Opened from an Error Feed issue whose Observe project is traced with
    traceAI: the traces already say which code this is — the service, the
    framework, the tools. The repository and folder are suggested from it,
    what we read in the code is checked against it (a mismatch blocks — it
    would point Fix at a different agent), the issue's Observe project comes
    with it, and the channel comes from the traces.
  */
  const project = prefill?.project ? observeProjectById(prefill.project) : null;
  const tracedCode = project?.traced?.via === "traceai" ? project.traced : null;
  const tracedFw = tracedCode
    ? FRAMEWORK_META[tracedCode.framework]?.label || tracedCode.framework
    : null;
  const suggestedRepo = tracedCode
    ? repoForTraces(tracedCode, installedRepos(integrations))
    : null;
  /* A pasted repository: picked from its host's connection when it can see it, otherwise typed in. */
  const [host, setHost] = useState(
    initial?.host || gitHostOfUrl(initial?.repo) || "github",
  );
  const reposOn = (h) =>
    h === "github"
      ? installedRepos(integrations)
      : gitHostRepos(h, integrations);
  const pasted = initial?.repo ? hostRepoNameOf(initial.repo) : null;
  const pastedVisible =
    !!pasted &&
    reposOn(host).some((r) => r.name.toLowerCase() === pasted.toLowerCase());
  /* The agent to start on: a preset's, or one picked from a connected account (New agent). */
  const startAgentId = preset?.agent?.id || initial?.agentId || null;
  const presetFolder = useMemo(() => {
    const repo = installedRepos(integrations).find(
      (r) => r.name === (preset?.repo || (pastedVisible ? pasted : null)),
    );
    return (
      repoFolders(repo).find((f) =>
        (f.agents || []).some((a) => a.id === startAgentId),
      )?.path || ""
    );
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  /* GitHub: connect the app and pick from its repositories, or paste a
     repository link and start — whichever the person has. Without the app
     installed, pasting a link is where it starts. */
  const githubReady = installedRepos(integrations).length > 0;
  const [mode, setMode] = useState(
    (pasted && !pastedVisible) ||
      (host === "github" && !githubReady && !preset?.repo && !suggestedRepo)
      ? "manual"
      : "github",
  );
  const [ghRepo, setGhRepo] = useState(
    preset?.repo || (pastedVisible ? pasted : "") || suggestedRepo || "",
  );
  /* Access granted on GitHub after opening: pick the matching repository then. */
  useEffect(() => {
    if (!ghRepo && suggestedRepo) setGhRepo(suggestedRepo);
  }, [suggestedRepo]); // eslint-disable-line react-hooks/exhaustive-deps
  const [manualRepo, setManualRepo] = useState(
    pasted && !pastedVisible ? pasted : "",
  );
  const [branch, setBranch] = useState("main");
  const [folder, setFolder] = useState(presetFolder);
  const [reading, setReading] = useState(false);
  const [found, setFound] = useState(null);
  const [facts, setFacts] = useState({});
  /* What must hold before it's simulated: tools that must never run for
     real and the repository's own tests. The test credentials to use are
     added in "Check what we found" on the agent's Overview. */
  const [checks, setChecks] = useState(null);
  const [neverReal, setNeverReal] = useState([]);
  const [testsFact, setTestsFact] = useState("");
  /* The rest of what reading found (detected), and what the person settled
     (confirmed): which entry point when there are several, where prompts
     built at runtime come from, whether the model is chosen at runtime, the
     reference each credential key name resolves through. */
  const [detected, setDetected] = useState(null);
  const [entryId, setEntryId] = useState(startAgentId);
  /* Where a direct-API agent starts, as the person named it. */
  const [entryPoint, setEntryPoint] = useState(
    preset?.agent?.framework === DIRECT ? preset.agent.entry : "",
  );
  const [pointers, setPointers] = useState({});
  const [modelAtRuntime, setModelAtRuntime] = useState(false);
  const [keyRefs, setKeyRefs] = useState({});
  /* The test credentials and settings our sandbox runs the code with —
     .env values (secrets by reference) and a credential file. */
  /* A pasted link reads a public repository; a private one is read
     through the GitHub App. */
  const [visibility, setVisibility] = useState("public");
  /* A private repository without the GitHub App: read with an access token
     or deploy key instead, by reference. */
  const [privateVia, setPrivateVia] = useState("app");
  const [accessRef, setAccessRef] = useState("");
  const savedAccessRef = keyRefOf(
    accessRef,
    "vault://acme/sim/github_repo_token",
  );
  /* Reading the picked repository for agents: scanning | error | timeout | found | none. */
  const [scanStatus, setScanStatus] = useState(null);
  const [envValues, setEnvValues] = useState([]);
  const [credFiles, setCredFiles] = useState([]);
  /* Public hostnames the sandbox may reach while the agent runs — hardcoded
     APIs, TURN endpoints. Everything else is blocked. */
  const [egressText, setEgressText] = useState("");
  const egress = parseEgress(egressText);
  const envRefOf = (name) =>
    envValues.find((v) => v.name === name && v.secret)?.ref;

  /* "github" mode is picking from the host's connection, whichever host. */
  const viaGithub = mode === "github";
  /* A pasted GitHub link — it can start at the repository root. */
  const pastedLink = host === "github" && mode === "manual";
  /* Private and not readable yet: the app, or a token not given. */
  const privatePasted =
    pastedLink &&
    visibility === "private" &&
    (privateVia !== "token" || !savedAccessRef);
  const hostLabel = HOST_LABEL[host];
  const review = reviewNounOf(host);
  const repo = viaGithub
    ? reposOn(host).find((r) => r.name === ghRepo) || null
    : manualRepo.trim()
      ? {
          name: manualRepo
            .trim()
            .replace(/^https?:\/\/[^/]+\//, "")
            .replace(/\.git$/, "")
            .replace(/\/+$/, ""),
          language: /\.ts|typescript|-web|-chat/i.test(manualRepo)
            ? "TypeScript"
            : "Python",
          agents: [],
        }
      : null;
  const branches = useMemo(() => repoBranches(repo), [repo?.name]); // eslint-disable-line react-hooks/exhaustive-deps

  /* Read one entry point of the folder — again when another is picked. */
  const apply = (asked) => {
    const dir = folder.trim().replace(/\/+$/, "");
    const pickedEntry =
      asked || (tracedCode ? entryForTraces(tracedCode, repo, dir) : null);
    const r = readAgentFolder({
      repo,
      folder: dir,
      branch,
      entryId: pickedEntry,
    });
    const d = repoDetectionOf({ repo, folder: dir, branch, found: r });
    setFound(r);
    setDetected(d);
    setEntryId(
      (d.entryPoints.find((e) => e.id === pickedEntry) || d.entryPoints[0])
        ?.id || null,
    );
    setFacts({
      name: r.name,
      ...Object.fromEntries(
        Object.entries(r.facts).map(([k, f]) => [k, f.value]),
      ),
      language: d.language.value,
      install: d.install.value,
    });
    setPointers({});
    setModelAtRuntime(d.modelAtRuntime.value);
    setKeyRefs({});
    const c = repoChecksOf({ repo, folder: dir, tools: r.facts.tools.value });
    setChecks(c);
    /* Safe by default: every tool that looks like it writes is stubbed. */
    setNeverReal(c.sideEffects.value);
    setTestsFact(c.tests.value);
  };
  /* We build it fresh from the code in our hosted sandbox. */
  const runLocation = recommendedRunLocation();
  const livekit = /livekit|pipecat/i.test(found?.framework || "");
  /* The agent the scan found and the person picked, before anything is read. */
  const pickedAgent = viaGithub
    ? (repo?.agents || []).find((a) => a.id === entryId) || null
    : null;
  /* Opened as "no framework", typed by hand, or named after a read found nothing: ask where it starts. */
  const direct = pickedAgent
    ? pickedAgent.framework === DIRECT
    : !!repo && (!viaGithub || !!initial?.direct || !!entryPoint.trim());
  /* The read of the picked repository is what a connection waits on. */
  const scanBusy =
    viaGithub && !!repo && (!scanStatus || scanStatus === "scanning");
  /* A failed read blocks — unless a timed-out one was given the entry file to read instead. */
  const scanFailed =
    viaGithub &&
    !!repo &&
    (scanStatus === "error" ||
      (scanStatus === "timeout" && !entryPoint.trim()));
  const typedUrl = manualRepo.trim();
  const manualUrl =
    !typedUrl || host === "github" || gitHostOfUrl(typedUrl)
      ? typedUrl
      : hostRepoUrlOf(host, hostRepoNameOf(typedUrl));
  const source = {
    kind: "repo",
    /* The git host is a setting on the agent, not a kind of agent. */
    provider: host,
    gitHost: host,
    /* The channel the traces show; else a LiveKit or Pipecat agent takes
       calls and anything else defaults to chat. */
    agentType: tracedCode?.channel || (livekit ? "voice" : "chat"),
    value: viaGithub ? hostRepoUrlOf(host, ghRepo) : manualUrl,
    ref: branch.trim() || "main",
    /* Through the GitHub App a merge is seen at once; a pasted link is
       checked once a day — or Synced any time. */
    releaseDetection: {
      watch: "branch",
      ref: (branch || "main").trim(),
      ...(pastedLink && { dailySync: true }),
    },
    folder: folder.trim(),
    entry: (direct && entryPoint.trim()) || facts.entry || "",
    ...(viaGithub && host === "github" && { githubApp: true }),
    ...(viaGithub && host !== "github" && { hostConnection: host }),
    framework: found?.framework,
    name: (facts.name || "").trim(),
    prompts: facts.prompts || [],
    tools: (facts.tools || []).map((name) => ({ name, desc: "", args: [] })),
    model: facts.model,
    runCommand: facts.run,
    runLocation,
    ...(checks && {
      neverRunForReal: neverReal.filter((t) => (facts.tools || []).includes(t)),
      repoTests: {
        command: (testsFact || "").trim(),
        checks: checks.tests.checks,
      },
    }),
    ...(detected && {
      language: facts.language,
      installCommand: facts.install,
      ...(modelAtRuntime && { modelAtRuntime: true }),
      runtimePrompts: detected.runtimePrompts.map((p) => ({
        kind: p.kind,
        what: p.what,
        where: p.where,
        ...((pointers[p.id] || "").trim() && {
          pointer: pointers[p.id].trim(),
        }),
      })),
      /* Key names and the reference each resolves through — never a value. */
      credentialKeys: detected.credentialKeys.value.map((name) => {
        const ref = isSecretRef(keyRefs[name])
          ? keyRefs[name].trim()
          : envRefOf(name);
        return { name, ...(ref && { ref }) };
      }),
      fagiPrompts: detected.fagiPrompts.value,
      ...(detected.members.length && {
        members: detected.members.map((m) => ({
          ...m,
          instructions: m.promptRef,
        })),
      }),
    }),
    ...envValuesSourceOf({ values: envValues, files: credFiles }),
    ...(egress.length && { allowedEgressDomains: egress }),
    ...(pastedLink && { repoVisibility: visibility }),
    ...(pastedLink &&
      visibility === "private" &&
      privateVia === "token" &&
      savedAccessRef && { repoAccess: { kind: "token", ref: savedAccessRef } }),
    versionNote: `Read from ${repo?.name || "the repository"} @ ${branch.trim() || "main"}.`,
  };

  /* What we read, against what the traces said. */
  const match =
    tracedCode && found
      ? traceMatchOf(tracedCode, {
          framework: found.framework,
          tools: facts.tools,
          model: facts.model,
        })
      : null;
  const mismatch = !!match && !match.ok;

  /* Connect reads the folder, then saves once what it read is in state. */
  const [saveWanted, setSaveWanted] = useState(false);
  const [changingBranch, setChangingBranch] = useState(false);
  const folderOfAgent = (r, agentId) =>
    repoFolders(r).find((f) => (f.agents || []).some((a) => a.id === agentId))
      ?.path || "";
  const connect = () => {
    setReading(true);
    setTimeout(() => {
      apply(entryId);
      setReading(false);
      setSaveWanted(true);
    }, 900);
  };
  useEffect(() => {
    if (!saveWanted || !found) return;
    setSaveWanted(false);
    /* A folder that isn't the agent behind the issue's traces would point Fix at another agent. */
    if (mismatch) return;
    onSave(source);
  }, [saveWanted, found]); // eslint-disable-line react-hooks/exhaustive-deps

  const ready =
    !!repo &&
    (!!folder.trim() || pastedLink) &&
    !!branch.trim() &&
    !privatePasted &&
    !scanBusy &&
    !scanFailed;

  /* Preflight reads the folder at the branch — files scanned, the services it
     talks to — and checks each credential the code reads is set and accepted. */
  const scan = useMemo(() => {
    if (!ready) return null;
    const dir = folder.trim().replace(/\/+$/, "");
    const r = readAgentFolder({ repo, folder: dir, branch, entryId });
    const d = repoDetectionOf({ repo, folder: dir, branch, found: r });
    const keys = d.credentialKeys.value || [];
    const files = 40 + ((`${repo.name}/${dir}`.length * 37) % 180);
    const connectors = [...new Set(keys.map(connectorOfKey).filter(Boolean))];
    return { files, keys, connectors };
  }, [ready, repo?.name, folder, branch, entryId]); // eslint-disable-line react-hooks/exhaustive-deps
  const secretKeys = (scan?.keys || []).filter((k) =>
    /KEY|TOKEN|SECRET|PASSWORD|DSN|_URL$/i.test(k),
  );
  const preflight = usePreflight(
    [
      factCheck({
        id: "repo",
        label: "Repository",
        ok: ready,
        okText: `${repo?.name || ""} @ ${branch.trim() || "main"} — ${scan?.files || 0} source files scanned.`,
        failText: privatePasted
          ? "Private repositories need the GitHub App or an access token."
          : "Pick the repository and the agent.",
      }),
      ...(pastedLink && visibility === "private" && privateVia === "token"
        ? [
            credentialCheck({
              id: "access",
              label: "Access token",
              ref: savedAccessRef,
              provider: "GitHub",
            }),
          ]
        : []),
      ...secretKeys.map((k) =>
        credentialCheck({
          id: `env:${k}`,
          label: k,
          ref: envRefOf(k) || envValues.find((v) => v.name === k)?.value || "",
          provider: connectorOfKey(k),
        }),
      ),
      ...(egress.length ? [egressCheck(egress)] : []),
    ],
    [
      repo?.name,
      folder,
      branch,
      entryId,
      envValues,
      egressText,
      visibility,
      privateVia,
      savedAccessRef,
    ],
  );

  /* The agent is chosen: its environment values are asked for — prefilled
     from what agents already connected from the same repository were given. */
  const agentChosen =
    !!repo && (pastedLink || (!!folder.trim() && !scanBusy && !scanFailed));
  const remembered = useMemo(
    () => envValuesFromRepo(connectedAgents, repo?.name),
    [connectedAgents, repo?.name],
  );
  const agentKeys = detected?.credentialKeys.value || scan?.keys || [];
  useEffect(() => {
    if (!agentChosen || !remembered.values.length) return;
    const want = prefillFor(remembered.values, agentKeys);
    setEnvValues((cur) => [
      ...cur,
      ...want.filter((w) => !cur.some((c) => c.name === w.name)),
    ]);
  }, [agentChosen, repo?.name, folder]); // eslint-disable-line react-hooks/exhaustive-deps
  const next = {
    label: submitLabel,
    icon: "solar:link-circle-linear",
    disabled: !ready || mismatch,
    hint: mismatch
      ? `Doesn't match ${project.name}'s traces`
      : !repo
        ? viaGithub
          ? "Choose a repository"
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
              : scanStatus === "none" && !folder.trim()
                ? "Name the entry file"
                : "Choose the agent",
    busy: reading ? `Reading ${folder || "the agent"} at ${branch}…` : null,
    onClick: connect,
  };

  /* GitHub only: the two ways in, always in this order. */
  const githubOption = (
    <WhereOption
      key="github"
      icon="mdi:github"
      label="Connect GitHub"
      on={viaGithub}
      onClick={() => setMode("github")}
      short="Pick from a list · private repositories · fixes as pull requests"
      hint="Install the GitHub App once. Every repository it can see is listed, a merge on the branch is picked up at once, and fixes come back as pull requests."
    />
  );
  const linkOption = (
    <WhereOption
      key="link"
      icon="solar:link-linear"
      label="Paste a repo link"
      on={!viaGithub}
      onClick={() => setMode("manual")}
      short="Start building now · public repositories"
      hint="We read the code when you connect and check the branch once a day — Sync any time to pick up a change now. Without the GitHub App, fixes come back as a patch."
    />
  );

  return (
    <ConnectSteps
      title={title}
      subtitle={`${
        !initial?.host || initial?.direct
          ? "Pick the git host, the repository and the agent in it."
          : host === "github"
            ? "Connect GitHub to pick from your repositories, or paste a repository link and start."
            : `Connect ${hostLabel} to pick the repository and the agent in it.`
      } We read the code when you connect — then confirm or correct what we found in Finish setting up, on the agent's Overview.`}
      steps={STEPS}
      active={0}
      next={next}
      preflight={{
        state: preflight,
        canRun: ready && !mismatch,
        blockedReason: !ready
          ? "Pick the repository and the agent — then preflight reads it and checks its credentials."
          : null,
        summary: scan
          ? `${scan.files} source files scanned${scan.connectors.length ? ` · Detected: ${scan.connectors.join(", ")}` : ""}`
          : null,
      }}
    >
      <Stack spacing={1.75}>
        {tracedCode && (
          <Typography
            component="div"
            sx={{
              typography: "s3",
              color: "text.secondary",
              px: 1.5,
              py: 1,
              borderRadius: 1,
              border: "1px solid",
              borderColor: "divider",
            }}
          >
            {suggestedRepo ? (
              <>
                {`Suggested from ${project.name}'s traces — service `}
                <Box component="span" sx={{ fontFamily: MONO }}>
                  {tracedCode.service}
                </Box>
                {` · ${tracedFw}.`}
              </>
            ) : (
              <>
                {`None of the repositories the GitHub App can see matches ${project.name}'s traces — service `}
                <Box component="span" sx={{ fontFamily: MONO }}>
                  {tracedCode.service}
                </Box>
                {` · ${tracedFw}. If its code is in another repository, give the app access with "Add it on GitHub" below.`}
              </>
            )}
          </Typography>
        )}
        {/* Picked on the directory (GitHub, GitLab, Bitbucket): nothing to
            switch. Direct model calls can live on any host, so they choose. */}
        {(!initial?.host || initial?.direct) && (
          <Box>
            <Label>Git host</Label>
            <Box
              sx={{ display: "flex", flexWrap: "wrap", gap: 0.75, mt: 0.75 }}
            >
              {HOSTS.map((h) => (
                <ChipCard
                  key={h.id}
                  icon={h.icon}
                  label={h.label}
                  on={host === h.id}
                  onClick={() => {
                    if (h.id === host) return;
                    setHost(h.id);
                    setMode("github");
                    setGhRepo("");
                    setEntryId(null);
                    setFolder("");
                    setFound(null);
                    setEntryPoint("");
                  }}
                />
              ))}
            </Box>
          </Box>
        )}
        {viaGithub && host !== "github" ? (
          <GitHostRepoPicker
            host={host}
            repo={ghRepo}
            onPick={({ repo: r }) => {
              setGhRepo(r);
              setEntryId(null);
              setFolder("");
              setEntryPoint("");
              setFound(null);
            }}
            onManual={(pre) => {
              setMode("manual");
              if (pre?.repo) setManualRepo(pre.repo);
            }}
          />
        ) : null}
        {host === "github" && (
          <Box
            sx={{
              display: "grid",
              gap: 1,
              gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" },
            }}
          >
            {/* Always Connect GitHub, then Paste a repo link — only which one is picked changes. */}
            {[githubOption, linkOption]}
          </Box>
        )}
        {viaGithub && host !== "github" ? null : viaGithub ? (
          <Box>
            {/* The repository only — it's read for agents below, once picked. */}
            <GithubRepoPicker
              manualLinks={false}
              showAgents={false}
              repo={ghRepo}
              agentId={entryId}
              onPick={({ repo: r }) => {
                setGhRepo(r);
                const picked = installedRepos(integrations).find(
                  (x) => x.name === r,
                );
                setEntryId(null);
                setFolder(
                  (tracedCode && folderForTraces(tracedCode, picked)) || "",
                );
                setEntryPoint("");
                setFound(null);
              }}
              onManual={(pre) => {
                setMode("manual");
                if (pre?.repo) setManualRepo(pre.repo);
              }}
            />
            {!integrations.github && (
              <TextLink
                onClick={() => {
                  setMode("manual");
                  setVisibility("private");
                  setPrivateVia("token");
                }}
                sx={{ mt: 0.75 }}
              >
                Can&apos;t install the app? Paste the repository link and use an
                access token
              </TextLink>
            )}
          </Box>
        ) : (
          <Stack spacing={1.5}>
            <Field
              label={pastedLink ? "Repository link" : `${hostLabel} repository`}
              required
              mono
              placeholder={
                pastedLink
                  ? "https://github.com/owner/repo"
                  : `${GIT_HOST_META[host].accountPlaceholder}/repo`
              }
              value={manualRepo}
              onChange={(v) => {
                setManualRepo(v);
                setFolder("");
              }}
              helper={
                pastedLink
                  ? "We read the code so scenarios stay in step with the agent's tools."
                  : `Read once now — Sync and fix ${review.long}s need the ${hostLabel} connection.`
              }
            />
            {pastedLink ? (
              <>
                <Box
                  sx={{
                    display: "grid",
                    gap: 1.5,
                    gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" },
                  }}
                >
                  <Field
                    label="Branch or tag"
                    mono
                    value={branch}
                    onChange={setBranch}
                    placeholder="main"
                  />
                  <Field
                    label="Entry path (optional)"
                    mono
                    placeholder="src/agent/index.ts"
                    value={entryPoint}
                    onChange={(v) => {
                      setEntryPoint(v);
                      setFolder(
                        v.includes("/") ? v.replace(/\/[^/]*$/, "") : "",
                      );
                    }}
                  />
                </Box>
                <Box>
                  <Label>Visibility</Label>
                  <Box
                    sx={{
                      display: "grid",
                      gap: 0.75,
                      mt: 0.75,
                      gridTemplateColumns: "1fr 1fr",
                    }}
                  >
                    <ChipCard
                      label="Public"
                      on={visibility === "public"}
                      onClick={() => setVisibility("public")}
                    />
                    <ChipCard
                      label="Private"
                      on={visibility === "private"}
                      onClick={() => setVisibility("private")}
                    />
                  </Box>
                </Box>
                {visibility === "private" && (
                  <Stack spacing={1}>
                    <Box
                      sx={{
                        display: "grid",
                        gap: 1,
                        gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" },
                      }}
                    >
                      <WhereOption
                        icon="mdi:github"
                        label="Install the GitHub App"
                        on={privateVia === "app"}
                        onClick={() => setPrivateVia("app")}
                        short="Pick from a list · fixes as pull requests"
                        hint="Install it once for the account; this repository is then picked from the list, a merge is seen at once, and fixes come back as pull requests."
                      />
                      <WhereOption
                        icon="solar:key-minimalistic-linear"
                        label="Use an access token"
                        on={privateVia === "token"}
                        onClick={() => setPrivateVia("token")}
                        short="A personal access token or deploy key · read only"
                        hint="For when the app can't be installed. We read the code with it and check the branch once a day; without the app, fixes come back as a patch."
                      />
                    </Box>
                    {privateVia === "app" ? (
                      <Stack
                        direction="row"
                        alignItems="center"
                        spacing={1.25}
                        sx={{
                          px: 1.5,
                          py: 1.25,
                          borderRadius: 1,
                          border: "1px solid",
                          borderColor: "divider",
                        }}
                      >
                        <Iconify
                          icon="solar:lock-keyhole-minimalistic-linear"
                          width={16}
                          sx={{ color: "text.secondary", flexShrink: 0 }}
                        />
                        <Typography
                          sx={{ typography: "s2", flex: 1, minWidth: 0 }}
                        >
                          Install the app once, then pick this repository from
                          the list.
                        </Typography>
                        <TextLink
                          onClick={() => {
                            setMode("github");
                            setVisibility("public");
                          }}
                        >
                          Connect GitHub
                        </TextLink>
                      </Stack>
                    ) : (
                      <SecretRefField
                        label="Access token or deploy key"
                        required
                        placeholder="vault://acme/sim/github_repo_token"
                        value={accessRef}
                        onChange={setAccessRef}
                        helper="A fine-grained personal access token with Contents: read on this repository, or a read-only deploy key. Stored as a reference — never shown again, never sent to the improver."
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
                placeholder="agents/returns"
                value={folder}
                onChange={setFolder}
                helper={
                  <HintLine hint="A monorepo can hold several agents.">
                    The folder the agent&apos;s entry file lives in.
                  </HintLine>
                }
              />
            )}
            {!pastedLink && (
              <TextLink
                onClick={() => setMode("github")}
              >{`Pick from ${hostLabel} instead`}</TextLink>
            )}
          </Stack>
        )}

        {/* The branch is the one choice with a default worth stating — changing it reads the repository again. */}
        {repo &&
          !pastedLink &&
          (changingBranch ? (
            <Box sx={{ maxWidth: 320 }}>
              {viaGithub ? (
                <Select
                  size="small"
                  fullWidth
                  value={branch}
                  onChange={(e) => {
                    setBranch(e.target.value);
                    setChangingBranch(false);
                    setFound(null);
                  }}
                  renderValue={(v) => (
                    <Box component="span" sx={{ fontFamily: MONO }}>
                      {v}
                    </Box>
                  )}
                  sx={{ typography: "s2" }}
                >
                  {branches.map((b) => (
                    <MenuItem
                      key={b}
                      value={b}
                      sx={{ typography: "s2", gap: 1 }}
                    >
                      <Box component="span" sx={{ fontFamily: MONO, flex: 1 }}>
                        {b}
                      </Box>
                      {b === "main" && (
                        <Box component="span" sx={{ color: "text.subtitle" }}>
                          default
                        </Box>
                      )}
                    </MenuItem>
                  ))}
                </Select>
              ) : (
                <Field
                  label="Branch or tag"
                  mono
                  value={branch}
                  onChange={setBranch}
                />
              )}
            </Box>
          ) : (
            <Typography
              component="div"
              sx={{ typography: "s3", color: "text.subtitle" }}
            >
              {"Reads "}
              <Box
                component="span"
                sx={{ fontFamily: MONO, color: "text.primary" }}
              >
                {branch}
              </Box>
              {" — a fix merged there counts as released. "}
              <TextLink onClick={() => setChangingBranch(true)}>
                Change
              </TextLink>
            </Typography>
          ))}

        {/* The picked repository, read for agents now — never ahead of time. */}
        {viaGithub && repo && (
          <RepoAgentScan
            repo={repo}
            branch={branch}
            hostLabel={hostLabel}
            agentId={entryId}
            onPick={(a) => {
              setEntryId(a.id);
              setFolder(folderOfAgent(repo, a.id));
              setEntryPoint(a.framework === DIRECT ? a.entry : "");
              setFound(null);
            }}
            entryFile={entryPoint}
            onEntryFile={(v) => {
              setEntryPoint(v);
              setEntryId(null);
              setFolder(
                v.trim()
                  ? v.split(":")[0].includes("/")
                    ? v.split(":")[0].replace(/\/[^/]*$/, "")
                    : "."
                  : "",
              );
              setFound(null);
            }}
            onStatus={setScanStatus}
            /* One already in Agents opens there — never connected twice. */
            connectedOf={
              host === "github"
                ? (a) =>
                    connectedAgents.find(
                      (x) =>
                        x.key ===
                        agentRecordFromSource(scannedAgentSource(repo, a)).key,
                    ) || null
                : undefined
            }
            onOpenConnected={(rec) =>
              navigate(
                `${paths.dashboard.agents}/connected/${agentIdOf(rec.key)}`,
              )
            }
          />
        )}

        {/* What improvements can change in the agent picked, before it's read. */}
        {pickedAgent && (
          <FrameworkTuning tuning={frameworkTuningOf(pickedAgent.framework)} />
        )}
        {direct && !pastedLink && (!viaGithub || !!pickedAgent) && (
          <Field
            label={
              pickedAgent
                ? "Where the agent starts"
                : "Where the agent starts (when it uses no framework)"
            }
            mono
            placeholder={ENTRY_EXAMPLE}
            value={entryPoint}
            onChange={setEntryPoint}
            helper={
              <HintLine hint="Code that calls the OpenAI, Anthropic or Gemini API itself has no framework to find the agent by. Name the file and function a conversation starts in — we read the prompts and tools it reaches from there.">
                {pickedAgent
                  ? `${pickedAgent.sdk || "Model API"} calls, no framework — check the function each conversation starts in.`
                  : "File and function each conversation starts in."}
              </HintLine>
            }
          />
        )}

        {/* The agent is chosen: what its code reads, to fill. */}
        {agentChosen && (
          <EnvironmentValues
            values={envValues}
            onValues={setEnvValues}
            files={credFiles}
            onFiles={setCredFiles}
            expectedNames={agentKeys}
            egress={egressText}
            onEgress={setEgressText}
          />
        )}

        {match && found && (
          <TraceMatch
            match={match}
            traced={tracedCode}
            tracedFw={tracedFw}
            projectName={project.name}
            found={found}
            tools={facts.tools || []}
          />
        )}
      </Stack>
    </ConnectSteps>
  );
}

RepoAgentFlow.propTypes = {
  title: PropTypes.string,
  initial: PropTypes.object,
  /* { repo, agent } — start from a repository the GitHub page already scanned. */
  preset: PropTypes.object,
  limits: PropTypes.object.isRequired,
  onLimits: PropTypes.func.isRequired,
  onSave: PropTypes.func.isRequired,
  profile: PropTypes.object.isRequired,
  onProfile: PropTypes.func.isRequired,
  observe: PropTypes.object,
  onObserve: PropTypes.func.isRequired,
  prefill: PropTypes.object,
};

/* What reading the code found, against what the Observe project's traces said. */
function TraceMatch({ match, traced, tracedFw, projectName, found, tools }) {
  if (match.ok) {
    return (
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{
          px: 1.5,
          py: 1,
          borderRadius: 1,
          border: "1px solid",
          borderColor: "divider",
        }}
      >
        <Iconify
          icon="solar:check-circle-linear"
          width={16}
          sx={{ color: "text.secondary", flexShrink: 0 }}
        />
        <Typography sx={{ typography: "s2" }}>
          <Box
            component="span"
            sx={{ fontWeight: 600 }}
          >{`Matches ${projectName}'s traces`}</Box>
          {` · ${tracedFw} · ${match.toolsFound} of ${match.toolsTraced} traced tools found${match.modelOk ? ` · ${traced.model}` : ""}`}
        </Typography>
      </Stack>
    );
  }
  return (
    <Alert
      severity="error"
      icon={<Iconify icon="solar:danger-triangle-linear" width={16} />}
    >
      <AlertTitle>{`Doesn't match ${projectName}'s traces`}</AlertTitle>
      <Stack spacing={0.25}>
        <Typography sx={{ typography: "s3", color: "text.secondary" }}>
          {"Traces: "}
          <Box
            component="span"
            sx={{ fontFamily: MONO }}
          >{`${tracedFw} · ${traced.tools.join(", ")}`}</Box>
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.secondary" }}>
          {"This folder: "}
          <Box
            component="span"
            sx={{ fontFamily: MONO }}
          >{`${found.framework} · ${tools.slice(0, 4).join(", ")}${tools.length > 4 ? "…" : ""}`}</Box>
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.secondary", mt: 0.5 }}>
          Pick another folder or repository — connecting this one would fix this
          issue against a different agent.
        </Typography>
      </Stack>
    </Alert>
  );
}
TraceMatch.propTypes = {
  match: PropTypes.object,
  traced: PropTypes.object,
  tracedFw: PropTypes.string,
  projectName: PropTypes.string,
  found: PropTypes.object,
  tools: PropTypes.array,
};

/* The service a credential name belongs to, for "Detected: …". */
const CONNECTORS = [
  [/OPENAI/i, "OpenAI"],
  [/ANTHROPIC|CLAUDE/i, "Anthropic"],
  [/GEMINI|GOOGLE/i, "Google"],
  [/STRIPE/i, "Stripe"],
  [/SHOPIFY/i, "Shopify"],
  [/TWILIO/i, "Twilio"],
  [/SLACK/i, "Slack"],
  [/ZENDESK/i, "Zendesk"],
  [/HUBSPOT/i, "HubSpot"],
  [/REDIS/i, "Redis"],
  [/DATABASE_URL|POSTGRES|PG_/i, "Postgres"],
  [/DEEPGRAM/i, "Deepgram"],
  [/ELEVEN/i, "ElevenLabs"],
  [/CARTESIA/i, "Cartesia"],
  [/LIVEKIT/i, "LiveKit"],
];
function connectorOfKey(name) {
  return (CONNECTORS.find(([re]) => re.test(name)) || [])[1] || null;
}
