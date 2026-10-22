import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  Box, Button, Card, Chip, Dialog, DialogActions, DialogContent, DialogContentText, DialogTitle,
  InputBase, Menu, MenuItem, Stack, Tooltip, Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { useConnectedAgents } from "../store";
import { projectsOfAgent } from "../_mock/projectLinks";
import { agentNeedsRuntime, agentRecordFromSource, agentSourceOf, definitionOf } from "../_mock/agentRegistry";
import {
  GITHUB_PERMISSIONS, GITHUB_WRITE_PERMISSIONS, dismissGithubRemoved, githubAccountsLabel, githubInstalls, installedRepos,
  markScanned, prCheckSummary, repoNameOf, reposOfInstall, scannedAgentSource, uninstallGithub, useIntegrations,
} from "../_mock/integrations";
import { hasVerifyCheck } from "../_mock/prChecks";
import BuildEnvironmentDialog from "../agents/BuildEnvironmentDialog";
import { buildPresetFor } from "../agents/agentViewUtils";
import BrandMark from "./BrandMark";
import GithubInstallDialog from "./GithubInstallDialog";
import { PrVerifyStatus } from "./PrVerifyCheck";
import { clickable, focusRing } from "../components/clickable";

const MONO = "ui-monospace, Menlo, monospace";

/* Re-reads are automatic — there is no per-agent switch (RSI PRD: "it also
   re-reads whenever the release branch moves and once a day"). */
const AUTO_REREAD = "We re-read each agent whenever its release branch moves and once a day. A change becomes a new version, linked to its commit.";

const CAPABILITIES = [
  { icon: "solar:magnifer-linear", title: "Connect agents straight from code", body: "We scan the repositories you choose, find the agents in them, and you pick which one to connect — no pasting URLs." },
  { icon: "solar:layers-minimalistic-linear", title: "A version for every change", body: "We re-read the agent whenever its release branch moves and once a day — every change to its prompts, tools or config becomes a version you can test, tied to its commit." },
  { icon: "solar:code-square-linear", title: "Fixes as draft pull requests", body: "A fix from an improvement opens as a draft PR — prompts and tool code — with the diagnosis and before/after results. Write access is asked for at the first one; we push only to branches we create, and you review and merge." },
  { icon: "solar:chart-2-linear", title: "Simulation results on the PR", body: "The PR carries a check with the scenarios it was tested on — re-run on every push. It informs the review; it never blocks a merge." },
];

/** Settings → Integrations → GitHub. */
export default function GithubIntegrationPage() {
  const navigate = useNavigate();
  const integrations = useIntegrations();
  const { github, githubRemoved } = integrations;
  /* GitLab merge requests and Bitbucket pull requests live on their own hosts. */
  const prs = integrations.prs.filter((p) => !p.host || p.host === "github");
  const { agents, state: simState } = useConnectedAgents();
  /* false | true (new install) | "<account>" (configure that installation) */
  const [installOpen, setInstallOpen] = useState(false);
  const [menu, setMenu] = useState(null); // { el, account }
  /* The account an Uninstall is being confirmed for. */
  const [uninstalling, setUninstalling] = useState(null);
  const [scanned, setScanned] = useState({});
  /* Back from connecting a scanned agent: say what's next for it. */
  const [params, setParams] = useSearchParams();
  const justConnected = agents.find((a) => a.id === params.get("connected")) || null;
  const dismissConnected = () => setParams((p) => { p.delete("connected"); return p; }, { replace: true });
  const [buildOpen, setBuildOpen] = useState(false);
  /* The agent the Build dialog is for — the one just connected, or a row below. */
  const [buildFor, setBuildFor] = useState(null);

  const repos = installedRepos(integrations);
  /* Agents linked through the app — plus ones that were, whose installation
     has since gone, so the loss is visible where it happened. */
  const ghAgents = useMemo(() => agents.filter((a) => {
    const src = agentSourceOf(a);
    return src.viaApp || src.appLost;
  }), [agents, repos]); // eslint-disable-line react-hooks/exhaustive-deps

  /* A scanned agent is connected when its key — repository + entry file — is in the registry. */
  const connectedKey = (repo, a) => agentRecordFromSource(scannedAgentSource(repo, a)).key;
  const connectedFor = (repo, a) => agents.find((x) => x.key === connectedKey(repo, a));
  /* One way to connect an agent (RSI PRD Flow 1): Agents → New agent opens
     on What we found for this scanned agent — the repository and folder are
     already known — then Observe and constraints, and comes back here with
     the agent in Agents. */
  const connectScanned = (repo, a) => navigate(`${paths.dashboard.agents}/connect?${new URLSearchParams({
    repo: repo.name, repoAgent: a.id, return: `${paths.dashboard.settings.integrations}/github?connected={agent}`,
  }).toString()}`);

  const scan = (name) => {
    setScanned((s) => ({ ...s, [name]: "scanning" }));
    setTimeout(() => { markScanned(name); setScanned((s) => ({ ...s, [name]: undefined })); }, 900);
  };

  return (
    <Box>
      <Button
        size="small" variant="outlined"
        startIcon={<Iconify icon="formkit:left" width={16} height={16} />}
        onClick={() => navigate(paths.dashboard.settings.integrations)}
        sx={{ mb: 2 }}
      >
        Back to Integrations
      </Button>

      <Card sx={{ mb: 3, p: 3 }}>
        <Stack direction="row" alignItems="center" spacing={2}>
          <BrandMark brand="github" size={48} />
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "m2", fontWeight: "fontWeightSemiBold" }}>GitHub</Typography>
            <Typography sx={{ typography: "s1", color: "text.secondary" }}>
              {github
                ? `Installed on ${githubAccountsLabel(integrations)} · ${repos.length} repositor${repos.length === 1 ? "y" : "ies"}`
                : "Read your agents' code, version them from commits, and open fixes as pull requests."}
            </Typography>
            <Stack direction="row" spacing={0.75} sx={{ mt: 1 }} flexWrap="wrap" rowGap={0.75}>
              {["Source code", "Agent discovery", "Pull requests", "Checks"].map((t) => (
                <Chip key={t} label={t} size="small" variant="outlined" sx={{ height: 22, typography: "s3" }} />
              ))}
            </Stack>
          </Box>
          {github ? (
            <>
              <StatusPill on />
              <Button
                size="small" variant="outlined" onClick={() => setInstallOpen(true)}
                startIcon={<Iconify icon="mdi:github" width={15} />}
                sx={{ typography: "s2", fontWeight: 700, color: "text.primary", borderColor: "divider" }}
              >
                Add installation
              </Button>
            </>
          ) : (
            <Stack alignItems="flex-end" spacing={0.5}>
              <Button
                variant="contained" color="primary" size="small" onClick={() => setInstallOpen(true)}
                startIcon={<Iconify icon="mdi:github" width={16} />}
                sx={{ typography: "s2", fontWeight: 700 }}
              >
                {githubRemoved.length ? "Reinstall GitHub App" : "Install GitHub App"}
              </Button>
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>Opens GitHub · you pick the repositories</Typography>
            </Stack>
          )}
        </Stack>
      </Card>

      {/* Just connected from a scan. Fix and improvements prove a change in
          simulation, so an agent with no environment is one step short. */}
      {justConnected && (
        <Notice
          icon="solar:check-circle-linear"
          title={`${justConnected.name} is connected`}
          action={(
            <>
              <Button size="small" onClick={dismissConnected} sx={{ typography: "s3", fontWeight: 700, color: "text.secondary" }}>Dismiss</Button>
              <Button
                size="small" variant={justConnected.usedIn?.length ? "contained" : "outlined"}
                onClick={() => navigate(`${paths.dashboard.agents}/connected/${justConnected.id}`)}
                sx={{ typography: "s3", fontWeight: 700, ...(!justConnected.usedIn?.length && { color: "text.primary", borderColor: "divider" }) }}
              >
                View agent
              </Button>
              {!justConnected.usedIn?.length && (
                <Button size="small" variant="contained" onClick={() => { setBuildFor(justConnected); setBuildOpen(true); }} sx={{ typography: "s3", fontWeight: 700 }}>
                  Build environment
                </Button>
              )}
            </>
          )}
        >
          {justConnected.usedIn?.length
            ? `It's tested in ${justConnected.usedIn.map((u) => u.envName).join(", ")}. Fixes for it open as draft pull requests here.`
            : "Next, build an environment for it. Improvements, including fixes for Error Feed issues, prove every change in simulation first, so they need one — the first you build becomes its main environment."}
        </Notice>
      )}
      {buildFor && (
        <BuildEnvironmentDialog
          open={buildOpen}
          onClose={() => setBuildOpen(false)}
          agent={buildFor}
          defaultVersion={buildFor.production || buildFor.latest}
          onBuild={(choice) => navigate(paths.dashboard.simulate.environmentBuild, { state: { presetSource: buildPresetFor(buildFor, choice) } })}
        />
      )}

      {/* The app was uninstalled on GitHub's side (installation.deleted) — per installation. */}
      {githubRemoved.map((r) => (
        <Notice
          key={r.account} icon="solar:link-broken-linear" title={`The Future AGI app was removed from ${r.account} on GitHub`}
          action={(
            <>
              <Button size="small" onClick={() => dismissGithubRemoved(r.account)} sx={{ typography: "s3", fontWeight: 700, color: "text.secondary" }}>Dismiss</Button>
              <Button size="small" variant="outlined" onClick={() => setInstallOpen(true)} sx={{ typography: "s3", fontWeight: 700, color: "text.primary", borderColor: "divider" }}>Reinstall</Button>
            </>
          )}
        >
          {`Agents from ${r.account} keep their versions and history. Re-reads and pull requests stop until it's reinstalled.`}
        </Notice>
      ))}


      {!github && (
        <Card sx={{ p: 3 }}>
          <Typography sx={{ typography: "s1", fontWeight: 700, mb: 2 }}>What connecting GitHub unlocks</Typography>
          <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" } }}>
            {CAPABILITIES.map((c) => (
              <Stack key={c.title} direction="row" spacing={1.5}>
                <Box sx={{ width: 32, height: 32, borderRadius: 1, flexShrink: 0, display: "grid", placeItems: "center", bgcolor: (t) => alpha(t.palette.text.primary, 0.05), color: "text.secondary" }}>
                  <Iconify icon={c.icon} width={16} />
                </Box>
                <Box>
                  <Typography sx={{ typography: "s2", fontWeight: 700 }}>{c.title}</Typography>
                  <Typography sx={{ typography: "s2", color: "text.secondary", mt: 0.25 }}>{c.body}</Typography>
                </Box>
              </Stack>
            ))}
          </Box>
          <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 2.5 }}>
            You choose the repositories on GitHub. We only read your code. We&apos;ll ask for write access when you open your first draft pull request — and then write only to branches we create, never to main or your branches.
          </Typography>
        </Card>
      )}

      {github && (
        <Stack spacing={3}>
          <Card>
            <SectionHead title="Installations" sub="Each GitHub account the app is installed on. Repository access is changed on GitHub." />
            {githubInstalls(integrations).map((inst, i) => {
              const n = reposOfInstall(inst).length;
              return (
                <Stack key={inst.id} direction="row" alignItems="center" spacing={1.5} sx={{ px: 3, py: 1.25, borderTop: i ? "1px solid" : "none", borderColor: "divider" }}>
                  <Box sx={{ width: 26, height: 26, borderRadius: inst.kind === "Organization" ? 0.75 : "50%", display: "grid", placeItems: "center", bgcolor: "background.neutral", flexShrink: 0 }}>
                    <Typography sx={{ typography: "s3", fontWeight: 700 }}>{inst.account[0].toUpperCase()}</Typography>
                  </Box>
                  <Box flex={1} minWidth={0}>
                    <Typography sx={{ typography: "s2", fontWeight: 600 }}>{inst.account}</Typography>
                    <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                      {`${inst.kind || "Account"} · ${inst.access === "all" ? "all repositories" : `${n} repositor${n === 1 ? "y" : "ies"}`} · ${inst.githubAccess === "write" ? "read and write (draft PRs)" : "read only"} · installed by ${inst.installedBy}`}
                    </Typography>
                  </Box>
                  <Button
                    size="small" variant="outlined" onClick={() => setInstallOpen(inst.account)}
                    endIcon={<Iconify icon="solar:arrow-right-up-linear" width={13} />}
                    sx={{ typography: "s3", fontWeight: 700, color: "text.primary", borderColor: "divider" }}
                  >
                    Configure on GitHub
                  </Button>
                  <Button size="small" aria-label="More actions" onClick={(e) => setMenu({ el: e.currentTarget, account: inst.account })} sx={{ minWidth: 0, color: "text.secondary" }}>
                    <Iconify icon="solar:menu-dots-bold" />
                  </Button>
                </Stack>
              );
            })}
            <Menu anchorEl={menu?.el} open={!!menu} onClose={() => setMenu(null)}>
              <MenuItem onClick={() => { setUninstalling(menu.account); setMenu(null); }} sx={{ color: "error.main", typography: "s2" }}>Uninstall</MenuItem>
            </Menu>
            <Dialog open={!!uninstalling} onClose={() => setUninstalling(null)} maxWidth="xs" fullWidth>
              <DialogTitle sx={{ typography: "m2", fontWeight: 700 }}>{`Uninstall from ${uninstalling}?`}</DialogTitle>
              <DialogContent>
                <DialogContentText sx={{ typography: "s2" }}>
                  {`Agents from ${uninstalling} keep their versions and history. Re-reads and pull requests stop until the app is installed again. To also remove the app on GitHub's side, uninstall it from ${uninstalling}'s GitHub settings.`}
                </DialogContentText>
              </DialogContent>
              <DialogActions sx={{ px: 3, pb: 2 }}>
                <Button onClick={() => setUninstalling(null)} sx={{ typography: "s2", fontWeight: 600, color: "text.secondary" }}>Cancel</Button>
                <Button
                  variant="contained"
                  onClick={() => { uninstallGithub(uninstalling); setUninstalling(null); }}
                  sx={{ typography: "s2", fontWeight: 700, bgcolor: "#DC2626", color: "#fff", "&:hover": { bgcolor: "#B91C1C", color: "#fff" } }}
                >
                  Uninstall
                </Button>
              </DialogActions>
            </Dialog>
          </Card>

          <Card>
            <SectionHead title="Repositories" sub="Scan a repository to find the agents in it, then connect the one you mean." />
            {repos.map((r, i) => {
              const s = scanned[r.name] || (integrations.scans?.[r.name] ? "done" : undefined);
              return (
                <Box key={r.name} sx={{ borderTop: i ? "1px solid" : "none", borderColor: "divider" }}>
                  <Stack direction="row" alignItems="center" spacing={1.5} sx={{ px: 3, py: 1.5 }}>
                    <Iconify icon={r.private ? "solar:lock-keyhole-minimalistic-linear" : "solar:folder-linear"} width={16} sx={{ color: "text.subtitle" }} />
                    <Typography sx={{ typography: "s2", fontFamily: MONO, fontWeight: 600, flex: 1 }}>{r.name}</Typography>
                    <Typography sx={{ typography: "s3", color: "text.subtitle", width: 90 }}>{r.language}</Typography>
                    <Typography sx={{ typography: "s3", color: "text.subtitle", width: 110 }}>{`Updated ${r.updated}`}</Typography>
                    <Button
                      size="small" variant="outlined" disabled={s === "scanning"} onClick={() => scan(r.name)}
                      startIcon={<Iconify icon="solar:magnifer-linear" width={14} />}
                      sx={{ typography: "s3", fontWeight: 700, color: "text.primary", borderColor: "divider", minWidth: 116 }}
                    >
                      {s === "scanning" ? "Scanning…" : s === "done" ? "Scan again" : "Scan"}
                    </Button>
                  </Stack>
                  {s === "done" && (
                    <Box sx={{ px: 3, pb: 1.75, pl: 6.5 }}>
                      {r.agents.length === 0 ? (
                        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>No agents found in this repository.</Typography>
                      ) : (
                        <RepoAgents
                          agents={r.agents}
                          connectedOf={(a) => connectedFor(r, a)}
                          onView={(c) => navigate(`${paths.dashboard.agents}/connected/${c.id}`)}
                          onConnect={(a) => connectScanned(r, a)}
                        />
                      )}
                    </Box>
                  )}
                </Box>
              );
            })}
          </Card>

          <Card>
            <SectionHead title="Agents connected from GitHub" sub={AUTO_REREAD} />
            {ghAgents.length === 0 ? (
              <Typography sx={{ px: 3, pb: 2.5, typography: "s2", color: "text.subtitle" }}>None yet — scan a repository above and connect an agent.</Typography>
            ) : ghAgents.map((a, i) => (
              <Stack
                key={a.id} direction="row" alignItems="center" spacing={1.5}
                {...clickable(() => navigate(`${paths.dashboard.agents}/connected/${a.id}`), { role: "link", label: `Open ${a.name}` })}
                sx={{ px: 3, py: 1.25, cursor: "pointer", borderTop: i ? "1px solid" : "none", borderColor: "divider", "&:hover": { bgcolor: "action.hover" }, ...focusRing }}
              >
                <Box flex={1} minWidth={0}>
                  <Typography sx={{ typography: "s2", fontWeight: 600 }}>{a.name}</Typography>
                  {agentSourceOf(a).appLost ? (
                    <Typography sx={{ typography: "s3", color: "text.secondary" }}>The app lost access to this repository — reinstall to sync it again</Typography>
                  ) : agentNeedsRuntime(a) && (
                    <Typography sx={{ typography: "s3", color: "text.secondary" }}>Add where it runs before the first simulation</Typography>
                  )}
                </Box>
                <Typography sx={{ typography: "s3", fontFamily: MONO, color: "text.subtitle" }}>{repoNameOf(definitionOf(a).repoUrl)}</Typography>
                <Typography sx={{ typography: "s3", color: "text.subtitle", width: 90, textAlign: "right" }}>{`${a.versions.length} version${a.versions.length === 1 ? "" : "s"}`}</Typography>
                {/* Its Observe project — what lets its Error Feed issues reach Fix. */}
                {(() => {
                  const projects = projectsOfAgent(a, simState, agents);
                  return (
                    <Tooltip arrow title={projects.length ? "Its production traces land here — Error Feed issues from it can be fixed." : "Its traces don't reach an Observe project yet, so its Error Feed issues can't be fixed. Set it on the agent's Connections tab."}>
                      <Typography noWrap sx={{ typography: "s3", color: projects.length ? "text.secondary" : "text.disabled", width: 150, textAlign: "right" }}>
                        {projects.length ? projects.map((p) => p.name).join(", ") : "No Observe project"}
                      </Typography>
                    </Tooltip>
                  );
                })()}
                {/* Not tested anywhere yet: the next step is its environment. */}
                {!a.usedIn?.length && (
                  <Button
                    size="small" variant="outlined"
                    onClick={(e) => { e.stopPropagation(); setBuildFor(a); setBuildOpen(true); }}
                    sx={{ typography: "s3", fontWeight: 700, height: 26, color: "text.primary", borderColor: "divider", flexShrink: 0 }}
                  >
                    Build environment
                  </Button>
                )}
              </Stack>
            ))}
          </Card>

          <Card>
            <SectionHead title="Pull requests opened by Future AGI" sub="Every fix we propose, and what happened to it." />
            {prs.length === 0 ? (
              <Typography sx={{ px: 3, pb: 2.5, typography: "s2", color: "text.subtitle" }}>None yet — fixes from Improvements and the Error Feed open here as draft pull requests.</Typography>
            ) : prs.map((p, i) => (
              <Stack key={p.id} direction="row" alignItems="center" spacing={1.5} sx={{ px: 3, py: 1.25, borderTop: i ? "1px solid" : "none", borderColor: "divider" }}>
                <PrStatus status={p.status} />
                <Box flex={1} minWidth={0}>
                  <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>{p.title}</Typography>
                  <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                    <Box component="span" sx={{ fontFamily: MONO }}>{`${p.repo} #${p.number}`}</Box>
                    {` · ${p.by ? `by ${p.by}` : `${p.files.length} file${p.files.length === 1 ? "" : "s"}`}${prCheckSummary(p.check) ? ` · ${prCheckSummary(p.check)}` : ""}`}
                  </Typography>
                </Box>
                {hasVerifyCheck(p) && <PrVerifyStatus pr={p} />}
              </Stack>
            ))}
          </Card>

          <Card>
            <SectionHead title="Permissions" sub="Read was granted at install. Write is asked for once, at the first draft pull request — Copy patch works without it." />
            {GITHUB_PERMISSIONS.map((p, i) => (
              <Stack key={p.scope} direction="row" spacing={2} sx={{ px: 3, py: 1.25, borderTop: i ? "1px solid" : "none", borderColor: "divider" }}>
                <Typography sx={{ typography: "s2", fontWeight: 600, width: 140 }}>{p.scope}</Typography>
                <Typography sx={{ typography: "s2", width: 120 }}>{p.access}</Typography>
                <Typography sx={{ typography: "s2", color: "text.secondary", flex: 1 }}>{p.why}</Typography>
              </Stack>
            ))}
            <Typography sx={{ px: 3, pt: 1.5, pb: 0.5, typography: "s3", fontWeight: 700, color: "text.subtitle", textTransform: "uppercase", letterSpacing: 0.5, borderTop: "1px solid", borderColor: "divider" }}>
              Asked at the first draft PR
            </Typography>
            {GITHUB_WRITE_PERMISSIONS.map((p) => (
              <Stack key={`w-${p.scope}`} direction="row" spacing={2} sx={{ px: 3, py: 1 }}>
                <Typography sx={{ typography: "s2", fontWeight: 600, width: 140 }}>{p.scope}</Typography>
                <Typography sx={{ typography: "s2", width: 120 }}>{p.access}</Typography>
                <Typography sx={{ typography: "s2", color: "text.secondary", flex: 1 }}>{p.why}</Typography>
              </Stack>
            ))}
            <Typography sx={{ px: 3, pt: 0.5, pb: 1.5, typography: "s3", color: "text.subtitle" }}>
              {githubInstalls(integrations).map((inst) => `${inst.account}: ${inst.githubAccess === "write" ? "write granted" : "read only so far"}`).join(" · ")}
            </Typography>
          </Card>
        </Stack>
      )}

      <GithubInstallDialog
        open={!!installOpen} onClose={() => setInstallOpen(false)}
        configure={typeof installOpen === "string" ? installOpen : undefined}
      />
    </Box>
  );
}

function Notice({ icon, iconColor = "text.secondary", title, action, children }) {
  return (
    <Card sx={{ mb: 3, px: 3, py: 2 }}>
      <Stack direction="row" alignItems="center" spacing={1.5}>
        <Iconify icon={icon} width={18} sx={{ color: iconColor, flexShrink: 0 }} />
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s2", fontWeight: 700 }}>{title}</Typography>
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>{children}</Typography>
        </Box>
        {action}
      </Stack>
    </Card>
  );
}
Notice.propTypes = { icon: PropTypes.string, iconColor: PropTypes.string, title: PropTypes.string, action: PropTypes.node, children: PropTypes.node };

function SectionHead({ title, sub }) {
  return (
    <Box sx={{ px: 3, pt: 2.5, pb: 1.5 }}>
      <Typography sx={{ typography: "s1", fontWeight: 700 }}>{title}</Typography>
      <Typography sx={{ typography: "s2", color: "text.secondary" }}>{sub}</Typography>
    </Box>
  );
}
SectionHead.propTypes = { title: PropTypes.string, sub: PropTypes.string };

/* Tinted, not filled — a solid green chip glares on dark and its white
   label fails contrast. The green is lighter on dark so the label stays readable. */
const ON_GREEN = (t) => (t.palette.mode === "dark" ? "#4ADE80" : "#15803D");

/* `labels` — [on, off]. Every integration reads Connected / Not connected;
   "installed" stays in GitHub's own detail line ("Installed on acme-inc"). */
export function StatusPill({ on, size = "medium", labels = ["Connected", "Not connected"] }) {
  const small = size === "small";
  return (
    <Stack
      direction="row" alignItems="center" spacing={0.5}
      sx={{
        px: small ? 0.75 : 1, height: small ? 20 : 24, borderRadius: 0.75, flexShrink: 0,
        bgcolor: (t) => (on ? alpha(ON_GREEN(t), t.palette.mode === "dark" ? 0.12 : 0.1) : alpha(t.palette.text.primary, 0.06)),
      }}
    >
      <Box sx={{ width: 6, height: 6, borderRadius: "50%", bgcolor: (t) => (on ? ON_GREEN(t) : t.palette.text.disabled) }} />
      <Typography sx={{ typography: "s3", fontWeight: 600, color: (t) => (on ? ON_GREEN(t) : t.palette.text.secondary) }}>{on ? labels[0] : labels[1]}</Typography>
    </Stack>
  );
}
StatusPill.propTypes = { on: PropTypes.bool, size: PropTypes.oneOf(["small", "medium"]), labels: PropTypes.arrayOf(PropTypes.string) };

export function PrStatus({ status }) {
  const meta = {
    open: { icon: "solar:code-square-linear", color: "#16A34A", label: "Open" },
    merged: { icon: "solar:branching-paths-up-linear", color: "#8250DF", label: "Merged" },
    closed: { icon: "solar:close-circle-linear", color: "#DC2626", label: "Closed" },
  }[status] || { icon: "solar:clock-circle-linear", color: "#CA8A04", text: "text.secondary", label: "Pending" };
  return (
    <Stack direction="row" alignItems="center" spacing={0.5} sx={{ px: 0.75, height: 22, borderRadius: 0.75, flexShrink: 0, color: meta.color, bgcolor: alpha(meta.color, 0.12) }}>
      <Iconify icon={meta.icon} width={13} />
      <Typography sx={{ typography: "s3", fontWeight: 700, color: meta.text || "inherit" }}>{meta.label}</Typography>
    </Stack>
  );
}
PrStatus.propTypes = { status: PropTypes.string };

/*
  What a scan found in one repository. A monorepo can hold dozens of agents,
  so the list has a search box and scrolls in its own frame past a handful
  of rows; connected ones are counted in the heading.
*/
function RepoAgents({ agents, connectedOf, onView, onConnect }) {
  const [query, setQuery] = useState("");
  const q = query.trim().toLowerCase();
  const shown = q ? agents.filter((a) => `${a.name} ${a.framework} ${a.entry}`.toLowerCase().includes(q)) : agents;
  const connected = agents.filter((a) => connectedOf(a)).length;
  return (
    <Stack spacing={0.75}>
      <Stack direction="row" alignItems="center" spacing={1.5}>
        <Typography sx={{ typography: "s3", color: "text.subtitle", flexShrink: 0, fontVariantNumeric: "tabular-nums" }}>
          {`Found ${agents.length} agent${agents.length === 1 ? "" : "s"}${connected ? ` · ${connected} connected` : ""}${q ? ` · ${shown.length} shown` : ""}`}
        </Typography>
        <Box
          sx={{
            ml: "auto", display: "flex", alignItems: "center", gap: 0.75, px: 1, height: 30, width: 280, maxWidth: "100%",
            border: "1px solid", borderColor: "divider", borderRadius: 1, bgcolor: "background.paper",
            "&:focus-within": { borderColor: (t) => alpha(t.palette.text.primary, 0.45) },
          }}
        >
          <Iconify icon="solar:magnifer-linear" width={14} sx={{ color: "text.disabled", flexShrink: 0 }} />
          <InputBase
            fullWidth value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Escape") setQuery(""); }}
            placeholder="Search name, framework or file"
            inputProps={{ "aria-label": "Search the agents found", spellCheck: false }}
            sx={{ typography: "s3", "& input::placeholder": { color: "text.disabled", opacity: 1 } }}
          />
        </Box>
      </Stack>
      {shown.length ? (
        <Stack spacing={0.75} sx={agents.length > 5 ? { maxHeight: 300, overflowY: "auto", pr: 0.5, mr: -0.5 } : undefined}>
          {shown.map((a) => {
            const c = connectedOf(a);
            return (
              <Stack key={a.id} direction="row" alignItems="center" spacing={1.25} sx={{ px: 1.5, py: 1, borderRadius: 1, border: "1px solid", borderColor: "divider", flexShrink: 0 }}>
                <Box flex={1} minWidth={0}>
                  <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>{a.name}</Typography>
                  <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
                    {`${a.framework} · `}
                    <Box component="span" sx={{ fontFamily: MONO }}>{a.entry}</Box>
                    {` · ${a.tools} tool${a.tools === 1 ? "" : "s"}`}
                  </Typography>
                </Box>
                {c ? (
                  <Button
                    size="small" variant="outlined" onClick={() => onView(c)}
                    startIcon={<Iconify icon="solar:check-circle-linear" width={14} />}
                    sx={{ typography: "s3", fontWeight: 700, color: "text.primary", borderColor: "divider" }}
                  >
                    Connected · View
                  </Button>
                ) : (
                  <Button size="small" variant="contained" color="primary" onClick={() => onConnect(a)} sx={{ typography: "s3", fontWeight: 700 }}>
                    Connect
                  </Button>
                )}
              </Stack>
            );
          })}
        </Stack>
      ) : (
        <Typography sx={{ typography: "s3", color: "text.subtitle", py: 0.5 }}>{`No agent here matches “${query.trim()}”.`}</Typography>
      )}
    </Stack>
  );
}
RepoAgents.propTypes = { agents: PropTypes.array.isRequired, connectedOf: PropTypes.func.isRequired, onView: PropTypes.func.isRequired, onConnect: PropTypes.func.isRequired };
