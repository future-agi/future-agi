import PropTypes from "prop-types";
import { useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Button, Grid, IconButton, Tooltip } from "@mui/material";
import Iconify from "src/components/iconify";
import { getSurface, getDomain } from "../_mock/surfaces";
import { DIFFICULTY_COLOR } from "../_mock/environments";
import { packStats } from "../_mock/scenarios";
import { SectionCard, EmptyState } from "../components/primitives";
import { useAppliedEvals, EvalRow } from "./evals/appliedEvals";
import AddEvalsDrawer from "./evals/AddEvalsDrawer";

/**
 * What this environment actually *is*.
 *
 * The three panels map to the three halves of the composite: the world's data,
 * what the agent can do inside it, and the rules the graders will hold it to.
 * A user should be able to read this and know what they're about to test.
 */
/**
 * "1,125 rows, restored fresh for every run" is accurate and tells a first-time
 * user nothing. This says what the rows *are* and why they get wiped — kept to
 * two lines at the card's width.
 */
const seedBlurb = (rows) =>
  `${rows.toLocaleString()} rows that fill this environment before your agent arrives — the world it actually works in. Rebuilt for every task, so nothing carries over.`;

export default function OverviewPanel({ env, envState, patch, onGo, agentConnected }) {
  const [pickerOpen, setPickerOpen] = useState(false);
  const { appliedEvals, appliedIds, add, remove } = useAppliedEvals(envState, patch);
  // Evals map onto what a run produces, so they need scenarios to exist first.
  const needsScenarios = envState.scenarios.length === 0;
  const surface = getSurface(env.surface);
  const domain = getDomain(env.domain);
  const totalRows = env.seed?.tables?.reduce((a, t) => a + t.rows, 0) || 0;
  const stats = packStats(env);

  return (
    <Box sx={{ p: 2 }}>
      {/* Next action banner — only while there is still a next move to make. */}
      {!agentConnected && (
      <Box
        sx={{
          p: 2.5, mb: 3, borderRadius: 1.5,
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
              {surface.blurb} We handle the {surface.transports.join(", ")} side —
              you point us at your agent.
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

      <Typography sx={{ typography: "s1", color: "text.secondary", mb: 3, maxWidth: 760 }}>
        {env.description}
      </Typography>

      <Grid container spacing={2}>
        {/* ── seeded world ── */}
        <Grid item xs={12} md={6}>
          <SectionCard
            title="Seeded data"
            subtitle={seedBlurb(totalRows)}
          >
            <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
              {env.seed.tables.map((t) => (
                <Stack key={t.name} direction="row" alignItems="center" spacing={2} sx={{ px: 2.5, py: 1.375 }}>
                  <Iconify icon="solar:database-linear" width={16} sx={{ color: "text.subtitle", flexShrink: 0 }} />
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

        {/*
          ── tools ──
          The tool definitions live on the agent, not on us: until it connects
          and declares them we genuinely do not know what it can call, so this
          panel shows the reason it is empty rather than a fabricated list.
        */}
        <Grid item xs={12} md={6}>
          <SectionCard
            title="Tools"
            subtitle={
              agentConnected
                ? `${env.tools.length} actions your agent declared`
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
            <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
              {env.tools.map((t) => (
                <Stack key={t.name} direction="row" alignItems="center" spacing={2} sx={{ px: 2.5, py: 1.375 }}>
                  <Box
                    sx={{
                      width: 26, height: 26, borderRadius: 0.75, display: "grid", placeItems: "center", flexShrink: 0,
                      bgcolor: (th) => alpha("#EA580C", th.palette.mode === "dark" ? 0.16 : 0.1),
                      color: "#EA580C",
                    }}
                  >
                    <Iconify icon="solar:settings-minimalistic-linear" width={14} />
                  </Box>
                  <Box flex={1} minWidth={0}>
                    <Typography sx={{ typography: "s2", fontWeight: 600, fontFamily: "ui-monospace, Menlo, monospace" }}>
                      {t.name}
                    </Typography>
                    <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>{t.desc}</Typography>
                  </Box>
                </Stack>
              ))}
            </Stack>
            )}
          </SectionCard>
        </Grid>

        {/*
          ── evals ──
          The counterpart to Tools: what the agent can do, and what decides
          whether it did it well. Empty until something is applied, because an
          environment scores nothing on its own.
        */}
        <Grid item xs={12}>
          <SectionCard
            title="Evals"
            subtitle={
              appliedEvals.length
                ? `${appliedEvals.length} applied — every task is scored against these`
                : needsScenarios
                  ? "Add scenarios first — evals score the tasks a run produces"
                  : "Nothing is scoring this environment yet"
            }
            action={
              appliedEvals.length > 0 && !needsScenarios && (
                <Button
                  size="small"
                  onClick={() => setPickerOpen(true)}
                  startIcon={<Iconify icon="solar:add-circle-linear" width={15} />}
                  sx={{ typography: "s2", fontWeight: 700, color: "primary.main" }}
                >
                  Add evals
                </Button>
              )
            }
          >
            {appliedEvals.length === 0 ? (
              <EmptyState
                icon={needsScenarios ? "solar:lock-keyhole-minimalistic-linear" : "solar:shield-check-linear"}
                title={needsScenarios ? "Add scenarios first" : "No evals yet"}
                body={
                  needsScenarios
                    ? "An eval scores the tasks a run produces, so it needs scenarios to point at. Add some and this unlocks."
                    : "Evals decide whether each task passed. Without them you'll get traces, but nothing telling you if the agent was right."
                }
                action={
                  <Button
                    variant="contained"
                    color="primary"
                    size="small"
                    onClick={() => (needsScenarios ? onGo("scenarios") : setPickerOpen(true))}
                    endIcon={needsScenarios ? <Iconify icon="solar:arrow-right-linear" width={15} /> : null}
                    sx={{ typography: "s2", fontWeight: 700 }}
                  >
                    {needsScenarios ? "Add scenarios" : "Add evals"}
                  </Button>
                }
              />
            ) : (
              <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
                {appliedEvals.map((e) => (
                  <EvalRow
                    key={e.id}
                    item={e}
                    action={
                      e.required ? (
                        <Tooltip title="Always on for every run" arrow>
                          <Typography sx={{ typography: "s3", color: "text.subtitle", px: 1 }}>
                            Always on
                          </Typography>
                        </Tooltip>
                      ) : (
                        <Tooltip title="Remove" arrow>
                          <IconButton size="small" onClick={() => remove(e.id)}>
                            <Iconify icon="solar:close-circle-linear" width={16} sx={{ color: "text.subtitle" }} />
                          </IconButton>
                        </Tooltip>
                      )
                    }
                  />
                ))}
              </Stack>
            )}
          </SectionCard>
        </Grid>

        {/* ── rules ── */}
        <Grid item xs={12} md={7}>
          <SectionCard
            title="Business rules"
            subtitle="Graders check the agent against every one of these"
          >
            <Stack sx={{ p: 2.5 }} spacing={1.25}>
              {env.rules.map((r) => (
                <Stack key={r} direction="row" spacing={1.25} alignItems="flex-start">
                  <Iconify
                    icon="solar:shield-check-linear"
                    width={16}
                    sx={{ color: "primary.main", flexShrink: 0, mt: "1px" }}
                  />
                  <Typography sx={{ typography: "s2", color: "text.secondary" }}>{r}</Typography>
                </Stack>
              ))}
            </Stack>
          </SectionCard>
        </Grid>

        {/* ── facts ── */}
        <Grid item xs={12} md={5}>
          <SectionCard title="Environment">
            <Stack sx={{ p: 2.5 }} spacing={1.75}>
              <Fact label="Channel" value={surface.label} icon={surface.icon} color={surface.color} />
              <Fact label="Domain" value={domain?.label} icon={domain?.icon} />
              <Fact
                label="Difficulty"
                value={env.difficulty}
                color={DIFFICULTY_COLOR[env.difficulty]}
                icon="solar:chart-2-linear"
              />
              <Fact
                label="Transports"
                value={surface.transports.join(" · ")}
                icon="solar:transmission-linear"
              />
              <Fact
                label="Scenario packs"
                value={`${stats.packs} packs · ${stats.scenarios} scenarios`}
                icon="solar:layers-minimalistic-linear"
              />
              <Fact
                label="Used by"
                value={`${env.popularity.toLocaleString()} teams`}
                icon="solar:users-group-rounded-linear"
              />
            </Stack>
          </SectionCard>
        </Grid>
      </Grid>

      {/* Same drawer as the Evals step — one flow, reachable from both. */}
      <AddEvalsDrawer
        open={pickerOpen}
        onClose={() => setPickerOpen(false)}
        env={env}
        envState={envState}
        existingIds={appliedIds}
        onAdd={add}
      />
    </Box>
  );
}

OverviewPanel.propTypes = {
  env: PropTypes.object.isRequired,
  envState: PropTypes.object.isRequired,
  patch: PropTypes.func.isRequired,
  onGo: PropTypes.func,
  agentConnected: PropTypes.bool,
};

function Fact({ label, value, icon, color }) {
  return (
    <Stack direction="row" alignItems="center" spacing={1.5}>
      <Iconify icon={icon || "solar:info-circle-linear"} width={16} sx={{ color: color || "text.subtitle", flexShrink: 0 }} />
      <Typography sx={{ typography: "s2", color: "text.subtitle", width: 108, flexShrink: 0 }}>{label}</Typography>
      <Typography sx={{ typography: "s2", fontWeight: 600, color: color || "text.primary" }}>{value}</Typography>
    </Stack>
  );
}
Fact.propTypes = { label: PropTypes.string, value: PropTypes.node, icon: PropTypes.string, color: PropTypes.string };
