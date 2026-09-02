import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, Button, Grid, MenuItem, TextField, Chip, Tooltip,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { getSurface, getDomain } from "../_mock/surfaces";
import { twinById, detectedTwinsFor, liveSandboxContentFor } from "../_mock/twins";
import TwinLogo from "./../components/TwinLogo";
import SlackSandboxMock from "../twins/SlackSandboxMock";
import NotionSandboxMock from "../twins/NotionSandboxMock";
import GmailSandboxMock from "../twins/GmailSandboxMock";
import SalesforceSandboxMock from "../twins/SalesforceSandboxMock";
import GenericSandboxMock from "../twins/GenericSandboxMock";
import { OpenApiDialog, OpenSurfaceDialog } from "../twins/TwinSandboxDialogs";
import TwinConnectDialog from "../twins/TwinConnectDialog";

const SANDBOX_MOCKS = {
  slack: SlackSandboxMock,
  notion: NotionSandboxMock,
  gmail: GmailSandboxMock,
  salesforce: SalesforceSandboxMock,
};
import { DIFFICULTIES } from "../_mock/environments";
import { packStats } from "../_mock/scenarios";
import { effectiveEnv } from "../_mock/rlContract";
import { contractFor } from "../_mock/contract";
import { provenanceFor } from "../_mock/provenance";
import { OriginChip, SectionCard, EmptyState } from "../components/primitives";
import CapabilityGraph from "./CapabilityGraph";

/**
 * What this environment is.
 *
 * The page answers three questions in order, and is grouped that way rather
 * than as a wall of equal cards: what the agent is (its contract), what world
 * it acts on, and what decides whether it did well. The facts that used to sit
 * in a tall card on the right are a strip under the title instead — they are
 * labels, not content, and they were taking a column from things that are.
 */
const seedBlurb = (rows) =>
  `${rows.toLocaleString()} rows that fill this environment before your agent arrives — the world it actually works in. Rebuilt for every task, so nothing carries over.`;

export default function OverviewPanel({ buildMode, env, envState, patch, onGo, agentConnected }) {

  const surface = getSurface(env.surface);
  const domain = getDomain(env.domain);
  const totalRows = env.seed?.tables?.reduce((a, t) => a + t.rows, 0) || 0;
  /* Depth is editable on the Scenarios step, so Overview must report what is
     in force rather than what the template shipped with. */
  const shown = effectiveEnv(env, envState);
  const stats = packStats(shown);
  const contract = contractFor(env);
  /* Rules carry where they were found, and a rule found in prose is held back
     rather than graded — the badge is the only place that is visible here, so
     the card links through to the review queue. */
  const ruleProv = provenanceFor(env).rules;
  const held = ruleProv.filter((r) => r.held && !(envState?.confirmedRules || []).includes(r.id));

  return (
    <Box sx={{ p: 2 }}>
      {/* Twin-backed env — the sandbox preview is the star of Overview
          so users land in the workspace and immediately see the live
          service their agent will talk to. Sub-header carries the
          service name, endpoint URL, and OpenAPI / Open surface
          buttons so there's no duplicated chrome below. */}
      {envState?.twinBacking && (
        <TwinSandboxSection env={env} envState={envState} />
      )}

      {/* Auto-detected twin suggestion — for agent-derived envs where
          we read the agent's code and noticed it talks to SaaS
          services. Nudges the user toward attaching a twin backing
          without forcing them; a one-click action provisions and
          drops them into the twin-config mini-flow. */}
      {!envState?.twinBacking && !buildMode && agentConnected && (
        <TwinSuggestionBanner env={env} envState={envState} patch={patch} />
      )}

      {/*
        The "Connect your agent" banner never fires in build mode
        because BuildFromAgent primes envState.agent before this
        panel mounts. Kept for the post-run workspace, hidden here
        so it can't flash on the first frame.
      */}
      {!agentConnected && !buildMode && (
        <Box
          sx={{
            p: 2, mb: 2, borderRadius: 1.5,
            border: "1px solid", borderColor: alpha(surface.color, 0.3),
            bgcolor: (t) => alpha(surface.color, t.palette.mode === "dark" ? 0.1 : 0.05),
          }}
        >
          <Stack direction={{ xs: "column", sm: "row" }} alignItems={{ sm: "center" }} spacing={2}>
            <Box flex={1}>
              <Typography sx={{ typography: "s1", fontWeight: 700 }}>
                Connect your agent to start testing
              </Typography>
              <Typography sx={{ typography: "s2", color: "text.secondary", mt: 0.25 }}>
                {surface.blurb} We handle the {surface.transports.join(", ")} side — you point us at your agent.
              </Typography>
            </Box>
            <Button
              variant="contained"
              color="primary"
              onClick={() => onGo("agent")}
              endIcon={<Iconify icon="solar:arrow-right-linear" width={16} />}
              sx={{ flexShrink: 0, typography: "s2", fontWeight: 700 }}
            >
              Connect agent
            </Button>
          </Stack>
        </Box>
      )}

      {/*
        Difficulty sits here rather than in the strip below: that strip is a
        row of read-only facts, and dropping one interactive cell into it broke
        both the alignment and the pattern. As a labelled select beside the
        description it reads as what it is — a setting you change before a run.
      */}
      <Stack direction={{ xs: "column", sm: "row" }} alignItems={{ sm: "flex-start" }} spacing={2}>
        <Typography sx={{ typography: "s2", color: "text.secondary", maxWidth: 820 }}>
          {env.description}
        </Typography>
        {/* Pushed to the far edge — without this the select sits wherever the
            description happens to end. */}
        <Box sx={{ flex: 1, display: { xs: "none", sm: "block" } }} />
        <TextField
          select size="small" label="Difficulty" value={shown.difficulty}
          onChange={(e) => patch({ difficulty: e.target.value })}
          sx={{ minWidth: 150, flexShrink: 0, "& .MuiInputBase-input": { typography: "s2", fontWeight: 600, py: 1 } }}
        >
          {DIFFICULTIES.map((d) => (
            <MenuItem key={d} value={d} sx={{ typography: "s2" }}>{d}</MenuItem>
          ))}
        </TextField>
      </Stack>

      {/* Labels, not content — a strip rather than a column of its own. */}
      <Stack
        direction="row"
        flexWrap="wrap"
        divider={<Box sx={{ width: "1px", bgcolor: "divider", alignSelf: "stretch", mx: 2 }} />}
        sx={{ my: 2, py: 1.25, px: 2, border: "1px solid", borderColor: "divider", borderRadius: 1.5, rowGap: 1 }}
      >
        <Fact label="Channel" value={surface.label} />
        <Fact label="Domain" value={domain?.label || "—"} />
        <Fact label="Transports" value={surface.transports.join(" · ")} />
        <Fact label="Scenario packs" value={`${stats.packs} packs · ${stats.scenarios} scenarios`} />
      </Stack>

      {/*
        Twin-backed envs get their own set of sections below —
        Capabilities/The world are read from a source repo, and twin
        envs don't have one. Skipping this whole block prevents empty
        "0 tools · 0 rules · 0 rows" cards from filling the screen.
      */}
      {envState?.twinBacking ? (
        <TwinBackedSections env={env} envState={envState} onGo={onGo} />
      ) : (
        <>
      {/*
        Formerly "The contract" — collided with the dedicated Contract
        tab (which holds the RL contract: adapter, spaces, dynamics,
        reward). This group is a capability inventory of the source
        agent, so the label now reflects that and the collision is
        gone.
      */}
      <GroupHeading>Capabilities</GroupHeading>
      {/*
        The graph first, then the lists. Twelve tools and five rules read as
        two unrelated inventories; drawn together they are one object, and the
        shape of the environment is legible before any of it is read.
      */}
      {agentConnected && !envState?.twinBacking && (
        <CapabilityGraph env={env} envState={envState} onGo={onGo} />
      )}
      <Grid container spacing={2} sx={{ mb: 3 }}>
        <Grid item xs={12} md={7}>
          <SectionCard
            title="Tools"
            subtitle={
              agentConnected
                ? `${env.tools.length} actions your agent declared, with the arguments it really takes`
                : "Read from your agent once it connects"
            }
          >
            {!agentConnected ? (
              <EmptyState
                icon="solar:settings-minimalistic-linear"
                title="No tools yet"
                body="Tool definitions come from the agent. Connect one and they'll be listed here."
                action={
                  <Button
                    variant="contained"
                    color="primary"
                    size="small"
                    onClick={() => onGo("agent")}
                    sx={{ typography: "s2", fontWeight: 700 }}
                  >
                    Connect agent
                  </Button>
                }
              />
            ) : (
              <Stack
                divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}
                sx={{ maxHeight: 360, overflowY: "auto" }}
              >
                {env.tools.map((t) => (
                  <Stack key={t.name} direction="row" alignItems="center" spacing={2} sx={{ px: 2.5, py: 1.125 }}>
                    <Box flex={1} minWidth={0}>
                      <Typography sx={{ typography: "s2", fontWeight: 600, fontFamily: "ui-monospace, Menlo, monospace" }}>
                        {t.name}
                      </Typography>
                      <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>{t.desc}</Typography>
                    </Box>
                    <Typography
                      noWrap
                      sx={{ typography: "s3", color: "text.subtitle", flexShrink: 0, fontFamily: "ui-monospace, Menlo, monospace" }}
                    >
                      {t.args?.length ? t.args.join(", ") : "no arguments"}
                    </Typography>
                  </Stack>
                ))}
              </Stack>
            )}
          </SectionCard>
        </Grid>

        <Grid item xs={12} md={5}>
          <SectionCard
            title="Hard rules"
            subtitle="Told to the agent, graded afterwards — hover a badge for where it was found"
            action={
              held.length > 0 && (
                <Button
                  size="small"
                  onClick={() => onGo("build")}
                  sx={{ typography: "s2", fontWeight: 700, color: "#DC2626" }}
                >
                  {held.length} held
                </Button>
              )
            }
          >
            <Stack sx={{ p: 2.5 }} spacing={1.25}>
              {ruleProv.map((r) => (
                <Stack key={r.id} direction="row" spacing={1.25} alignItems="flex-start">
                  <Iconify icon="solar:shield-check-linear" width={15} sx={{ color: "primary.main", flexShrink: 0, mt: "1px" }} />
                  <Typography sx={{ typography: "s2", color: "text.secondary", flex: 1, minWidth: 0 }}>{r.subject}</Typography>
                  <OriginChip origin={r.origin} file={r.file} line={r.line} showPath={false} />
                </Stack>
              ))}
            </Stack>
          </SectionCard>
        </Grid>

        <Grid item xs={12} md={7}>
          <SectionCard title="Use cases" subtitle="What it is actually for">
            <Stack sx={{ p: 2.5 }} spacing={1}>
              {contract.useCases.map((u) => (
                <Stack key={u} direction="row" spacing={1.25} alignItems="flex-start">
                  <Box sx={{ width: 4, height: 4, borderRadius: "50%", bgcolor: "text.subtitle", flexShrink: 0, mt: "7px" }} />
                  <Typography sx={{ typography: "s2", color: "text.secondary" }}>{u}</Typography>
                </Stack>
              ))}
            </Stack>
          </SectionCard>
        </Grid>

        <Grid item xs={12} md={5}>
          {!buildMode && (

          <SectionCard title="Amendments" subtitle="Changed after reading, each with its reason">
            <Stack sx={{ p: 2.5 }} spacing={1.25}>
              {contract.amendments.map((a) => (
                <Box
                  key={a.subject}
                  sx={{
                    p: 1.5, borderRadius: 1,
                    border: "1px solid", borderColor: alpha("#CA8A04", 0.3),
                    bgcolor: (t) => alpha("#CA8A04", t.palette.mode === "dark" ? 0.1 : 0.05),
                  }}
                >
                  <Typography sx={{ typography: "s2", fontWeight: 700, fontFamily: "ui-monospace, Menlo, monospace" }}>
                    {a.subject}
                  </Typography>
                  <Typography sx={{ typography: "s3", color: "text.secondary", mt: 0.25 }}>{a.note}</Typography>
                </Box>
              ))}
            </Stack>
          </SectionCard>
          )}
        </Grid>
      </Grid>

      {/* ── the world it acts on ── */}
      <GroupHeading>The world</GroupHeading>
      <Grid container spacing={2} sx={{ mb: 3 }}>
        <Grid item xs={12} md={7}>
          <SectionCard title="Seeded data" subtitle={seedBlurb(totalRows)}>
            <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
              {env.seed.tables.map((t) => (
                <Stack key={t.name} direction="row" alignItems="center" spacing={2} sx={{ px: 2.5, py: 1.25 }}>
                  <Iconify icon="solar:database-linear" width={15} sx={{ color: "text.subtitle", flexShrink: 0 }} />
                  <Box flex={1} minWidth={0}>
                    <Typography sx={{ typography: "s2", fontWeight: 600, fontFamily: "ui-monospace, Menlo, monospace" }}>
                      {t.name}
                    </Typography>
                    {t.note && (
                      <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>{t.note}</Typography>
                    )}
                  </Box>
                  <Typography sx={{ typography: "s2", fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>
                    {t.rows.toLocaleString()}
                  </Typography>
                </Stack>
              ))}
            </Stack>
          </SectionCard>
        </Grid>

        <Grid item xs={12} md={5}>
          <SectionCard title="What it depends on" subtitle="Built and torn down with the environment">
            <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
              {contract.dependsOn.map((d) => (
                <Box key={d.name} sx={{ px: 2.5, py: 1.5 }}>
                  <Stack direction="row" alignItems="center" spacing={1}>
                    <Typography sx={{ typography: "s2", fontWeight: 700, fontFamily: "ui-monospace, Menlo, monospace" }}>
                      {d.name}
                    </Typography>
                    <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{d.kind}</Typography>
                  </Stack>
                  <Typography sx={{ typography: "s2", color: "text.secondary", mt: 0.25 }}>{d.provides}</Typography>
                  <Typography
                    sx={{ typography: "s3", color: "text.subtitle", mt: 0.5, fontFamily: "ui-monospace, Menlo, monospace" }}
                  >
                    used by {d.usedBy}
                  </Typography>
                </Box>
              ))}
            </Stack>
          </SectionCard>
        </Grid>
      </Grid>
      </>
      )}

    </Box>
  );
}

OverviewPanel.propTypes = {
  "buildMode": PropTypes.bool,
  env: PropTypes.object.isRequired,
  envState: PropTypes.object.isRequired,
  patch: PropTypes.func.isRequired,
  onGo: PropTypes.func,
  agentConnected: PropTypes.bool,
};

function GroupHeading({ children }) {
  return (
    <Typography
      sx={{
        typography: "s3", fontWeight: 700, color: "text.primary",
        textTransform: "uppercase", letterSpacing: .5, mb: 1.25,
      }}
    >
      {children}
    </Typography>
  );
}
GroupHeading.propTypes = { children: PropTypes.node };

function Fact({ label, value, color }) {
  return (
    <Box sx={{ minWidth: 0 }}>
      <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>{label}</Typography>
      <Typography noWrap sx={{ typography: "s2", fontWeight: 600, color: color || "text.primary" }}>
        {value}
      </Typography>
    </Box>
  );
}
Fact.propTypes = { label: PropTypes.string, value: PropTypes.node, color: PropTypes.string };

/* ── twin backing surface panel ───────────────────────────────────────────── */

/*
  Activity derivation. In production the twin runtime streams every
  read/write; here we materialise the same shape from the run
  history so the counters read as real usage of the sandbox rather
  than static fixtures. Stable per (envState, service) so a
  refresh doesn't renumber them, and grows monotonically with
  run count so the surface feels alive without needing a websocket.
*/
function activityForService(envState, service, index, totalServices) {
  const runs = envState?.runs || [];
  const totalRuns = runs.length;
  const seed = hash(service) % 7;
  const writesPerRun = 2 + ((seed + index) % 4);
  const readsPerRun = 4 + ((seed + index * 3) % 5);
  const totalWrites = totalRuns * writesPerRun;
  const totalReads = totalRuns * readsPerRun;
  /*
    Last-touched marker — the service that would have been written most
    recently in the last run. Rotates across services by run index so
    the live pulse doesn't sit on the same lane forever.
  */
  const lastTouched = totalRuns > 0 && index === (totalRuns - 1) % Math.max(1, totalServices);
  const lastAt = totalRuns > 0 ? runs[0]?.finishedAt : null;
  const trend = Array.from({ length: 6 }, (_, i) => {
    if (totalRuns === 0) return 0;
    const runIdx = Math.max(0, totalRuns - 6 + i);
    return (runIdx < totalRuns) ? ((hash(`${service}-${runIdx}`) % 5) + 1) : 0;
  });
  return { writes: totalWrites, reads: totalReads, lastTouched, lastAt, trend };
}

function hash(s) {
  let h = 0;
  for (let i = 0; i < s.length; i += 1) h = (h * 31 + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}

function timeAgo(iso) {
  if (!iso) return "—";
  const s = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

/**
 * Twin sandbox preview — the star of the Overview tab for twin-backed
 * envs. Renders the same service mock (Slack, Notion, Gmail,
 * Salesforce, generic fallback) the standalone Twin detail page shows,
 * plus header chrome (service chips for multi-service twins, endpoint
 * URL, OpenAPI/Open surface buttons). This is what makes a twin-backed
 * env feel like a real running product on the Overview, not a wall of
 * metadata cards.
 */
function TwinSandboxSection({ env, envState }) {
  const backing = envState.twinBacking;
  const services = backing.services || [];
  const [activeServiceId, setActiveServiceId] = useState(services[0]);
  const [openApiOpen, setOpenApiOpen] = useState(false);
  const [surfaceOpen, setSurfaceOpen] = useState(false);
  const [connectOpen, setConnectOpen] = useState(false);
  const activeTwin = twinById(activeServiceId);
  const SandboxMock = SANDBOX_MOCKS[activeServiceId] || null;
  const SUCCESS = "#16A34A";
  const TWIN_TINT = "#7857FC";

  return (
    <Box sx={{ mb: 2 }}>
      {/* service chip tabs — only shown when multi-service */}
      {services.length > 1 && (
        <Stack direction="row" spacing={0.75} sx={{ mb: 1.5 }} flexWrap="wrap" useFlexGap>
          {services.map((sId) => {
            const t = twinById(sId);
            const on = sId === activeServiceId;
            return (
              <Chip
                key={sId} size="small"
                onClick={() => setActiveServiceId(sId)}
                icon={<TwinLogo twin={t} width={11} sx={{ ml: "6px !important" }} />}
                label={t?.name || sId}
                /*
                  Hover was inheriting MUI's default background darken/
                  lighten, which on dark theme flashed a near-white pill
                  and washed out the multicolor logos. Explicit hover
                  bgcolor keeps the pill's contrast against both the
                  panel and the logo underneath. Active state uses the
                  darker end of the tinted-purple range on dark so a
                  Notion "N" or Docs-blue icon still reads.
                */
                sx={{
                  height: 24, borderRadius: 999, cursor: "pointer",
                  border: "1px solid",
                  borderColor: (th) => on
                    ? alpha(TWIN_TINT, th.palette.mode === "dark" ? 0.6 : 0.35)
                    : th.palette.divider,
                  bgcolor: (th) => on
                    ? alpha(TWIN_TINT, th.palette.mode === "dark" ? 0.22 : 0.08)
                    : "background.paper",
                  color: (th) => on
                    ? (th.palette.mode === "dark" ? "#B7A6FC" : TWIN_TINT)
                    : th.palette.text.primary,
                  "& .MuiChip-label": { pl: 0.5, pr: 1, typography: "s2", fontWeight: 700 },
                  "&:hover": {
                    bgcolor: (th) => on
                      ? alpha(TWIN_TINT, th.palette.mode === "dark" ? 0.28 : 0.12)
                      : alpha(th.palette.text.primary, th.palette.mode === "dark" ? 0.06 : 0.04),
                    borderColor: (th) => on
                      ? alpha(TWIN_TINT, th.palette.mode === "dark" ? 0.75 : 0.5)
                      : th.palette.text.disabled,
                  },
                }}
              />
            );
          })}
        </Stack>
      )}

      {/* sub-header — service name + endpoint + OpenAPI / Open surface */}
      <Stack direction="row" alignItems="center" spacing={1.25} sx={{ mb: 1.25 }}>
        <TwinLogo twin={activeTwin} width={22} />
        <Box flex={1} minWidth={0}>
          <Stack direction="row" alignItems="baseline" spacing={0.75}>
            <Typography sx={{ typography: "s1_2", fontWeight: 700 }}>
              {activeTwin?.name || "Sandbox"}
            </Typography>
            <Stack direction="row" alignItems="center" spacing={0.5}>
              <Box sx={{ width: 6, height: 6, borderRadius: "50%", bgcolor: SUCCESS }} />
              <Typography sx={{ typography: "s3", fontWeight: 700, color: SUCCESS }}>
                Serving
              </Typography>
            </Stack>
          </Stack>
          <Stack direction="row" alignItems="center" spacing={0.75}>
            <Typography sx={{
              typography: "s3", color: "text.subtitle",
              fontFamily: "ui-monospace, Menlo, monospace",
            }} noWrap>
              {backing.endpoints?.[activeServiceId] || "—"}
            </Typography>
            {/*
              API surface badge — reads at a glance whether this twin
              also exposes a browsable UI or is API-only. Drives the
              conditional Open-surface render below so we never offer
              a UI on a twin that has none.
            */}
            <ApiSurfaceBadge apiLevel={activeTwin?.apiLevel} />
          </Stack>
        </Box>
        {/*
          Agent connection state: the composer captures the agent
          before provisioning, so this sub-header only shows the
          connection state — it doesn't re-offer connecting. Clicking
          the pill opens the connect dialog for reconnection.
          The bare "Connect agent" button only appears for envs that
          arrived here without a connected agent (legacy adopt path).
        */}
        {envState?.agent?.values?.sdkEndpoint ? (
          <Tooltip arrow title={`Connected: ${envState.agent.values.sdkEndpoint}`}>
            <Stack
              direction="row" alignItems="center" spacing={0.75}
              onClick={() => setConnectOpen(true)}
              sx={{
                px: 1, height: 28, borderRadius: 999, cursor: "pointer",
                border: (t) => `1px solid ${alpha("#16A34A", t.palette.mode === "dark" ? 0.35 : 0.28)}`,
                bgcolor: (t) => alpha("#16A34A", t.palette.mode === "dark" ? 0.14 : 0.08),
                color: "#16A34A",
                "&:hover": {
                  bgcolor: (t) => alpha("#16A34A", t.palette.mode === "dark" ? 0.2 : 0.12),
                },
              }}
            >
              <Iconify icon="solar:check-circle-linear" width={12} />
              <Typography sx={{ typography: "s3", fontWeight: 700, letterSpacing: 0.3 }}>
                AGENT CONNECTED
              </Typography>
            </Stack>
          </Tooltip>
        ) : (
          <Button variant="contained" size="small"
            onClick={() => setConnectOpen(true)}
            startIcon={<Iconify icon="solar:link-circle-linear" width={13} />}
            sx={{ typography: "s2", fontWeight: 700 }}
          >
            Connect agent
          </Button>
        )}
        <Button variant="outlined" size="small"
          onClick={() => setOpenApiOpen(true)}
          startIcon={<Iconify icon="solar:code-square-linear" width={13} />}
          sx={{ typography: "s2", fontWeight: 700, color: "text.primary", borderColor: "divider" }}
        >
          OpenAPI
        </Button>
        {activeTwin?.apiLevel === "api+ui" && (
          <Button variant="outlined" size="small"
            onClick={() => setSurfaceOpen(true)}
            startIcon={<Iconify icon="solar:square-top-down-linear" width={13} />}
            sx={{ typography: "s2", fontWeight: 700, color: "text.primary", borderColor: "divider" }}
          >
            Open surface
          </Button>
        )}
      </Stack>

      <SandboxActivityRibbon envState={envState} serviceId={activeServiceId} />

      {/*
        Render logic:
          · api+ui twin with a bespoke mock → the mock
          · api+ui twin without one → generic browser-chrome mock
          · api-only twin → an "API-only" panel that doesn't lie
            about a browsable surface. Reinforces the badge above.
      */}
      {activeTwin?.apiLevel === "api"
        ? <ApiOnlySandboxPanel twin={activeTwin} endpoint={backing.endpoints?.[activeServiceId] || ""} envState={envState} />
        : SandboxMock
          ? <SandboxMock workspace={env.name} envState={envState} />
          : <GenericSandboxMock twin={activeTwin} />}

      <OpenApiDialog
        open={openApiOpen}
        onClose={() => setOpenApiOpen(false)}
        twin={activeTwin}
        endpoint={backing.endpoints?.[activeServiceId] || ""}
      />
      <OpenSurfaceDialog
        open={surfaceOpen}
        onClose={() => setSurfaceOpen(false)}
        twin={activeTwin}
        env={env}
        SandboxMock={SandboxMock}
      />
      <TwinConnectDialog
        open={connectOpen}
        onClose={() => setConnectOpen(false)}
        twin={activeTwin}
        endpoint={backing.endpoints?.[activeServiceId] || ""}
        sessionId={`sess_${(env?.id || "").slice(-8) || "01ABC"}`}
      />
    </Box>
  );
}
TwinSandboxSection.propTypes = { env: PropTypes.object, envState: PropTypes.object };

function TwinBackingPanel({ backing, envState }) {
  const services = backing.services || [];
  const totalRuns = envState?.runs?.length || 0;
  const anyActivity = totalRuns > 0;

  return (
    <Box sx={{ mb: 2 }}>
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }}>
        <Iconify icon="solar:server-square-linear" width={14} sx={{ color: "primary.main" }} />
        <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.primary", letterSpacing: 0.5 }}>
          TWIN-BACKED WORLD
        </Typography>
        <Chip
          size="small" label={anyActivity ? "Live" : "Ready"}
          sx={{
            height: 18, borderRadius: 0.75,
            bgcolor: (t) => alpha("#16A34A", t.palette.mode === "dark" ? 0.18 : 0.1),
            color: "#16A34A", border: "1px solid", borderColor: alpha("#16A34A", 0.35),
            "& .MuiChip-label": { px: 0.75, typography: "s3", fontWeight: 700, letterSpacing: 0.4 },
          }}
        />
        <Box flex={1} />
        {anyActivity && (
          <Typography sx={{ typography: "s3", color: "text.subtitle", fontVariantNumeric: "tabular-nums" }}>
            {totalRuns} run{totalRuns === 1 ? "" : "s"} against this sandbox
          </Typography>
        )}
      </Stack>
      {backing.seedPrompt && (
        <Typography sx={{ typography: "s3", color: "text.subtitle", mb: 1 }}>
          Seeded from prompt: <Box component="span" sx={{ color: "text.primary", fontStyle: "italic" }}>
            &ldquo;{backing.seedPrompt}&rdquo;
          </Box>
        </Typography>
      )}
      <Stack spacing={1}>
        {services.map((sId, index) => {
          const t = twinById(sId);
          if (!t) return null;
          const activity = activityForService(envState, sId, index, services.length);
          return (
            <Box key={sId} sx={{
              p: 1.5, borderRadius: 1.25, border: "1px solid",
              borderColor: activity.lastTouched
                ? alpha("#16A34A", 0.5)
                : "divider",
              bgcolor: "background.paper",
              position: "relative",
              transition: "border-color .3s ease",
            }}>
              <Stack direction="row" alignItems="center" spacing={1.5}>
                <Box sx={{
                  position: "relative", flexShrink: 0,
                  width: 30, height: 30, display: "grid", placeItems: "center",
                }}>
                  <TwinLogo twin={t} width={22} />
                  {activity.lastTouched && (
                    /*
                      Live-pulse dot on the service that took the most
                      recent write. Purely presentational — the ring
                      pulses via keyframes rather than a JS interval so
                      it costs nothing and stays smooth even while the
                      user is interacting with the surface.
                    */
                    <Box sx={{
                      position: "absolute", top: -2, right: -2,
                      width: 8, height: 8, borderRadius: "50%",
                      bgcolor: "#16A34A",
                      boxShadow: "0 0 0 2px background.paper",
                      "&::after": {
                        content: '""', position: "absolute", inset: -3,
                        borderRadius: "50%",
                        border: "2px solid #16A34A", opacity: 0.4,
                        animation: "twin-pulse 1.8s ease-out infinite",
                      },
                      "@keyframes twin-pulse": {
                        "0%": { transform: "scale(0.85)", opacity: 0.5 },
                        "80%": { transform: "scale(1.9)", opacity: 0 },
                        "100%": { transform: "scale(1.9)", opacity: 0 },
                      },
                    }} />
                  )}
                </Box>
                <Box flex={1} minWidth={0}>
                  <Stack direction="row" alignItems="baseline" spacing={0.75}>
                    <Typography noWrap sx={{ typography: "s2", fontWeight: 700 }}>{t.name}</Typography>
                    {activity.lastTouched && (
                      <Typography sx={{
                        typography: "s3", fontWeight: 700, color: "#16A34A",
                        textTransform: "uppercase", letterSpacing: 0.4,
                      }}>
                        just now
                      </Typography>
                    )}
                  </Stack>
                  <Typography noWrap sx={{
                    typography: "s3", color: "text.subtitle",
                    fontFamily: "ui-monospace, Menlo, monospace",
                  }}>
                    {backing.endpoints?.[sId] || "—"}
                  </Typography>
                </Box>
                {anyActivity && (
                  <>
                    <ActivityMetric label="writes" value={activity.writes} tint="#7857FC" />
                    <ActivityMetric label="reads" value={activity.reads} />
                    <TrendSparkline points={activity.trend} tint={t.color} />
                    <Typography sx={{
                      typography: "s3", color: "text.subtitle",
                      minWidth: 56, textAlign: "right", flexShrink: 0,
                      fontVariantNumeric: "tabular-nums",
                    }}>
                      {timeAgo(activity.lastAt)}
                    </Typography>
                  </>
                )}
                <Button size="small" variant="outlined"
                  startIcon={<Iconify icon="solar:code-square-linear" width={12} />}
                  sx={{ typography: "s3", fontWeight: 700, color: "text.primary", borderColor: "divider", height: 26, px: 1 }}
                >
                  OpenAPI
                </Button>
                <Button size="small" variant="outlined"
                  startIcon={<Iconify icon="solar:square-top-down-linear" width={12} />}
                  sx={{ typography: "s3", fontWeight: 700, color: "text.primary", borderColor: "divider", height: 26, px: 1 }}
                >
                  Open surface
                </Button>
              </Stack>
            </Box>
          );
        })}
      </Stack>
      {!anyActivity && (
        <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 1, fontStyle: "italic" }}>
          Activity counters and per-service sparklines light up as soon as your first run lands.
        </Typography>
      )}
    </Box>
  );
}
TwinBackingPanel.propTypes = { backing: PropTypes.object, envState: PropTypes.object };

function ActivityMetric({ label, value, tint }) {
  return (
    <Box sx={{ minWidth: 44, flexShrink: 0, textAlign: "right" }}>
      <Typography sx={{
        typography: "s2", fontWeight: 700,
        color: tint || "text.primary",
        fontVariantNumeric: "tabular-nums", lineHeight: 1.1,
      }}>
        {value}
      </Typography>
      <Typography sx={{
        typography: "s3", color: "text.subtitle",
        textTransform: "uppercase", letterSpacing: 0.3, lineHeight: 1.1,
      }}>
        {label}
      </Typography>
    </Box>
  );
}
ActivityMetric.propTypes = { label: PropTypes.string, value: PropTypes.number, tint: PropTypes.string };

/**
 * Six-point sparkline of writes per run over the last six runs. SVG
 * kept inline — a fixed 60×22 box, path built from the values on
 * every render (cheap). Preferred over Recharts here because we need
 * this to render dozens of times on one page without layout thrash.
 */
function TrendSparkline({ points, tint }) {
  const w = 60; const h = 22;
  const max = Math.max(1, ...points);
  const step = w / Math.max(1, points.length - 1);
  const d = points
    .map((v, i) => `${i === 0 ? "M" : "L"}${(i * step).toFixed(1)},${(h - (v / max) * (h - 4) - 2).toFixed(1)}`)
    .join(" ");
  return (
    <Box sx={{ flexShrink: 0, width: w, height: h, display: { xs: "none", md: "block" } }}>
      <svg width={w} height={h}>
        <path d={d} fill="none" stroke={tint || "#7857FC"} strokeWidth={1.4} strokeLinecap="round" strokeLinejoin="round" />
        {points.map((v, i) => (
          <circle
            key={i} cx={i * step} cy={h - (v / max) * (h - 4) - 2}
            r={i === points.length - 1 ? 2 : 1.2}
            fill={tint || "#7857FC"}
          />
        ))}
      </svg>
    </Box>
  );
}
TrendSparkline.propTypes = { points: PropTypes.array, tint: PropTypes.string };

/*
  Detected-twins banner for agent-derived envs. Reads what services
  the agent talks to (from imports and tool schemas in the real
  product; scripted per-surface in the prototype) and nudges the
  user toward attaching a twin backing.

  The wedge: NOBODY else auto-detects. Arga Labs offers a menu of
  30 SaaS logos. We know the agent talks to Slack, so we say so
  and offer a one-click attach.
*/
function TwinSuggestionBanner({ env, envState, patch }) {
  const detected = useMemo(() => detectedTwinsFor(env).map(twinById).filter(Boolean), [env]);
  if (detected.length === 0) return null;
  const attachAll = () => {
    const services = detected.map((t) => t.id);
    patch({
      twinBacking: {
        services,
        seedPrompt: "",
        seed: JSON.stringify(
          Object.fromEntries(services.map((s) => {
            try { return [s, JSON.parse(twinById(s)?.seedShape || "{}")]; }
            catch { return [s, {}]; }
          })),
          null, 2,
        ),
        endpoints: Object.fromEntries(services.map((s) => [
          s, `https://${s}.sandbox.futureagi.com/e/${env.id.slice(-6)}`,
        ])),
        activity: Object.fromEntries(services.map((s) => [s, { requests: 0, failures: 0 }])),
        provisionedAt: new Date().toISOString(),
        status: "ready",
      },
    });
  };
  return (
    <Box sx={{
      mb: 2, p: 1.75, borderRadius: 1.5,
      border: "1px solid",
      borderColor: (t) => alpha("#7857FC", t.palette.mode === "dark" ? 0.4 : 0.35),
      bgcolor: (t) => alpha("#7857FC", t.palette.mode === "dark" ? 0.08 : 0.04),
    }}>
      <Stack direction={{ xs: "column", sm: "row" }} alignItems={{ sm: "center" }} spacing={1.5}>
        <Iconify icon="solar:magnet-linear" width={16} sx={{ color: "#7857FC", flexShrink: 0 }} />
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s2", fontWeight: 700, color: "text.primary" }}>
            We detected {detected.length} service{detected.length === 1 ? "" : "s"} your agent talks to.
          </Typography>
          <Stack direction="row" alignItems="center" spacing={0.75} flexWrap="wrap" rowGap={0.5} sx={{ mt: 0.75 }}>
            {detected.map((t) => (
              <Stack key={t.id} direction="row" alignItems="center" spacing={0.5} sx={{
                px: 0.75, py: 0.25, borderRadius: 0.75,
                bgcolor: (th) => alpha(t.color, th.palette.mode === "dark" ? 0.14 : 0.08),
                color: t.color,
                border: "1px solid", borderColor: alpha(t.color, 0.35),
              }}>
                <Iconify icon={t.icon} width={11} />
                <Typography sx={{ typography: "s3", fontWeight: 700 }}>{t.name}</Typography>
              </Stack>
            ))}
          </Stack>
          <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.75 }}>
            Attach twins so runs test what actually landed in {detected.length === 1 ? detected[0].name : "these services"} — not just the decision to call.
          </Typography>
        </Box>
        <Button
          variant="contained" color="primary" size="small"
          onClick={attachAll}
          startIcon={<Iconify icon="solar:link-circle-linear" width={14} />}
          sx={{ typography: "s2", fontWeight: 700, flexShrink: 0 }}
        >
          Attach {detected.length} twin{detected.length === 1 ? "" : "s"}
        </Button>
      </Stack>
    </Box>
  );
}
TwinSuggestionBanner.propTypes = {
  env: PropTypes.object, envState: PropTypes.object, patch: PropTypes.func,
};

/*
  Overview sections for twin-backed envs — replace the source-derived
  Capabilities and "The world" groups. Focused on what actually exists
  in a twin env: the twinned services, the starting state each run
  begins from, what evals can inspect on the end state, and the agent
  wired to the sandbox.
*/
function TwinBackedSections({ env, envState, onGo }) {
  const backing = envState.twinBacking;
  const services = backing?.services || [];
  const seedShape = backing?.seed || {};
  const agent = envState?.agent;

  /*
    Human-readable summary of what the seed prompt actually produced,
    per service. Falls back to "Empty starting state" if no seed
    entities were resolved for that twin.
  */
  const seedSummary = (sId) => {
    const shape = seedShape[sId] || {};
    const parts = [];
    Object.entries(shape).forEach(([key, val]) => {
      if (Array.isArray(val) && val.length > 0) {
        parts.push(`${val.length} ${key}`);
      }
    });
    return parts.length ? parts.join(" · ") : "Empty starting state";
  };

  const evalPresets = env?.evalPreset || [];
  const evalLabels = {
    task_success: { title: "Task success", body: "Did the run reach the expected outcome?" },
    twin_end_state_match: { title: "End-state match", body: "Did the correct rows / messages / records actually land in the cloned services?" },
    twin_no_extra_writes: { title: "No extra writes", body: "Did the agent write only what the task required — no spurious side effects?" },
  };

  return (
    <>
      <GroupHeading>Clone surface</GroupHeading>
      <Grid container spacing={2} sx={{ mb: 3 }}>
        {/* ── services list ── */}
        <Grid item xs={12} md={7}>
          <SectionCard
            title="Clone services"
            subtitle={`${services.length} sandbox${services.length === 1 ? "" : "es"} provisioned for this environment`}
          >
            <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
              {services.map((sId) => {
                const twin = twinById(sId);
                return (
                  <Stack key={sId} direction="row" alignItems="center" spacing={1.5}
                    sx={{ px: 2.5, py: 1.25 }}
                  >
                    <TwinLogo twin={twin} width={18} />
                    <Box flex={1} minWidth={0}>
                      <Typography sx={{ typography: "s2", fontWeight: 700 }}>
                        {twin?.name || sId}
                      </Typography>
                      <Typography noWrap sx={{
                        typography: "s3", color: "text.subtitle",
                        fontFamily: "ui-monospace, Menlo, monospace",
                      }}>
                        {backing?.endpoints?.[sId] || "—"}
                      </Typography>
                    </Box>
                    <ApiSurfaceBadge apiLevel={twin?.apiLevel} />
                  </Stack>
                );
              })}
            </Stack>
          </SectionCard>
        </Grid>

        {/* ── connected agent ── */}
        <Grid item xs={12} md={5}>
          <SectionCard
            title="Connected agent"
            subtitle="Where the sandbox calls your agent"
          >
            <Stack sx={{ p: 2.5 }} spacing={1.25}>
              <Stack direction="row" alignItems="center" spacing={1}>
                <Iconify icon="solar:link-circle-linear" width={13} sx={{ color: "primary.main" }} />
                <Typography sx={{
                  typography: "s2", fontWeight: 600,
                  fontFamily: "ui-monospace, Menlo, monospace",
                  flex: 1, minWidth: 0,
                }} noWrap>
                  {agent?.values?.sdkEndpoint || "Not connected"}
                </Typography>
              </Stack>
              <Stack direction="row" alignItems="center" spacing={1}>
                <Iconify icon="solar:key-linear" width={13} sx={{ color: "text.subtitle" }} />
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                  {agent?.values?.authToken ? "Auth token set" : "No auth header — endpoint is open"}
                </Typography>
              </Stack>
              {agent?.connectedAt && (
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                  Connected {timeAgo(agent.connectedAt)}
                </Typography>
              )}
              <Box>
                <Button size="small" onClick={() => onGo?.("agent")}
                  startIcon={<Iconify icon="solar:settings-linear" width={13} />}
                  sx={{ typography: "s3", fontWeight: 700, px: 0.5 }}
                >
                  Change agent
                </Button>
              </Box>
            </Stack>
          </SectionCard>
        </Grid>
      </Grid>

      <GroupHeading>Starting state</GroupHeading>
      <Grid container spacing={2} sx={{ mb: 3 }}>
        {/* ── seed prompt + resolved shape ── */}
        <Grid item xs={12} md={7}>
          <SectionCard
            title="Seed prompt"
            subtitle="Natural-language description of what the sandbox starts with each run"
          >
            <Box sx={{ p: 2.5 }}>
              {backing?.seedPrompt ? (
                <Typography sx={{
                  typography: "s2", color: "text.secondary", fontStyle: "italic",
                  p: 1.5, borderRadius: 1, borderLeft: "3px solid",
                  borderColor: "primary.main", bgcolor: "background.neutral",
                }}>
                  &ldquo;{backing.seedPrompt}&rdquo;
                </Typography>
              ) : (
                <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
                  No seed prompt. Each service starts empty; scenarios can seed per-run.
                </Typography>
              )}
            </Box>
          </SectionCard>
        </Grid>

        {/* ── resolved shape per service ── */}
        <Grid item xs={12} md={5}>
          <SectionCard
            title="Resolves to"
            subtitle="Concrete state per service after seeding"
          >
            <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
              {services.map((sId) => {
                const twin = twinById(sId);
                return (
                  <Stack key={sId} direction="row" alignItems="center" spacing={1.5}
                    sx={{ px: 2.5, py: 1.25 }}
                  >
                    <TwinLogo twin={twin} width={14} />
                    <Typography sx={{ typography: "s3", fontWeight: 600, minWidth: 80 }}>
                      {twin?.name || sId}
                    </Typography>
                    <Typography sx={{
                      typography: "s3", color: "text.subtitle",
                      fontFamily: "ui-monospace, Menlo, monospace", flex: 1, minWidth: 0,
                    }} noWrap>
                      {seedSummary(sId)}
                    </Typography>
                  </Stack>
                );
              })}
            </Stack>
          </SectionCard>
        </Grid>
      </Grid>

      <GroupHeading>What evals can check</GroupHeading>
      <Grid container spacing={2} sx={{ mb: 3 }}>
        <Grid item xs={12}>
          <SectionCard
            title="End-state graders"
            subtitle="Clone evals inspect what actually landed after each run — not just whether a tool was called"
            action={
              <Button size="small" onClick={() => onGo?.("evals")}
                sx={{ typography: "s3", fontWeight: 700 }}
              >
                Configure
              </Button>
            }
          >
            <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
              {evalPresets.map((k) => {
                const meta = evalLabels[k];
                if (!meta) return null;
                return (
                  <Stack key={k} direction="row" spacing={1.5} alignItems="flex-start"
                    sx={{ px: 2.5, py: 1.5 }}
                  >
                    <Iconify icon="solar:target-linear" width={14}
                      sx={{ color: "primary.main", mt: "3px", flexShrink: 0 }} />
                    <Box flex={1}>
                      <Typography sx={{ typography: "s2", fontWeight: 700 }}>
                        {meta.title}
                      </Typography>
                      <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                        {meta.body}
                      </Typography>
                    </Box>
                    <Typography sx={{
                      typography: "s3", fontWeight: 700, color: "text.subtitle",
                      textTransform: "uppercase", letterSpacing: 0.3, flexShrink: 0,
                      fontFamily: "ui-monospace, Menlo, monospace",
                    }}>
                      {k}
                    </Typography>
                  </Stack>
                );
              })}
            </Stack>
          </SectionCard>
        </Grid>
      </Grid>
    </>
  );
}
TwinBackedSections.propTypes = {
  env: PropTypes.object, envState: PropTypes.object, onGo: PropTypes.func,
};

/*
  Small badge that reads "API" or "API + UI". Purple when the twin
  also exposes a UI (this is the higher-value tier — the sandbox
  can be inspected visually mid-run); neutral when API-only (still
  useful, just no browsable surface). Sits next to the endpoint URL
  so it's read in the same glance.
*/
function ApiSurfaceBadge({ apiLevel }) {
  const isUi = apiLevel === "api+ui";
  const label = isUi ? "API + UI" : "API only";
  const tint = isUi ? "#7857FC" : "#6B7280";
  return (
    <Tooltip arrow title={
      isUi
        ? "This twin exposes both an API for the agent to call and a browsable UI you can open to inspect state."
        : "This twin exposes an API only. Inspect state via the OpenAPI viewer or the Raw requests tab in a run."
    }>
      <Typography sx={{
        typography: "s3", fontWeight: 700, letterSpacing: 0.3,
        px: 0.625, py: 0.125, borderRadius: 0.5,
        color: tint,
        bgcolor: (t) => alpha(tint, t.palette.mode === "dark" ? 0.16 : 0.08),
        border: (t) => `1px solid ${alpha(tint, t.palette.mode === "dark" ? 0.35 : 0.25)}`,
        flexShrink: 0,
      }}>
        {label}
      </Typography>
    </Tooltip>
  );
}
ApiSurfaceBadge.propTypes = { apiLevel: PropTypes.string };

/*
  Replacement for the browser-chrome sandbox mock when the twin is
  API-only (Jira, HubSpot, Linear, Stripe, QuickBooks). We do not
  fake a UI these services expose only via API — that would misread
  as "our sandbox has this UI" and drift from Arga's own model.
  Instead: a data-panel showing the base URL, the modelled surfaces,
  and pointers to the two ways to inspect state (OpenAPI + Raw
  requests during a run).
*/
function ApiOnlySandboxPanel({ twin, endpoint, envState }) {
  const requests = envState?.twinBacking?.activity?.[twin?.id]?.requests || 0;
  return (
    <Box sx={{
      borderRadius: 1.5, overflow: "hidden",
      border: "1px solid", borderColor: "divider",
      bgcolor: "background.paper",
      height: 800, display: "flex", flexDirection: "column",
    }}>
      {/* header */}
      <Stack direction="row" alignItems="center" spacing={1.5} sx={{
        px: 2, py: 1.25, borderBottom: "1px solid", borderColor: "divider",
        bgcolor: "background.neutral",
      }}>
        <TwinLogo twin={twin} width={22} />
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s2", fontWeight: 700 }}>
            {twin?.name} twin — API only
          </Typography>
          <Typography sx={{
            typography: "s3", color: "text.subtitle",
            fontFamily: "ui-monospace, Menlo, monospace",
          }} noWrap>
            {endpoint}
          </Typography>
        </Box>
        <Stack direction="row" alignItems="center" spacing={0.5}>
          <Box sx={{ width: 6, height: 6, borderRadius: "50%", bgcolor: "#16A34A" }} />
          <Typography sx={{ typography: "s3", fontWeight: 700, color: "#16A34A" }}>Serving</Typography>
        </Stack>
      </Stack>
      {/* body */}
      <Stack sx={{ flex: 1, p: 3, gap: 2.5, overflow: "auto" }}>
        <Box>
          <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle", textTransform: "uppercase", letterSpacing: 0.4, mb: 1 }}>
            Modelled surfaces
          </Typography>
          <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
            {(twin?.depth || []).map((d) => (
              <Typography key={d} sx={{
                px: 1, py: 0.375, borderRadius: 0.75,
                typography: "s2", fontWeight: 600,
                bgcolor: "background.neutral",
                border: "1px solid", borderColor: "divider",
              }}>{d}</Typography>
            ))}
          </Stack>
        </Box>
        <Box>
          <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle", textTransform: "uppercase", letterSpacing: 0.4, mb: 1 }}>
            How to inspect state
          </Typography>
          <Stack spacing={0.75}>
            <InspectRow icon="solar:code-square-linear" title="OpenAPI viewer" body="Above · every endpoint the clone serves, with request/response shapes." />
            <InspectRow icon="solar:list-linear" title="Raw requests tab" body="On any run drawer · chronological HTTP log with request + response payloads." />
            <InspectRow icon="solar:pulse-2-linear" title="Clone state timeline" body="On any run drawer · semantic view of what the agent wrote to this clone." />
          </Stack>
        </Box>
        <Box>
          <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle", textTransform: "uppercase", letterSpacing: 0.4, mb: 1 }}>
            Activity this session
          </Typography>
          <Typography sx={{ typography: "s1", fontWeight: 700 }}>
            {requests} request{requests === 1 ? "" : "s"}
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            Resets when you reset the sandbox state.
          </Typography>
        </Box>
      </Stack>
    </Box>
  );
}
ApiOnlySandboxPanel.propTypes = {
  twin: PropTypes.object, endpoint: PropTypes.string, envState: PropTypes.object,
};

function InspectRow({ icon, title, body }) {
  return (
    <Stack direction="row" spacing={1.25} alignItems="flex-start" sx={{ py: 0.5 }}>
      <Iconify icon={icon} width={13} sx={{ color: "primary.main", mt: "3px", flexShrink: 0 }} />
      <Box>
        <Typography sx={{ typography: "s2", fontWeight: 700 }}>{title}</Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{body}</Typography>
      </Box>
    </Stack>
  );
}
InspectRow.propTypes = { icon: PropTypes.string, title: PropTypes.string, body: PropTypes.string };

/*
  Recent-activity ribbon that sits between the sub-header and the
  sandbox mock. Reads the run history and reports what the agent
  actually wrote to this twin during the session — "3 writes this
  session · last: Agent posted to #support-urgent". Gives the
  sandbox a live-state feel without every mock needing bespoke
  render code for what the agent did.
*/
function SandboxActivityRibbon({ envState, serviceId }) {
  const items = liveSandboxContentFor(envState, serviceId);
  if (!items.length) return null;
  const last = items[0];
  const location = last.channel || last.page || last.thread || last.record || "sandbox";
  return (
    <Stack direction="row" alignItems="center" spacing={1} sx={{
      mb: 1.25, px: 1.25, py: 0.75, borderRadius: 0.875,
      border: (t) => `1px solid ${alpha("#7857FC", t.palette.mode === "dark" ? 0.4 : 0.28)}`,
      bgcolor: (t) => alpha("#7857FC", t.palette.mode === "dark" ? 0.08 : 0.04),
    }}>
      <Iconify icon="solar:pulse-2-linear" width={13} sx={{ color: "#7857FC", flexShrink: 0 }} />
      <Typography sx={{ typography: "s3", fontWeight: 700, color: "#7857FC", letterSpacing: 0.3, flexShrink: 0 }}>
        {items.length} WRITE{items.length === 1 ? "" : "S"} THIS SESSION
      </Typography>
      <Typography noWrap sx={{ typography: "s3", color: "text.subtitle", flex: 1, minWidth: 0 }}>
        · Latest: {last.author} — {location}
      </Typography>
      <Typography sx={{ typography: "s3", color: "text.subtitle", flexShrink: 0 }}>
        {last.runLabel}
      </Typography>
    </Stack>
  );
}
SandboxActivityRibbon.propTypes = { envState: PropTypes.object, serviceId: PropTypes.string };
