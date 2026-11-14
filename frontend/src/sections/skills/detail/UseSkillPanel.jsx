import PropTypes from "prop-types";
import { useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import {
  Alert,
  Box,
  Button,
  Link,
  MenuItem,
  Stack,
  Tab,
  TextField,
  Typography,
} from "@mui/material";

import { HOST_API } from "src/config-global";
import { paths } from "src/routes/paths";
import CodeBlock from "src/components/code-block";
import Iconify from "src/components/iconify";
import { enqueueSnackbar } from "notistack";
import TechnicalDetails from "../components/TechnicalDetails";
import Section from "src/components/section";
import EmptyState from "src/components/empty-state";
import { SegmentedTabs } from "src/components/tabs/tabs";
import { INSTALL_CLIENTS } from "../constants";
import VersionSelect from "../components/VersionSelect";
import {
  buildInstallCommand,
  buildCurlResolve,
  buildPythonSnippet,
  buildTypeScriptSnippet,
  buildMcpSnippet,
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

const CLIENTS = [
  ...INSTALL_CLIENTS,
  { id: "curl", label: "cURL" },
  { id: "python", label: "Python" },
  { id: "typescript", label: "TypeScript" },
  { id: "mcp", label: "MCP" },
];

export default function UseSkillPanel({ skill }) {
  const [group, setGroup] = useState('coding');
  const [clientId, setClientId] = useState("claude-code");
  const [location, setLocation] = useState("project");
  const [selector, setSelector] = useState(() => skill.labels.some((item) => item.name === 'production') ? 'label:production' : 'version');
  const [pinnedVersion, setPinnedVersion] = useState(skill.latestVersion);
  const [tag, setTag] = useState(skill.tags[0] || "");
  const client = CLIENTS.find((item) => item.id === clientId);
  const installClient = Boolean(client.projectDir);
  const label = selector === "version" ? undefined : selector.slice(6);
  const version = selector === "version" ? pinnedVersion : undefined;
  const missingLabel =
    label &&
    label !== "latest" &&
    !skill.labels.some((item) => item.name === label);
  const selectedNumber = selector === "version" ? pinnedVersion : label === "latest" ? skill.latestVersion : skill.labels.find((item) => item.name === label)?.version;
  const selectorName = label ? label.charAt(0).toUpperCase() + label.slice(1) : `v${pinnedVersion}`;
  const selectorHelp = installClient
    ? label ? `${selectorName} resolves${selectedNumber ? ` to v${selectedNumber}` : ""} when you run this command; installed files don't follow later releases.` : `Downloads v${pinnedVersion}; installed files don't follow later releases.`
    : `${label ? selectorName : "A label"} resolves when requested; a numbered version stays fixed.`;
  const installDir = location === "personal" ? client.personalDir : client.projectDir;
  const overwriteWarning = `This can overwrite files in ${installDir}. Use an empty folder or back up local changes first. It doesn't remove files deleted from the published skill.`;
  const selectorReady = selector !== "version" || Number.isInteger(version);
  const args = {
    hostApi: HOST_API,
    names: [skill.name],
    label,
    version,
    dir: location === "personal" ? client.personalDir : client.projectDir,
  };
  const snippet = () => {
    if (installClient) return buildInstallCommand(args);
    if (clientId === "curl") return buildCurlResolve(args);
    if (clientId === "python") return buildPythonSnippet(args);
    if (clientId === "typescript") return buildTypeScriptSnippet(args);
    return `get_skill(${JSON.stringify({ name: skill.name, ...(version != null ? { version } : { label }) }, null, 2)})`;
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
      <Typography variant="s1">{installClient ? `Install ${skill.name} into ${client.label}.` : `Fetch ${skill.name} with ${client.label}.`} {skill.labels.some((item) => item.name === 'production') ? `Production is v${skill.labels.find((item) => item.name === 'production').version}.` : 'Production is not assigned.'}</Typography>
      <Section
        title="Choose what agents receive"
        description={selectorHelp}
      >
        <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
          <TextField
            select
            size="small"
            label={installClient ? "Version to download" : "Version to fetch"}
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
                {name === "production" && skill.labels.some((item) => item.name === name) ? `Production (currently v${skill.labels.find((item) => item.name === name).version})` : name}
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
            onChange={(_event, value) => { setGroup(value); setClientId(value === 'coding' ? 'claude-code' : value === 'application' ? 'curl' : 'mcp'); }}
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
          {group !== 'mcp' && <TextField select size="small" label={group === 'coding' ? 'Coding client' : 'Fetch with'} value={clientId} onChange={(event) => setClientId(event.target.value)}>
            {CLIENTS.filter((item) => group === 'coding' ? Boolean(item.projectDir) : ['curl', 'python', 'typescript'].includes(item.id)).map((item) => <MenuItem key={item.id} value={item.id}>{item.label}</MenuItem>)}
          </TextField>}
          {installClient && <TechnicalDetails title="Installation details"><Typography variant="s3">Keep existing files and locks before replacing them. Installed files are not proof of model delivery.</Typography></TechnicalDetails>}
          {installClient && (
            <SegmentedTabs
              value={location}
              onChange={(_event, value) => setLocation(value)}
              aria-label="Install location"
            >
              <Tab value="project" label="Project" />
              <Tab value="personal" label="Personal" />
            </SegmentedTabs>
          )}
          <Typography variant="s1" color="text.secondary">
            Set FI_API_KEY and FI_SECRET_KEY in your environment.{" "}
            <Link component={RouterLink} to={paths.dashboard.settings.apiKeys}>
              Manage API keys
            </Link>
            .
          </Typography>
          <Typography variant="s3" color="text.secondary">
            API keys carry their existing access permissions; these commands do
            not make them read-only. Keep credentials out of source control.
          </Typography>
          {group === "mcp" && <Typography variant="s1">Ask your connected MCP server for the selected skill, then read its files using the returned version.</Typography>}
          {group === "mcp" && <TechnicalDetails title="MCP file-reading protocol"><Typography variant="s3" sx={{ whiteSpace: "pre-wrap" }}>{buildMcpSnippet(args)}</Typography></TechnicalDetails>}
          {selectorReady ? (
            <Stack spacing={1}>{installClient && <Typography variant="s3" color="text.secondary">{overwriteWarning}</Typography>}<CopyableCode code={snippet()} /></Stack>
          ) : (
            <Alert severity="info">
              Choose a published version to see the command.
            </Alert>
          )}
          {installClient && (
            <Typography variant="s3" color="text.secondary">
              Run from your project root for a project install. The download
              includes a lock file with the resolved version and digest. Review
              scripts before letting an agent run them.
            </Typography>
          )}
        </Stack>
      </Section>
      {installClient && skill.tags.length > 0 && (
        <Section
          title="Install every skill with a tag"
          description="Downloads up to 50 skills. The lock file lists skills skipped because they do not have the chosen label."
        >
          <Stack spacing={1.5}>
            <TextField
              select
              size="small"
              label="Tag"
              value={tag}
              onChange={(event) => setTag(event.target.value)}
              sx={{ maxWidth: 320 }}
            >
              {skill.tags.map((name) => (
                <MenuItem key={name} value={name}>
                  {name === "production" && skill.labels.some((item) => item.name === name) ? `Production (currently v${skill.labels.find((item) => item.name === name).version})` : name}
                </MenuItem>
              ))}
            </TextField>
            {selector === "version" ? (
              <Alert severity="info">
                Choose a label to install multiple skills. Version numbers are
                specific to one skill.
              </Alert>
            ) : (
              <Stack spacing={1}><Typography variant="s3" color="text.secondary">{overwriteWarning}</Typography><CopyableCode code={buildInstallCommand({ ...args, names: [], tag })} /></Stack>
            )}
          </Stack>
        </Section>
      )}
    </Stack>
  );
}

UseSkillPanel.propTypes = { skill: PropTypes.object.isRequired };
