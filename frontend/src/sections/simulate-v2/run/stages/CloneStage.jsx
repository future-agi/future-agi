import PropTypes from "prop-types";
import { useMemo, useState, useEffect } from "react";
import { alpha, keyframes } from "@mui/material/styles";
import { Box, Stack, Typography, Chip } from "@mui/material";
import Iconify from "src/components/iconify";
import { twinById } from "../../_mock/twins";
import TwinLogo from "../../components/TwinLogo";
import SlackSandboxMock from "../../twins/SlackSandboxMock";
import NotionSandboxMock from "../../twins/NotionSandboxMock";
import GmailSandboxMock from "../../twins/GmailSandboxMock";
import SalesforceSandboxMock from "../../twins/SalesforceSandboxMock";
import GenericSandboxMock from "../../twins/GenericSandboxMock";

const SANDBOX_MOCKS = {
  slack: SlackSandboxMock,
  notion: NotionSandboxMock,
  gmail: GmailSandboxMock,
  salesforce: SalesforceSandboxMock,
};

const CLONE_TINT = "#7857FC";

const pulse = keyframes`
  0%,100% { box-shadow: 0 0 0 2px rgba(120,87,252,0.25); }
  50%     { box-shadow: 0 0 0 8px rgba(120,87,252,0.08); }
`;

const eventIn = keyframes`
  from { opacity: 0; transform: translateY(4px); }
  to   { opacity: 1; transform: translateY(0); }
`;

/**
 * Live stage for a clone-backed run.
 *
 * The persona/agent chat is a poor fit for clone envs because the
 * agent's real work is writes against Slack/Notion/Salesforce, not a
 * spoken conversation. This stage puts the actual sandbox in the
 * center — the mock that matches whichever twin the agent is
 * currently touching — with a live activity feed underneath showing
 * every write as it lands, one row per step, and a tabbed switcher
 * across the top when the env has multiple twins.
 *
 * A synthetic envState (with the in-flight run injected) is handed to
 * each sandbox mock so `liveSandboxContentFor` picks up this run's
 * writes and renders them alongside seeded content.
 */
export default function CloneStage({ task, stepIndex, twinBacking }) {
  const services = twinBacking?.services || [];
  /* Build one event per run step from the task's own subTasks —
     the chat-canned steps rarely match twinTimelineFor's regexes,
     but every scenario carries an explicit subTasks list that IS
     the agent's plan. Each visible step is one write against the
     task's primary twinService (or a cycle across services when
     none is declared). */
  const events = useMemo(
    () => eventsFromTask(task, services),
    [task, services],
  );
  const visibleEvents = events.slice(0, Math.max(0, stepIndex + 1));

  /* Which service the agent is touching *right now*. Tracks the most
     recent visible event so the tab auto-follows the agent. Falls
     back to the first service before any writes land. */
  const currentService = visibleEvents[visibleEvents.length - 1]?.service || services[0];
  const [activeService, setActiveService] = useState(currentService);
  useEffect(() => {
    if (currentService && currentService !== activeService) setActiveService(currentService);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentService]);

  /* Feed a synthetic envState to the SandboxMocks — it has this run
     pre-committed so the mock's liveSandboxContentFor sees the writes
     already landed and renders them into the surface. */
  const syntheticEnvState = useMemo(() => syntheticStateFor(twinBacking, task, visibleEvents), [twinBacking, task, visibleEvents]);

  const activeTwin = twinById(activeService);
  const SandboxMock = SANDBOX_MOCKS[activeService];
  const writesForActive = visibleEvents.filter((e) => e.service === activeService);

  return (
    <Box sx={{
      flex: 1, minHeight: 0, display: "flex", flexDirection: "column",
      bgcolor: "background.neutral",
    }}>
      {/* service switcher */}
      {services.length > 1 && (
        <Stack direction="row" spacing={0.75} sx={{
          px: 2.5, py: 1.25, borderBottom: "1px solid", borderColor: "divider",
          bgcolor: "background.paper", flexWrap: "wrap", useFlexGap: true,
        }}>
          {services.map((sId) => {
            const twin = twinById(sId);
            const on = sId === activeService;
            const writes = visibleEvents.filter((e) => e.service === sId).length;
            const isCurrent = sId === currentService;
            return (
              <Chip
                key={sId} size="small"
                onClick={() => setActiveService(sId)}
                icon={<TwinLogo twin={twin} width={13} sx={{ ml: "6px !important" }} />}
                label={
                  <Stack direction="row" alignItems="center" spacing={0.5}>
                    <span>{twin?.name || sId}</span>
                    {writes > 0 && (
                      <Typography sx={{
                        typography: "s3", fontWeight: 700, color: "inherit", opacity: 0.7,
                        fontVariantNumeric: "tabular-nums",
                      }}>
                        {writes}
                      </Typography>
                    )}
                  </Stack>
                }
                sx={{
                  height: 26, borderRadius: 999, cursor: "pointer",
                  border: "1px solid",
                  borderColor: (th) => on
                    ? alpha(th.palette.text.primary, th.palette.mode === "dark" ? 0.5 : 0.35)
                    : th.palette.divider,
                  bgcolor: (th) => on
                    ? alpha(th.palette.text.primary, th.palette.mode === "dark" ? 0.14 : 0.06)
                    : "background.paper",
                  color: "text.primary",
                  animation: isCurrent ? `${pulse} 1.6s ease-in-out infinite` : "none",
                  "& .MuiChip-label": {
                    pl: 0.5, pr: 1, typography: "s2", fontWeight: 700,
                    color: "text.primary",
                  },
                  "&:hover": {
                    bgcolor: (th) => on
                      ? alpha(th.palette.text.primary, th.palette.mode === "dark" ? 0.18 : 0.08)
                      : alpha(th.palette.text.primary, th.palette.mode === "dark" ? 0.06 : 0.04),
                  },
                }}
              />
            );
          })}
        </Stack>
      )}

      {/* active sandbox */}
      <Box sx={{ flex: 1, minHeight: 0, overflow: "auto", p: 2 }}>
        <Stack direction="row" alignItems="center" spacing={1.25} sx={{ mb: 1.25 }}>
          <TwinLogo twin={activeTwin} width={18} />
          <Typography sx={{ typography: "s1_2", fontWeight: 700 }}>
            {activeTwin?.name || "Sandbox"}
          </Typography>
          <Typography sx={{
            typography: "s3", color: "text.subtitle",
            fontFamily: "ui-monospace, Menlo, monospace",
          }} noWrap>
            {twinBacking?.endpoints?.[activeService]?.replace(/^https?:\/\//, "")}
          </Typography>
          <Box flex={1} />
          {writesForActive.length > 0 && (
            <Typography sx={{
              typography: "s3", fontWeight: 700, color: "text.subtitle",
              textTransform: "uppercase", letterSpacing: 0.3,
            }}>
              {writesForActive.length} write{writesForActive.length === 1 ? "" : "s"} this run
            </Typography>
          )}
        </Stack>

        {SandboxMock
          ? <SandboxMock workspace="Default Workspace" envState={syntheticEnvState} />
          : <GenericSandboxMock twin={activeTwin} />}

        {/* activity feed — every event so far, most recent first */}
        <Box sx={{ mt: 2.5 }}>
          <Typography sx={{
            typography: "s3", fontWeight: 700, color: "text.subtitle",
            textTransform: "uppercase", letterSpacing: 0.4, mb: 1,
          }}>
            Activity · agent writes as they land
          </Typography>
          {visibleEvents.length === 0 ? (
            <Box sx={{
              p: 2, borderRadius: 1, border: "1px dashed",
              borderColor: "divider", textAlign: "center",
            }}>
              <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
                Waiting for the agent&apos;s first write…
              </Typography>
            </Box>
          ) : (
            <Stack spacing={0.5}>
              {[...visibleEvents].reverse().map((e, idx) => (
                <ActivityRow key={`${e.turn}-${idx}`} event={e} latest={idx === 0} />
              ))}
            </Stack>
          )}
        </Box>
      </Box>
    </Box>
  );
}
CloneStage.propTypes = {
  task: PropTypes.object,
  stepIndex: PropTypes.number,
  live: PropTypes.bool,
  twinBacking: PropTypes.object,
};

/* ── bits ────────────────────────────────────────────────────────────── */

function ActivityRow({ event, latest }) {
  const twin = twinById(event.service);
  return (
    <Stack direction="row" alignItems="center" spacing={1.25}
      sx={{
        px: 1.25, py: 1, borderRadius: 0.875,
        border: "1px solid", borderColor: latest ? alpha(CLONE_TINT, 0.35) : "divider",
        bgcolor: (t) => latest
          ? alpha(CLONE_TINT, t.palette.mode === "dark" ? 0.1 : 0.05)
          : "background.paper",
        animation: latest ? `${eventIn} 240ms ease-out` : "none",
      }}
    >
      <TwinLogo twin={twin} width={14} />
      <Typography sx={{
        typography: "s3", fontWeight: 700, minWidth: 60,
        fontVariantNumeric: "tabular-nums", color: "text.subtitle",
      }}>
        T{event.turn + 1}
      </Typography>
      <Iconify
        icon={event.isWrite ? "solar:pen-linear" : "solar:eye-linear"}
        width={12}
        sx={{ color: event.isWrite ? "#16A34A" : "text.subtitle", flexShrink: 0 }}
      />
      <Typography sx={{
        typography: "s3", fontWeight: 700, minWidth: 70, color: "text.subtitle",
        textTransform: "uppercase", letterSpacing: 0.3,
      }}>
        {event.isWrite ? "wrote" : "read"}
      </Typography>
      <Typography noWrap sx={{ typography: "s2", flex: 1, minWidth: 0 }}>
        {event.summary}
      </Typography>
      {event.target && (
        <Typography noWrap sx={{
          typography: "s3", color: "text.subtitle",
          fontFamily: "ui-monospace, Menlo, monospace", flexShrink: 0,
        }}>
          {event.target}
        </Typography>
      )}
    </Stack>
  );
}
ActivityRow.propTypes = { event: PropTypes.object, latest: PropTypes.bool };

/* ── helpers ─────────────────────────────────────────────────────────── */

/*
  Every scenario carries a `subTasks` list — that IS the agent's
  plan. Turn each visible step in the run into one activity event,
  attributing it to the task's primary twinService (or cycling
  across services when the task doesn't name one). Classifies
  read vs. write from action verbs in the subTask label so the
  activity feed can render a proper "wrote / read" pill.
*/
const WRITE_VERBS = /\b(reply|post|send|create|update|add|apply|comment|move|assign|refund|revoke|attach|log|open|forward|dm|label|draft|highlight|insert|accept|reject|decline|nudge|carry|resolve|classify|retry|note|route|welcome)\b/i;

function eventsFromTask(task, services) {
  if (!task) return [];
  const subTasks = task.subTasks || [];
  const steps = task.steps || [];
  const total = Math.max(steps.length, subTasks.length, 1);
  const primaryService = task.twinService || services[0];

  /* Which twins are in play — used to detect service mentions in
     subTask labels ("Search Slack for X" → slack). Falls back to
     twinService or cycling when no service is named in the label. */
  const catalog = services.map((sId) => ({ id: sId, twin: twinById(sId) }));

  return Array.from({ length: total }, (_, i) => {
    const sub = subTasks[Math.min(i, Math.max(0, subTasks.length - 1))];
    const label = sub?.label || steps[i]?.text || "acting on the clone";
    /*
      Attribution order:
        1. Named service in the label ("Search Slack …" → slack)
        2. Task's declared twinService (single-service scenarios)
        3. Cycle across the env's services (fallback for cross-service
           combos that didn't declare per-step attribution)
    */
    const namedService = catalog.find(({ id, twin }) => {
      const nameHit = twin?.name && new RegExp(`\\b${escapeReg(twin.name)}\\b`, "i").test(label);
      const idHit = new RegExp(`\\b${escapeReg(id)}\\b`, "i").test(label);
      return nameHit || idHit;
    })?.id;
    const service = namedService
      || task.twinService
      || services[i % Math.max(services.length, 1)]
      || primaryService;
    const isWrite = WRITE_VERBS.test(label);
    return {
      turn: i,
      service,
      isWrite,
      summary: label,
      target: extractTarget(label),
    };
  });
}

function escapeReg(s) {
  return String(s).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/*
  Try to pluck a #channel, @user, or capitalised noun from the
  subTask label so the activity row shows something concrete on the
  right (e.g. "#support-urgent" or "Acme"). Falls back to null.
*/
function extractTarget(label) {
  if (!label) return null;
  const chan = label.match(/#[\w-]+/);
  if (chan) return chan[0];
  const at = label.match(/@[\w-]+/);
  if (at) return at[0];
  return null;
}

/*
  liveSandboxContentFor (which the SandboxMocks call) reads
  envState.runs and derives sample chat/DB rows per run. Passing our
  synthesised in-flight run through it makes the mock automatically
  render the visible events into the surface.
*/
function syntheticStateFor(twinBacking, task, visibleEvents) {
  return {
    twinBacking: {
      services: twinBacking?.services || [],
      seed: twinBacking?.seed || {},
      endpoints: twinBacking?.endpoints || {},
      activity: twinBacking?.activity || {},
      provisionedAt: twinBacking?.provisionedAt || new Date(0).toISOString(),
    },
    /* We stack one "run" per event so far — liveSandboxContentFor
       picks the templated row for each and appends it into the
       mock's surface content. */
    runs: visibleEvents.map((e, i) => ({
      id: `evt-${e.turn}-${i}`,
      label: `Step ${i + 1}`,
      finishedAt: new Date().toISOString(),
    })),
  };
}
