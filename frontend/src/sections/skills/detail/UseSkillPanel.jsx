import PropTypes from "prop-types";
import { useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import {
  Alert,
  Box,
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

const CLIENTS = [
  ...INSTALL_CLIENTS,
  { id: "curl", label: "cURL" },
  { id: "python", label: "Python" },
  { id: "typescript", label: "TypeScript" },
  { id: "mcp", label: "MCP" },
];

export default function UseSkillPanel({ skill }) {
  const [clientId, setClientId] = useState("claude-code");
  const [location, setLocation] = useState("project");
  const [selector, setSelector] = useState("label:production");
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
    return buildMcpSnippet(args);
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
      <Section
        title="Choose what agents receive"
        description="A label follows promotions. A pinned version stays unchanged."
      >
        <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
          <TextField
            select
            size="small"
            label="Use label or version"
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
                {name}
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
            value={clientId}
            onChange={(_event, value) => setClientId(value)}
            variant="scrollable"
            scrollButtons="auto"
            allowScrollButtonsMobile
            sx={{ maxWidth: "100%" }}
            aria-label="Agent or integration"
          >
            {CLIENTS.map((item) => (
              <Tab key={item.id} value={item.id} label={item.label} />
            ))}
          </SegmentedTabs>
        </Box>
        <Stack spacing={1.5} sx={{ mt: 1.5, minWidth: 0 }}>
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
          {selectorReady ? (
            <CodeBlock code={snippet()} wrap />
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
                  {name}
                </MenuItem>
              ))}
            </TextField>
            {selector === "version" ? (
              <Alert severity="info">
                Choose a label to install multiple skills. Version numbers are
                specific to one skill.
              </Alert>
            ) : (
              <CodeBlock
                wrap
                code={buildInstallCommand({ ...args, names: [], tag })}
              />
            )}
          </Stack>
        </Section>
      )}
    </Stack>
  );
}

UseSkillPanel.propTypes = { skill: PropTypes.object.isRequired };
