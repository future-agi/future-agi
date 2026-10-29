import PropTypes from "prop-types";
import React, { useMemo, useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box,
  Stack,
  Typography,
  IconButton,
  Tooltip,
  TextField,
  InputAdornment,
  Tab,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { SegmentedTabs, CustomTabs } from "src/components/tabs/tabs";
import { effectiveModality } from "../_mock/rlContract";
import { getSurface } from "../_mock/surfaces";
import { browserAppOf } from "../_mock/runStream";
import BrowserApp, { deriveState, focusOf, urlFor } from "./stages/BrowserApps";
import { callDetail } from "../_mock/callDetail";
import { subGoalResultsOf, rollupLine } from "../_mock/scenarioSubGoals";
import CallGraph from "./CallGraph";
import TwinStateTimeline from "./TwinStateTimeline";
import TwinRequestStream from "./TwinRequestStream";
import EvalsTabView from "src/components/traceDetail/EvalsTabView";
import { localizeEval } from "../_mock/errorLocalization";
import TraceWaterfall from "./TraceWaterfall";
import { VERDICT_CAUSES } from "../_mock/decoupled";
import { STALE_LABEL, verdictLineOf } from "../_mock/runCauses";
import { textTone } from "../components/textTone";
import { clickable, focusRing } from "../components/clickable";
import HandoffTimeline from "./HandoffTimeline";
import { membersOf } from "../_mock/improvementPolicy";

/**
 * One call, in full.
 *
 * Two panes, because a call has two kinds of question. The left is the
 * artifact: what was said, in order, with the silences that shaped it. The
 * right is the measurement: the channel numbers, the graders' verdicts, the
 * raw messages behind the transcript.
 *
 * Built here rather than mounted from the call-logs drawer. That drawer's
 * `LeftSection` / `RightSection` take a `data` object shaped around a real
 * provider call — recordings, provider latencies, span ids — and a simulated
 * run has none of it, so they render their own empty states instead of our
 * content. Matching that shape would mean fabricating provider data, which is
 * worse than owning the two panes.
 *
 * Modality decides the left pane. A voice run produced a call, so it gets a
 * recording and a talk ratio; a chat run produced a transcript with neither,
 * and a dead audio player there would misrepresent what was captured.
 * Resolved through `effectiveModality` so it tracks the connected agent.
 */
const ROLE_LABEL = { agent: "Assistant", customer: "Customer" };

/*
  What the run produced, and therefore what to call it.

  A browser agent did not make a call, and labelling its trace "Call ID" with an
  empty transcript underneath is the drawer telling you it does not know what it
  is looking at. The surface decides the noun, the left pane and which of these
  numbers are even measurable.
*/
const SURFACE_COPY = {
  voice: { id: "Call ID", analytics: "Call analytics", log: "Call log" },
  chat: {
    id: "Conversation ID",
    analytics: "Chat analytics",
    log: "Tool calls",
  },
  browser: {
    id: "Session ID",
    analytics: "Session analytics",
    log: "Tool calls",
  },
  terminal: {
    id: "Session ID",
    analytics: "Shell analytics",
    log: "Tool calls",
  },
  tools: { id: "Trace ID", analytics: "Trace analytics", log: "Tool calls" },
  email: { id: "Thread ID", analytics: "Thread analytics", log: "Tool calls" },
  sim: {
    id: "Conversation ID",
    analytics: "Conversation analytics",
    log: "Tool calls",
  },
};

export default function CallDrawer({
  task,
  env,
  envState,
  /* Opened from the Cost tab: the trace leads with what the calls cost. */
  showCost = false,
  focus,
  focusEvalName,
  initialPane,
  onClose,
  onPrev,
  onNext,
  verdict = null,
}) {
  /* Opened from a diagnosis that named a step, so open on the trajectory with
     that step lit rather than on the transcript with the reader hunting.
     Opened from Insights evidence, it lands on the trace, pinned to the span
     where the scenario went wrong. */
  const [pane, setPane] = useState(
    initialPane || (focus ? "graph" : "transcript"),
  );
  const [side, setSide] = useState(focusEvalName ? "evals" : "analytics");
  /* Same row shape the voice drawer feeds EvalsTabView, so both drawers show
     the explanation + error localization identically. */
  const evalRows = useMemo(
    () =>
      (task.evalResults || []).map((r) => {
        const loc = localizeEval(task, r);
        return {
          id: r.id,
          eval_name: r.name,
          score: Math.round((r.score ?? 0) * 100),
          passed: !!r.passed,
          explanation: r.reason,
          ...(loc
            ? {
                error_analysis: loc.error_analysis,
                error_localizer_status: loc.error_localizer_status,
                selected_input_key: loc.selected_input_key,
                datapoint: {
                  selected_input_key: loc.selected_input_key,
                  input_data: loc.input_data,
                  input_types: loc.input_types,
                },
              }
            : {}),
        };
      }),
    [task],
  );
  const [roleFilter, setRoleFilter] = useState("all");
  const [query, setQuery] = useState("");

  const voice = effectiveModality(env, envState) === "voice";
  const surface = getSurface(env?.surface);
  const stage = surface.stage;
  /* Voice and chat produced a conversation; everything else produced a trace of
     actions, and those two want completely different left panes. */
  const conversational = stage === "voice" || stage === "chat";
  const copy = SURFACE_COPY[stage] || SURFACE_COPY.tools;
  const [stepIndex, setStepIndex] = useState(null);
  /* The trajectory, the checklist and the read — built the same way the
     comparison panel builds them, from the run that already happened. */
  const detail = useMemo(
    () => callDetail({ env, envState, run: null, task, scenario: task }),
    [env, envState, task],
  );

  const log = task.callLog || {
    calls: [],
    missing: [],
    unsupportedClaim: null,
  };
  const turns = useMemo(() => task.steps || [], [task.steps]);
  const shown = turns.filter((s) => {
    if (roleFilter !== "all" && s.role !== roleFilter) return false;
    if (query && !s.text?.toLowerCase().includes(query.toLowerCase()))
      return false;
    return true;
  });

  const words = turns.reduce(
    (a, s) => a + (s.text?.split(/\s+/).length || 0),
    0,
  );
  /* How the run was spent, in the verbs this surface actually has. */
  const actionTally = useMemo(() => {
    const counts = {};
    turns.forEach((t) => {
      const verb = t.action || t.tool || t.kind || (t.cmd ? "command" : null);
      if (verb) counts[verb] = (counts[verb] || 0) + 1;
    });
    return Object.entries(counts)
      .map(([action, count]) => ({ action, count }))
      .slice(0, 6);
  }, [turns]);
  const failed = (task.evalResults || []).filter((r) => !r.passed);
  const agentTurns = turns.filter((s) => s.role === "agent").length;
  const talk = turns.length
    ? Math.round((agentTurns / turns.length) * 100)
    : 50;

  return (
    <Stack sx={{ height: "100%" }}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{
          px: 2,
          py: 1.25,
          borderBottom: "1px solid",
          borderColor: "divider",
          flexShrink: 0,
        }}
      >
        <Typography noWrap sx={{ typography: "s2", color: "text.secondary" }}>
          {copy.id} :{" "}
          <Box component="span" sx={{ color: "text.primary" }}>
            {task.id}
          </Box>
        </Typography>
        <Tooltip arrow title={`Copy ${copy.id.toLowerCase()}`}>
          <IconButton
            size="small"
            onClick={() => navigator.clipboard?.writeText(task.id)}
          >
            <Iconify
              icon="solar:copy-linear"
              width={14}
              sx={{ color: "text.tertiary" }}
            />
          </IconButton>
        </Tooltip>
        <VerdictCauseChip verdict={verdict || task.verdictCause} task={task} />
        <Box flex={1} />
        <IconButton size="small" onClick={onPrev} disabled={!onPrev}>
          <Iconify
            icon="solar:alt-arrow-up-linear"
            width={16}
            sx={{ color: "text.tertiary" }}
          />
        </IconButton>
        <IconButton size="small" onClick={onNext} disabled={!onNext}>
          <Iconify
            icon="solar:alt-arrow-down-linear"
            width={16}
            sx={{ color: "text.tertiary" }}
          />
        </IconButton>
        <IconButton size="small" onClick={onClose}>
          <Iconify
            icon="solar:close-linear"
            width={16}
            sx={{ color: "text.tertiary" }}
          />
        </IconButton>
      </Stack>

      <Stack
        direction={{ xs: "column", md: "row" }}
        sx={{ flex: 1, minHeight: 0 }}
      >
        {/* ── left: the artifact ── */}
        <Stack
          sx={{
            flex: 1.15,
            minWidth: 0,
            borderRight: { md: "1px solid" },
            borderColor: { md: "divider" },
          }}
        >
          <Stack
            direction="row"
            alignItems="center"
            spacing={1}
            sx={{ px: 2, pt: 1.5 }}
          >
            <Iconify
              icon={surface.icon}
              width={16}
              sx={{ color: "text.tertiary" }}
            />
            <Typography sx={{ typography: "s2", fontWeight: 600 }}>
              {surface.label}
            </Typography>
          </Stack>

          <Box
            sx={{ px: 1, borderBottom: "1px solid", borderColor: "divider" }}
          >
            <CustomTabs value={pane} onChange={(_, v) => setPane(v)}>
              <Tab
                value="transcript"
                label={conversational ? "Transcript" : "Actions"}
              />
              <Tab value="checklist" label="Checklist" />
              <Tab value="graph" label="Graph" />
              <Tab value="trace" label="Trace" />
              {membersOf(envState?.agent).length > 1 && (
                <Tab value="handoffs" label="Handoffs" />
              )}
              {envState?.twinBacking && (
                <Tab value="twin" label="World state" />
              )}
              {envState?.twinBacking && (
                <Tab value="requests" label="Raw requests" />
              )}
            </CustomTabs>
          </Box>

          <Box sx={{ flex: 1, minHeight: 0, overflow: "auto" }}>
            {pane === "handoffs" && (
              <HandoffTimeline agent={envState?.agent} task={task} />
            )}
            {/* A run that produced actions rather than speech gets the actions,
                and — where the surface has one — the screen they happened on. */}
            {pane === "transcript" && !conversational && (
              <ActionTrace
                task={task}
                stage={stage}
                at={stepIndex}
                onScrub={setStepIndex}
              />
            )}

            {pane === "transcript" && conversational && (
              <>
                {voice && <Recording turns={turns.length} />}
                {voice && <TalkRatio agent={talk} />}

                <Stack
                  direction="row"
                  alignItems="center"
                  spacing={1}
                  sx={{ px: 2, py: 1.25 }}
                >
                  <TextField
                    size="small"
                    fullWidth
                    placeholder="Search transcript"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    InputProps={{
                      sx: { typography: "s2" },
                      startAdornment: (
                        <InputAdornment position="start">
                          <Iconify
                            icon="solar:magnifer-linear"
                            width={14}
                            sx={{ color: "text.tertiary" }}
                          />
                        </InputAdornment>
                      ),
                    }}
                  />
                  <SegmentedTabs
                    value={roleFilter}
                    onChange={(_, v) => setRoleFilter(v)}
                    sx={{ flexShrink: 0 }}
                  >
                    <Tab value="all" label="All" />
                    <Tab value="agent" label="Assistant" />
                    <Tab value="customer" label="Customer" />
                  </SegmentedTabs>
                </Stack>

                <Stack sx={{ px: 2, pb: 2 }}>
                  {shown.map((s, i) => (
                    <React.Fragment key={s.id || i}>
                      <Box
                        sx={{
                          borderLeft: "2px solid",
                          borderColor: (t) =>
                            s.role === "agent"
                              ? t.palette.primary.main
                              : t.palette.text.disabled,
                          bgcolor:
                            s.role === "agent"
                              ? "status.discovery.bg"
                              : "background.neutral",
                          px: 1.75,
                          py: 1.25,
                        }}
                      >
                        <Typography
                          sx={{
                            typography: "s3",
                            color: "text.tertiary",
                            fontFamily: "ui-monospace, Menlo, monospace",
                          }}
                        >
                          {`0:${String(i).padStart(2, "0")}`} ·{" "}
                          {ROLE_LABEL[s.role] || s.role}
                        </Typography>
                        <Typography sx={{ typography: "s1", mt: 0.25 }}>
                          {s.text}
                        </Typography>
                      </Box>
                      {/* Dead air is part of a voice call's state, so it is a
                          row in the transcript rather than a gap between rows. */}
                      {voice && i < shown.length - 1 && (
                        <Stack
                          direction="row"
                          alignItems="center"
                          spacing={0.75}
                          sx={{ px: 1.75, py: 0.5 }}
                        >
                          <Iconify
                            icon="solar:hourglass-line-linear"
                            width={12}
                            sx={{ color: "text.disabled" }}
                          />
                          <Typography
                            sx={{
                              typography: "s3",
                              color: "text.disabled",
                              fontFamily: "ui-monospace, Menlo, monospace",
                            }}
                          >
                            1.0s silence
                          </Typography>
                          <Box
                            sx={{
                              flex: 1,
                              borderBottom: "1px dashed",
                              borderColor: "divider",
                            }}
                          />
                        </Stack>
                      )}
                    </React.Fragment>
                  ))}
                </Stack>
              </>
            )}

            {/* The contract already states what this scenario has to settle, so
                the checklist is real rather than annotated after the fact. */}
            {/* The same checklist and the same trajectory the comparison panel
                draws — one run rather than three, but nothing less detailed:
                a drawer that reports "3 checks" while the compare view shows
                which step was skipped is two products. */}
            {pane === "checklist" && (
              <Stack sx={{ p: 2 }} spacing={1}>
                {/* The scenario's sub-goals first — each critical or
                    supporting, and the verdict they roll up to — then the
                    reference path step by step. */}
                <SubGoalRollup task={task} env={env} envState={envState} />
                <Typography
                  sx={{
                    typography: "s3",
                    fontWeight: 600,
                    color: "text.tertiary",
                    textTransform: "none",
                    letterSpacing: 0.4,
                    pt: 1,
                  }}
                >
                  Reference path
                </Typography>
                <Stack
                  direction="row"
                  alignItems="center"
                  spacing={1.5}
                  sx={{ pb: 0.5 }}
                >
                  <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                    {detail.checklist.pct}% ·{" "}
                    {detail.checklist.pass + detail.checklist.partial}/
                    {detail.checklist.steps.length} steps
                  </Typography>
                  <Typography
                    sx={{ typography: "s3", color: "status.success.fg" }}
                  >
                    {detail.checklist.pass} pass
                  </Typography>
                  <Typography
                    sx={{ typography: "s3", color: "text.secondary" }}
                  >
                    {detail.checklist.partial} partial
                  </Typography>
                  <Typography
                    sx={{ typography: "s3", color: "status.danger.fg" }}
                  >
                    {detail.checklist.missed} missed
                  </Typography>
                </Stack>
                {detail.checklist.steps.map((c) => {
                  const tone =
                    c.status === "addressed"
                      ? "status.success.fg"
                      : c.status === "partial"
                        ? "text.secondary"
                        : "status.danger.fg";
                  return (
                    <Stack
                      key={c.id}
                      direction="row"
                      spacing={1.25}
                      alignItems="flex-start"
                      sx={{
                        p: 1.5,
                        border: "1px solid",
                        borderColor: "divider",
                        borderRadius: 1,
                      }}
                    >
                      <Iconify
                        icon={
                          c.status === "addressed"
                            ? "solar:check-circle-bold"
                            : c.status === "partial"
                              ? "solar:info-circle-bold"
                              : "solar:close-circle-bold"
                        }
                        width={16}
                        sx={{ color: tone, flexShrink: 0, mt: "1px" }}
                      />
                      <Box flex={1} minWidth={0}>
                        <Stack direction="row" alignItems="center" spacing={1}>
                          <Typography
                            sx={{
                              typography: "s2",
                              fontWeight: 600,
                              fontFamily: "ui-monospace, Menlo, monospace",
                            }}
                          >
                            {c.name}
                          </Typography>
                          <Typography
                            sx={{
                              typography: "s3",
                              fontWeight: 600,
                              color: tone,
                              textTransform: "none",
                            }}
                          >
                            {c.status}
                          </Typography>
                        </Stack>
                        <Typography
                          sx={{
                            typography: "s2",
                            color: "text.secondary",
                            fontStyle: "italic",
                          }}
                        >
                          &ldquo;{c.expectation}&rdquo;
                        </Typography>
                        {!c.evidence && c.status !== "addressed" && (
                          <Typography
                            sx={{
                              typography: "s3",
                              color: "text.disabled",
                              mt: 0.25,
                            }}
                          >
                            No matching step — the agent never touched this.
                          </Typography>
                        )}
                      </Box>
                    </Stack>
                  );
                })}
              </Stack>
            )}

            {/* Session → turns → LLM, tool and guard spans on one time axis. */}
            {pane === "trace" && (
              <TraceWaterfall
                task={task}
                env={env}
                envState={envState}
                showCost={showCost}
              />
            )}

            {pane === "twin" && envState?.twinBacking && (
              <TwinStateTimeline envState={envState} task={task} />
            )}

            {pane === "requests" && envState?.twinBacking && (
              <TwinRequestStream envState={envState} task={task} />
            )}

            {pane === "graph" && (
              <Box sx={{ p: 2 }}>
                <CallGraph
                  spine={detail.graph.spine}
                  branches={detail.graph.branches}
                  focus={focus}
                />
                <Box
                  sx={{
                    mt: 1.5,
                    p: 1.5,
                    borderRadius: 1,
                    border: "1px solid",
                    borderColor: "divider",
                  }}
                >
                  <Typography
                    sx={{
                      typography: "s3",
                      fontWeight: 600,
                      color: "text.tertiary",
                      textTransform: "none",
                      letterSpacing: 0.4,
                      mb: 0.5,
                    }}
                  >
                    Falcon analysis
                  </Typography>
                  <Typography
                    sx={{ typography: "s2", color: "text.secondary" }}
                  >
                    {detail.analysis}
                  </Typography>
                </Box>
              </Box>
            )}
          </Box>
        </Stack>

        {/* ── right: the measurement ── */}
        <Stack sx={{ flex: 1, minWidth: 0 }}>
          <Box sx={{ px: 2, pt: 1.75, pb: 1.25 }}>
            <Stack direction="row" flexWrap="wrap" gap={0.75}>
              <Meta
                label="Type"
                value={env?.direction === "inbound" ? "Inbound" : "Outbound"}
              />
              <Meta
                label="Status"
                value={task.status === "passed" ? "completed" : task.status}
              />
              <Meta
                label="Duration"
                value={`${((task.durationMs || 0) / 1000).toFixed(1)}s`}
              />
              <Meta label="Turns" value={turns.length} />
              <Meta label="Provider" value="future-agi-sandbox" />
            </Stack>
          </Box>

          {/* Ours before theirs: an unmeasured run gets its own banner, and the
              grader failures below it are not the agent's to answer for. */}
          {detail && !detail.measured && (
            <Stack
              direction="row"
              spacing={1.25}
              alignItems="flex-start"
              sx={{
                mx: 2,
                mb: 1.5,
                p: 1.5,
                borderRadius: 1,
                border: "1px solid",
                borderColor: alpha(detail.domain.color, 0.35),
                bgcolor: (t) =>
                  alpha(
                    detail.domain.color,
                    t.palette.mode === "dark" ? 0.1 : 0.05,
                  ),
              }}
            >
              <Iconify
                icon="solar:shield-cross-bold"
                width={16}
                sx={{ color: detail.domain.color, flexShrink: 0, mt: "1px" }}
              />
              <Box minWidth={0}>
                <Typography
                  sx={{
                    typography: "s2",
                    fontWeight: 600,
                    color: textTone(detail.domain.color),
                  }}
                >
                  {detail.domain.label} — this scenario produced no verdict
                </Typography>
                <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                  {detail.fault}
                </Typography>
                <Typography
                  sx={{ typography: "s3", color: "text.tertiary", mt: 0.25 }}
                >
                  {detail.domain.next}
                </Typography>
              </Box>
            </Stack>
          )}

          {detail?.measured && failed.length > 0 && (
            <Stack
              sx={{
                mx: 2,
                mb: 1.5,
                p: 1.5,
                borderRadius: 1,
                border: "1px solid",
                borderColor: "status.danger.border",
                bgcolor: "status.danger.bg",
              }}
              spacing={1}
            >
              {failed.map((r) => (
                <Stack
                  key={r.id}
                  direction="row"
                  spacing={1.25}
                  alignItems="flex-start"
                >
                  <Iconify
                    icon="solar:close-circle-bold"
                    width={16}
                    sx={{ color: "status.danger.fg", flexShrink: 0, mt: "1px" }}
                  />
                  <Box minWidth={0}>
                    <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                      {r.name} failed ({Math.round(r.score * 100)})
                    </Typography>
                    <Typography
                      sx={{ typography: "s2", color: "text.secondary" }}
                    >
                      {r.reason}
                    </Typography>
                  </Box>
                </Stack>
              ))}
            </Stack>
          )}

          <Box
            sx={{ px: 1, borderBottom: "1px solid", borderColor: "divider" }}
          >
            <CustomTabs
              value={side}
              onChange={(_, v) => setSide(v)}
              variant="scrollable"
              scrollButtons={false}
            >
              <Tab value="analytics" label={copy.analytics} />
              <Tab value="calls" label={`${copy.log} (${log.calls.length})`} />
              <Tab
                value="evals"
                label={`Evals (${(task.evalResults || []).length})`}
              />
              <Tab value="messages" label="Messages" />
              <Tab value="attributes" label="Attributes" />
            </CustomTabs>
          </Box>

          <Box sx={{ flex: 1, minHeight: 0, overflow: "auto" }}>
            {side === "analytics" && (
              <Box
                sx={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fill, minmax(120px, 1fr))",
                }}
              >
                {/* Only what this surface can actually be measured on. A dash in
                    every second cell is a drawer built for a different run. */}
                <Cell
                  label="Duration"
                  value={`${((task.durationMs || 0) / 1000).toFixed(1)}s`}
                />
                {conversational ? (
                  <>
                    <Cell label="Turns" value={turns.length} />
                    <Cell label="Words" value={words} />
                    <Cell label="Latency" value={voice ? "500ms" : "—"} />
                    <Cell
                      label={voice ? "Silence" : "Idle"}
                      value={voice ? `${(turns.length - 1).toFixed(2)}s` : "—"}
                    />
                    <Cell label="TTFW" value={voice ? "0ms" : "—"} />
                    <Cell
                      label="Talk ratio"
                      value={voice ? `${talk}/${100 - talk}` : "—"}
                    />
                  </>
                ) : (
                  <>
                    <Cell label="Steps" value={turns.length} />
                    <Cell
                      label="Avg step"
                      value={`${Math.round((task.durationMs || 0) / Math.max(1, turns.length))}ms`}
                    />
                    {actionTally.map((a) => (
                      <Cell key={a.action} label={a.action} value={a.count} />
                    ))}
                    <Cell label="Tool calls" value={log.calls.length} />
                    <Cell label="Never called" value={log.missing.length} />
                  </>
                )}
                <Cell
                  label="Tokens"
                  value={(task.tokens || 0).toLocaleString()}
                />
              </Box>
            )}

            {/*
              What the agent did to the world.

              The claim check leads, because it is the finding: the transcript
              can say a refund was issued while the log shows the tool was
              never called, and every grader that reads words will score that
              conversation as fine.
            */}
            {side === "calls" && (
              <Stack>
                {log.unsupportedClaim && (
                  <Stack
                    direction="row"
                    spacing={1.25}
                    alignItems="flex-start"
                    sx={{
                      m: 2,
                      p: 1.5,
                      borderRadius: 1,
                      border: "1px solid",
                      borderColor: "status.danger.border",
                      bgcolor: "status.danger.bg",
                    }}
                  >
                    <Iconify
                      icon="solar:danger-triangle-bold"
                      width={16}
                      sx={{
                        color: "status.danger.fg",
                        flexShrink: 0,
                        mt: "1px",
                      }}
                    />
                    <Box minWidth={0}>
                      <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                        Said, but not done
                      </Typography>
                      <Typography
                        sx={{ typography: "s2", color: "text.secondary" }}
                      >
                        {log.unsupportedClaim}
                      </Typography>
                    </Box>
                  </Stack>
                )}

                {log.calls.map((c) => (
                  <Stack
                    key={c.id}
                    direction="row"
                    spacing={1.5}
                    alignItems="flex-start"
                    sx={{
                      px: 2,
                      py: 1.5,
                      borderBottom: "1px solid",
                      borderColor: "divider",
                    }}
                  >
                    <Iconify
                      icon={
                        c.wrote
                          ? "solar:pen-new-square-linear"
                          : "solar:eye-linear"
                      }
                      width={16}
                      sx={{ color: "text.tertiary", flexShrink: 0, mt: "2px" }}
                    />
                    <Box flex={1} minWidth={0}>
                      <Typography
                        sx={{
                          typography: "s2",
                          fontWeight: 600,
                          fontFamily: "ui-monospace, Menlo, monospace",
                        }}
                      >
                        {c.name}(
                        {Object.entries(c.args)
                          .map(([k, v]) => `${k}: ${v}`)
                          .join(", ")}
                        )
                      </Typography>
                      <Typography
                        sx={{ typography: "s3", color: "text.tertiary" }}
                      >
                        {c.wrote ? "wrote to the world" : "read only"} ·
                        returned {c.rows} {c.rows === 1 ? "row" : "rows"} ·{" "}
                        {c.ms}ms
                      </Typography>
                    </Box>
                  </Stack>
                ))}

                {/* A tool the scenario needed and the agent never reached for.
                    Absence is the point, so it is a row rather than a gap. */}
                {log.missing.map((name) => (
                  <Stack
                    key={name}
                    direction="row"
                    spacing={1.5}
                    alignItems="flex-start"
                    sx={{
                      px: 2,
                      py: 1.5,
                      borderBottom: "1px solid",
                      borderColor: "divider",
                    }}
                  >
                    <Iconify
                      icon="solar:close-circle-bold"
                      width={16}
                      sx={{
                        color: "status.danger.fg",
                        flexShrink: 0,
                        mt: "2px",
                      }}
                    />
                    <Box flex={1} minWidth={0}>
                      <Typography
                        sx={{
                          typography: "s2",
                          fontWeight: 600,
                          fontFamily: "ui-monospace, Menlo, monospace",
                          color: "text.tertiary",
                        }}
                      >
                        {name}
                      </Typography>
                      <Typography
                        sx={{ typography: "s3", color: "status.danger.fg" }}
                      >
                        never called — this scenario needed it
                      </Typography>
                    </Box>
                  </Stack>
                ))}

                {log.calls.length === 0 && log.missing.length === 0 && (
                  <Typography
                    sx={{ p: 2, typography: "s2", color: "text.tertiary" }}
                  >
                    This scenario needed no tools — the agent only had to talk.
                  </Typography>
                )}
              </Stack>
            )}

            {side === "evals" && (
              <EvalsTabView
                evals={evalRows}
                emptyMessage="No evaluations for this run"
                showSpanColumn={false}
                focusEvalName={focusEvalName}
                hideRowFix
              />
            )}

            {side === "messages" && (
              <Box
                component="pre"
                sx={{
                  m: 0,
                  p: 2,
                  typography: "s3",
                  fontFamily: "ui-monospace, Menlo, monospace",
                  color: "text.secondary",
                  whiteSpace: "pre-wrap",
                }}
              >
                {JSON.stringify(
                  turns.map((s) => ({ role: s.role, content: s.text })),
                  null,
                  2,
                )}
              </Box>
            )}

            {side === "attributes" && (
              <Stack>
                <Attr label="scenario" value={task.title} />
                <Attr
                  label="persona"
                  value={`${task.persona?.name} · ${task.persona?.age} · ${task.persona?.voice}`}
                />
                <Attr
                  label="traits"
                  value={(task.persona?.traits || []).join(", ")}
                />
                <Attr label="expected" value={task.expected} />
                <Attr label="critical" value={String(!!task.critical)} />
              </Stack>
            )}
          </Box>
        </Stack>
      </Stack>
    </Stack>
  );
}

CallDrawer.propTypes = {
  showCost: PropTypes.bool,
  task: PropTypes.object,
  env: PropTypes.object,
  envState: PropTypes.object,
  focus: PropTypes.string,
  focusEvalName: PropTypes.string,
  initialPane: PropTypes.string,
  onClose: PropTypes.func,
  onPrev: PropTypes.func,
  onNext: PropTypes.func,
  verdict: PropTypes.object,
};

/*
  The scenario's outcome and its cause (PRD v7 §4.10.10, updated RSI PRD), in
  the drawer header: "Failed · agent", "Test invalid · simulator", "Failed a
  required evaluation · Policy adherence". Red only for a fail; what the
  cause means sits in the tooltip. A retry and a stale scenario trail it.
*/
function VerdictCauseChip({ verdict, task }) {
  if (!verdict) return null;
  const fail = !verdict.pulled && verdict.cause?.startsWith("fail-");
  const tip = verdict.pulled
    ? `${verdict.reason} Outside every count.`
    : VERDICT_CAUSES[verdict.cause]?.note || "";
  const label =
    verdict.cause === "fail-required" && !verdict.pulled
      ? verdictLineOf(verdict)
      : verdict.label;
  const retried = verdict.retried || task?.retried;
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={0.75}
      sx={{ minWidth: 0 }}
    >
      <Tooltip arrow title={tip || ""}>
        <Box
          sx={{
            px: 0.75,
            height: 22,
            display: "inline-flex",
            alignItems: "center",
            borderRadius: "4px",
            flexShrink: 0,
            border: "1px solid",
            borderStyle:
              verdict.cause?.startsWith("inc-") || verdict.pulled
                ? "dashed"
                : "solid",
            borderColor: fail ? "status.danger.border" : "divider",
            bgcolor: fail ? "status.danger.bg" : "transparent",
            color: fail ? "status.danger.fg" : "text.secondary",
          }}
        >
          <Typography
            noWrap
            sx={{ typography: "s3", fontWeight: 600, color: "inherit" }}
          >
            {label}
          </Typography>
        </Box>
      </Tooltip>
      {retried && (
        <Tooltip
          arrow
          title={`First try ended ${retried.kind === "error" ? "error" : "test invalid"} — ${String(retried.first || "").replace(/\.$/, "")}.`}
        >
          <Typography noWrap sx={{ typography: "s3", color: "text.tertiary" }}>
            {retried.recovered ? "Retried once" : "Retried once · same again"}
          </Typography>
        </Tooltip>
      )}
      {verdict.stale && (
        <Tooltip
          arrow
          title="It ran, but counts toward no decision until it's updated or archived."
        >
          <Typography
            noWrap
            sx={{ typography: "s3", color: "text.tertiary" }}
          >{`${STALE_LABEL[verdict.stale] || "Stale"} · not counted`}</Typography>
        </Tooltip>
      )}
      {task?.regraded?.changed && (
        <Typography
          noWrap
          sx={{ typography: "s3", color: "text.tertiary" }}
        >{`Re-graded · was ${task.regraded.from}`}</Typography>
      )}
    </Stack>
  );
}
VerdictCauseChip.propTypes = {
  verdict: PropTypes.object,
  task: PropTypes.object,
};

/**
 * The recording, stated as absent.
 *
 * A simulated call has no audio to play, and a player that looks live but does
 * nothing is worse than one that says so.
 */
function Recording({ turns }) {
  return (
    <Box sx={{ px: 2, pt: 1.5 }}>
      <Typography
        sx={{
          typography: "s3",
          fontWeight: 600,
          color: "text.tertiary",
          letterSpacing: 0.4,
          textTransform: "none",
          mb: 0.75,
        }}
      >
        Recording
      </Typography>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.25}
        sx={{ opacity: 0.45 }}
      >
        <Iconify
          icon="solar:play-bold"
          width={20}
          sx={{ color: "text.tertiary" }}
        />
        <Stack
          direction="row"
          alignItems="center"
          spacing={0.25}
          sx={{ flex: 1, height: 22 }}
        >
          {Array.from({ length: 64 }, (_, i) => (
            <Box
              key={i}
              sx={{
                flex: 1,
                height: 2,
                borderRadius: 1,
                bgcolor: "text.disabled",
              }}
            />
          ))}
        </Stack>
        <Typography
          sx={{
            typography: "s3",
            color: "text.tertiary",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          0:00 / 0:{String(turns * 4).padStart(2, "0")}
        </Typography>
      </Stack>
      <Typography sx={{ typography: "s3", color: "text.disabled", mt: 0.5 }}>
        No audio — this call was simulated, so the transcript is the recording.
      </Typography>
    </Box>
  );
}
Recording.propTypes = { turns: PropTypes.number };

function TalkRatio({ agent }) {
  return (
    <Box sx={{ px: 2, pt: 1.5 }}>
      <Typography
        sx={{
          typography: "s3",
          fontWeight: 600,
          color: "text.tertiary",
          letterSpacing: 0.4,
          textTransform: "none",
          mb: 0.75,
        }}
      >
        Talk ratio
      </Typography>
      <Stack
        direction="row"
        sx={{
          height: 8,
          borderRadius: 1,
          overflow: "hidden",
          bgcolor: "background.neutral",
        }}
      >
        <Box sx={{ width: `${agent}%`, bgcolor: "primary.main" }} />
        <Box sx={{ width: `${100 - agent}%`, bgcolor: "text.disabled" }} />
      </Stack>
      <Typography sx={{ typography: "s3", color: "text.tertiary", mt: 0.5 }}>
        {agent}% agent · {100 - agent}% customer
      </Typography>
    </Box>
  );
}
TalkRatio.propTypes = { agent: PropTypes.number };

/**
 * What the agent did, and the screen it did it on.
 *
 * A transcript view of a browser run renders a column of empty timestamps,
 * because there is nothing said — the artifact is a sequence of actions against
 * a page. So the actions are the list, each one showing its verb, its target,
 * the value it typed and the reason it gave; and for a browser the page itself
 * is rebuilt at the selected step, which is the only way to answer "did it
 * click the row it meant to".
 */
function ActionTrace({ task, stage, at, onScrub }) {
  const steps = task.steps || [];
  const index = at == null ? steps.length - 1 : Math.min(at, steps.length - 1);
  const shown = steps.slice(0, index + 1);

  const app = stage === "browser" ? browserAppOf(task) : null;
  const state = app ? deriveState(app, shown) : null;
  const focus = app ? focusOf(steps[index]) : null;

  const line = (st) => {
    if (st.action)
      return `${st.target || ""}${st.value ? ` = "${st.value}"` : ""}`;
    if (st.tool)
      return `${st.tool}(${Object.entries(st.args || {})
        .map(([k, v]) => `${k}: ${v}`)
        .join(", ")})`;
    if (st.cmd) return st.cmd;
    if (st.subject) return st.subject;
    return st.obs || "";
  };
  const verb = (st) =>
    st.action || st.tool || st.kind || (st.cmd ? "shell" : "step");
  const note = (st) => st.thought || st.note || st.result || st.out || "";

  return (
    <Box>
      {app && (
        <Box sx={{ px: 2, pt: 1.5 }}>
          <Box
            sx={{
              borderRadius: "8px",
              overflow: "hidden",
              border: "1px solid",
              borderColor: "divider",
              bgcolor: "background.paper",
            }}
          >
            <Stack
              direction="row"
              alignItems="center"
              spacing={1}
              sx={{
                px: 1.25,
                py: 0.75,
                borderBottom: "1px solid",
                borderColor: "divider",
                bgcolor: "background.neutral",
              }}
            >
              <Stack direction="row" spacing={0.5}>
                {/* eslint-disable-next-line no-restricted-syntax -- ds: data colour */}
                {["#FF5F57", "#FEBC2E", "#28C840"].map((c) => (
                  <Box
                    key={c}
                    sx={{
                      width: 8,
                      height: 8,
                      borderRadius: "50%",
                      bgcolor: c,
                      opacity: 0.85,
                    }}
                  />
                ))}
              </Stack>
              <Typography
                noWrap
                sx={{
                  typography: "s3",
                  color: "text.tertiary",
                  fontFamily: "ui-monospace, Menlo, monospace",
                }}
              >
                {urlFor(app, state)}
              </Typography>
            </Stack>
            <Box sx={{ position: "relative", height: 260 }}>
              <BrowserApp app={app} state={state} focus={focus} />
            </Box>
          </Box>
          <Typography
            sx={{ typography: "s3", color: "text.tertiary", mt: 0.75 }}
          >
            Step {index + 1} of {steps.length} — click a row to rewind the page
            to that point.
          </Typography>
        </Box>
      )}

      <Stack sx={{ p: 2 }} spacing={0.5}>
        {steps.map((st, i) => {
          const active = i === index;
          const future = i > index;
          return (
            <React.Fragment key={st.id || i}>
              <Stack
                direction="row"
                alignItems="flex-start"
                spacing={1.25}
                {...clickable(() => onScrub(i))}
                sx={{
                  px: 1.25,
                  py: 0.875,
                  borderRadius: 1,
                  cursor: "pointer",
                  border: "1px solid",
                  borderColor: active ? "primary.main" : "divider",
                  bgcolor: "transparent",
                  opacity: future ? 0.45 : 1,
                  "&:hover": { bgcolor: "action.hover" },
                  ...focusRing,
                }}
              >
                <Typography
                  sx={{
                    typography: "s3",
                    color: "text.disabled",
                    fontFamily: "ui-monospace, Menlo, monospace",
                    width: 22,
                    flexShrink: 0,
                  }}
                >
                  {String(i + 1).padStart(2, "0")}
                </Typography>
                <Box
                  sx={{
                    px: 0.75,
                    borderRadius: "4px",
                    flexShrink: 0,
                    bgcolor: "status.warning.bg",
                  }}
                >
                  <Typography
                    sx={{
                      typography: "s3",
                      fontWeight: 600,
                      color: "text.primary",
                      fontFamily: "ui-monospace, Menlo, monospace",
                    }}
                  >
                    {verb(st)}
                  </Typography>
                </Box>
                <Box flex={1} minWidth={0}>
                  <Typography
                    sx={{
                      typography: "s2",
                      fontFamily: "ui-monospace, Menlo, monospace",
                      wordBreak: "break-word",
                    }}
                  >
                    {line(st)}
                  </Typography>
                  {note(st) && (
                    <Typography
                      sx={{ typography: "s3", color: "text.tertiary" }}
                    >
                      {note(st)}
                    </Typography>
                  )}
                </Box>
                <Typography
                  sx={{
                    typography: "s3",
                    color: "text.disabled",
                    flexShrink: 0,
                  }}
                >
                  {((st.duration || 0) / 1000).toFixed(1)}s
                </Typography>
              </Stack>
            </React.Fragment>
          );
        })}
        {steps.length === 0 && (
          <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
            This task recorded no steps.
          </Typography>
        )}
      </Stack>
    </Box>
  );
}

ActionTrace.propTypes = {
  task: PropTypes.object,
  stage: PropTypes.string,
  at: PropTypes.number,
  onScrub: PropTypes.func,
};

function Meta({ label, value }) {
  return (
    <Stack
      direction="row"
      spacing={0.5}
      sx={{
        px: 1,
        py: 0.5,
        borderRadius: "4px",
        border: "1px solid",
        borderColor: "divider",
      }}
    >
      <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
        {label} :
      </Typography>
      <Typography
        sx={{ typography: "s3", color: "text.primary", fontWeight: 500 }}
      >
        {value}
      </Typography>
    </Stack>
  );
}
Meta.propTypes = { label: PropTypes.string, value: PropTypes.any };

function Cell({ label, value }) {
  return (
    <Box
      sx={{
        p: 1.75,
        borderBottom: "1px solid",
        borderRight: "1px solid",
        borderColor: "divider",
      }}
    >
      <Typography
        noWrap
        sx={{
          typography: "s3",
          fontWeight: 600,
          color: "text.tertiary",
          letterSpacing: 0.3,
          textTransform: "none",
        }}
      >
        {label}
      </Typography>
      <Typography
        sx={{
          typography: "m2",
          fontWeight: 600,
          fontFamily: "ui-monospace, Menlo, monospace",
          lineHeight: 1.3,
        }}
      >
        {value}
      </Typography>
    </Box>
  );
}
Cell.propTypes = { label: PropTypes.string, value: PropTypes.any };

function Attr({ label, value }) {
  return (
    <Stack
      direction="row"
      spacing={2}
      sx={{
        px: 2,
        py: 1.25,
        borderBottom: "1px solid",
        borderColor: "divider",
      }}
    >
      <Typography
        sx={{
          width: 96,
          flexShrink: 0,
          typography: "s3",
          color: "text.tertiary",
          fontFamily: "ui-monospace, Menlo, monospace",
        }}
      >
        {label}
      </Typography>
      <Typography sx={{ typography: "s2", color: "text.secondary" }}>
        {value || "—"}
      </Typography>
    </Stack>
  );
}
Attr.propTypes = { label: PropTypes.string, value: PropTypes.string };

/*
  The checklist's sub-goals: one failed critical sub-goal fails the scenario,
  an unsure critical one makes it inconclusive (never a pass), supporting
  misses are reported and don't fail it.
*/
const SUBGOAL_STATUS = {
  met: {
    icon: "solar:check-circle-linear",
    color: "status.success.fg",
    label: "Met",
  },
  failed: {
    icon: "solar:close-circle-linear",
    color: "status.danger.fg",
    label: "Failed",
  },
  inconclusive: {
    icon: "solar:question-circle-linear",
    color: "text.tertiary",
    label: "Inconclusive",
  },
};

function SubGoalRollup({ task, env, envState }) {
  const { items, rollup } = subGoalResultsOf(task, env, envState);
  if (!items.length) return null;
  return (
    <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1 }}>
      <Box
        sx={{
          px: 1.5,
          py: 1,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Typography
          sx={{
            typography: "s2",
            fontWeight: 600,
            color:
              rollup.verdict === "failed" ? "status.danger.fg" : "text.primary",
          }}
        >
          {rollupLine(rollup)}
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
          {`${items.filter((x) => x.status === "met").length} of ${items.length} sub-goals met · score ${rollup.score}`}
        </Typography>
      </Box>
      {items.map((x) => {
        const st = SUBGOAL_STATUS[x.status];
        return (
          <Stack
            key={x.key}
            direction="row"
            alignItems="center"
            spacing={1}
            sx={{
              px: 1.5,
              py: 0.75,
              "&:not(:last-of-type)": {
                borderBottom: "1px solid",
                borderColor: "divider",
              },
            }}
          >
            <Iconify
              icon={st.icon}
              width={14}
              sx={{ color: st.color, flexShrink: 0 }}
            />
            <Typography
              sx={{
                typography: "s2",
                color: "text.secondary",
                flex: 1,
                minWidth: 0,
              }}
            >
              {x.text}
            </Typography>
            <Typography
              sx={{
                typography: "s3",
                fontWeight: x.mark === "critical" ? 600 : 500,
                color: x.mark === "critical" ? "text.primary" : "text.tertiary",
                flexShrink: 0,
              }}
            >
              {x.mark === "critical"
                ? "Critical · decides"
                : "Supporting · reported"}
            </Typography>
            <Typography
              sx={{
                typography: "s3",
                color: "text.tertiary",
                width: 76,
                textAlign: "right",
                flexShrink: 0,
              }}
            >
              {st.label}
            </Typography>
          </Stack>
        );
      })}
    </Box>
  );
}
SubGoalRollup.propTypes = {
  task: PropTypes.object,
  env: PropTypes.object,
  envState: PropTypes.object,
};
