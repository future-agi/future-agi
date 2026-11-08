import { useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  MenuItem,
  Stack,
  Tab,
  Tabs,
  TextField,
  Typography,
} from "@mui/material";
import Section from "src/components/section";
import { IdentityValue, ReviewState } from "../review/ReleaseReviewBody";

const quote = (value) => `'${String(value).replaceAll("'", "'\"'\"'")}'`;
const safeValue = (value) =>
  typeof value === "string" &&
  value.length > 0 &&
  [...value].every(
    (char) => char.codePointAt(0) >= 32 && char.codePointAt(0) !== 127,
  );
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

// Version and destination are explicit. No zip overlay, global installation,
// preview opt-in, ambient broad API keys or invented released package number.
export function buildContextPlan({
  snapshot,
  origin,
  organizationId,
  workspaceId,
  profile,
  packageVersion,
  project,
}) {
  const url = new URL(origin);
  if (
    url.protocol !== "https:" ||
    url.username ||
    url.password ||
    url.search ||
    url.hash ||
    !uuid.test(organizationId) ||
    !uuid.test(workspaceId)
  )
    throw new Error("Use an HTTPS origin and explicit workspace identity.");
  if (
    !safeValue(snapshot.name) ||
    !/^[a-z0-9]+(-[a-z0-9]+)*$/.test(snapshot.name) ||
    snapshot.kind !== "agent_context" ||
    !Number.isSafeInteger(snapshot.version) ||
    snapshot.version < 1 ||
    !uuid.test(snapshot.versionId) ||
    !/^sha256:[a-f0-9]{64}$/.test(snapshot.digest) ||
    !safeValue(project) ||
    project.startsWith("-")
  )
    throw new Error("Choose a complete immutable context and a project path.");
  if (
    !["codex", "claude-code"].includes(profile.id) ||
    profile.state !== "verified" ||
    !profile.version ||
    !/^\d+\.\d+\.\d+(?:[a-zA-Z0-9.+-]*)$/.test(packageVersion)
  )
    throw new Error("This package and host profile are not qualified.");
  return [
    `python -m pip install ${quote(`futureagi==${packageVersion}`)}`,
    `export FI_CONTEXT_ORIGIN=${quote(url.href.replace(/\/$/, ""))}`,
    `export FI_CONTEXT_ORGANIZATION_ID=${quote(organizationId)}`,
    `export FI_CONTEXT_WORKSPACE_ID=${quote(workspaceId)}`,
    ': "${FI_CONTEXT_CREDENTIAL:?Set your isolated consume credential securely; do not paste it into this command}"',
    `python -m fi.context plan --name ${quote(snapshot.name)} --version ${snapshot.version} --client ${quote(profile.id)} --project ${quote(project)} --out ${quote("context-plan.json")}`,
    `python -m fi.context inspect --plan ${quote("context-plan.json")} --diff`,
  ].join("\n");
}

function UseContent({
  snapshot,
  origin,
  organizationId,
  workspaceId,
  qualification,
  workspaceName,
}) {
  const [group, setGroup] = useState("coding");
  const [profileId, setProfileId] = useState("codex");
  const [project, setProject] = useState(".");
  const [copied, setCopied] = useState(null);
  const [copyError, setCopyError] = useState("");
  const [copyBusy, setCopyBusy] = useState(false);
  const profile = qualification?.profiles?.find(
    (item) => item.id === profileId,
  );
  let command = "";
  let problem =
    "A released package and a verified host/version profile are required. This capability is not qualified yet.";
  if (qualification?.released === true && profile?.state === "verified") {
    try {
      command = buildContextPlan({
        snapshot,
        origin,
        organizationId,
        workspaceId,
        profile,
        packageVersion: qualification.packageVersion,
        project,
      });
      problem = "";
    } catch (error) {
      problem = error.message;
    }
  }
  return (
    <Stack spacing={2} sx={{ minWidth: 0 }}>
      <Section title="Use this exact context">
        <Stack spacing={1}>
          <IdentityValue
            label="Workspace"
            value={`${workspaceName ?? workspaceId} · ${workspaceId}`}
          />
          <IdentityValue
            label="Pinned candidate"
            value={`v${snapshot.version} · ${snapshot.versionId}`}
          />
          <IdentityValue label="Digest" value={snapshot.digest} />
          <Typography variant="s3">
            {snapshot.requested?.label
              ? `Selected ${snapshot.requested.label}; the handoff is pinned to the resolved version, not a moving label.`
              : "Pinned version. A later release requires a new reviewed plan."}
          </Typography>
        </Stack>
      </Section>
      <Tabs
        value={group}
        onChange={(_, value) => {
          setGroup(value);
          setCopyError("");
        }}
        variant="scrollable"
        scrollButtons="auto"
        aria-label="Context integration type"
      >
        <Tab value="coding" label="Coding agent" />
        <Tab value="application" label="Application" />
        <Tab value="mcp" label="MCP" />
      </Tabs>
      {group === "coding" && (
        <Stack spacing={1.5}>
          <TextField
            select
            size="small"
            label="Project profile"
            value={profileId}
            onChange={(event) => {
              setProfileId(event.target.value);
              setCopyError("");
            }}
          >
            <MenuItem value="codex">Codex</MenuItem>
            <MenuItem value="claude-code">Claude Code</MenuItem>
          </TextField>
          <TextField
            size="small"
            label="Project directory"
            value={project}
            onChange={(event) => {
              setProject(event.target.value);
              setCopyError("");
            }}
            helperText="Project scope only. Existing local instructions require an explicit ownership plan; they are never silently replaced."
          />
          <Typography variant="s3">
            Profile {profile?.version ?? "unqualified"} · host versions{" "}
            {profile?.hostVersions?.join(", ") ?? "not qualified"} · package{" "}
            {qualification?.released
              ? qualification.packageVersion
              : "not released"}
          </Typography>
          <Typography variant="s1">
            Standing instructions: project AGENTS.md. Skills:{" "}
            {profileId === "codex" ? ".agents/skills/" : ".claude/skills/"}.
          </Typography>
          {profileId === "claude-code" && (
            <Typography variant="s3">
              Existing CLAUDE.md or local instructions may affect AGENTS.md
              discovery. No settings, imports or permissions are changed without
              a separately reviewed plan.
            </Typography>
          )}
          {problem ? (
            <Alert severity="info">
              {problem} {profile?.reason ?? ""}
            </Alert>
          ) : (
            <>
              <Typography variant="m3">
                Create and inspect an install plan
              </Typography>
              <Typography
                component="pre"
                variant="s1"
                sx={{
                  m: 0,
                  p: 1.5,
                  border: 1,
                  borderColor: "divider",
                  borderRadius: 1,
                  whiteSpace: "pre-wrap",
                  overflowWrap: "anywhere",
                }}
              >
                {command}
              </Typography>
              <Button
                variant="contained"
                disabled={copyBusy}
                onClick={async () => {
                  setCopyBusy(true);
                  setCopyError("");
                  setCopied(null);
                  try {
                    if (!navigator.clipboard?.writeText)
                      throw new Error("Unavailable");
                    await navigator.clipboard.writeText(command);
                    setCopied(command);
                  } catch {
                    setCopyError(
                      "Could not copy. Select and copy the full command manually.",
                    );
                  } finally {
                    setCopyBusy(false);
                  }
                }}
              >
                Copy plan command
              </Button>
              {copied === command && (
                <Typography variant="s3" role="status">
                  Copied the complete plan command.
                </Typography>
              )}
              {copyError && <Alert severity="error">{copyError}</Alert>}
              <Box component="details">
                <Typography component="summary" variant="m3">
                  Apply only after reviewing the plan
                </Typography>
                <Typography variant="s3">
                  Stop or quiesce the coding client, inspect every
                  destination/preimage and confirm the immutable version and
                  digest above. This command is not run by this page.
                </Typography>
                <Typography
                  component="pre"
                  variant="s1"
                  sx={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}
                >
                  {
                    "python -m fi.context apply --plan 'context-plan.json' --quiescent"
                  }
                </Typography>
              </Box>
            </>
          )}
        </Stack>
      )}
      {group === "application" && (
        <Alert severity="info">
          Application integration requires a qualified Python adapter, immutable
          session identity and live access checks. Retain existing Langfuse
          tracing and CI. Resumed threads keep their original context; upgrades
          start a clean thread. No native JavaScript certification or offline
          fallback is implied.
        </Alert>
      )}
      {group === "mcp" && (
        <Alert severity="info">
          Context MCP is unavailable until the complete generated
          read-before-write contract and human-bound ingress are qualified.
          Existing configurations must opt in after release. Isolated
          Context-token MCP authentication and service proposals are not
          supported in this pilot.
        </Alert>
      )}
      <Alert severity="info">
        Installed files are not proof of model delivery. Resolved, installed,
        discovered and delivered observations are independent. A byte verifier
        is not behavioral regression evidence.
      </Alert>
    </Stack>
  );
}
UseContent.propTypes = {
  snapshot: PropTypes.object.isRequired,
  origin: PropTypes.string.isRequired,
  organizationId: PropTypes.string.isRequired,
  workspaceId: PropTypes.string.isRequired,
  qualification: PropTypes.object,
  workspaceName: PropTypes.string,
};

export default function ContextUsePanel({
  scopeKey,
  state = "unavailable",
  snapshot,
  onRetry,
  ...props
}) {
  if (!scopeKey || state !== "ready" || !snapshot)
    return (
      <ReviewState state={!scopeKey ? "forbidden" : state} onRetry={onRetry} />
    );
  return (
    <UseContent
      key={`${scopeKey}:${snapshot.versionId}`}
      {...props}
      snapshot={snapshot}
    />
  );
}
ContextUsePanel.propTypes = {
  scopeKey: PropTypes.string,
  state: PropTypes.string,
  snapshot: PropTypes.object,
  onRetry: PropTypes.func,
};
