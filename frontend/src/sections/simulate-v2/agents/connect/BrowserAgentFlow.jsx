import PropTypes from "prop-types";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Box,
  Button,
  MenuItem,
  Stack,
  Tab,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { SegmentedTabs } from "src/components/tabs/tabs";
import { hostRepoUrlOf } from "../../_mock/integrations";
import {
  BROWSER_FRAMEWORKS,
  MODEL_KEYS,
  VIEWPORTS,
  browserSetupOf,
} from "../../_mock/browserAgents";
import { isSecretRef } from "../../_mock/secretRefs";
import ConnectSteps from "./ConnectSteps";
import GithubRepoPicker from "../../integrations/GithubRepoPicker";
import SecretRefField from "./SecretRefField";
import { useConnectSubmitLabel } from "./connectSubmit";

/*
  Test a browser agent — one screen: the code that drives the browser, what
  we found in it, the model key and the browser it gets. We start Chromium in
  the sandbox for it; each environment sets the sites it may open. Saved as a
  repository agent, so its page works like any other code agent's.
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
    alignItems="center"
    sx={{
      px: 1.5,
      py: 0.75,
      minHeight: 36,
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
    <Box sx={{ typography: "s1", color: "text.secondary", minWidth: 0 }}>
      {children}
    </Box>
  </Stack>
);
FoundRow.propTypes = { label: PropTypes.node, children: PropTypes.node };

export default function BrowserAgentFlow({ title, onSave }) {
  const submitLabel = useConnectSubmitLabel();
  const [via, setVia] = useState("github");
  const [repo, setRepo] = useState(null);
  const [files, setFiles] = useState([]);
  const fileRef = useRef(null);
  const [branch, setBranch] = useState("main");
  const [framework, setFramework] = useState(null);
  const [viewport, setViewport] = useState(VIEWPORTS[0].id);
  const [key, setKey] = useState("");
  const [reading, setReading] = useState(false);
  /* Check the connection (connect.md step 3): it starts, opens a page and
     takes a screenshot. Changing the source, framework or key resets it. */
  const [check, setCheck] = useState("idle");

  const src = via === "github" ? repo : files[0]?.name || null;
  const setup = useMemo(() => browserSetupOf(src), [src]);
  /* What we found is a starting point; the developer can correct the framework. */
  useEffect(() => setFramework(setup?.framework || null), [setup]);
  const fw = framework ? BROWSER_FRAMEWORKS[framework] : null;
  const modelKey = MODEL_KEYS[setup?.provider || "anthropic"];
  const keyOk = !!key.trim() && (!/:\/\//.test(key) || isSecretRef(key.trim()));
  const ready =
    !!src && (via === "upload" || !!branch.trim()) && !!framework && keyOk;
  useEffect(() => setCheck("idle"), [via, src, branch, framework, key]);
  const runCheck = () => {
    setCheck("running");
    setTimeout(() => setCheck("passed"), 1600);
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
        agentType: "browser",
        typeId: "browser_agent",
        framework,
        values: { framework, viewport, recordVideo: true },
        name: `${src
          .split("/")
          .pop()
          .replace(/\.zip$/, "")} · Browser agent`,
        model: setup?.model,
        credentialKeys: [
          {
            name: modelKey.name,
            ...(isSecretRef(key)
              ? { ref: key.trim() }
              : { ref: `env://${modelKey.name}` }),
          },
        ],
        versionNote:
          via === "github"
            ? `${fw.label} agent read from ${repo} @ ${branch.trim()}.`
            : `${fw.label} agent uploaded (${files.length} file${files.length === 1 ? "" : "s"}).`,
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
        : "Upload the agent"
      : via === "github" && !branch.trim()
        ? "Name the branch"
        : !keyOk
          ? `Add the ${modelKey.label}`
          : check !== "passed"
            ? "Check it starts first"
            : null,
    busy: reading ? `Saving the browser agent from ${src}…` : null,
    onClick: connect,
  };

  return (
    <ConnectSteps
      title={title || "Browser agent"}
      steps={["Connect"]}
      active={0}
      next={next}
    >
      <Stack spacing={2.25}>
        <Typography sx={{ typography: "s1", color: "text.secondary" }}>
          Your agent drives a browser we start in a sandbox. Each environment
          sets the sites it may open; every action and screenshot is kept.
        </Typography>

        <Box>
          <SegmentedTabs
            value={via}
            onChange={(_, v) => setVia(v)}
            sx={{ mb: 1.5 }}
          >
            <Tab value="github" label="From GitHub" />
            <Tab value="upload" label="Upload the agent" />
          </SegmentedTabs>
          {via === "upload" ? (
            <Box>
              <input
                ref={fileRef}
                hidden
                multiple
                type="file"
                accept=".zip,.py,.ts,.js,.json,.toml,.yaml,.yml"
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
                    : "The agent's code — read once; fixes come back as a patch"}
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
              helperText="We test the agent at this branch, and open pull requests against it."
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
              <FoundRow label="Framework">
                <TextField
                  select
                  size="small"
                  value={framework || ""}
                  onChange={(e) => setFramework(e.target.value)}
                  sx={{ width: 240 }}
                >
                  {Object.entries(BROWSER_FRAMEWORKS).map(([id, f]) => (
                    <MenuItem key={id} value={id}>
                      {f.label}
                    </MenuItem>
                  ))}
                </TextField>
              </FoundRow>
              <FoundRow label="Starts at">
                <Box
                  component="span"
                  sx={{ fontFamily: "ui-monospace, Menlo, monospace" }}
                >
                  {setup.entry}
                </Box>
              </FoundRow>
              <FoundRow label="Model">{setup.model}</FoundRow>
              <FoundRow label="First page">
                {setup.startUrl ||
                  "None set — each scenario gives the page to start on"}
              </FoundRow>
            </Box>
          </Box>
        )}

        <Box>
          <Label hint="Chromium in the sandbox. Each environment's allowed sites decide where it can go; anything else is blocked and shown in the trial.">
            The browser
          </Label>
          <TextField
            select
            size="small"
            label="Window"
            value={viewport}
            onChange={(e) => setViewport(e.target.value)}
            sx={{ width: 280 }}
          >
            {VIEWPORTS.map((v) => (
              <MenuItem key={v.id} value={v.id}>
                {v.label}
              </MenuItem>
            ))}
          </TextField>
        </Box>

        <Box>
          <SecretRefField
            label={`Your ${modelKey.label}`}
            required
            value={key}
            onChange={setKey}
            placeholder="a vault://… reference"
            helper="The agent's model runs on your account, so runs are charged to this key. Stored encrypted, never shown again."
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
                ? "Starting your agent with a fresh browser and asking it to open a page…"
                : check === "passed"
                  ? "Started, opened a page and took a screenshot in 3.4 s."
                  : ready
                    ? "Starts it once with a fresh browser; it opens one page and we take a screenshot."
                    : "Available once the agent and key are in."}
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
BrowserAgentFlow.propTypes = {
  title: PropTypes.string,
  onSave: PropTypes.func.isRequired,
};
