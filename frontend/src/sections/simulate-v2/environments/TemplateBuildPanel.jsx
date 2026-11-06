import PropTypes from "prop-types";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Box,
  Stack,
  Typography,
  Button,
  MenuItem,
  Select,
  TextField,
  Tooltip,
} from "@mui/material";
import { paths } from "src/routes/paths";
import Iconify from "src/components/iconify";
import { getSurface } from "../_mock/surfaces";
import { useConnectedAgents } from "../store";
import { SectionCard } from "../components/primitives";
import BuildEnvironmentDialog from "../agents/BuildEnvironmentDialog";
import { HintLine } from "../agents/connect/replicationBits";
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
}) {
  const navigate = useNavigate();
  /* Starter pack by default; a named pack or the full world on request. */
  const [pack, setPack] = useState("starter");
  const { agents: all } = useConnectedAgents();
  const agents = useMemo(() => buildableAgentsOf(all), [all]);
  const [agentId, setAgentId] = useState(null);
  const agent = agents.find((a) => a.id === agentId) || null;
  const [dialogOpen, setDialogOpen] = useState(false);
  const build = useBuildForAgent();
  /* The tool binding the person reviewed; null until they change one. */
  const [binding, setBinding] = useState(null);
  /* Adapting writes scenarios for the agent: a fork that doesn't compare. */
  const [adapt, setAdapt] = useState(false);
  /* What to change when customizing without an agent. */
  const [changes, setChanges] = useState("");
  /* Mappings whose differences someone reviewed, and the measurable-only choice. */
  const [reviewed, setReviewed] = useState([]);
  const [measurableOk, setMeasurableOk] = useState(false);
  useEffect(() => {
    setBinding(null);
    setReviewed([]);
    setMeasurableOk(false);
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
  const startBuild = (choice) =>
    build(agent, choice, {
      ...templateWorldSource(template),
      /* The fixed suite: this pack, this release, the reviewed binding. */
      pack,
      ...(entry && { release: entry.release.version, catalogueId: entry.id }),
      ...(fit && {
        binding: fit.binding,
        unmeasured: fit.unmeasured.map((x) => x.id),
      }),
      ...(adapt && { adapt: true }),
    });

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

      <SectionCard
        title="Use it with your agent"
        subtitle="The world is the starting point; the agent is what's tested."
      >
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
              measurableOk={measurableOk}
              onMeasurable={setMeasurableOk}
              onOtherWorld={() =>
                navigate(`${paths.dashboard.simulate.environments}?view=worlds`)
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
              <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                {`${agent.name} is tested live and calls its own tools; this world only grades it. Test a simulated copy for simulated tools.`}
              </Typography>
            </Stack>
          )}
          <Stack spacing={1.25}>
            {[
              {
                icon: "solar:box-minimalistic-linear",
                text: "Seeded with this world's tables, tools and hard rules.",
              },
              {
                icon: "solar:user-speak-rounded-linear",
                text: "Your agent is read and its tools are checked against the world.",
                hint: "A tool the world doesn't answer is named before anything runs.",
              },
              {
                icon: "solar:play-circle-linear",
                text: `The world's ${scenarioCount} proven scenarios come over unchanged. Run them once to see where your agent stands.`,
              },
            ].map((row) => (
              <Stack
                key={row.text}
                direction="row"
                spacing={1.25}
                alignItems="flex-start"
              >
                <Iconify
                  icon={row.icon}
                  width={16}
                  sx={{ color: "text.subtitle", flexShrink: 0, mt: "1px" }}
                />
                <Typography
                  component="div"
                  sx={{ typography: "s2", color: "text.secondary" }}
                >
                  {row.hint ? (
                    <HintLine hint={row.hint}>{row.text}</HintLine>
                  ) : (
                    row.text
                  )}
                </Typography>
              </Stack>
            ))}
          </Stack>
          {/* The copy, read before it's made. */}
          <Stack
            spacing={0.75}
            sx={{ pt: 1.5, borderTop: "1px solid", borderColor: "divider" }}
          >
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              Review before you adopt
            </Typography>
            <Line
              label="Copied"
              value={`${rows.toLocaleString()} seeded rows · ${scenarioCount} scenarios`}
            />
            <Line
              label="Source"
              value={
                entry
                  ? `${entry.id} · release ${entry.release.version}`
                  : env.name
              }
            />
            <Line label="Visibility" value="Private to this workspace" />
            <Line label="Catalogue" value="The release stays unchanged" />
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
          <Select
            size="small"
            value={pack}
            onChange={(e) => setPack(e.target.value)}
            inputProps={{ "aria-label": "Scenario pack" }}
            sx={{ typography: "s2", minWidth: 170 }}
          >
            {packs.map((p) => (
              <MenuItem key={p.id} value={p.id} sx={{ typography: "s2" }}>
                {`${p.name} · ${p.count}`}
              </MenuItem>
            ))}
          </Select>
          <Box flex={1} />
          {/* The other way: write scenarios for this agent — a fork that doesn't compare. */}
          <Button
            variant="outlined"
            size="small"
            color="inherit"
            disabled={!agent || !!blocked}
            onClick={() => {
              setAdapt(true);
              setDialogOpen(true);
            }}
            sx={{ whiteSpace: "nowrap" }}
          >
            Adapt scenarios to my agent
          </Button>
          {/* Needs input waits for the reviewed mappings; Partial for the measurable-only choice. */}
          <Tooltip
            arrow
            title={
              fit?.verdict === "needs_input"
                ? "Review the mappings that need input first"
                : fit?.verdict === "partial" &&
                    fit.unmeasured.length &&
                    !measurableOk
                  ? "Choose to run the measurable cases, or repair the fit"
                  : ""
            }
          >
            <span>
              <Button
                variant="contained"
                color="primary"
                disabled={
                  !agent ||
                  !!blocked ||
                  fit?.verdict === "needs_input" ||
                  (fit?.verdict === "partial" &&
                    fit.unmeasured.length > 0 &&
                    !measurableOk)
                }
                onClick={() => {
                  setAdapt(false);
                  setDialogOpen(true);
                }}
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

      {/* Customize a ready environment (plan builder §1, T02): no agent is
          needed to design — a private copy with the changes asked for,
          reviewed before it's built. */}
      <SectionCard
        title="Customize it first"
        subtitle="No agent needed. Say what to change; you review the design before a private copy is built."
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
                      worldSource: { ...templateWorldSource(template), pack },
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
              Customize without an agent
            </Button>
          </Stack>
        </Stack>
      </SectionCard>

      <SectionCard title="What this world gives you">
        <Stack sx={{ px: 2.5, py: 2 }} spacing={1.25}>
          <Line label="World" value={`${rows.toLocaleString()} seeded rows`} />
          <Line
            label="Tools"
            value={`${env.tools?.length || 0} the world answers`}
          />
          <Line
            label="Rules"
            value={`${env.rules?.length || 0} graded on every run`}
          />
          <Line
            label="Agent"
            value={agent ? agent.name : "Not picked yet"}
            muted={!agent}
          />
          <Line label="Runs" value="In Future AGI" />
          {/* Cloud only for now (clarified plan); local runs are a later ALK direction. */}
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            Running a world on your own machine is coming soon with ALK, our
            open-source toolkit.
          </Typography>
        </Stack>
      </SectionCard>

      <Box
        sx={{
          p: 2,
          borderRadius: "8px",
          border: "1px solid",
          borderColor: "status.success.border",
          bgcolor: "status.success.bg",
        }}
      >
        <Stack direction="row" spacing={1.25} alignItems="flex-start">
          <Iconify
            icon="solar:shield-keyhole-linear"
            width={16}
            sx={{ color: "status.success.fg", flexShrink: 0, mt: "1px" }}
          />
          <Typography
            component="div"
            sx={{ typography: "s2", fontWeight: 600, color: "text.primary" }}
          >
            <HintLine hint="The world's tools answer from seeded data in a sandbox.">
              The world never touches production.
            </HintLine>
          </Typography>
        </Stack>
      </Box>

      {agent && (
        <BuildEnvironmentDialog
          open={dialogOpen}
          onClose={() => setDialogOpen(false)}
          agent={agent}
          defaultVersion={agent.production || agent.latest}
          onBuild={(choice) => {
            setDialogOpen(false);
            startBuild(choice);
          }}
        />
      )}
    </Stack>
  );
}
TemplateBuildPanel.propTypes = {
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
