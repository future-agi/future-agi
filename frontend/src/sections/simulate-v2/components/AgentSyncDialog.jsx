import PropTypes from "prop-types";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Box, Button, CircularProgress, Dialog, IconButton, Stack, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import { agentSourceOf, envAgentKey, syncCheck, syncedVersion } from "../_mock/agentRegistry";
import { getEnvironment } from "../_mock/environments";
import { worldTouchOf } from "../_mock/specChange";
import { worldToolsFor } from "../_mock/toolFit";
import {
  raiseNudgesIn, dispatchNudges, nudgeAnnouncement, behaviourNudgesOf, likelyFixLine, openNudgesIn,
} from "../_mock/nudgeEffects";
import { useSimStore } from "../store";

const CHANGE_ICON = {
  instructions: "solar:document-text-linear",
  tool: "solar:widget-add-linear",
  model: "solar:cpu-bolt-linear",
  voice: "solar:soundwave-linear",
  knowledge: "solar:library-linear",
};

/**
 * Sync = re-read the agent's source and, if anything changed, save it as the
 * next AGENT version. That is all it saves (PRD v7 §4.10.1): the environment
 * and its scenarios never change because the agent did. What no longer fits
 * is raised as nudges in every environment that uses the agent, and the
 * flow ends by pointing at them.
 *
 *   checking → up to date
 *            → changes found → save → saved: "2 nudges waiting in …"
 */
export default function AgentSyncDialog({
  open, onClose, agent, versions, env, baseTools, onSave, runningLabel, onRunHere, usedInCount = 0, onOpenEnvironments,
}) {
  const source = useMemo(() => agentSourceOf(agent), [agent]);
  const [phase, setPhase] = useState("checking");
  const [step, setStep] = useState(0);
  const [result, setResult] = useState(null);
  const [saved, setSaved] = useState(null);
  const navigate = useNavigate();
  const { state, dispatch } = useSimStore();
  /* Every environment testing this agent — the version is minted in each of
     them, so each gets its own nudges. */
  const envs = useMemo(() => {
    const key = agent?.key || envAgentKey(agent, env?.id);
    const all = state.myEnvironments || [];
    const testing = key ? all.filter((e) => envAgentKey(state.byEnv?.[e.id]?.agent, e.id) === key) : [];
    if (testing.length) return testing;
    return agent?.usedIn?.length
      ? agent.usedIn.map((u) => all.find((e) => e.id === u.envId) || getEnvironment(u.envId)).filter(Boolean)
      : [env].filter(Boolean);
  }, [agent, env, state.myEnvironments, state.byEnv]);

  const steps = [
    source.kind === "framework" ? `Connecting to the ${source.label} adapter` : `Connecting to ${source.label}`,
    source.kind === "repo" ? "Reading the latest commit" : source.kind === "mcp" ? "Reading the tool manifest"
      : source.kind === "framework" ? "Asking the adapter to describe the agent" : "Reading the agent's config",
    `Comparing with ${versions[versions.length - 1]?.label || "v1"}`,
  ];

  useEffect(() => {
    if (!open) return undefined;
    setPhase("checking");
    setStep(0);
    setSaved(null);
    const timers = [
      setTimeout(() => setStep(1), 450),
      setTimeout(() => setStep(2), 900),
      setTimeout(() => {
        const r = syncCheck({ agent, versions, env });
        setResult(r);
        setPhase(r.upToDate ? "upToDate" : "changes");
      }, 1350),
    ];
    return () => timers.forEach(clearTimeout);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  /* A synced version is production: environments following production test
     it next. From inside an environment the one other choice is to hold this
     environment on the version it tests now (a pin). */
  const inEnvironment = !!(runningLabel && onRunHere);
  /* A repository commit can touch the world's code too. That's proposed as
     a nudge (update the handler?), never saved as an environment version. */
  const world = phase === "changes" ? worldTouchOf(result, source) : null;

  const save = (switchHere = inEnvironment) => {
    const version = syncedVersion({ result, baseTools });
    onSave?.(version);
    /* After the save lands: the new version follows production here; staying
       pins the version it tests now. */
    if (inEnvironment) onRunHere(switchHere ? version.label : runningLabel);
    const results = raiseNudgesIn(state, envs, version, {
      /* Only tools the world already served can "behave differently" — a new
         one is an Add nudge. */
      extra: (e, es) => (world ? behaviourNudgesOf({ ...world, tools: world.tools.filter((t) => worldToolsFor(e, es).some((w) => w.name === t)) }, version) : []),
    });
    dispatchNudges(dispatch, results);
    setSaved({ version, switched: switchHere, results, announcement: nudgeAnnouncement(results) });
    setPhase("saved");
  };

  const review = (envId) => { onClose(); openNudgesIn(navigate, envId, inEnvironment ? env?.id : null); };
  const fixes = (saved?.results || []).flatMap((r) => r.likelyFixes.map((f) => ({ ...f, envName: r.env.name })));
  const fixLines = [...new Map(fixes.map((f) => [f.ruleId, f])).values()];

  return (
    <Dialog
      open={open}
      onClose={phase === "checking" ? undefined : onClose}
      PaperProps={{
        sx: {
          borderRadius: 2, maxWidth: 520, width: "100%",
          bgcolor: "background.paper", backgroundImage: "none",
          border: "1px solid", borderColor: "divider",
        },
      }}
    >
      <Box sx={{ p: 3 }}>
        <Stack direction="row" alignItems="flex-start" spacing={1.25} sx={{ mb: 2 }}>
          <Box sx={{
            width: 30, height: 30, borderRadius: 1, flexShrink: 0,
            display: "grid", placeItems: "center",
            bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.08 : 0.05),
            color: "text.secondary",
          }}>
            <Iconify icon={source.icon} width={16} />
          </Box>
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "m3", fontWeight: 700 }}>
              {phase === "checking" && `Syncing from ${source.label}`}
              {phase === "upToDate" && `Up to date with ${result?.latest}`}
              {phase === "changes" && `${result.changes.length} change${result.changes.length === 1 ? "" : "s"} since ${result.basedOn}`}
              {phase === "saved" && `Saved agent ${saved.version.label}`}
            </Typography>
            <Typography sx={{ typography: "s2", color: "text.subtitle", mt: 0.25 }}>
              {phase === "checking" && "Checking the source for anything that changed since the last version."}
              {phase === "upToDate" && `Nothing changed in ${source.label} since ${result?.latest} was saved.`}
              {phase === "changes" && `Saving creates agent ${result.label} only; ${result.basedOn} stays as it was. The environment and its scenarios stay as they are — we'll list anything that no longer fits.`}
              {phase === "saved" && (saved.switched
                ? `This environment's default is production, so it tests ${saved.version.label} from the next run. The environment and scenarios didn't change.`
                : inEnvironment
                  ? `This environment's default stays ${runningLabel}; environments whose default is production test ${saved.version.label}. The environment and scenarios didn't change.`
                  : usedInCount
                    ? "Environments whose default is production test it on their next run; the others stay on theirs. Their worlds and scenarios didn't change."
                    : "Nothing else changed.")}
            </Typography>
          </Box>
          {phase !== "checking" && (
            <IconButton size="small" onClick={onClose} sx={{ mt: -0.5, mr: -0.5 }}>
              <Iconify icon="eva:close-fill" width={18} />
            </IconButton>
          )}
        </Stack>

        {phase === "checking" && (
          <Stack spacing={1} sx={{ p: 2, borderRadius: 1.25, border: "1px solid", borderColor: "divider", bgcolor: "background.neutral" }}>
            {steps.map((s, i) => (
              <Stack key={s} direction="row" alignItems="center" spacing={1}>
                {i < step
                  ? <Iconify icon="mdi:check" width={14} sx={{ color: "text.secondary" }} />
                  : i === step
                    ? <CircularProgress size={12} thickness={5} sx={{ color: "text.subtitle" }} />
                    : <Box sx={{ width: 14, height: 14, display: "grid", placeItems: "center" }}><Box sx={{ width: 5, height: 5, borderRadius: "50%", bgcolor: "text.disabled" }} /></Box>}
                <Typography sx={{ typography: "s2", color: i <= step ? "text.primary" : "text.disabled" }}>{s}</Typography>
              </Stack>
            ))}
          </Stack>
        )}

        {world && (
          <Stack direction="row" spacing={1} alignItems="flex-start" sx={{ mb: 1.5, p: 1.25, borderRadius: 1.25, border: "1px solid", borderColor: "divider", bgcolor: "background.neutral" }}>
            <Iconify icon="solar:database-linear" width={16} sx={{ color: "text.subtitle", mt: 0.125, flexShrink: 0 }} />
            <Typography sx={{ typography: "s3", color: "text.secondary", flex: 1, minWidth: 0 }}>
              <Box component="span" sx={{ fontWeight: 700, color: "text.primary" }}>
                {`This commit also touches the world's code (${world.paths[0]}${world.paths.length > 1 ? ` and ${world.paths.length - 1} more` : ""}).`}
              </Box>
              {" That's proposed to the environment as a suggestion after saving — not applied."}
            </Typography>
          </Stack>
        )}

        {phase === "changes" && (
          <Box sx={{ borderRadius: 1.25, border: "1px solid", borderColor: "divider", overflow: "hidden" }}>
            {result.changes.map((c, i) => (
              <Stack
                key={c.title}
                direction="row" alignItems="flex-start" spacing={1.25}
                sx={{ px: 1.75, py: 1.25, borderTop: i ? "1px solid" : "none", borderColor: "divider" }}
              >
                <Iconify icon={CHANGE_ICON[c.kind] || "solar:pen-2-linear"} width={16} sx={{ color: "text.subtitle", mt: 0.25, flexShrink: 0 }} />
                <Box minWidth={0}>
                  <Typography sx={{ typography: "s2", fontWeight: 600 }}>{c.title}</Typography>
                  <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.125 }}>{c.detail}</Typography>
                </Box>
              </Stack>
            ))}
          </Box>
        )}

        {phase === "saved" && (
          <Stack spacing={1}>
            <Box sx={{ borderRadius: 1.25, border: "1px solid", borderColor: "divider", overflow: "hidden" }}>
              <Stack direction="row" alignItems="center" spacing={1} sx={{ px: 1.75, py: 1.125, bgcolor: "background.neutral" }}>
                <Iconify icon="solar:bell-linear" width={15} sx={{ color: "text.secondary", flexShrink: 0 }} />
                <Typography sx={{ typography: "s2", fontWeight: 700, flex: 1, minWidth: 0 }}>{saved.announcement.text}</Typography>
              </Stack>
              {saved.announcement.envs.map((e) => (
                <Stack key={e.id} direction="row" alignItems="center" spacing={1} sx={{ px: 1.75, py: 0.75, borderTop: "1px solid", borderColor: "divider" }}>
                  <Typography noWrap sx={{ typography: "s3", flex: 1, minWidth: 0 }}>{e.name}</Typography>
                  <Typography sx={{ typography: "s3", color: "text.subtitle", flexShrink: 0 }}>{`Agent ${saved.version.label} changed`}</Typography>
                  <Button
                    size="small" onClick={() => review(e.id)}
                    endIcon={<Iconify icon="solar:arrow-right-linear" width={13} />}
                    sx={{ typography: "s3", fontWeight: 700, color: "text.primary", minWidth: 0, flexShrink: 0 }}
                  >
                    Review
                  </Button>
                </Stack>
              ))}
            </Box>
            {fixLines.map((f) => (
              <Stack key={f.ruleId} direction="row" spacing={1} alignItems="center">
                <Iconify icon="solar:check-read-linear" width={14} sx={{ color: "text.subtitle", flexShrink: 0 }} />
                <Typography sx={{ typography: "s3", color: "text.secondary" }}>{likelyFixLine(f)}</Typography>
              </Stack>
            ))}
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              None of this blocks a run. An environment changes only when someone rebuilds it there.
            </Typography>
          </Stack>
        )}

        {phase !== "checking" && (
          <Stack direction="row" justifyContent="flex-end" spacing={1} sx={{ mt: 2.5 }}>
            {/* Saved from the agent page: following environments already test
                it — this is where to see which, and move a pinned one. */}
            {phase === "saved" && !inEnvironment && usedInCount > 0 && onOpenEnvironments && (
              <Button
                variant="outlined" size="small" onClick={() => { onClose(); onOpenEnvironments(); }}
                sx={{ typography: "s2", fontWeight: 700, color: "text.primary", borderColor: "divider" }}
              >
                Review environments
              </Button>
            )}
            {(phase === "upToDate" || phase === "saved") && (
              <Button variant="contained" color="primary" size="small" onClick={onClose} sx={{ typography: "s2", fontWeight: 700 }}>Done</Button>
            )}
            {phase === "changes" && (
              <>
                <Button size="small" onClick={onClose} sx={{ typography: "s2", fontWeight: 700, color: "text.secondary" }}>Cancel</Button>
                {inEnvironment && (
                  <Button
                    variant="outlined" size="small" onClick={() => save(false)}
                    sx={{ typography: "s2", fontWeight: 700, color: "text.primary", borderColor: "divider" }}
                  >
                    {`Save, stay on ${runningLabel}`}
                  </Button>
                )}
                <Button variant="contained" color="primary" size="small" onClick={() => save()} sx={{ typography: "s2", fontWeight: 700 }}>
                  {inEnvironment ? `Save and test ${result.label} here` : `Save as ${result.label}`}
                </Button>
              </>
            )}
          </Stack>
        )}
      </Box>
    </Dialog>
  );
}

AgentSyncDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  agent: PropTypes.object,
  versions: PropTypes.array.isRequired,
  env: PropTypes.object,
  baseTools: PropTypes.array,
  onSave: PropTypes.func,
  runningLabel: PropTypes.string,
  onRunHere: PropTypes.func,
  /* On the agent page: opens its Environments tab, where each one switches. */
  onOpenEnvironments: PropTypes.func,
  usedInCount: PropTypes.number,
  /* Accepted for older callers; the add nudge now says what the world lacks. */
  worldTools: PropTypes.array,
  envVersionLabel: PropTypes.string,
};
