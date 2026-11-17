import PropTypes from "prop-types";
import { useState } from "react";
import {
  Alert,
  Box,
  Button,
  MenuItem,
  Stack,
  Tab,
  TextField,
  Typography,
} from "@mui/material";

import CodeBlock from "src/components/code-block";
import Iconify from "src/components/iconify";
import { enqueueSnackbar } from "notistack";
import { useSkillVersion } from "src/api/skills";
import { useSkillV11Scope } from "src/api/skills/testing";
import TechnicalDetails from "../components/TechnicalDetails";
import Section from "src/components/section";
import EmptyState from "src/components/empty-state";
import { SegmentedTabs } from "src/components/tabs/tabs";
import { INSTALL_CLIENTS } from "../constants";
import VersionSelect from "../components/VersionSelect";
import ContextPilotCredentials from "../contexts/ContextPilotCredentials";
import { InstallSteps } from "../contexts/ContextUsePanel";
import { useSkillsAccess } from "../utils/access";
import { publicApiOrigin } from "../utils/origin";
import {
  CREDENTIAL_COMMENT,
  buildCurlInstall,
  buildCurlResolve,
  buildMcpSnippet,
  buildPythonSnippet,
  buildSkillUpdate,
  buildTypeScriptSnippet,
  registryEnvLines,
  registryScope,
} from "../utils/install";

// Retain confirmed-copy feedback until the shared SH8 CodeBlock API lands.
function CopyableCode({ code }) {
  const [error, setError] = useState("");
  const copy = async () => {
    try { await navigator.clipboard.writeText(code); setError(""); enqueueSnackbar("Copied"); }
    catch { setError("Couldn't copy. Select the command and copy it manually."); }
  };
  return <Stack spacing={0.5}><CodeBlock code={code} wrap copyLabel="Copy command" action={<Button variant="outlined" size="small" startIcon={<Iconify icon="solar:copy-linear" />} onClick={copy}>Copy</Button>} />{error && <Typography variant="s3" role="status">{error}</Typography>}</Stack>;
}
CopyableCode.propTypes = { code: PropTypes.string.isRequired };

// The key step shows this while the exact version loads. It ignores the props the install steps add to the key panel.
function KeyStatus({ children }) {
  return <Typography variant="s3" color="text.secondary" role="status">{children}</Typography>;
}
KeyStatus.propTypes = { children: PropTypes.node };

const APPLICATION_CLIENTS = [
  { id: "python", label: "Python" },
  { id: "typescript", label: "TypeScript" },
  { id: "curl", label: "cURL" },
];

// Shows a builder's command, or why it cannot be built, without throwing during render.
function Built({ build }) {
  let code = "";
  let problem = "";
  try { code = build(); } catch (error) { problem = error.message; }
  return problem ? <Alert severity="warning">{problem}</Alert> : <CopyableCode code={code} />;
}
Built.propTypes = { build: PropTypes.func.isRequired };

// For tools the Python installer does not cover (Cursor, Gemini CLI, VS Code), and for machines
// without Python: the same read-only key, read file by file through the consumption routes.
function WithoutPython({ build, selectorReady }) {
  const [clientId, setClientId] = useState("claude-code");
  const [location, setLocation] = useState("project");
  const client = INSTALL_CLIENTS.find((item) => item.id === clientId);
  const dir = location === "personal" ? client.personalDir : client.projectDir;
  return (
    <TechnicalDetails title="Without Python">
      <Stack spacing={1.5} sx={{ minWidth: 0 }}>
        <Typography variant="s3" color="text.secondary">
          Uses curl and jq with the same read-only key. It writes the files into {dir}/ without
          a preview and does not check digests; the Python installer does both. Back up local changes first.
          It doesn&apos;t remove files deleted from the published skill.
        </Typography>
        <TextField select size="small" label="Coding client" value={clientId} onChange={(event) => setClientId(event.target.value)}>
          {INSTALL_CLIENTS.map((item) => <MenuItem key={item.id} value={item.id}>{item.label}</MenuItem>)}
        </TextField>
        <SegmentedTabs value={location} onChange={(_event, value) => setLocation(value)} aria-label="Install location">
          <Tab value="project" label="Project" />
          <Tab value="personal" label="Personal" />
        </SegmentedTabs>
        {selectorReady
          ? <Built build={() => build(dir)} />
          : <Alert severity="info">Choose a published version to see the command.</Alert>}
      </Stack>
    </TechnicalDetails>
  );
}
WithoutPython.propTypes = { build: PropTypes.func.isRequired, selectorReady: PropTypes.bool };

// `qualification` and `footer` are optional slots: the host passes the installer qualification
// when one exists, and the runtime section passes "Check tracing" for the foot of the tab.
export default function UseSkillPanel({ skill, qualification, footer }) {
  const [group, setGroup] = useState('coding');
  const [clientId, setClientId] = useState("python");
  const [selector, setSelector] = useState(() => skill.labels.some((item) => item.name === 'production') ? 'label:production' : 'version');
  const [pinnedVersion, setPinnedVersion] = useState(skill.latestVersion);
  const access = useSkillV11Scope();
  const rights = useSkillsAccess();
  const api = publicApiOrigin();
  const label = selector === "version" ? undefined : selector.slice(6);
  const version = selector === "version" ? pinnedVersion : undefined;
  const missingLabel =
    label &&
    label !== "latest" &&
    !skill.labels.some((item) => item.name === label);
  const selectedNumber = selector === "version" ? pinnedVersion : label === "latest" ? skill.latestVersion : skill.labels.find((item) => item.name === label)?.version;
  // A numbered version needs its version ID: the read-only key is bound to it.
  const pinned = useSkillVersion(selector === "version" ? skill.id : null, selector === "version" ? pinnedVersion : null);
  const selectorName = label ? label.charAt(0).toUpperCase() + label.slice(1) : `v${pinnedVersion}`;
  const coding = group === "coding";
  const selectorHelp = coding
    ? label ? `${selectorName} resolves${selectedNumber ? ` to v${selectedNumber}` : ""} when you run the plan; installed files don't follow later releases.` : `Installs v${pinnedVersion}; installed files don't follow later releases.`
    : `${label ? selectorName : "A label"} resolves when requested; a numbered version stays fixed.`;
  const selectorReady = selector !== "version" || Number.isInteger(version);
  const names = [skill.name];
  const scope = { origin: api.origin, organizationId: access.scope?.organizationId, workspaceId: access.scope?.workspaceId };
  const hasProduction = skill.labels.some((item) => item.name === 'production');

  // Step 2 of the install flow. The key is limited to what the command will ask for: the label
  // when you install a label, or the exact version when you pin one.
  const keyPanel = selector === "version"
    ? (pinned.data?.id
      ? <ContextPilotCredentials key={pinned.data.id} kind="skill" resourceId={skill.id} snapshot={{ version: pinnedVersion, versionId: pinned.data.id }} canIssue={rights.canAdmin} />
      : <KeyStatus>{pinned.isError ? "Could not load this version. Choose another version or try again." : "Loading this version…"}</KeyStatus>)
    : <ContextPilotCredentials key={`label:${label}`} kind="skill" resourceId={skill.id} label={label} snapshot={{ version: selectedNumber }} canIssue={rights.canAdmin} />;

  const snapshot = {
    kind: "skill",
    name: skill.name,
    ...(label ? { label } : {}),
    ...(selectedNumber != null ? { version: selectedNumber } : {}),
    ...(selector === "version" && pinned.data ? { versionId: pinned.data.id, digest: pinned.data.digest } : {}),
  };
  const noOrigin = !api.origin || api.error ? "This workspace has no public HTTPS API address configured. Ask an admin to set VITE_PUBLIC_API_ORIGIN." : "";
  const guarded = (build) => (...args) => { if (noOrigin) throw new Error(noOrigin); return build(...args); };
  const environment = () => {
    const url = registryScope(scope);
    return [...registryEnvLines({ url, organizationId: scope.organizationId, workspaceId: scope.workspaceId }), CREDENTIAL_COMMENT].join("\n");
  };
  const applicationSnippet = () => {
    if (clientId === "curl") return buildCurlResolve({ ...scope, names, label, version });
    if (clientId === "typescript") return buildTypeScriptSnippet({ names, label, version });
    return buildPythonSnippet({ names, label, version });
  };

  if (!skill.latestVersion) {
    return (
      <EmptyState
        title="Publish before using this skill"
        description="Publish a version from your draft, then choose a label or pin that version here."
      />
    );
  }

  return (
    <Stack
      spacing={3}
      sx={{
        minWidth: 0,
        "& pre": { overflowWrap: "anywhere", wordBreak: "break-word" },
      }}
    >
      <Typography variant="s1">{coding ? `Install ${skill.name} into your coding agent.` : `Fetch ${skill.name} ${group === "mcp" ? "with MCP" : "from your application"}.`} {hasProduction ? `Production is v${skill.labels.find((item) => item.name === 'production').version}.` : 'Production is not assigned.'}</Typography>
      <Section
        title="Choose what agents receive"
        description={selectorHelp}
      >
        <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
          <TextField
            select
            size="small"
            label={coding ? "Version to install" : "Version to fetch"}
            value={selector}
            onChange={(event) => setSelector(event.target.value)}
            sx={{ minWidth: 200 }}
          >
            {[
              ...new Set([
                "production",
                "staging",
                "latest",
                ...skill.labels.map((item) => item.name),
              ]),
            ].map((name) => (
              <MenuItem key={name} value={`label:${name}`}>
                {name === "production" && hasProduction ? `Production (currently v${skill.labels.find((item) => item.name === name).version})` : name}
              </MenuItem>
            ))}
            <MenuItem value="version">Pinned version</MenuItem>
          </TextField>
          {selector === "version" && (
            <VersionSelect
              skillId={skill.id}
              value={pinnedVersion}
              onChange={setPinnedVersion}
              includeLabels={false}
            />
          )}
        </Stack>
        {missingLabel && (
          <Alert severity="warning" sx={{ mt: 1.5 }}>
            This skill has no {label} label. These commands return a not-found
            error until you assign it; they do not fall back to latest.
          </Alert>
        )}
      </Section>
      <Section title="Use in your agent">
        <Box sx={{ minWidth: 0, maxWidth: "100%", overflowX: "auto" }}>
          <SegmentedTabs
            value={group}
            onChange={(_event, value) => { setGroup(value); setClientId("python"); }}
            variant="scrollable"
            scrollButtons="auto"
            allowScrollButtonsMobile
            sx={{ maxWidth: "100%" }}
            aria-label="Agent or integration"
          >
            <Tab value="coding" label="Coding agent" />
            <Tab value="application" label="Application" />
            <Tab value="mcp" label="MCP" />
          </SegmentedTabs>
        </Box>
        <Stack spacing={1.5} sx={{ mt: 1.5, minWidth: 0 }}>
          {group === "coding" && (
            selectorReady
              ? <InstallSteps
                snapshot={snapshot}
                origin={api.origin}
                originError={api.error}
                organizationId={scope.organizationId}
                workspaceId={scope.workspaceId}
                qualification={qualification}
                runtimeAccess={keyPanel}
                title={`Install ${skill.name}${selectedNumber != null ? ` v${selectedNumber}` : ""}`}
                afterInstall={<Stack spacing={1}>
                  <TechnicalDetails title="Update an installed skill">
                    <Stack spacing={1}>
                      <Typography variant="s3" color="text.secondary">Run from the project root. The first command checks whether a newer release exists; the second writes a new plan to review with the inspect command, then apply it as above.</Typography>
                      <Built build={guarded(() => buildSkillUpdate({ ...scope, name: skill.name, project: "." }))} />
                    </Stack>
                  </TechnicalDetails>
                  <WithoutPython selectorReady={selectorReady} build={guarded((dir) => buildCurlInstall({ ...scope, names, label, version, dir }))} />
                </Stack>}
              />
              : <Alert severity="info">Choose a published version to see the command.</Alert>
          )}
          {group === "application" && (
            <Stack spacing={1.5}>
              <Typography variant="m3" component="h3">1. Create a read-only key</Typography>
              {keyPanel}
              <Typography variant="m3" component="h3">2. Fetch the skill</Typography>
              <Typography variant="s3" color="text.secondary">
                The SDK verifies every file against the manifest digest, and the load can be recorded in your traces.
                Set these in your application&apos;s environment; never paste the key into code.
              </Typography>
              <Built build={guarded(environment)} />
              <TextField select size="small" label="Fetch with" value={clientId} onChange={(event) => setClientId(event.target.value)}>
                {APPLICATION_CLIENTS.map((item) => <MenuItem key={item.id} value={item.id}>{item.label}</MenuItem>)}
              </TextField>
              {selectorReady
                ? <Built build={clientId === "curl" ? guarded(applicationSnippet) : applicationSnippet} />
                : <Alert severity="info">Choose a published version to see the command.</Alert>}
            </Stack>
          )}
          {group === "mcp" && <Typography variant="s1">Ask your connected MCP server for the selected skill, then read its files using the returned version.</Typography>}
          {group === "mcp" && <TechnicalDetails title="MCP file-reading protocol"><Typography variant="s3" sx={{ whiteSpace: "pre-wrap" }}>{buildMcpSnippet({ names, label, version })}</Typography></TechnicalDetails>}
          {group === "mcp" && (selectorReady
            ? <CopyableCode code={`get_skill(${JSON.stringify({ name: skill.name, ...(version != null ? { version } : { label }) }, null, 2)})`} />
            : <Alert severity="info">Choose a published version to see the command.</Alert>)}
        </Stack>
      </Section>
      {footer}
    </Stack>
  );
}

UseSkillPanel.propTypes = { skill: PropTypes.object.isRequired, qualification: PropTypes.object, footer: PropTypes.node };
