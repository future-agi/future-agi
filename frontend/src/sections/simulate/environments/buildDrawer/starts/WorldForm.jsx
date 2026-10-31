import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Button, Divider, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { generatedPool } from "src/api/simulate-environments/_fixtures/scenarioPool";
import { BUILD_TONES } from "../../buildEnvironment/buildTones";
import { HintLine, Label, WhereOption } from "../forms/formBits";
import { BTN_SX, T12, T13 } from "../drawerStyles";
import { modalityOfWorld } from "../worlds";
import AgentPicker, { useWorldAgents } from "./AgentPicker";
import BuildForAgentDialog from "./BuildForAgentDialog";
import { IconLine, LocalSteps, StartCard } from "./startBits";
import { slugOf, uiOnlyToast } from "./startUtils";

const footSx = {
  px: 2,
  py: 1.5,
  borderTop: "1px solid",
  borderColor: "divider",
};
const twoUp = { display: "grid", gap: 1, gridTemplateColumns: "1fr 1fr" };

function Line({ label, value, muted = false }) {
  return (
    <Stack direction="row" spacing={2}>
      <Typography
        sx={{ ...T13, color: "text.secondary", width: 96, flexShrink: 0 }}
      >
        {label}
      </Typography>
      <Typography
        sx={{
          ...T13,
          fontWeight: 600,
          color: muted ? "text.secondary" : "text.primary",
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

/*
  On your machine: the CLI steps for this world and agent, with the defaults
  Open the build uses (the agent's latest version, 10 scenarios). The machine
  never checks in here, since the CLI isn't released.
*/
function LocalRun({ template, agent }) {
  const version = agent.latestLabel || "v1";
  const slug = slugOf(`${agent.name} ${template.name}`);
  const steps = [
    {
      title: "Install the CLI",
      body: "Once per machine.",
      cmd: "pip install futureagi",
    },
    {
      title: "Pull this world for your agent",
      body: `This world's tables, tools and rules, and ${agent.name} at ${version}.`,
      hint: `These are the defaults Open the build here uses: ${version} and 10 scenarios. For another version or size, build it In Future AGI.`,
      cmd: `fai env init ${slug} --world ${template.id} --agent "${agent.name}" --version ${version} --scenarios 10`,
    },
    {
      title: "Build it on your machine",
      body: "Builds the world locally and opens a secure tunnel back to this workspace.",
      hint: "Nothing in the world leaves your machine. The tunnel carries results, traces and scores.",
      cmd: `fai env up ${slug}`,
    },
  ];
  return (
    <Box sx={{ borderTop: "1px solid", borderColor: "divider" }}>
      <Box sx={{ px: 2, pt: 2, pb: 2.5 }}>
        <LocalSteps steps={steps} />
      </Box>
      <Divider />
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.25}
        sx={{ px: 2, py: 1.5 }}
      >
        <Iconify
          icon="solar:laptop-2-linear"
          width={16}
          sx={{ color: "text.secondary", flexShrink: 0 }}
        />
        <Typography
          sx={{ ...T13, color: "text.secondary", flex: 1, minWidth: 0 }}
        >
          Run these on your machine. It shows here when it checks in.
        </Typography>
        <Button
          variant="contained"
          color="primary"
          onClick={() => uiOnlyToast("building on your machine")}
          sx={BTN_SX}
        >
          Open the build
        </Button>
      </Stack>
    </Box>
  );
}
LocalRun.propTypes = {
  template: PropTypes.object.isRequired,
  agent: PropTypes.object.isRequired,
};

/**
 * A prebuilt world: try it now with its sample agent, or use it with one of
 * your connected agents, in Future AGI or on your machine. What the world
 * gives is read from its template. UI only: nothing here builds yet.
 */
export default function WorldForm({ item }) {
  const { world } = item;
  const template = world.template;
  const modality = modalityOfWorld(world);
  const [mode, setMode] = useState("try");
  const [where, setWhere] = useState("cloud");
  const { agents, isLoading } = useWorldAgents(modality);
  const [agentId, setAgentId] = useState(null);
  const agent = agents.find((a) => a.id === agentId) || null;
  const [dialogFor, setDialogFor] = useState(null);

  const rows = (template.seed?.tables || []).reduce(
    (n, t) => n + (t.rows || 0),
    0,
  );
  const scenarioCount = useMemo(
    () => generatedPool(template).length,
    [template],
  );
  const evalCount = (template.evalPreset || []).length;
  const sampleName = `${template.name} sample agent`;

  return (
    <Stack spacing={2}>
      <Box role="radiogroup" aria-label="How to use this world" sx={twoUp}>
        <WhereOption
          icon="solar:play-circle-linear"
          label="Try it now"
          on={mode === "try"}
          onClick={() => setMode("try")}
          short="With the world's sample agent · ready in seconds"
          hint="The world exactly as designed: its sample agent, scenarios and evaluations. Nothing to connect or build."
        />
        <WhereOption
          icon="solar:link-circle-linear"
          label="Use it with your agent"
          on={mode === "agent"}
          onClick={() => setMode("agent")}
          short="Build this world for an agent you've connected"
          hint="Your agent is read and its tools are checked against this world, then scenarios are written and proven for it."
        />
      </Box>

      {mode === "try" ? (
        <StartCard
          title="Try it now"
          subtitle="The world exactly as designed, tested with its sample agent."
        >
          <Stack sx={{ p: 2 }} spacing={1.25}>
            <IconLine
              icon="solar:user-speak-rounded-linear"
              text={`${sampleName}, the agent this world was designed and proven with.`}
              hint="It's in Agents, tagged Sample. Connect your own agent from the environment's Overview when you're ready. It takes the sample agent's place."
            />
            <IconLine
              icon="solar:checklist-minimalistic-linear"
              text={`${scenarioCount} scenarios, already proven, and ${evalCount} evaluation${evalCount === 1 ? "" : "s"}.`}
            />
            <IconLine
              icon="solar:play-circle-linear"
              text="Ready to run in seconds. Nothing to connect, nothing to build."
            />
          </Stack>
          <Stack direction="row" alignItems="center" spacing={1.5} sx={footSx}>
            <Button
              variant="contained"
              color="primary"
              onClick={() => uiOnlyToast("trying a prebuilt world")}
              startIcon={<Iconify icon="solar:play-bold" width={14} />}
              sx={BTN_SX}
            >
              Try it now
            </Button>
            <Typography sx={{ ...T12, color: "text.secondary" }}>
              Opens a ready environment
            </Typography>
          </Stack>
        </StartCard>
      ) : (
        <StartCard
          title="Build an agent's environment from this world"
          subtitle="The world is the starting point; the agent is what's tested."
        >
          <Stack sx={{ p: 2 }} spacing={2}>
            <AgentPicker
              agents={agents}
              isLoading={isLoading}
              value={agentId}
              onChange={setAgentId}
              modality={modality}
            />
            <Box>
              <Box sx={{ mb: 0.75 }}>
                <Label>Where it runs</Label>
              </Box>
              <Box role="radiogroup" aria-label="Where it runs" sx={twoUp}>
                <WhereOption
                  icon="solar:cloud-linear"
                  label="In Future AGI"
                  on={where === "cloud"}
                  onClick={() => setWhere("cloud")}
                  short="Our hosted sandbox · ready to run in seconds"
                  hint="Built in an isolated sandbox from this world's tables, tools and rules. Runs are metered by the minute."
                />
                <WhereOption
                  icon="solar:laptop-2-linear"
                  label="On your machine"
                  on={where === "local"}
                  onClick={() => setWhere("local")}
                  short="Through our CLI · the world stays on your laptop"
                  hint="Builds the world on your machine; runs still show up here. Good for regulated or offline code."
                />
              </Box>
            </Box>
            {where === "cloud" && (
              <Stack spacing={1.25}>
                <IconLine
                  icon="solar:box-minimalistic-linear"
                  text="Seeded with this world's tables, tools and hard rules."
                />
                <IconLine
                  icon="solar:user-speak-rounded-linear"
                  text="Your agent is read and its tools are checked against the world."
                  hint="A tool the world doesn't answer is named before anything runs."
                />
                <IconLine
                  icon="solar:play-circle-linear"
                  text="Scenarios are written and proven, then you run them once to see where the agent stands today."
                />
              </Stack>
            )}
          </Stack>
          {where === "local" &&
            (agent ? (
              <LocalRun template={template} agent={agent} />
            ) : (
              <Typography sx={{ ...footSx, ...T12, color: "text.secondary" }}>
                Pick the agent this environment is for. The commands to run on
                your machine show here.
              </Typography>
            ))}
          {where === "cloud" && (
            <Stack
              direction="row"
              alignItems="center"
              spacing={1.5}
              sx={footSx}
            >
              <Button
                variant="contained"
                color="primary"
                disabled={!agent}
                onClick={() => setDialogFor(agent)}
                startIcon={
                  <Iconify icon="solar:magic-stick-3-bold" width={14} />
                }
                sx={BTN_SX}
              >
                Build environment
              </Button>
              {!agent && (
                <Typography sx={{ ...T12, color: "text.secondary" }}>
                  Pick the agent this environment is for
                </Typography>
              )}
            </Stack>
          )}
        </StartCard>
      )}

      <StartCard title="What this world gives you">
        <Stack sx={{ px: 2, py: 1.75 }} spacing={1.25}>
          <Line label="World" value={`${rows.toLocaleString()} seeded rows`} />
          <Line
            label="Tools"
            value={`${(template.tools || []).length} the world answers`}
          />
          <Line
            label="Rules"
            value={`${(template.rules || []).length} graded on every run`}
          />
          {mode === "try" ? (
            <>
              <Line label="Scenarios" value={`${scenarioCount} proven`} />
              <Line label="Agent" value={sampleName} />
            </>
          ) : (
            <>
              <Line
                label="Agent"
                value={agent ? agent.name : "Not picked yet"}
                muted={!agent}
              />
              <Line
                label="Runs"
                value={where === "local" ? "On your machine" : "In Future AGI"}
              />
            </>
          )}
        </Stack>
      </StartCard>

      <Stack
        direction="row"
        spacing={1.25}
        alignItems="flex-start"
        sx={{
          p: 2,
          borderRadius: "8px",
          border: "1px solid",
          borderColor: alpha(BUILD_TONES.green, 0.3),
          bgcolor: alpha(BUILD_TONES.green, 0.06),
        }}
      >
        <Iconify
          icon="solar:shield-keyhole-linear"
          width={16}
          sx={{ color: BUILD_TONES.green, flexShrink: 0, mt: "2px" }}
        />
        <Typography component="div" sx={{ ...T13, fontWeight: 600 }}>
          <HintLine
            text="The world never touches production."
            hint="The world's tools answer from seeded data in a sandbox."
          />
        </Typography>
      </Stack>

      <BuildForAgentDialog
        agent={dialogFor}
        onClose={() => setDialogFor(null)}
      />
    </Stack>
  );
}
WorldForm.propTypes = { item: PropTypes.object.isRequired };
