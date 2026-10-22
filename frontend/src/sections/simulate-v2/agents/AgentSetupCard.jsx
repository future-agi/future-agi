import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { useConnectedAgents, useSimStore } from "../store";
import { PROVIDER_LABELS, agentNeedsRuntime, agentSourceOf, sourceKindOf } from "../_mock/agentRegistry";
import { needsLiveConsent, platformTestingOf } from "../_mock/rsiModel";
import { elevenlabsVersioningOn } from "../_mock/improvementPolicy";
import { replicationOf } from "../_mock/replication";
import { traceAiSetupFor } from "../_mock/integrations";
import { likelyProjectFor } from "../_mock/projectLinks";
import { observeProjectOf } from "./observeProject";
import { resolveObserve } from "../_mock/connectAgent";
import { ObserveProjectStep } from "./connect/ConnectAboutStep";
import { showsSetup, testedInLine } from "./agentViewUtils";
import AgentFoundReview from "./AgentFoundReview";
import LocalSteps from "../environments/LocalSteps";
import { isCodeAgent, needsFoundCheck } from "./foundFacts";

/**
 * Next steps — what a newly connected agent is for, as two equal ways
 * forward (team feedback: after connecting, the next step is getting traces
 * or building an environment, not one prominent "Build environment"):
 *
 *   In production   get its traces into an Observe project — Error Feed
 *                   finds issues in real calls, and Fix can repair them
 *   In simulation   build an environment — scenarios run against simulated
 *                   tools and data, and improvements try fixes before they ship
 *
 * Either can come first; each says what it unlocks and, once done, where it
 * stands. Under them, only what blocks a run (what we read from code to
 * confirm, where it runs, consent, versioning); the constraints, already at
 * safe defaults, are a footnote.
 *
 * Shown for an agent connected through the one-step connect, or any agent
 * not tested anywhere yet (`showsSetup`), until both are done or it's hidden.
 * A code agent's "Check what we found" is needed before it runs or is
 * improved, so the card can't be hidden while it's open.
 */
export default function AgentSetupCard({ agent, env, onTab, onBuild, onSaveValues }) {
  const { agents, state } = useConnectedAgents();
  const customLimits = !!state?.agentLimits?.[agent?.key];
  const { dispatch } = useSimStore();
  /* The traces setup, opened in place under the two paths. */
  const [choosing, setChoosing] = useState(false);
  const [observeChoice, setObserveChoice] = useState(null);
  const [reviewing, setReviewing] = useState(false);
  if (!showsSetup(agent)) return null;
  const v = agent.values || {};

  const source = agentSourceOf(agent);
  const kind = sourceKindOf(agent);
  const readFrom = source.kind === "repo" ? "the code" : source.kind === "framework" ? "the adapter" : source.kind === "platform" ? source.label : null;
  /* One Observe project per agent. */
  const project = observeProjectOf(agent, state, agents);
  /* Settled only when someone chose it — or a platform's call logs decide it. */
  const tracesDone = !!project && (!!v.observeChosenAt || (source.kind === "platform" && !!v.observability?.on));
  const envDone = (agent.usedIn || []).length > 0;
  /* Unconfirmed: the project it's linked to for now, else the existing one
     its traces most likely land in — suggested before a new, empty one. */
  const suggested = !tracesDone && (project
    ? { id: project.project, name: project.name }
    : (() => { const p = likelyProjectFor(agent, state, agents); return p ? { id: p.id, name: p.name, traces: p.traces } : null; })());
  const saveObserve = (choice = observeChoice) => {
    const chosen = resolveObserve(choice, null, agent.name);
    Object.entries(state?.projectLinks || {})
      .filter(([p, k]) => k === agent.key && p !== chosen.id)
      .forEach(([p]) => dispatch({ type: "linkProjectAgent", project: p, agentKey: null }));
    dispatch({ type: "linkProjectAgent", project: chosen.id, agentKey: agent.key });
    setChoosing(false);
    onSaveValues({ observeChosenAt: new Date().toISOString() }, chosen.mode === "create" ? `Created ${chosen.id} — ${agent.name}'s traces land there.` : `Linked to ${chosen.id} — Error Feed watches its traces.`);
  };
  const consent = needsLiveConsent(agent) && !replicationOf(agent, env).needsConsent;

  /* How its traces reach Observe, by where it lives. */
  const provider = PROVIDER_LABELS[v.provider] || source.label;
  const sendsCode = ["repo", "framework", "upload", "endpoint"].includes(kind);
  const tracesHow = kind === "platform"
    ? `${provider} call logs come in on their own — choose the Observe project they land in.`
    : kind === "builtin"
      ? "Choose the Observe project its production calls land in."
      : "Add traceAI to its code, or link the project it already sends to.";

  /* What blocks a run — only what applies. */
  const foundChecked = !!v.foundCheckedAt;
  const foundNeeded = needsFoundCheck(agent);
  const gates = [
    isCodeAgent(agent) && {
      id: "found",
      done: foundChecked && !reviewing,
      title: "Check what we found",
      body: foundChecked
        ? "Entry point, prompts, tools, model, how to run it and its tests — confirmed."
        : `What we read from ${readFrom || "the code"}: confirm or correct each item, say which tools must never run for real and which test credentials to use.`,
      action: reviewing ? null : { label: foundChecked ? "Review" : "Check", onClick: () => setReviewing(true) },
      panel: reviewing && (
        <Box sx={{ pl: 4.25, pr: 2, pb: 1.5 }}>
          <AgentFoundReview
            agent={agent}
            onCancel={() => setReviewing(false)}
            onSave={(patch, message) => { onSaveValues(patch, message); setReviewing(false); }}
          />
        </Box>
      ),
    },
    agentNeedsRuntime(agent) && {
      id: "runtime",
      done: false,
      title: "Say where it runs",
      body: "Simulations call the agent on every turn — add the address our adapter's serve() answers on.",
      action: { label: "Add", onClick: () => onTab("connections") },
    },
    consent && {
      id: "consent",
      done: false,
      title: "Allow test calls",
      body: `Tests call your real ${PROVIDER_LABELS[v.provider] ? `${PROVIDER_LABELS[v.provider]} ` : ""}agent, so anything it does — like issuing a refund — really happens. Approve this once before the first run.`,
      action: { label: "Review", onClick: () => onTab("connections") },
    },
    platformTestingOf(agent)?.needsVersioning && {
      id: "versioning",
      done: elevenlabsVersioningOn(agent) || !!v.versioningOnAt,
      title: "Switch versioning on",
      body: elevenlabsVersioningOn(agent) || v.versioningOnAt
        ? "On — improvements can ship their change as a branch."
        : `In ${PROVIDER_LABELS[v.provider] || "the platform"}: Agent → Settings → Versioning. It can't be undone. Until then, tests run but a change can't ship as a branch.`,
      action: elevenlabsVersioningOn(agent) || v.versioningOnAt ? null : {
        label: "It's on",
        onClick: () => {
          const at = new Date().toISOString();
          onSaveValues({ versioningOnAt: at, versioning: { at } }, "Versioning recorded as on — changes can ship as a branch.");
        },
      },
    },
  ].filter(Boolean);
  const gatesOpen = gates.filter((g) => !g.done).length;

  if (tracesDone && envDone && !gatesOpen) return null;

  /* The project name its traces would land in — for the traceAI lines. */
  const chosen = resolveObserve(observeChoice, null, agent.name);
  const framework = agent.definition?.framework || v.framework || "OpenAI SDK";
  const entry = String(agent.definition?.entry || v.entry || "");
  const setup = traceAiSetupFor({ framework, language: /\.(ts|tsx|js)$/.test(entry.split(":")[0]) ? "TypeScript" : "Python", project: chosen.id });

  const paths = [
    {
      id: "traces",
      eyebrow: "In production",
      title: tracesDone ? "Traces" : suggested ? "Confirm its traces" : "Get its traces",
      body: tracesDone
        ? `${project.name} · Error Feed watches its real calls`
        : suggested
          ? `Its traces look like they land in ${suggested.name}${suggested.traces ? ` (${suggested.traces.split(" · ")[0]})` : ""}. Confirm it, or pick another project.`
          : tracesHow,
      unlocks: tracesDone ? null : "Error Feed finds issues in real calls, and Fix can repair them.",
      done: tracesDone,
      action: choosing ? null : tracesDone
        ? { label: "Change", onClick: () => { setObserveChoice({ mode: "existing", id: project.project }); setChoosing(true); } }
        : suggested
          ? { label: `Confirm ${suggested.name}`, onClick: () => saveObserve({ mode: "existing", id: suggested.id }) }
          : { label: kind === "platform" || kind === "builtin" ? "Choose project" : "Connect traces", onClick: () => { setObserveChoice(null); setChoosing(true); } },
      secondary: !tracesDone && suggested && !choosing
        ? { label: "Pick another", onClick: () => { setObserveChoice({ mode: "existing", id: suggested.id }); setChoosing(true); } }
        : null,
    },
    {
      id: "environment",
      eyebrow: "In simulation",
      title: envDone ? "Environment" : "Build an environment",
      body: envDone ? testedInLine(agent.usedIn) : "Simulated tools, data and callers to run scenarios against.",
      unlocks: envDone ? null : "Test changes, and let improvements try fixes before they ship.",
      done: envDone,
      action: envDone ? { label: "View", onClick: () => onTab("environments") } : onBuild ? { label: "Build environment", onClick: () => onBuild() } : null,
    },
  ];

  return (
    <Box sx={{ mb: 2, border: "1px solid", borderColor: "divider", borderRadius: 1.5, bgcolor: "background.paper", overflow: "hidden" }}>
      <Stack direction="row" alignItems="center" spacing={1} sx={{ px: 2, py: 1.25, borderBottom: "1px solid", borderColor: "divider" }}>
        <Typography sx={{ typography: "s1", fontWeight: 700 }}>Next steps</Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>Do either first — the other is here when you need it.</Typography>
        <Box flex={1} />
        {!foundNeeded && (
          <Button
            size="small"
            onClick={() => onSaveValues({ setupDismissedAt: new Date().toISOString() }, "Hidden — everything here is still on the agent's tabs.")}
            sx={{ typography: "s3", fontWeight: 600, color: "text.secondary", minWidth: 0 }}
          >
            Hide
          </Button>
        )}
      </Stack>

      {/* ── the two ways forward, side by side and equal ── */}
      <Box sx={{
        display: "grid", gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" },
        "& > *": { borderColor: "divider", borderStyle: "solid", borderWidth: 0 },
        "& > *:not(:first-of-type)": { borderLeftWidth: { md: 1 }, borderTopWidth: { xs: 1, md: 0 } },
      }}>
        {paths.map((p) => <PathCell key={p.id} {...p} />)}
      </Box>

      {/* ── getting traces, opened in place ── */}
      {choosing && (
        <Box sx={{ px: 2, py: 1.75, borderTop: "1px solid", borderColor: "divider" }}>
          <ObserveProjectStep agentName={agent.name} agentKey={agent.key} value={observeChoice} onChange={setObserveChoice} />
          {/* A new project has nothing coming in yet — the lines that start it. */}
          {sendsCode && chosen.mode === "create" && (
            <Box sx={{ mt: 2 }}>
              <Typography sx={{ typography: "s2", fontWeight: 600 }}>Then start sending traces</Typography>
              <Typography sx={{ typography: "s3", color: "text.subtitle", mb: 1.25 }}>
                {`Add traceAI where the agent runs — its calls land in ${chosen.id} from the next one.`}
              </Typography>
              <LocalSteps
                steps={[
                  { title: "Install traceAI", body: "In the agent's environment.", cmd: setup.install },
                  { title: "Set your keys", body: "Where the agent runs — copied from Keys, read from the environment, never written into the code.", cmd: setup.env },
                  { title: "Add these lines before the agent starts", body: `Every call it makes is traced to ${chosen.id}.`, cmd: setup.code },
                ]}
              />
            </Box>
          )}
          <Stack direction="row" justifyContent="flex-end" spacing={1} sx={{ mt: 1.5 }}>
            <Button size="small" onClick={() => setChoosing(false)} sx={{ typography: "s3", fontWeight: 700, color: "text.secondary" }}>Cancel</Button>
            <Button size="small" variant="contained" color="primary" onClick={() => saveObserve()} sx={{ typography: "s3", fontWeight: 700 }}>
              {chosen.mode === "create" ? `Create ${chosen.id}` : `Link ${chosen.id}`}
            </Button>
          </Stack>
        </Box>
      )}

      {/* ── only what blocks a run ── */}
      {gates.length > 0 && (
        <>
          <Typography sx={{ px: 2, pt: 1.25, pb: 0.5, typography: "s3", fontWeight: 700, color: "text.subtitle", borderTop: "1px solid", borderColor: "divider" }}>
            {gatesOpen ? `Needed before it runs or is fixed · ${gatesOpen} left` : "Needed before it runs or is fixed · done"}
          </Typography>
          {gates.map((g) => <Row key={g.id} {...g} />)}
        </>
      )}

      {/* The constraints start at safe defaults — said, not a step. */}
      <Stack direction="row" alignItems="center" spacing={1} sx={{ px: 2, py: 1, borderTop: "1px solid", borderColor: "divider" }}>
        <Iconify icon="solar:shield-check-linear" width={14} sx={{ color: "text.subtitle", flexShrink: 0 }} />
        <Typography noWrap sx={{ typography: "s3", color: "text.subtitle", flex: 1, minWidth: 0 }}>
          {customLimits
            ? "Constraints set on the Constraints tab — every improvement is held to them."
            : "Constraints: safe defaults — prompts and tool descriptions only, model locked, no cost increase."}
        </Typography>
        <Button size="small" onClick={() => onTab("constraints")} sx={{ typography: "s3", fontWeight: 700, color: "text.secondary", minWidth: 0, flexShrink: 0 }}>
          Review
        </Button>
      </Stack>
    </Box>
  );
}

AgentSetupCard.propTypes = {
  agent: PropTypes.object,
  env: PropTypes.object,
  /* Opens one of the agent's tabs ("definition", "connections", "constraints", "environments"). */
  onTab: PropTypes.func.isRequired,
  onBuild: PropTypes.func,
  /* (valuesPatch, message) — the agent page's own save. */
  onSaveValues: PropTypes.func.isRequired,
};

/* One way forward: where it applies, what to do, what it unlocks — or, done, where it stands. */
function PathCell({ eyebrow, title, body, unlocks, done, action, secondary }) {
  return (
    <Stack spacing={0.5} sx={{ px: 2, py: 1.75, minWidth: 0 }}>
      <Stack direction="row" alignItems="center" spacing={0.75}>
        <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle", textTransform: "uppercase", letterSpacing: 0.4 }}>{eyebrow}</Typography>
        {done && <Iconify icon="solar:check-circle-bold" width={14} sx={{ color: "text.secondary" }} />}
      </Stack>
      <Typography sx={{ typography: "s1", fontWeight: 700 }}>{title}</Typography>
      <Typography sx={{ typography: "s2", color: "text.secondary" }}>{body}</Typography>
      {unlocks && <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{unlocks}</Typography>}
      {action && (
        <Stack direction="row" spacing={0.5} sx={{ pt: 0.75 }}>
          <Button
            size="small" variant="outlined" onClick={action.onClick}
            sx={{ typography: "s2", fontWeight: 700, height: 30, color: "text.primary", borderColor: "divider" }}
          >
            {action.label}
          </Button>
          {secondary && (
            <Button size="small" onClick={secondary.onClick} sx={{ typography: "s2", fontWeight: 600, height: 30, color: "text.secondary" }}>
              {secondary.label}
            </Button>
          )}
        </Stack>
      )}
    </Stack>
  );
}
PathCell.propTypes = {
  eyebrow: PropTypes.string, title: PropTypes.string, body: PropTypes.node, unlocks: PropTypes.string,
  done: PropTypes.bool, action: PropTypes.object, secondary: PropTypes.object,
};

function Row({ done, title, body, action, panel }) {
  return (
    <Box>
      <Stack direction="row" alignItems="center" spacing={1.5} sx={{ px: 2, py: 1 }}>
        <Iconify
          icon={done ? "solar:check-circle-bold" : "solar:circle-linear"}
          width={18}
          sx={{ color: done ? "text.secondary" : "text.disabled", flexShrink: 0 }}
        />
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s2", fontWeight: 600, color: done ? "text.secondary" : "text.primary" }}>{title}</Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{body}</Typography>
        </Box>
        {action && (
          <Button
            size="small" variant="outlined" onClick={action.onClick}
            sx={{ typography: "s3", fontWeight: 700, height: 28, flexShrink: 0, color: "text.primary", borderColor: "divider" }}
          >
            {action.label}
          </Button>
        )}
      </Stack>
      {panel}
    </Box>
  );
}
Row.propTypes = {
  done: PropTypes.bool,
  title: PropTypes.string,
  body: PropTypes.node,
  action: PropTypes.object,
  panel: PropTypes.node,
};
