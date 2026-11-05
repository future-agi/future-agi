import PropTypes from "prop-types";
import { useEffect, useMemo, useRef, useState } from "react";
import { Box, Button, Stack, Tab, TextField, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { SegmentedTabs } from "src/components/tabs/tabs";
import { hostRepoUrlOf } from "../../_mock/integrations";
import { CODING_AGENTS, codingSetupOf } from "../../_mock/codingAgents";
import { isSecretRef } from "../../_mock/secretRefs";
import ConnectSteps from "./ConnectSteps";
import GithubRepoPicker from "../../integrations/GithubRepoPicker";
import SecretRefField from "./SecretRefField";
import { useConnectSubmitLabel } from "./connectSubmit";

/*
  Test your Claude Code (or Codex) agent — one screen: the repository that
  holds its set-up, what we found there, and the user's own key. We run the
  coding agent with that set-up in our sandbox and grade its work with checks
  it can't see; a fix changes the set-up and comes back as a pull request.
  Saved as a repository agent, so its page works like any other code agent's.
*/
const Label = ({ children, hint }) => (
  <Box sx={{ mb: 0.75 }}>
    <Typography sx={{ typography: "s1", fontWeight: 500 }}>
      {children}
    </Typography>
    {hint && (
      <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
        {hint}
      </Typography>
    )}
  </Box>
);
Label.propTypes = { children: PropTypes.node, hint: PropTypes.node };

const FoundRow = ({ label, children }) => (
  <Stack
    direction="row"
    spacing={2}
    sx={{
      px: 1.5,
      py: 0.75,
      borderTop: "1px solid",
      borderColor: "divider",
      "&:first-of-type": { borderTop: 0 },
    }}
  >
    <Typography
      sx={{
        typography: "s1",
        color: "text.tertiary",
        width: 140,
        flexShrink: 0,
      }}
    >
      {label}
    </Typography>
    <Typography sx={{ typography: "s1", color: "text.secondary", minWidth: 0 }}>
      {children}
    </Typography>
  </Stack>
);
FoundRow.propTypes = { label: PropTypes.node, children: PropTypes.node };

export default function CodingAgentFlow({ title, onSave, initial = null }) {
  const agentId = initial?.codingAgent || "claude-code";
  const meta = CODING_AGENTS[agentId];
  const submitLabel = useConnectSubmitLabel();
  /* The set-up comes from a GitHub repository, or is uploaded (connect.md, Choose your source). */
  const [via, setVia] = useState("github");
  const [repo, setRepo] = useState(null);
  const [files, setFiles] = useState([]);
  const fileRef = useRef(null);
  const [branch, setBranch] = useState("main");
  const [key, setKey] = useState("");
  const [reading, setReading] = useState(false);
  /* Check the connection (connect.md step 3): it starts and answers one message.
     Changing the source, branch or key invalidates a passed check. */
  const [check, setCheck] = useState("idle");

  const src = via === "github" ? repo : files[0]?.name || null;
  const setup = useMemo(() => codingSetupOf(agentId, src), [agentId, src]);
  /* A key, or a well-formed secret-manager reference — not a half-written one. */
  const keyOk = !!key.trim() && (!/:\/\//.test(key) || isSecretRef(key.trim()));
  const ready = !!src && (via === "upload" || !!branch.trim()) && keyOk;
  const missingSecrets = (setup?.mcp || []).filter((m) => m.secret);
  useEffect(() => setCheck("idle"), [via, src, branch, key]);
  const runCheck = () => {
    setCheck("running");
    setTimeout(() => setCheck("passed"), 1500);
  };

  const connect = () => {
    setReading(true);
    setTimeout(() => {
      setReading(false);
      onSave({
        ...(via === "github"
          ? {
              kind: "repo",
              provider: "github",
              gitHost: "github",
              githubApp: true,
              value: hostRepoUrlOf("github", repo),
              ref: branch.trim(),
              releaseDetection: { watch: "branch", ref: branch.trim() },
              folder: "",
            }
          : {
              kind: "upload",
              files: files.map((f) => ({ name: f.name, size: f.size })),
              runtime: { endpoint: "", location: "hosted" },
              runLocation: "hosted",
            }),
        agentType: "chat",
        codingAgent: agentId,
        framework: agentId,
        name: `${src
          .split("/")
          .pop()
          .replace(/\.zip$/, "")} · ${meta.label}`,
        model: setup?.model,
        prompts: setup?.empty ? [] : [setup.instructions],
        tools: (setup?.mcp || []).map((m) => ({
          name: m.name,
          desc: "MCP server",
          args: [],
        })),
        credentialKeys: [
          {
            name: meta.keyName,
            ...(isSecretRef(key)
              ? { ref: key.trim() }
              : { ref: `env://${meta.keyName}` }),
          },
          ...missingSecrets.map((m) => ({ name: m.secret })),
        ],
        codingSetup: setup,
        versionNote:
          via === "github"
            ? `${meta.label} set-up read from ${repo} @ ${branch.trim()}.`
            : `${meta.label} set-up uploaded (${files.length} file${files.length === 1 ? "" : "s"}).`,
      });
    }, 700);
  };

  const next = {
    label: submitLabel,
    icon: "solar:link-circle-linear",
    disabled: !ready || check !== "passed",
    hint: !src
      ? via === "github"
        ? "Choose a repository"
        : "Upload the set-up"
      : via === "github" && !branch.trim()
        ? "Name the branch"
        : !keyOk
          ? `Add the ${meta.keyLabel}`
          : check !== "passed"
            ? "Check it starts first"
            : null,
    busy: reading ? `Saving ${meta.label} from ${src}…` : null,
    onClick: connect,
  };

  return (
    <ConnectSteps
      title={title || meta.label}
      steps={["Connect"]}
      active={0}
      next={next}
    >
      <Stack spacing={2.25}>
        <Typography sx={{ typography: "s1", color: "text.secondary" }}>
          {`Your agent is ${meta.label} plus your repository's set-up. We run it in a sandbox; fixes come back as a pull request (a patch, for an upload).`}
        </Typography>

        <Box>
          <SegmentedTabs
            value={via}
            onChange={(_, v) => setVia(v)}
            sx={{ mb: 1.5 }}
          >
            <Tab value="github" label="From GitHub" />
            <Tab value="upload" label="Upload the set-up" />
          </SegmentedTabs>
          {via === "upload" ? (
            <Box>
              <input
                ref={fileRef}
                hidden
                multiple
                type="file"
                accept=".zip,.md,.json,.toml,.yaml,.yml"
                onChange={(e) => {
                  setFiles(Array.from(e.target.files || []));
                  e.target.value = "";
                }}
              />
              <Stack direction="row" alignItems="center" spacing={1.25}>
                <Button
                  size="small"
                  variant="outlined"
                  startIcon={<Iconify icon="solar:upload-linear" width={16} />}
                  onClick={() => fileRef.current?.click()}
                >
                  {files.length ? "Change files" : "Choose files or a .zip"}
                </Button>
                <Typography
                  noWrap
                  sx={{ typography: "s3", color: "text.tertiary", minWidth: 0 }}
                >
                  {files.length
                    ? files.map((f) => f.name).join(", ")
                    : `${meta.instructions}, ${meta.folder}/ and its settings — read once; fixes come back as a patch`}
                </Typography>
              </Stack>
            </Box>
          ) : (
            <GithubRepoPicker
              manualLinks={false}
              showAgents={false}
              repo={repo}
              onPick={({ repo: r }) => setRepo(r)}
            />
          )}
          {via === "github" && repo && (
            <TextField
              size="small"
              label="Branch"
              value={branch}
              onChange={(e) => setBranch(e.target.value)}
              helperText="We test the set-up at this branch, and open pull requests against it."
              sx={{ mt: 1.5, width: 360 }}
            />
          )}
        </Box>

        {setup && (
          <Box>
            <Label>What we found</Label>
            <Box
              sx={{
                border: "1px solid",
                borderColor: "divider",
                borderRadius: "8px",
              }}
            >
              {setup.empty ? (
                <FoundRow label="Set-up">
                  {`No ${meta.label} set-up found — we'll run plain ${meta.label} on this repository.`}
                </FoundRow>
              ) : (
                <>
                  <FoundRow label="Instructions">{setup.instructions}</FoundRow>
                  <FoundRow label="Skills">
                    {setup.skills.length
                      ? `${setup.skills.length} · ${setup.skills.map((s) => s.split("/").pop()).join(", ")}`
                      : "None"}
                  </FoundRow>
                  <FoundRow label="Tool connections">
                    {setup.mcp.length
                      ? setup.mcp
                          .map((m) =>
                            m.secret ? `${m.name} (needs ${m.secret})` : m.name,
                          )
                          .join(" · ")
                      : "None"}
                  </FoundRow>
                  <FoundRow label="Helper agents">
                    {setup.helpers.length ? setup.helpers.join(", ") : "None"}
                  </FoundRow>
                  <FoundRow label="Permissions">{setup.permissions}</FoundRow>
                </>
              )}
              <FoundRow label="Model">{setup.model}</FoundRow>
            </Box>
            {missingSecrets.length > 0 && (
              <Typography
                sx={{ typography: "s3", color: "text.tertiary", mt: 0.75 }}
              >
                {`Add secrets (${missingSecrets.map((m) => m.secret).join(", ")}) after connecting, in Finish setting up.`}
              </Typography>
            )}
          </Box>
        )}

        <Box>
          <SecretRefField
            label={`Your ${meta.keyLabel}`}
            required
            value={key}
            onChange={setKey}
            placeholder="a vault://… reference"
            helper={`${meta.label} runs on your account, so runs are charged to this key. Stored encrypted, never shown again.`}
            fullWidth
          />
        </Box>

        <Box>
          <Stack direction="row" alignItems="center" spacing={1.25}>
            <Button
              size="small"
              variant="outlined"
              disabled={!ready || check === "running"}
              onClick={runCheck}
              startIcon={<Iconify icon="solar:play-linear" width={16} />}
            >
              {check === "passed" ? "Check again" : "Check it starts"}
            </Button>
            <Typography sx={{ typography: "s3", color: "text.secondary" }}>
              {check === "running"
                ? `Starting ${meta.label} in the sandbox and sending one test message…`
                : check === "passed"
                  ? `Started and answered a test message in 2.1 s.`
                  : ready
                    ? "Starts it once in the sandbox and sends one test message."
                    : "Available once the set-up and key are in."}
            </Typography>
          </Stack>
          {check === "passed" && (
            <Typography
              sx={{ typography: "s3", color: "text.tertiary", mt: 0.5 }}
            >
              This checks the connection only — not how it does on an
              environment&apos;s tasks.
            </Typography>
          )}
        </Box>
      </Stack>
    </ConnectSteps>
  );
}
CodingAgentFlow.propTypes = {
  title: PropTypes.string,
  onSave: PropTypes.func.isRequired,
  initial: PropTypes.object,
};
