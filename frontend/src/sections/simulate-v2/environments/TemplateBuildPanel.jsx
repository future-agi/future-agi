import PropTypes from "prop-types";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Box,
  Stack,
  Typography,
  Button,
  ButtonBase,
  Menu,
  MenuItem,
  TextField,
  Tooltip,
} from "@mui/material";
import { paths } from "src/routes/paths";
import Iconify from "src/components/iconify";
import { getSurface } from "../_mock/surfaces";
import { useConnectedAgents } from "../store";
import { SectionCard } from "../components/primitives";
import WorldAgentPicker from "./AgentChoice";
import WorldFitReport from "./WorldFitReport";
import { worldFitOf } from "../_mock/worldFit";
import {
  buildableAgentsOf,
  templateWorldSource,
  useBuildForAgent,
  scenariosForPack,
  worldChannelOf,
  worldPacksOf,
} from "./worldSource";
import { replicationOf } from "../_mock/replication";

/**
 * A prebuilt world, used with one of your agents (cloud only; no sample
 * agents in the product).
 *
 * The world is the world source of one agent's environment. Every environment
 * belongs to exactly one agent in Agents (RSI PRD), so a prebuilt world is
 * never built on its own or with a stand-in: the user picks one of their
 * agents (or connects one here), then the builder builds that agent's
 * environment starting from this world's tables, tools and rules — reading
 * the agent, checking its tools against the world, proving the
 * scenarios, then running the suite once.
 *
 * It runs in Future AGI's hosted sandbox: the same Build environment dialog
 * the agent page uses (name, version, 10–1,000 scenarios) opens first.
 *
 * Rendered in the world drawer, so it takes a base `template`.
 */
export default function TemplateBuildPanel({
  template,
  showName = false,
  entry = null,
  blocked = null,
  /* "use" (the default) or "customize" — which path the drawer's tab shows. */
  mode = "use",
}) {
  const navigate = useNavigate();
  /* The starter suite by default; a named group or all scenarios on request. */
  const [suiteAnchor, setSuiteAnchor] = useState(null);
  const [pack, setPack] = useState("starter");
  const { agents: all } = useConnectedAgents();
  const agents = useMemo(() => buildableAgentsOf(all), [all]);
  const [agentId, setAgentId] = useState(null);
  const agent = agents.find((a) => a.id === agentId) || null;
  const build = useBuildForAgent();
  /* The tool binding the person reviewed; null until they change one. */
  const [binding, setBinding] = useState(null);
  /* Adapting writes scenarios for the agent: a fork that doesn't compare. */
  /* What to change when customizing without an agent. */
  const [changes, setChanges] = useState("");
  /* Mappings whose differences someone reviewed. */
  const [reviewed, setReviewed] = useState([]);
  useEffect(() => {
    setBinding(null);
    setReviewed([]);
  }, [agentId]);
  const fit = useMemo(
    () =>
      agent
        ? worldFitOf(
            template,
            agent,
            binding,
            scenariosForPack(template, pack),
            reviewed,
          )
        : null,
    [template, agent, binding, pack, reviewed],
  );
  /* Everything the build needs is already chosen here — agent, suite, fit —
     so Run builds straight away; no second dialog asks again. */
  const startBuild = (withAdapt) =>
    build(
      agent,
      {
        name: `${template.name} · ${agent.name}`,
        version: agent.production || agent.latest,
        /* Read at click time: the suite picked below. */
        count: scenarioCount,
      },
      {
        ...templateWorldSource(template),
        /* The fixed suite: this pack, this release, the reviewed binding. */
        pack,
        ...(entry && { release: entry.release.version, catalogueId: entry.id }),
        ...(fit && {
          binding: fit.binding,
          unmeasured: fit.unmeasured.map((x) => x.id),
        }),
        ...(withAdapt && { adapt: true }),
      },
    );

  if (!template) return null;
  const env = template;
  const surface = getSurface(env.surface);
  const rows = (env.seed?.tables || []).reduce((a, t) => a + t.rows, 0);
  const packs = worldPacksOf(template);
  const picked = packs.find((p) => p.id === pack) || packs[0];
  const scenarioCount = picked.count;

  return (
    <Stack spacing={2}>
      {showName && (
        <Box>
          <Stack direction="row" alignItems="center" spacing={0.75}>
            <Iconify
              icon={surface.icon}
              width={14}
              sx={{ color: "text.subtitle" }}
            />
            <Typography
              sx={{
                typography: "s3",
                color: "text.subtitle",
                fontWeight: 600,
                letterSpacing: 0.5,
                textTransform: "none",
              }}
            >
              {env.surface}
            </Typography>
          </Stack>
          <Typography sx={{ typography: "m2", fontWeight: 600, mt: 0.5 }}>
            {env.name}
          </Typography>
          <Typography
            sx={{ typography: "s2", color: "text.secondary", mt: 0.25 }}
          >
            {env.tagline}
          </Typography>
        </Box>
      )}

      {/* Use and Customize are separate tabs of the world's drawer — each
          shows only its own path. */}
      {mode !== "customize" && (
        <>
          <SectionCard title="Use it with your agent">
            <Stack sx={{ p: 2.5 }} spacing={2}>
              <WorldAgentPicker
                value={agentId}
                onChange={setAgentId}
                surface={worldChannelOf(template)}
              />
              {/* Review fit: the suite stays fixed; tools bind to the agent's. */}
              {agent && fit && (
                <WorldFitReport
                  template={template}
                  agent={agent}
                  fit={fit}
                  onBind={(w, a) =>
                    setBinding({ ...(binding || fit.binding), [w]: a })
                  }
                  onReview={(w, on) =>
                    setReviewed((r) =>
                      on ? [...new Set([...r, w])] : r.filter((x) => x !== w),
                    )
                  }
                  onOtherWorld={() =>
                    navigate(
                      `${paths.dashboard.simulate.environments}?view=worlds`,
                    )
                  }
                />
              )}
              {agent && replicationOf(agent).proofLabel === "Live" && (
                <Stack
                  direction="row"
                  spacing={1.25}
                  alignItems="flex-start"
                  sx={{
                    px: 1.5,
                    py: 1.25,
                    borderRadius: 1,
                    border: "1px solid",
                    borderColor: "divider",
                    bgcolor: "background.neutral",
                  }}
                >
                  <Iconify
                    icon="solar:info-circle-linear"
                    width={16}
                    sx={{ color: "text.secondary", flexShrink: 0, mt: "1px" }}
                  />
                  <Typography
                    sx={{ typography: "s2", color: "text.secondary" }}
                  >
                    {`${agent.name} is tested live and calls its own tools; this world only grades it. Test a simulated copy for simulated tools.`}
                  </Typography>
                </Stack>
              )}
              {/* The copy, read before it's made. */}
              <Stack
                spacing={0.75}
                sx={{ pt: 1.5, borderTop: "1px solid", borderColor: "divider" }}
              >
                <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                  Review before you adopt
                </Typography>
                {/* catalogue.md step 3: copied data and scenarios, source
                    version, visibility — in two lines. */}
                <Line
                  label="Copied"
                  value={`${rows.toLocaleString()} seeded rows · ${scenarioCount} scenarios · ${
                    entry ? `release ${entry.release.version}` : env.name
                  }`}
                />
                <Line
                  label="Visibility"
                  value="Private to this workspace — the release stays unchanged"
                />
              </Stack>
            </Stack>
            <Stack
              direction="row"
              alignItems="center"
              spacing={1.5}
              sx={{
                px: 2.5,
                py: 2,
                borderTop: "1px solid",
                borderColor: "divider",
              }}
            >
              {/* Which of the world's scenarios come along (a suite, in the
                  plan's words) — the starter set unless changed. */}
              <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                <Box
                  component="span"
                  sx={{ color: "text.primary", fontWeight: 500 }}
                >
                  {`${packs.find((p) => p.id === pack)?.count ?? 0} scenarios`}
                </Box>
                {` · ${packs.find((p) => p.id === pack)?.name || ""} · `}
                <ButtonBase
                  onClick={(e) => setSuiteAnchor(e.currentTarget)}
                  sx={{
                    typography: "s2",
                    fontWeight: 500,
                    color: "text.primary",
                    textDecoration: "underline",
                    textUnderlineOffset: 3,
                    verticalAlign: "baseline",
                  }}
                >
                  Change
                </ButtonBase>
              </Typography>
              <Menu
                anchorEl={suiteAnchor}
                open={!!suiteAnchor}
                onClose={() => setSuiteAnchor(null)}
              >
                {packs.map((p) => (
                  <MenuItem
                    key={p.id}
                    selected={p.id === pack}
                    onClick={() => {
                      setPack(p.id);
                      setSuiteAnchor(null);
                    }}
                    sx={{ typography: "s2", gap: 2 }}
                  >
                    <Box component="span" sx={{ flex: 1 }}>
                      {p.name}
                    </Box>
                    <Box
                      component="span"
                      sx={{
                        color: "text.tertiary",
                        fontVariantNumeric: "tabular-nums",
                      }}
                    >
                      {p.count}
                    </Box>
                  </MenuItem>
                ))}
              </Menu>
              <Box flex={1} />
              {/* The other way: write scenarios for this agent — a fork that doesn't compare. */}
              <Button
                variant="outlined"
                size="small"
                color="inherit"
                disabled={!agent || !!blocked}
                onClick={() => startBuild(true)}
                sx={{ whiteSpace: "nowrap" }}
              >
                Adapt to my agent
              </Button>
              {/* Needs input waits for the reviewed mappings; Partial for the measurable-only choice. */}
              <Tooltip
                arrow
                title={
                  fit?.verdict === "needs_input"
                    ? "Review the mappings that need input first"
                    : ""
                }
              >
                <span>
                  <Button
                    variant="contained"
                    color="primary"
                    disabled={
                      !agent || !!blocked || fit?.verdict === "needs_input"
                    }
                    onClick={() => startBuild(false)}
                    startIcon={<Iconify icon="solar:play-bold" width={16} />}
                    sx={{ whiteSpace: "nowrap" }}
                  >
                    {fit?.verdict === "partial" && fit.unmeasured.length
                      ? `Run the ${fit.total - fit.unmeasured.length} measurable cases`
                      : "Run the same cases"}
                  </Button>
                </span>
              </Tooltip>
            </Stack>
          </SectionCard>
        </>
      )}
      {mode === "customize" && (
        <>
          {/* Customize a ready environment (plan builder §1, T02): no agent is
          needed to design — a private copy with the changes asked for,
          reviewed before it's built. */}
          <SectionCard
            title="Customize a private copy"
            subtitle={
              entry
                ? "Your own editable copy — its scores won't compare with this release."
                : "Your own editable copy — the original stays as it is."
            }
          >
            <Stack sx={{ p: 2.5 }} spacing={1.25}>
              <TextField
                multiline
                minRows={2}
                size="small"
                placeholder="For example: add a loyalty tier table, and refunds over $500 need a supervisor"
                value={changes}
                onChange={(e) => setChanges(e.target.value)}
                disabled={!!blocked}
              />
              <Stack direction="row" justifyContent="flex-end">
                <Button
                  variant="outlined"
                  size="small"
                  color="inherit"
                  disabled={!!blocked || changes.trim().length < 5}
                  onClick={() =>
                    navigate(paths.dashboard.simulate.environmentBuild, {
                      state: {
                        presetSource: {
                          worldSource: {
                            ...templateWorldSource(template),
                            pack,
                          },
                          ...(entry && {
                            release: entry.release.version,
                            catalogueId: entry.id,
                          }),
                          scenarioCount,
                          envName: `${template.name} · copy`,
                          request: changes.trim(),
                        },
                      },
                    })
                  }
                  startIcon={
                    <Iconify icon="solar:pen-new-square-linear" width={16} />
                  }
                >
                  Build my copy
                </Button>
              </Stack>
            </Stack>
          </SectionCard>
        </>
      )}
      {mode !== "customize" && <></>}
    </Stack>
  );
}
TemplateBuildPanel.propTypes = {
  mode: PropTypes.oneOf(["use", "customize"]),
  template: PropTypes.object,
  showName: PropTypes.bool,
  /* The catalogue task (V01…M10): release and readiness. */
  entry: PropTypes.object,
  /* Why it can't be adopted — Build is off. */
  blocked: PropTypes.string,
};

function Line({ label, value, muted = false }) {
  return (
    <Stack direction="row" spacing={2}>
      <Typography
        sx={{
          typography: "s2",
          color: "text.subtitle",
          width: 96,
          flexShrink: 0,
        }}
      >
        {label}
      </Typography>
      <Typography
        sx={{
          typography: "s2",
          fontWeight: 600,
          color: muted ? "text.subtitle" : "text.primary",
        }}
      >
        {value}
      </Typography>
    </Stack>
  );
}
Line.propTypes = {
  label: PropTypes.string,
  value: PropTypes.node,
  muted: PropTypes.bool,
};
