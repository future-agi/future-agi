import PropTypes from "prop-types";
import { useEffect, useMemo, useState } from "react";
import {
  Box,
  Button,
  CircularProgress,
  Dialog,
  IconButton,
  MenuItem,
  Select,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { definitionOf, envAgentKey, agentNameOf } from "../_mock/agentRegistry";
import { linkedRepoOf } from "../agents/agentLimits";
import { useConnectedAgents } from "../store";
import {
  GITHUB_WRITE_PERMISSIONS,
  GIT_HOST_META,
  canWriteTo,
  gitHostAccountOf,
  gitHostConnection,
  gitHostRepos,
  grantGitHostWrite,
  grantGithubWrite,
  hostRepoNameOf,
  hostRepoUrlOf,
  installationOfRepo,
  installedRepos,
  openPr,
  repoNameOf,
  reviewNounOf,
  useIntegrations,
} from "../_mock/integrations";
import { gitHostOf } from "../agents/agentProfile";
import { GitHostConnectForm } from "../agents/connect/GitHostRepoPicker";
import BrandMark from "./BrandMark";
import GithubInstallDialog from "./GithubInstallDialog";

const MONO = "ui-monospace, Menlo, monospace";
const FILE_KIND = {
  prompt: "Prompt",
  code: "Tool code",
  tracing: "Tracing setup",
  deps: "Dependencies",
  entry: "Entry point",
  orchestration: "Orchestration",
  guardrail: "Guardrail",
};
/**
 * Turn a fix into a pull request on the agent's repository — prompts and
 * tool code alike. The description carries the evidence (which scenarios it
 * fixes, before/after) so a reviewer who wasn't here can judge it. It opens
 * straight away: there is no approval step of ours — the team's own GitHub
 * reviewers and branch protection decide what merges.
 *
 * The GitHub App is installed with read access only. The first PR on an
 * installation that can't write yet asks for write first (the "access"
 * step); not granting it leaves the patch to copy, which needs nothing.
 */
export default function OpenPrDialog({
  open,
  onClose,
  env,
  envState,
  agent,
  files,
  summary,
  onOpened,
  draft,
  kind = "fix",
  heading,
  subheading,
  meta,
  proof,
  patchText,
}) {
  const integrations = useIntegrations();
  const [installOpen, setInstallOpen] = useState(false);
  const { setAgentValues } = useConnectedAgents();

  /* The agent may come from an environment or straight from the Agents page. */
  const theAgent = agent || envState?.agent;
  const def = definitionOf(theAgent);
  /* Its git host — GitHub through the App; GitLab (merge requests) or
     Bitbucket (pull requests) through the workspace's connection. */
  const host = gitHostOf(theAgent).id;
  const other = host === "gitlab" || host === "bitbucket";
  const hostLabel = other ? GIT_HOST_META[host].label : "GitHub";
  const review = reviewNounOf(host);
  const connection = other ? gitHostConnection(host, integrations) : null;
  const github = other ? connection : integrations.github;
  const repos = other
    ? gitHostRepos(host, integrations)
    : installedRepos(integrations);
  /* Write is per connection — the group or workspace that holds the repository. */
  const canWrite = (name) =>
    other
      ? gitHostAccountOf(host, name, integrations)?.access === "write"
      : canWriteTo(name);
  /* The repository is the agent's (its Connections), never the environment's:
     a known one is fixed here, and one picked for a repo agent that has none
     yet is saved on the agent. A hosted or endpoint agent doesn't live in the
     repo picked here, so linking it would be a guess. */
  const isRepoAgent = def.kind === "repo" || def.kind === "framework";
  const known = [linkedRepoOf(theAgent)]
    .map(other ? hostRepoNameOf : repoNameOf)
    .find((n) => n && repos.some((r) => r.name === n));
  const [repo, setRepo] = useState(known || "");
  const [base, setBase] = useState(def.ref || "main");
  const [title, setTitle] = useState("");
  /* "access" asks for write before the form; `after` says whether granting
     goes back to the form or straight on to open the PR. */
  const [step, setStep] = useState("form");
  const [after, setAfter] = useState(null);
  const [granting, setGranting] = useState(false);
  const [copied, setCopied] = useState(false);
  const branch =
    draft?.branch ||
    `future-agi/fix-${env?.id || "agent"}-${files.length}-changes`;
  const agentName =
    theAgent?.name ||
    agentNameOf(theAgent) ||
    (env ? `${env.name} agent` : "the agent");

  useEffect(() => {
    if (!open) return;
    setRepo(known || "");
    setBase(def.ref || "main");
    setTitle(
      draft?.title ||
        (summary?.headline
          ? `Fix: ${summary.headline}`
          : `Fix ${files.length} issue${files.length === 1 ? "" : "s"} found in simulation`),
    );
    setStep(known && !canWrite(known) ? "access" : "form");
    setAfter(null);
    setGranting(false);
    setCopied(false);
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  const body = useMemo(
    () =>
      draft?.body ||
      [
        "## What this fixes",
        summary?.fixes
          ? `${summary.fixes} failing scenario${summary.fixes === 1 ? "" : "s"} in ${env?.name} now pass in simulation (${summary.before}% → ${summary.after}%).`
          : `Changes proposed from a simulation run in ${env?.name}.`,
        "",
        "## Changes",
        ...files.map((f) => `- \`${f.path}\` — ${f.title}`),
        "",
        /* An improvement brings its proof; anything else says how it'll be checked. */
        ...(proof
          ? [proof, ""]
          : [
              "## Evidence",
              "Same scenarios, same seed. The check on this PR re-runs them against this branch.",
              "",
            ]),
        "_Opened by Future AGI as a draft. Nothing changes in your agent until you merge._",
      ].join("\n"),
    [files, summary, env, draft, proof],
  );

  const pr = {
    repo,
    base,
    branch,
    title,
    body,
    files,
    check:
      summary?.fixes != null
        ? {
            before: summary.before,
            after: summary.after,
            scenarios: summary.scenarios,
          }
        : null,
    envId: env?.id,
    envName: env?.name,
    agentKey: theAgent?.key || envAgentKey(theAgent, env?.id),
    agentName,
    kind,
    host,
    ...(draft?.meta || {}),
    ...(meta || {}),
  };

  const send = () => {
    const agentKey = theAgent?.key || envAgentKey(theAgent, env?.id);
    if (isRepoAgent && !known && repo && agentKey)
      setAgentValues(agentKey, { repoUrl: hostRepoUrlOf(host, repo) });
    const opened = openPr(pr);
    onOpened?.(opened);
    onClose();
  };
  const submit = () => {
    if (!canWrite(repo)) {
      setStep("access");
      setAfter("submit");
      return;
    }
    send();
  };

  /* Granting happens on GitHub; the prototype takes a moment, then records it. */
  useEffect(() => {
    if (!granting) return undefined;
    const t = setTimeout(() => {
      if (other) grantGitHostWrite(host, repo);
      else grantGithubWrite(repo);
      setGranting(false);
      if (after === "submit") send();
      else setStep("form");
    }, 700);
    return () => clearTimeout(t);
  }, [granting]); // eslint-disable-line react-hooks/exhaustive-deps

  /* Not now: the same change as a patch, applied by hand. */
  const patchOut =
    patchText ||
    [
      `# ${title || "Change"}`,
      ...files.map(
        (f) =>
          `# ${f.path} — ${f.title}${f.adds != null ? ` (+${f.adds} −${f.dels})` : ""}`,
      ),
      ...(draft?.preview?.code ? ["", draft.preview.code] : []),
    ].join("\n");
  const copyPatch = () => {
    navigator.clipboard?.writeText(patchOut).catch(() => {});
    setCopied(true);
  };
  const asking = !!github && step === "access";
  const account = other
    ? connection?.account
    : installationOfRepo(repo)?.account;

  return (
    <Dialog
      open={open}
      onClose={onClose}
      PaperProps={{
        sx: {
          borderRadius: 2,
          maxWidth: 720,
          width: "100%",
          bgcolor: "background.paper",
          backgroundImage: "none",
          border: "1px solid",
          borderColor: "divider",
        },
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{ px: 3, pt: 2.5, pb: 2 }}
      >
        {other ? (
          <HostMark icon={GIT_HOST_META[host].icon} />
        ) : (
          <BrandMark brand="github" size={36} />
        )}
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "m3", fontWeight: 600 }}>
            {asking ? (
              <>
                {`Allow Future AGI to open draft ${review.long}s on `}
                <Box
                  component="span"
                  sx={{ fontFamily: MONO, fontSize: "0.92em" }}
                >
                  {repo}
                </Box>
              </>
            ) : (
              heading || `Open a ${review.long}`
            )}
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            {asking
              ? `Asked once. ${other ? `${hostLabel} was connected` : "The app was installed"} to read your code only — opening a draft ${review.short} needs write access to a new branch.`
              : subheading ||
                `The fix goes to review on ${hostLabel}. Nothing changes in your agent until you merge.`}
          </Typography>
        </Box>
        <IconButton size="small" onClick={onClose}>
          <Iconify icon="solar:close-linear" width={20} />
        </IconButton>
      </Stack>

      {!github && other ? (
        <Box sx={{ px: 3, pb: 3 }}>
          <GitHostConnectForm host={host} />
        </Box>
      ) : !github ? (
        <Box sx={{ px: 3, pb: 3 }}>
          <Stack
            direction="row"
            alignItems="center"
            spacing={1.5}
            sx={{
              p: 1.75,
              borderRadius: "8px",
              border: "1px dashed",
              borderColor: "divider",
            }}
          >
            <Box flex={1}>
              <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                Install the GitHub App first
              </Typography>
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                Install the Future AGI app on the repository this agent lives in
                — once, for the whole workspace.
              </Typography>
            </Box>
            <Button
              size="small"
              variant="contained"
              color="primary"
              onClick={() => setInstallOpen(true)}
              startIcon={<Iconify icon="mdi:github" width={16} />}
            >
              Install GitHub App
            </Button>
          </Stack>
        </Box>
      ) : asking ? (
        <Stack spacing={1.75} sx={{ px: 3, pb: 2.5 }}>
          <Box>
            <Label>{`Write access we'll ask ${hostLabel} for`}</Label>
            <Box
              sx={{
                border: "1px solid",
                borderColor: "divider",
                borderRadius: 1,
              }}
            >
              {(other
                ? GIT_HOST_META[host].writeScopes
                : GITHUB_WRITE_PERMISSIONS
              ).map((p, i) => (
                <Stack
                  key={p.scope}
                  direction="row"
                  spacing={1.5}
                  sx={{
                    px: 1.5,
                    py: 1,
                    borderTop: i ? "1px solid" : "none",
                    borderColor: "divider",
                  }}
                >
                  <Typography
                    sx={{
                      typography: "s2",
                      fontWeight: 600,
                      width: 130,
                      flexShrink: 0,
                      ...(other && { fontFamily: MONO, fontWeight: 500 }),
                    }}
                  >
                    {p.scope}
                  </Typography>
                  <Typography
                    sx={{ typography: "s2", width: 96, flexShrink: 0 }}
                  >
                    {p.access}
                  </Typography>
                  <Typography
                    sx={{
                      typography: "s3",
                      color: "text.secondary",
                      flex: 1,
                      minWidth: 0,
                    }}
                  >
                    {p.why}
                  </Typography>
                </Stack>
              ))}
            </Box>
          </Box>
          <Stack spacing={0.5}>
            {[
              [
                "solar:shield-check-linear",
                <>
                  {"Never merges, and never pushes to "}
                  <Box component="span" sx={{ fontFamily: MONO }}>
                    {base || "main"}
                  </Box>
                  {` or any branch you made — every ${review.short} is a draft a person reviews.`}
                </>,
              ],
              [
                "solar:users-group-rounded-linear",
                other
                  ? `The token's scopes belong to the ${hostLabel} connection, so this covers every repository it sees in ${account || "this account"}.`
                  : `GitHub permissions belong to the installation, so this covers every repository the app sees on ${account || "this account"}.`,
              ],
              [
                "solar:copy-linear",
                "Not now? Copy the patch instead and apply it yourself — that needs no write access.",
              ],
            ].map(([icon, text]) => (
              <Stack
                key={icon}
                direction="row"
                spacing={1}
                alignItems="flex-start"
              >
                <Iconify
                  icon={icon}
                  width={14}
                  sx={{ color: "text.secondary", mt: 0.25, flexShrink: 0 }}
                />
                <Typography sx={{ typography: "s3", color: "text.secondary" }}>
                  {text}
                </Typography>
              </Stack>
            ))}
          </Stack>
          {copied && (
            <Stack
              direction="row"
              spacing={1}
              alignItems="center"
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
              <Typography sx={{ typography: "s3", color: "text.secondary" }}>
                {`Patch copied · ${files.length} file${files.length === 1 ? "" : "s"}. Nothing was written to ${repo}; access stays read only.`}
              </Typography>
            </Stack>
          )}
        </Stack>
      ) : (
        <Stack
          spacing={2}
          sx={{ px: 3, pb: 2.5, maxHeight: "64vh", overflowY: "auto" }}
        >
          <Stack direction="row" spacing={1.5}>
            <Box flex={2}>
              <Label>Repository</Label>
              <Select
                size="small"
                fullWidth
                displayEmpty
                value={repo}
                onChange={(e) => setRepo(e.target.value)}
                disabled={!!known}
                renderValue={(v) =>
                  v ? (
                    <Box component="span" sx={{ fontFamily: MONO }}>
                      {v}
                    </Box>
                  ) : (
                    <Box component="span" sx={{ color: "text.disabled" }}>
                      Where this agent&apos;s code lives
                    </Box>
                  )
                }
                sx={{ typography: "s2" }}
              >
                {repos.map((r) => (
                  <MenuItem
                    key={r.name}
                    value={r.name}
                    sx={{ typography: "s2", fontFamily: MONO }}
                  >
                    {r.name}
                  </MenuItem>
                ))}
              </Select>
            </Box>
            <Box flex={1}>
              <Label>Into</Label>
              <TextField
                size="small"
                fullWidth
                value={base}
                onChange={(e) => setBase(e.target.value)}
                InputProps={{ sx: { typography: "s2", fontFamily: MONO } }}
              />
            </Box>
          </Stack>
          <Typography sx={{ typography: "s3", color: "text.subtitle", mt: -1 }}>
            {"From branch "}
            <Box component="span" sx={{ fontFamily: MONO }}>
              {branch}
            </Box>
            {known
              ? " · the repository this agent is read from"
              : isRepoAgent
                ? " · we'll remember this repository for the agent"
                : ""}
            {repo && !canWrite(repo)
              ? " · read only so far — we'll ask for write access first"
              : ""}
          </Typography>

          <Box>
            <Label>Title</Label>
            <TextField
              size="small"
              fullWidth
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              InputProps={{ sx: { typography: "s2" } }}
            />
          </Box>

          <Box>
            <Label>{`Files · ${files.length}`}</Label>
            <Box
              sx={{
                border: "1px solid",
                borderColor: "divider",
                borderRadius: 1,
              }}
            >
              {files.map((f, i) => (
                <Stack
                  key={`${f.path}-${i}`}
                  direction="row"
                  alignItems="center"
                  spacing={1}
                  sx={{
                    px: 1.25,
                    py: 0.75,
                    borderTop: i ? "1px solid" : "none",
                    borderColor: "divider",
                  }}
                >
                  <Iconify
                    icon={
                      f.kind === "prompt"
                        ? "solar:document-text-linear"
                        : f.kind === "deps"
                          ? "solar:box-linear"
                          : "solar:code-linear"
                    }
                    width={14}
                    sx={{ color: "text.subtitle" }}
                  />
                  <Typography
                    noWrap
                    sx={{
                      typography: "s3",
                      fontFamily: MONO,
                      flex: 1,
                      minWidth: 0,
                    }}
                  >
                    {f.path}
                  </Typography>
                  <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                    {FILE_KIND[f.kind] || "Code"}
                  </Typography>
                  <Typography
                    sx={{
                      typography: "s3",
                      fontFamily: MONO,
                      color: "status.success.fg",
                    }}
                  >{`+${f.adds}`}</Typography>
                  <Typography
                    sx={{
                      typography: "s3",
                      fontFamily: MONO,
                      color: "status.danger.fg",
                    }}
                  >{`−${f.dels}`}</Typography>
                </Stack>
              ))}
            </Box>
          </Box>

          {draft?.preview && (
            <Box>
              <Label>{draft.preview.label}</Label>
              <Box
                component="pre"
                sx={{
                  m: 0,
                  p: 1.5,
                  borderRadius: 1,
                  bgcolor: "background.neutral",
                  border: "1px solid",
                  borderColor: "divider",
                  typography: "s3",
                  fontFamily: MONO,
                  whiteSpace: "pre-wrap",
                  color: "text.primary",
                  maxHeight: 220,
                  overflowY: "auto",
                }}
              >
                {draft.preview.code}
              </Box>
            </Box>
          )}

          <Box>
            <Label>Description</Label>
            <Box
              component="pre"
              sx={{
                m: 0,
                p: 1.5,
                borderRadius: 1,
                bgcolor: "background.neutral",
                border: "1px solid",
                borderColor: "divider",
                typography: "s3",
                fontFamily: MONO,
                whiteSpace: "pre-wrap",
                color: "text.secondary",
                maxHeight: 180,
                overflowY: "auto",
              }}
            >
              {body}
            </Box>
          </Box>
        </Stack>
      )}

      {asking && (
        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          sx={{
            px: 3,
            py: 1.75,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <Button
            size="small"
            onClick={copied ? onClose : copyPatch}
            disabled={granting}
            startIcon={
              copied ? null : <Iconify icon="solar:copy-linear" width={16} />
            }
            sx={{ color: "text.secondary" }}
          >
            {copied ? "Close" : "Not now · Copy patch instead"}
          </Button>
          <Box flex={1} />
          <Button
            size="small"
            variant="contained"
            color="primary"
            disabled={granting || !repo}
            onClick={() => setGranting(true)}
            startIcon={
              granting ? (
                <CircularProgress
                  size={12}
                  thickness={6}
                  sx={{ color: "inherit" }}
                />
              ) : (
                <Iconify
                  icon={other ? GIT_HOST_META[host].icon : "mdi:github"}
                  width={16}
                />
              )
            }
          >
            {granting ? `Updating on ${hostLabel}…` : "Grant write access"}
          </Button>
        </Stack>
      )}
      {github && !asking && (
        <Stack
          direction="row"
          justifyContent="flex-end"
          spacing={1}
          sx={{
            px: 3,
            py: 1.75,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <Button
            size="small"
            onClick={onClose}
            sx={{ color: "text.secondary" }}
          >
            Cancel
          </Button>
          <Button
            size="small"
            variant="contained"
            color="primary"
            disabled={!repo || !title.trim()}
            onClick={submit}
          >
            {`Open ${review.long}`}
          </Button>
        </Stack>
      )}
      {!other && (
        <GithubInstallDialog
          open={installOpen}
          onClose={() => setInstallOpen(false)}
        />
      )}
    </Dialog>
  );
}

OpenPrDialog.propTypes = {
  proof: PropTypes.string,
  patchText: PropTypes.string,
  open: PropTypes.bool,
  onClose: PropTypes.func,
  env: PropTypes.object,
  envState: PropTypes.object,
  files: PropTypes.array.isRequired,
  summary: PropTypes.object,
  onOpened: PropTypes.func,
  agent: PropTypes.object,
  draft: PropTypes.object,
  kind: PropTypes.string,
  heading: PropTypes.string,
  subheading: PropTypes.string,
  meta: PropTypes.object,
};

/* GitLab's and Bitbucket's marks on the same neutral tile as BrandMark. */
function HostMark({ icon }) {
  return (
    <Box
      sx={{
        width: 36,
        height: 36,
        borderRadius: "8px",
        flexShrink: 0,
        display: "grid",
        placeItems: "center",
        border: "1px solid",
        borderColor: "divider",
        bgcolor: "action.hover",
      }}
    >
      <Iconify icon={icon} width={20} sx={{ color: "text.primary" }} />
    </Box>
  );
}
HostMark.propTypes = { icon: PropTypes.string };

function Label({ children }) {
  return (
    <Typography
      sx={{
        typography: "s3",
        fontWeight: 600,
        textTransform: "none",
        letterSpacing: 0.5,
        color: "text.subtitle",
        mb: 0.5,
      }}
    >
      {children}
    </Typography>
  );
}
Label.propTypes = { children: PropTypes.node };
