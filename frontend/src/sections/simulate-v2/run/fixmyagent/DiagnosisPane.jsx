import PropTypes from "prop-types";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Box,
  Stack,
  Typography,
  Button,
  Collapse,
  IconButton,
  Tooltip,
  Menu,
  MenuItem,
  ListItemText,
  Tab,
  Tabs,
  TextField,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { clickable, focusRing } from "../../components/clickable";
import { connectedAgentPath } from "../../improvements/improvementNav";
import { RunTraceLog } from "../../components/RunTrace";
import { outcomeOf } from "../taskOutcome";
import TaskLinks from "./TaskLinks";
import CallScanStrip from "./CallScanStrip";
import OmegaHandoff from "../OmegaHandoff";
import ImaginePane from "./ImaginePane";
import CopyBriefButton from "../../components/CopyBriefButton";
import { useSimStore, useConnectedAgents } from "../../store";
import { envAgentKey, agentDefinitionOf } from "../../_mock/agentRegistry";
import { limitsOf, surfaceAvailability } from "../../agents/agentLimits";
import { fixScope, codingAgentBrief } from "../../_mock/codingAgentBrief";
import { decidingMoment } from "../../_mock/comparison";
import { runIdentityOf } from "../../_mock/runIdentity";
import { scenarioTitle } from "../../_mock/scenarios";
import { checkToFix } from "../../_mock/omega";
import { enqueueSnackbar } from "notistack";
import { getEval, TOOL_CALL_JUDGE_ID } from "../../_mock/evals";
import { worldToolsFor } from "../../_mock/toolFit";
import { estimateRegrade } from "../../_mock/metering";
import { evalVersionOf } from "../../_mock/envModel";
import { useAppliedEvals } from "../../workspace/evals/useAppliedEvals";

/**
 * What is wrong, and what to change about it.
 *
 * This is the old Fix-my-agent suggestions pane with the diagnosis put back
 * underneath it. The version it replaces opened straight onto a list of
 * recommendations with an insights paragraph above them — which is fine until
 * somebody asks where a recommendation came from, and the honest answer is
 * "a model looked at your run". Six named analyzers can each be argued with,
 * and the one that matters most is about the measurement rather than the agent.
 *
 * The old split into fixable and not-fixable is kept, because it was right —
 * but the line moves. "Not fixable" here is not a shrug at system-level
 * problems; it is the set of changes that belong to the environment rather
 * than the agent, and shipping a prompt edit instead of one of those is how a
 * team spends a month improving a number that was never measuring anything.
 */

/* A change that fixes a critical scenario outranks one that lifts the average. */
const priorityOf = (p, tasks) => {
  const blockers = p.addresses.filter(
    (id) => tasks.find((t) => t.id === id)?.critical,
  ).length;
  if (blockers) return { label: "Critical", role: "danger", level: 0 };
  if (p.addresses.length > 2)
    return { label: "High", role: "warning", text: "text.primary", level: 1 };
  return { label: "Medium", role: "neutral", level: 2 };
};

/*
  Module-level cache of run ids whose diagnosis animation has already
  played this session. Reopening the drawer for a run in this set
  skips straight to "done" — no repeat animation.
*/
const analyzedRuns = new Set();

/* Reading the whole run takes about nine seconds, however many calls it has;
   a failing call takes longer to read than a clean one, and the jitter keeps
   the strip from ticking like a metronome. */
const SCAN_MS = 9000;
const hashId = (s) => {
  let h = 0;
  for (let i = 0; i < String(s).length; i += 1)
    h = ((h << 5) - h + String(s).charCodeAt(i)) | 0;
  return Math.abs(h);
};
const readMs = (t, base) => {
  const outcome = outcomeOf(t);
  const weight =
    outcome === "failed" || outcome === "flaky"
      ? 1.35
      : outcome === "unmeasured"
        ? 0.6
        : 0.85;
  return Math.round(base * weight * (0.7 + (hashId(t?.id) % 60) / 100));
};

/* One call is an anecdote; an issue is shown once its pattern has turned up
   in two of them (or in its only one). */
const CONFIRM_AFTER = 2;

/* The proposal's sentence carries its count ("22 tasks came in under…");
   while the scan is still finding calls, the count is the live one. */
const liveWhy = (why, n) => {
  if (!why) return why;
  return why.replace(
    /^\d+( (?:release |long |critical )?)(task|episode|blocker|scenario)s?\b/,
    (_, mid, noun) => `${n}${mid}${noun}${n === 1 ? "" : "s"}`,
  );
};

/*
  Which of the agent's constraints a fix touches, in one quiet line — each
  named, with what the fix does to it and whether that's allowed. A fix the
  constraints rule out stays on the list — it is still what the evidence
  says — with the reason and the way to change it, rather than vanishing.
*/
function ScopeLine({ scope, available, onChangeConstraints }) {
  const blocked = !scope.environment && !scope.ok;
  const link = blocked && onChangeConstraints && (
    <>
      {" · "}
      <Box
        component="span"
        {...clickable(onChangeConstraints, { role: "link" })}
        sx={{
          fontWeight: 600,
          color: "text.secondary",
          cursor: "pointer",
          "&:hover": { color: "text.primary" },
          ...focusRing,
        }}
      >
        Change constraints
      </Box>
    </>
  );
  /* Allowed, but this agent can't take that kind of change yet (no code
     connected) — said after the verdict. */
  const unavailable = available?.ok === false ? available.why : null;
  const checks = (scope.checks || []).map((c, i) => (
    <Box component="span" key={c.id}>
      {i > 0 && " · "}
      {c.constraint}
      {c.ceiling ? ` (${c.ceiling})` : ""}
      {": "}
      {c.says}
      {c.path && (
        <>
          {" "}
          <Box
            component="span"
            sx={{ fontFamily: "ui-monospace, Menlo, monospace" }}
          >
            {c.path}
          </Box>
        </>
      )}
      {" — "}
      <Box
        component="span"
        sx={
          c.blocked ? { color: "text.secondary", fontWeight: 600 } : undefined
        }
      >
        {c.verdict}
      </Box>
      {c.id === "changes" &&
        !c.blocked &&
        unavailable &&
        ` · ${unavailable.charAt(0).toLowerCase()}${unavailable.slice(1)}`}
    </Box>
  ));

  return (
    <Tooltip
      arrow
      placement="top-start"
      title={
        blocked
          ? "An improvement only tries changes this agent's constraints allow — it will skip this one."
          : ""
      }
    >
      <Stack
        direction="row"
        alignItems="flex-start"
        spacing={0.625}
        sx={{ mt: 1 }}
      >
        <Iconify
          icon={
            scope.environment
              ? "solar:test-tube-linear"
              : blocked
                ? "solar:lock-keyhole-minimalistic-linear"
                : "solar:shield-check-linear"
          }
          width={14}
          sx={{ color: "text.disabled", flexShrink: 0, mt: "2px" }}
        />
        <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
          {scope.environment ? scope.label : checks}
          {link}
        </Typography>
      </Stack>
    </Tooltip>
  );
}
ScopeLine.propTypes = {
  scope: PropTypes.object.isRequired,
  available: PropTypes.object,
  onChangeConstraints: PropTypes.func,
};

export default function DiagnosisPane({
  tasks,
  report,
  trace,
  proposals,
  verdict,
  applied,
  setApplied,
  current,
  projected,
  willFix,
  measured,
  failing,
  onOpenTask,
  onViewIssue,
  onOptimize,
  onClose,
  env,
  envState,
  patch,
  handoff: handoffProps,
  runId,
  previous = [],
  onOpenPrevious,
  onHandToAgent,
  picked = null,
  onEditChange = null,
}) {
  /* Editing a proposed change's wording before the search: one at a time. */
  const [editing, setEditing] = useState(null);
  /* First open of this runId: play the analyzer animation. Every
     subsequent open in this session: skip straight to the results. */
  const [phase, setPhase] = useState(() =>
    runId && analyzedRuns.has(runId) ? "done" : "running",
  );
  useEffect(() => {
    if (phase === "done" && runId) analyzedRuns.add(runId);
  }, [phase, runId]);

  /* The scan: calls are read one at a time, and everything below — the strip,
     the issue cards, their counts — is derived from how far it has got. */
  const total = tasks.length;
  const [scanned, setScanned] = useState(() => (phase === "done" ? total : 0));
  const running = phase === "running";
  const baseMs = SCAN_MS / Math.max(1, total);
  useEffect(() => {
    if (!running) return undefined;
    if (scanned >= total) {
      const t = setTimeout(() => setPhase("done"), 650);
      return () => clearTimeout(t);
    }
    const t = setTimeout(
      () => setScanned((n) => n + 1),
      readMs(tasks[scanned], baseMs),
    );
    return () => clearTimeout(t);
  }, [running, scanned, total, tasks, baseMs]);
  const rescan = () => {
    setScanned(0);
    setPhase("running");
  };
  const [handoff, setHandoff] = useState(false);
  const [previousAnchor, setPreviousAnchor] = useState(null);
  const [open, setOpen] = useState({});
  /* Folded to one line — what to fix comes first; the six checks are a click away. */
  const [showDiagnosis, setShowDiagnosis] = useState(false);
  /* Diagnosis is the fixed six-analyzer read; Imagine is the freeform
     follow-up canvas over the same run. Kept as a top-level tab inside
     this pane so the user can jump between them without losing the
     drawer's context (env pin, run id, failing task list). */
  const [tab, setTab] = useState("diagnosis");

  const included = proposals.filter((p) => applied[p.id]);
  /* Nothing failed, so there is nothing to search for. The refactor into a
     drawer dropped this branch and left an empty recommendation list under a
     live "Run self improvement" button — a search over an empty candidate pool
     that would have burned episodes to re-confirm the score it started from. */
  const nothingToFix = !failing.length;

  /* The agent this environment tests, and the constraints every fix for it
     is held to — the same ones an improvement run searches within. */
  const navigate = useNavigate();
  const { state } = useSimStore();
  const { agents } = useConnectedAgents();
  const agentKey = envAgentKey(envState?.agent, env?.id);
  const agent = useMemo(
    () => agents.find((a) => a.key === agentKey) || null,
    [agents, agentKey],
  );
  const limits = useMemo(() => limitsOf(state, agentKey), [state, agentKey]);
  const available = useMemo(
    () => (agent ? surfaceAvailability(agent) : {}),
    [agent],
  );
  const model = useMemo(() => {
    if (!agent || !env) return null;
    const def = agentDefinitionOf({ agent, env, envState });
    return def.model?.visible ? def.model.name : null;
  }, [agent, env, envState]);
  const changeConstraints = agent
    ? () => {
        onClose?.();
        navigate(connectedAgentPath(agent.id, "constraints"));
      }
    : null;
  /* The checks pass conversations that didn't do the work (the gaming
     analyzer's high finding — the verdict says fix them first). An
     improvement scored by them could win without getting better, so fixing
     the checks leads and Improve steps back. */
  const checksFirst =
    report.find((a) => a.severity === "high")?.id === "gaming";
  /* The exact check: the evaluation that passed them (opened on the
     Evaluations tab, which says what to change), or the score's efficiency
     term (the contract). */
  const toFix = checksFirst ? checkToFix(report) : null;
  const unit = (n) =>
    `${n} ${env?.surface === "voice" ? "call" : "conversation"}${n === 1 ? "" : "s"}`;
  const runLabel = (() => {
    const run = (envState?.runs || []).find((r) => r.id === runId);
    return run?.ordinal ? `Run ${run.ordinal}` : "this run";
  })();
  const checksLine = !toFix
    ? "The checks pass conversations that didn't do the work"
    : toFix.signal === "efficiency-exploit"
      ? `The score paid ${unit(toFix.count)} for finishing early by doing less`
      : `${toFix.check.name} passed ${unit(toFix.check.count)} ${
          toFix.signal === "hollow-pass"
            ? "that skipped a step the scenario requires"
            : "where the agent claimed an action it never took"
        }`;
  /*
    The check that passed claims nobody did is fixed here, in place: tool
    call evaluation is turned on for the environment and this run is
    re-graded against the calls it already recorded — no new run, a few
    grader credits, seconds — and the diagnosis reads the corrected verdicts.
    Anything else (the score's efficiency term, a check that needs editing)
    still opens the environment.
  */
  const { add: addEvals, appliedIds } = useAppliedEvals(envState, patch);
  const toolEval = getEval(TOOL_CALL_JUDGE_ID);
  const regradeHere =
    !!toFix?.check &&
    !!toolEval &&
    !!patch &&
    !!runId &&
    !appliedIds.has(TOOL_CALL_JUDGE_ID) &&
    worldToolsFor(env, envState).length > 0;
  const regradeCredits = regradeHere
    ? Math.round(
        estimateRegrade({
          calls: tasks.reduce(
            (n, t) => n + Math.max(1, t.samples?.length || t.repeats || 1),
            0,
          ),
          evals: [toolEval],
        }),
      )
    : 0;
  const regradeInPlace = () => {
    addEvals([toolEval]);
    patch({
      runs: (envState?.runs || []).map((r) =>
        r.id === runId
          ? {
              ...r,
              regradedWith: {
                label: "tool call evaluation",
                toolCalls: true,
                /* Graded under the evals as they are once the check is added
                   (one more eval change), so no second re-grade is offered. */
                evalVersion: evalVersionOf({
                  ...envState,
                  evalChanges: [...(envState?.evalChanges || []), {}],
                }),
                scenarioIds: tasks.map((t) => t.id),
                credits: regradeCredits,
                at: new Date().toISOString(),
              },
            }
          : r,
      ),
    });
    enqueueSnackbar(
      `Tool call evaluation is on, and ${runLabel} was re-graded against its recorded tool calls — no new run.`,
      { variant: "success" },
    );
  };
  const fixLabel = regradeHere
    ? `Turn on tool-call checks and re-grade ${runLabel} · ≈ ${regradeCredits.toLocaleString()} credits`
    : !toFix?.check
      ? toFix
        ? "Fix the score"
        : "Fix the checks"
      : `Fix ${toFix.check.name}`;
  const fixChecks = regradeHere
    ? regradeInPlace
    : env?.id
      ? () => {
          onClose?.();
          const base = paths.dashboard.simulate.environmentDetail(env.id);
          if (toFix && !toFix.check) return navigate(`${base}/contract`);
          navigate(`${base}/evals`, {
            state: toFix?.check && {
              fixCheck: {
                evalId: toFix.check.id,
                name: toFix.check.name,
                count: toFix.check.count,
                signal: toFix.signal,
                runLabel,
              },
            },
          });
        }
      : null;

  const sorted = useMemo(
    () =>
      [...proposals].sort(
        (a, b) =>
          priorityOf(a, tasks).level - priorityOf(b, tasks).level ||
          b.addresses.length - a.addresses.length,
      ),
    [proposals, tasks],
  );

  /* Where each call sits in the reading order, and the point in the scan at
     which each issue has enough calls behind it to be shown. */
  const found = useMemo(() => {
    const at = new Map(tasks.map((t, i) => [t.id, i]));
    const list = sorted.map((p, rank) => {
      const idx = p.addresses
        .map((id) => at.get(id))
        .filter((i) => i != null)
        .sort((a, b) => a - b);
      const k = Math.min(CONFIRM_AFTER, idx.length);
      return { p, rank, idx, foundAt: k ? idx[k - 1] + 1 : 0 };
    });
    /* Paced across the whole read, one at a time: two calls usually confirm
       every issue in the first few, which made them land all at once. Each
       now waits for its share of the scan and a gap after the one before. */
    const live = list
      .filter((f) => f.idx.length)
      .sort((a, b) => a.foundAt - b.foundAt || a.rank - b.rank);
    const n = live.length;
    const slot = total / (n + 1);
    const gap = Math.max(1, Math.floor(slot * 0.6));
    let prev = 0;
    live.forEach((f, i) => {
      f.foundAt = Math.min(
        total,
        Math.max(f.foundAt, prev + gap, Math.round((i + 1) * slot)),
      );
      prev = f.foundAt;
    });
    return list;
  }, [sorted, tasks, total]);

  /* Discovery order while reading; ranked order once every call is in. Each
     card carries only the calls read so far, so its count, lift and priority
     grow with the scan instead of arriving fully formed. */
  const shownIssues = useMemo(() => {
    if (!running) return sorted.map((p, i) => ({ p, rank: i + 1 }));
    const pct = (n) =>
      measured.length ? Math.round((n / measured.length) * 100) : 0;
    /* Newest first while reading, so each one arrives where it's seen. */
    return found
      .filter((f) => f.idx.length && scanned >= f.foundAt)
      .sort((a, b) => b.foundAt - a.foundAt || a.rank - b.rank)
      .map(({ p, idx }) => {
        const seen = idx.filter((i) => i < scanned).map((i) => tasks[i].id);
        return {
          p: {
            ...p,
            addresses: seen,
            lift: pct(seen.length),
            why: liveWhy(p.why, seen.length),
          },
          rank: null,
        };
      });
  }, [running, sorted, found, scanned, tasks, measured.length]);

  /* When the list re-ranks at the end, each card slides from where it was to
     where it belongs rather than jumping. */
  const cardEls = useRef({});
  const lastTops = useRef({});
  const orderKey = shownIssues.map((x) => x.p.id).join("|");
  const lastOrder = useRef(orderKey);
  useLayoutEffect(() => {
    const tops = {};
    Object.entries(cardEls.current).forEach(([id, el]) => {
      if (el) tops[id] = el.offsetTop;
    });
    if (lastOrder.current !== orderKey && !running) {
      Object.entries(tops).forEach(([id, top]) => {
        const dy = (lastTops.current[id] ?? top) - top;
        if (dy) {
          cardEls.current[id]?.animate?.(
            [{ transform: `translateY(${dy}px)` }, { transform: "none" }],
            { duration: 520, easing: "cubic-bezier(0.2, 0.8, 0.2, 1)" },
          );
        }
      });
    }
    lastOrder.current = orderKey;
    lastTops.current = tops;
  });

  /* Overall run summary — the failures collapse into a handful of causes, and
     fixing the top-ranked one frees a concrete number of scenarios. Kept to
     data this pane already has so it can never disagree with the list below. */
  const causeCount = proposals.length;
  const topFrees = sorted[0]?.addresses.length || 0;
  const alreadyPass = Math.max(0, measured.length - failing.length);

  /* The diagnosis as a brief for a coding agent: the verdict and what the
     analyzers flagged, every fix with the constraint it touches, and the
     calls the top fix addresses first as evidence. */
  /* "agent v3 × env v7" — the run the brief is about, named the way every screen names it. */
  const runPairing = (() => {
    const run = (envState?.runs || []).find((r) => r.id === runId);
    return run && env ? runIdentityOf(run, envState, env).label : null;
  })();
  const brief = () => {
    const top = new Set(sorted[0]?.addresses || []);
    const byTop = [...failing].sort(
      (a, b) => Number(top.has(b.id)) - Number(top.has(a.id)),
    );
    const flagged = report.filter((a) => a.severity && a.severity !== "low");
    return codingAgentBrief({
      agent: agent ? { name: agent.name, key: agent.key } : null,
      env,
      issue: {
        title: `${failing.length} of ${measured.length} measured scenarios failing in ${env?.name || "this environment"}`,
        severity: sorted[0]
          ? priorityOf(sorted[0], tasks).label.toLowerCase()
          : null,
      },
      runId,
      pairing: runPairing,
      rootCause: [
        verdict,
        ...flagged.map((a) => `- ${a.label}: ${a.headline}`),
      ].join("\n"),
      suggestedFix: sorted.map((p) => ({
        ...p,
        scope: fixScope(p, { env, limits }),
      })),
      evidence: byTop.map((t) => ({
        source: scenarioTitle(t.title) || t.id,
        text: decidingMoment(t).text,
      })),
      failingScenarios: failing.map((t) => scenarioTitle(t.title) || t.id),
      limits,
      model,
    });
  };

  /* One fix card as its own brief: just that proposal, the calls it
     addresses as evidence. Visible scenarios only — never a held-back one,
     which is only fair to judge on if nobody has seen it. */
  const briefFor = (p) => () => {
    const visible = tasks.filter(
      (t) => p.addresses.includes(t.id) && !t.held && !t.heldBack && !t.heldOut,
    );
    return codingAgentBrief({
      agent: agent ? { name: agent.name, key: agent.key } : null,
      env,
      issue: {
        title: p.title,
        severity: priorityOf(p, tasks)?.label?.toLowerCase() || null,
      },
      runId,
      pairing: runPairing,
      rootCause: p.why,
      suggestedFix: [{ ...p, scope: fixScope(p, { env, limits }) }],
      evidence: visible.map((t) => ({
        source: scenarioTitle(t.title) || t.id,
        text: decidingMoment(t).text,
      })),
      failingScenarios: visible
        .filter((t) => t.status !== "passed")
        .map((t) => scenarioTitle(t.title) || t.id),
      limits,
      model,
    });
  };

  /* Export the diagnosis as a plain-text report. Defensive: restricted
     sandboxes block programmatic downloads, so a failure is swallowed. */
  const exportReport = () => {
    try {
      const lines = [
        `Run report${runId ? ` — ${runId}` : ""}`,
        `${failing.length} failing of ${measured.length} measured`,
        "",
        `Summary: ${verdict}`,
        "",
        "Fix in this order:",
        ...sorted.map((p, i) => {
          const pr = priorityOf(p, tasks);
          return `  ${i + 1}. [${pr.label}] ${p.title} — +${p.lift}%, ${p.addresses.length} call${p.addresses.length === 1 ? "" : "s"}${p.why ? `\n     ${p.why}` : ""}`;
        }),
      ];
      const blob = new Blob([lines.join("\n")], { type: "text/plain" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${runId || "run"}-report.txt`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch {
      /* download blocked (sandbox / private mode) — no-op */
    }
  };

  return (
    <Stack sx={{ height: "100%", minHeight: 0 }}>
      {/* ── header ── */}
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.25}
        sx={{
          px: 2.5,
          py: 2,
          borderBottom: "1px solid",
          borderColor: "divider",
          flexShrink: 0,
        }}
      >
        <Box
          sx={{
            width: 30,
            height: 30,
            borderRadius: 1,
            display: "grid",
            placeItems: "center",
            flexShrink: 0,
            border: "1px solid",
            borderColor: "divider",
            bgcolor: "background.paper",
            color: "text.secondary",
          }}
        >
          <Iconify icon="solar:magic-stick-3-linear" width={16} />
        </Box>
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s1", fontWeight: 600 }}>
            Debug failures
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            {picked
              ? `Only the ${picked.count} you picked${picked.leftOut ? ` · ${picked.leftOut} other failing left out` : ""}`
              : `${failing.length} failing of ${measured.length} measured`}
            {!picked && tasks.length - measured.length
              ? ` · ${tasks.length - measured.length} not measured`
              : ""}
          </Typography>
        </Box>
        {/* Earlier improvement runs reopen from here — the one place a
            finished run was otherwise unreachable. */}
        {!!previous.length && (
          <Button
            size="small"
            onClick={(e) => setPreviousAnchor(e.currentTarget)}
            sx={{
              color: "text.secondary",
              "&:hover": { bgcolor: "transparent", color: "text.primary" },
            }}
          >
            {`Improvements (${previous.length})`}
          </Button>
        )}
        <Menu
          anchorEl={previousAnchor}
          open={!!previousAnchor}
          onClose={() => setPreviousAnchor(null)}
          anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
          transformOrigin={{ vertical: "top", horizontal: "right" }}
          slotProps={{ paper: { sx: { minWidth: 260 } } }}
        >
          {previous.map((r) => (
            <MenuItem
              key={r.id}
              onClick={() => {
                setPreviousAnchor(null);
                onOpenPrevious?.(r.id);
              }}
            >
              <ListItemText
                primary={r.name}
                secondary={r.sub}
                primaryTypographyProps={{
                  sx: { typography: "s2", fontWeight: 600 },
                }}
                secondaryTypographyProps={{
                  sx: { typography: "s3", color: "text.tertiary" },
                }}
              />
            </MenuItem>
          ))}
        </Menu>
        {phase === "done" && !nothingToFix && (
          <Button
            size="small"
            onClick={exportReport}
            startIcon={
              <Iconify icon="solar:upload-minimalistic-linear" width={14} />
            }
            sx={{
              color: "text.secondary",
              "&:hover": { bgcolor: "transparent", color: "text.primary" },
            }}
          >
            Export report
          </Button>
        )}
        {phase === "done" && (
          <Tooltip arrow title="Read the run again">
            <IconButton size="small" onClick={rescan}>
              <Iconify
                icon="solar:refresh-linear"
                width={16}
                sx={{ color: "text.tertiary" }}
              />
            </IconButton>
          </Tooltip>
        )}
        <IconButton size="small" onClick={onClose}>
          <Iconify icon="solar:close-linear" width={16} />
        </IconButton>
      </Stack>

      {/* ── tab bar — there from the start so nothing jumps when the scan
            ends; Imagine waits for the diagnosis it reads from. ── */}
      {!(phase === "done" && nothingToFix) && (
        <Box
          sx={{
            px: 2.5,
            borderBottom: "1px solid",
            borderColor: "divider",
            flexShrink: 0,
          }}
        >
          <Tabs
            value={tab}
            onChange={(_, v) => setTab(v)}
            aria-label="Debug failures"
          >
            <Tab
              value="diagnosis"
              label="Diagnosis"
              icon={
                <Iconify icon="solar:document-medicine-linear" width={14} />
              }
              iconPosition="start"
            />
            <Tab
              value="imagine"
              label="Imagine"
              icon={<Iconify icon="solar:magic-stick-3-linear" width={14} />}
              iconPosition="start"
              disabled={running || nothingToFix}
            />
          </Tabs>
        </Box>
      )}

      {phase === "done" && nothingToFix && (
        <Stack
          alignItems="center"
          justifyContent="center"
          spacing={1.5}
          sx={{ flex: 1, px: 4, textAlign: "center" }}
        >
          <Box
            sx={{
              width: 44,
              height: 44,
              borderRadius: "8px",
              display: "grid",
              placeItems: "center",
              bgcolor: "status.success.bg",
              color: "status.success.fg",
            }}
          >
            <Iconify icon="solar:cup-star-linear" width={20} />
          </Box>
          <Typography sx={{ typography: "s1", fontWeight: 600 }}>
            Nothing to fix
          </Typography>
          <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
            Every scenario passed — add harder scenarios before improving
            against this run.
          </Typography>
        </Stack>
      )}

      {phase === "done" && !nothingToFix && tab === "imagine" && (
        <ImaginePane tasks={tasks} env={env} />
      )}

      {(running || !nothingToFix) && tab === "diagnosis" && (
        <>
          <Box sx={{ flex: 1, overflowY: "auto", minHeight: 0 }}>
            {/* ── how far the read has got ── */}
            <Collapse in={running} unmountOnExit>
              <Box sx={{ px: 2.5, pt: 2.5 }}>
                <CallScanStrip tasks={tasks} scanned={scanned} />
              </Box>
            </Collapse>

            {/* ── diagnosis — the analyzers read across the whole run, so
                 they wait for the last call and then report together ── */}
            <Box sx={{ px: 2.5, pt: 2.5 }}>
              <Stack
                direction="row"
                alignItems="center"
                spacing={0.75}
                {...clickable(
                  running ? null : () => setShowDiagnosis((v) => !v),
                  { expanded: showDiagnosis },
                )}
                sx={{
                  cursor: running ? "default" : "pointer",
                  mb: 1,
                  ...focusRing,
                }}
              >
                <Typography sx={{ typography: "s2", fontWeight: 600, flex: 1 }}>
                  Diagnosis
                </Typography>
                <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                  {running
                    ? `${report.length} checks · report once every call is read`
                    : (() => {
                        const found = report.filter(
                          (a) => a.severity && a.severity !== "low",
                        ).length;
                        return `${report.length} checks · ${found ? `${found} found problems` : "nothing serious"}`;
                      })()}
                </Typography>
                {!running && (
                  <Iconify
                    icon={
                      showDiagnosis
                        ? "solar:alt-arrow-up-linear"
                        : "solar:alt-arrow-down-linear"
                    }
                    width={16}
                    sx={{ color: "text.tertiary" }}
                  />
                )}
              </Stack>
              <Collapse in={!running && showDiagnosis}>
                <Stack
                  divider={
                    <Box
                      sx={{ borderBottom: "1px solid", borderColor: "divider" }}
                    />
                  }
                  sx={{
                    border: "1px solid",
                    borderColor: "divider",
                    borderRadius: 1,
                  }}
                >
                  {report.map((a, i) => {
                    return (
                      <Box
                        key={a.id}
                        sx={{
                          animation: `diag-in 320ms ease ${i * 90}ms both`,
                          "@keyframes diag-in": {
                            from: { opacity: 0, transform: "translateY(-4px)" },
                          },
                        }}
                      >
                        <Stack
                          direction="row"
                          alignItems="center"
                          spacing={1}
                          {...clickable(
                            () => setOpen((o) => ({ ...o, [a.id]: !o[a.id] })),
                            { expanded: !!open[a.id] },
                          )}
                          sx={{
                            px: 1.25,
                            py: 0.625,
                            cursor: "pointer",
                            "&:hover": { bgcolor: "action.hover" },
                            ...focusRing,
                          }}
                        >
                          <Typography
                            sx={{
                              typography: "s2",
                              fontWeight: 600,
                              flexShrink: 0,
                            }}
                          >
                            {a.label}
                          </Typography>
                          {a.always && (
                            <Typography
                              sx={{
                                typography: "s3",
                                color: "text.disabled",
                                flexShrink: 0,
                              }}
                            >
                              · always on
                            </Typography>
                          )}
                          <Typography
                            noWrap
                            sx={{
                              typography: "s3",
                              fontWeight: 500,
                              flex: 1,
                              minWidth: 0,
                              color: "text.secondary",
                            }}
                          >
                            {a.headline}
                          </Typography>
                          <Iconify
                            icon={
                              open[a.id]
                                ? "solar:alt-arrow-up-linear"
                                : "solar:alt-arrow-down-linear"
                            }
                            width={12}
                            sx={{ color: "text.tertiary", flexShrink: 0 }}
                          />
                        </Stack>
                        <Collapse in={!!open[a.id]}>
                          <Box sx={{ px: 1.25, pb: 1.75 }}>
                            <Typography
                              sx={{
                                typography: "s3",
                                color: "text.tertiary",
                                mb: 0.5,
                              }}
                            >
                              reads {a.reads}
                            </Typography>
                            <Typography
                              sx={{
                                typography: "s2",
                                color: "text.secondary",
                                mb: a.rows?.length ? 1.25 : 0,
                              }}
                            >
                              {a.detail}
                            </Typography>
                            <Stack spacing={0.75}>
                              {(a.rows || []).map((r) => (
                                <Box key={r.label}>
                                  <Typography sx={{ typography: "s2" }}>
                                    {r.label}
                                    {r.value != null && (
                                      <Box
                                        component="span"
                                        sx={{
                                          color: "text.tertiary",
                                          ml: 0.75,
                                        }}
                                      >
                                        {r.value}
                                      </Box>
                                    )}
                                  </Typography>
                                  {r.note && (
                                    <Typography
                                      sx={{
                                        typography: "s3",
                                        color: "text.tertiary",
                                      }}
                                    >
                                      {r.note}
                                    </Typography>
                                  )}
                                  <TaskLinks
                                    ids={r.tasks}
                                    tasks={tasks}
                                    step={r.step}
                                    onOpen={onOpenTask}
                                  />
                                </Box>
                              ))}
                            </Stack>
                          </Box>
                        </Collapse>
                      </Box>
                    );
                  })}
                </Stack>
                <Box sx={{ mt: 0.5, mx: -1 }}>
                  <RunTraceLog
                    label={`Read ${tasks.length} conversations`}
                    steps={trace}
                  />
                </Box>
              </Collapse>
            </Box>

            {/* ── overall run summary — sits after the diagnosis so it reads as
                 the takeaway from the analyzers above, not a claim ahead of them ── */}
            <Collapse in={!running}>
              <Box
                sx={{
                  mx: 2.5,
                  mt: 2.5,
                  px: 1.75,
                  py: 1.5,
                  borderRadius: "8px",
                  border: "1px solid",
                  borderColor: "divider",
                  bgcolor: "background.paper",
                }}
              >
                <Stack direction="row" alignItems="flex-start" spacing={1.25}>
                  <Iconify
                    icon="solar:lightbulb-bolt-linear"
                    width={16}
                    sx={{ color: "text.secondary", flexShrink: 0, mt: "2px" }}
                  />
                  <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                    {verdict}
                  </Typography>
                </Stack>
                {causeCount > 0 && topFrees > 0 && (
                  <Typography
                    sx={{
                      typography: "s2",
                      color: "text.secondary",
                      mt: 1,
                      pl: 3.375,
                    }}
                  >
                    These <b>{failing.length} failures</b> collapse into{" "}
                    <b>
                      {causeCount} {causeCount === 1 ? "cause" : "causes"}
                    </b>
                    . Fix the top one and{" "}
                    <b>
                      {topFrees}{" "}
                      {topFrees === 1 ? "scenario flips" : "scenarios flip"}
                    </b>{" "}
                    green.
                  </Typography>
                )}
                {alreadyPass > 0 && (
                  <Typography
                    sx={{
                      typography: "s3",
                      color: "text.tertiary",
                      mt: 0.75,
                      pl: 3.375,
                    }}
                  >
                    {alreadyPass} of {measured.length} measured scenarios
                    already pass — the gaps are concentrated, not scattered.
                  </Typography>
                )}
              </Box>
            </Collapse>

            {/*
              Hand the diagnosis's changes over without a search — the
              quieter alternative. Same source-aware hand-off as a winner's;
              a version is saved only when the change lands.
            */}
            {!running &&
              (handoff || handoffProps?.landedVersion) &&
              !!included.length && (
                <Box sx={{ px: 2.5, pt: 2.5 }}>
                  {/* Never blocked — but never passed off as checked either. */}
                  <Stack
                    direction="row"
                    spacing={1}
                    alignItems="flex-start"
                    sx={{ mb: 1.25 }}
                  >
                    <Iconify
                      icon="solar:info-circle-linear"
                      width={14}
                      sx={{ color: "text.tertiary", flexShrink: 0, mt: "2px" }}
                    />
                    <Typography
                      sx={{ typography: "s3", color: "text.secondary" }}
                    >
                      Not searched and not checked against your must-pass
                      scenarios. Re-run it once it lands, before you release it.
                    </Typography>
                  </Stack>
                  <OmegaHandoff
                    env={env}
                    envState={envState}
                    patch={patch}
                    included={included}
                    projected={projected}
                    projectedOn="if every addressed scenario passes"
                    current={current}
                    willFix={willFix}
                    handoffKey={handoffProps?.key}
                    landedVersion={handoffProps?.landedVersion}
                    provenance={handoffProps?.provenance}
                    onLand={(info) =>
                      handoffProps?.onLand?.(info, included, projected)
                    }
                    onRerun={handoffProps?.onRerun}
                  />
                </Box>
              )}

            {/* ── fixable: change the agent, in priority order ── */}
            <Box sx={{ px: 2.5, pt: 2.5, pb: 2.5 }}>
              <Stack
                direction="row"
                alignItems="center"
                spacing={1.5}
                sx={{ mb: 1 }}
              >
                <Box flex={1} minWidth={0}>
                  <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                    {running ? "Issues found" : "Fix in this order"}
                  </Typography>
                  <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                    {running
                      ? `${shownIssues.length} so far · ranked once every call is read`
                      : "ranked by severity, then how many scenarios each one frees"}
                  </Typography>
                </Box>
                {/* For teams whose coding agent makes the edit — it gets the
                    evidence and the constraints, and submits back here. */}
                {!running && !!sorted.length && (
                  <CopyBriefButton
                    size="small"
                    getBrief={brief}
                    target={{ kind: "run", envId: env?.id, runId }}
                    onOpened={onHandToAgent}
                  />
                )}
              </Stack>
              {running && !shownIssues.length && (
                <Box
                  sx={{
                    px: 2,
                    py: 2.5,
                    borderRadius: 1,
                    border: "1px dashed",
                    borderColor: "divider",
                    textAlign: "center",
                  }}
                >
                  <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                    Each issue appears here as soon as two calls confirm it,
                    with the calls it affects and the change that addresses it.
                  </Typography>
                </Box>
              )}
              <Stack spacing={1} sx={{ position: "relative" }}>
                {shownIssues.map(({ p, rank }) => {
                  const pr = priorityOf(p, tasks);
                  const scope = fixScope(p, { env, limits });
                  return (
                    <Box
                      key={p.id}
                      ref={(el) => {
                        cardEls.current[p.id] = el;
                      }}
                      sx={{
                        /* Presentation-only card — every proposal is
                           included; the improvement run acts on the whole
                           diagnosis, not a user-picked subset. */
                        p: 1.75,
                        borderRadius: 1,
                        border: "1px solid",
                        borderColor: "divider",
                        bgcolor: "transparent",
                        /* A card arrives with a short fade and slide — no accent
                           border; its place at the top says it's new. */
                        ...(running && {
                          animation: "issue-in 360ms ease both",
                          "@keyframes issue-in": {
                            from: { opacity: 0, transform: "translateY(-6px)" },
                            to: { opacity: 1, transform: "none" },
                          },
                        }),
                      }}
                    >
                      <Stack
                        direction="row"
                        alignItems="flex-start"
                        spacing={1.5}
                      >
                        {/* Rank — the order to work through them. Until every
                            call is read there is no order yet; the slot is
                            held so the card does not shift when it lands. */}
                        {rank != null ? (
                          <Typography
                            sx={{
                              typography: "s1",
                              fontWeight: 600,
                              color: "text.disabled",
                              width: 18,
                              textAlign: "center",
                              flexShrink: 0,
                              lineHeight: 1.35,
                              fontVariantNumeric: "tabular-nums",
                            }}
                          >
                            {rank}
                          </Typography>
                        ) : (
                          <Box sx={{ width: 18, flexShrink: 0 }} />
                        )}
                        <Box flex={1} minWidth={0}>
                          <Stack
                            direction="row"
                            alignItems="center"
                            spacing={0.625}
                            flexWrap="wrap"
                            rowGap={0.375}
                            sx={{ mb: 0.375 }}
                          >
                            <Typography
                              component="span"
                              sx={{
                                typography: "s3",
                                fontWeight: 600,
                                color: "text.secondary",
                              }}
                            >
                              {p.kind}
                            </Typography>
                            {pr && (
                              <Typography
                                component="span"
                                sx={{
                                  typography: "s3",
                                  fontWeight: 600,
                                  color: pr.text || `status.${pr.role}.fg`,
                                }}
                              >
                                {pr.label}
                              </Typography>
                            )}
                            <Typography
                              sx={{
                                typography: "s3",
                                color: "status.success.fg",
                                fontWeight: 600,
                              }}
                            >
                              +{p.lift}%
                            </Typography>
                          </Stack>
                          <Typography
                            sx={{ typography: "s2", fontWeight: 600 }}
                          >
                            {p.title}
                            {p.edited && (
                              <Box
                                component="span"
                                sx={{
                                  typography: "s3",
                                  fontWeight: 500,
                                  color: "text.tertiary",
                                  ml: 1,
                                }}
                              >
                                Edited
                              </Box>
                            )}
                          </Typography>
                          <Typography
                            sx={{
                              typography: "s3",
                              color: "text.tertiary",
                              mt: 0.25,
                            }}
                          >
                            {p.why}
                          </Typography>
                          {/*
                            Impact + action row. Replaces the wall of
                            per-task chips (TaskLinks) that used to
                            sit here — a "N calls affected" number
                            plus a "View affected calls" button reads
                            faster and pushes the drawer toward
                            action rather than reading. Clicking
                            "View" filters the trace table to the
                            exact task ids this finding addresses.
                          */}
                          <Stack
                            direction="row"
                            alignItems="center"
                            spacing={1.5}
                            sx={{ mt: 1 }}
                          >
                            <Stack
                              direction="row"
                              alignItems="center"
                              spacing={0.5}
                            >
                              <Iconify
                                icon="solar:play-circle-linear"
                                width={14}
                                sx={{ color: "text.tertiary" }}
                              />
                              <Typography
                                sx={{
                                  typography: "s3",
                                  fontWeight: 600,
                                  color: "text.secondary",
                                  fontVariantNumeric: "tabular-nums",
                                }}
                              >
                                {p.addresses.length} call
                                {p.addresses.length === 1 ? "" : "s"} affected
                              </Typography>
                            </Stack>
                            {onViewIssue && p.addresses.length > 0 && (
                              <Button
                                size="small"
                                variant="outlined"
                                onClick={() =>
                                  onViewIssue(p.title, p.addresses)
                                }
                                startIcon={
                                  <Iconify icon="solar:eye-linear" width={14} />
                                }
                                sx={{
                                  color: "text.primary",
                                  borderColor: "divider",
                                  "&:hover": {
                                    borderColor: "text.primary",
                                    bgcolor: "transparent",
                                  },
                                }}
                              >
                                View affected calls
                              </Button>
                            )}
                            {/* Your wording, tried by the search as written. */}
                            {!running &&
                              onEditChange &&
                              p.diff.some((d) => d.type === "add") && (
                                <Button
                                  size="small"
                                  variant="outlined"
                                  onClick={() =>
                                    setEditing({
                                      id: p.id,
                                      text: p.diff
                                        .filter((d) => d.type === "add")
                                        .map((d) => d.text)
                                        .join("\n"),
                                    })
                                  }
                                  startIcon={
                                    <Iconify
                                      icon="solar:pen-linear"
                                      width={14}
                                    />
                                  }
                                  sx={{
                                    color: "text.primary",
                                    borderColor: "divider",
                                  }}
                                >
                                  Edit
                                </Button>
                              )}
                            {/* This proposal alone, for a coding agent. */}
                            {!running && (
                              <CopyBriefButton
                                size="small"
                                getBrief={briefFor(p)}
                                target={{ kind: "run", envId: env?.id, runId }}
                                onOpened={onHandToAgent}
                              />
                            )}
                          </Stack>
                          {editing?.id === p.id ? (
                            <Box sx={{ mt: 1.125 }}>
                              <TextField
                                multiline
                                minRows={2}
                                fullWidth
                                size="small"
                                autoFocus
                                value={editing.text}
                                onChange={(e) =>
                                  setEditing({
                                    ...editing,
                                    text: e.target.value,
                                  })
                                }
                                inputProps={{
                                  "aria-label": `Wording for ${p.title}`,
                                }}
                                sx={{
                                  "& textarea": {
                                    typography: "s3",
                                    fontFamily:
                                      "ui-monospace, Menlo, monospace",
                                  },
                                }}
                              />
                              <Stack
                                direction="row"
                                alignItems="center"
                                spacing={1}
                                sx={{ mt: 0.75 }}
                              >
                                <Typography
                                  sx={{
                                    typography: "s3",
                                    color: "text.tertiary",
                                    flex: 1,
                                  }}
                                >
                                  One line per added line. The search tries this
                                  wording as written.
                                </Typography>
                                {p.edited && (
                                  <Button
                                    size="small"
                                    onClick={() => {
                                      onEditChange(p.id, null);
                                      setEditing(null);
                                    }}
                                    sx={{ color: "text.secondary" }}
                                  >
                                    Undo edit
                                  </Button>
                                )}
                                <Button
                                  size="small"
                                  onClick={() => setEditing(null)}
                                  sx={{ color: "text.secondary" }}
                                >
                                  Cancel
                                </Button>
                                <Button
                                  size="small"
                                  variant="contained"
                                  color="primary"
                                  disabled={!editing.text.trim()}
                                  onClick={() => {
                                    onEditChange(
                                      p.id,
                                      editing.text.split("\n"),
                                    );
                                    setEditing(null);
                                  }}
                                >
                                  Save
                                </Button>
                              </Stack>
                            </Box>
                          ) : (
                            <Stack spacing={0.375} sx={{ mt: 1.125 }}>
                              {p.diff.map((d) => (
                                <Stack
                                  key={d.text}
                                  direction="row"
                                  spacing={0.875}
                                  sx={{
                                    px: 1.125,
                                    py: 0.75,
                                    borderRadius: "4px",
                                    bgcolor:
                                      d.type === "add"
                                        ? "status.success.bg"
                                        : "status.danger.bg",
                                  }}
                                >
                                  <Typography
                                    sx={{
                                      typography: "s3",
                                      fontWeight: 600,
                                      color:
                                        d.type === "add"
                                          ? "status.success.fg"
                                          : "status.danger.fg",
                                      flexShrink: 0,
                                    }}
                                  >
                                    {d.type === "add" ? "+" : "−"}
                                  </Typography>
                                  <Typography
                                    sx={{
                                      typography: "s3",
                                      fontFamily:
                                        "ui-monospace, Menlo, monospace",
                                      color:
                                        d.type === "add"
                                          ? "text.primary"
                                          : "text.disabled",
                                      textDecoration:
                                        d.type === "add"
                                          ? "none"
                                          : "line-through",
                                    }}
                                  >
                                    {d.text}
                                  </Typography>
                                </Stack>
                              ))}
                            </Stack>
                          )}
                          <ScopeLine
                            scope={scope}
                            available={available[scope.surface]}
                            onChangeConstraints={changeConstraints}
                          />
                        </Box>
                      </Stack>
                    </Box>
                  );
                })}
              </Stack>
            </Box>
          </Box>

          {/* ── the footer that never scrolls away ── */}
          <Stack
            spacing={1.25}
            sx={{
              px: 2.5,
              py: 2,
              borderTop: "1px solid",
              borderColor: "divider",
              flexShrink: 0,
            }}
          >
            {/*
              The primary path is an improvement run: it searches over the
              diagnosis's changes, trying combinations as trials scored on
              the training scenarios. Hand-off stays as the quiet
              alternative for teams that would rather skip the search.
            */}
            {checksFirst && !running && (
              <>
                <Typography sx={{ typography: "s3", color: "text.secondary" }}>
                  {`${checksLine}, so an improvement scored by ${toFix?.check ? "it" : "them"} can win without getting better. Fix ${toFix?.check ? "it" : "that"} first.`}
                </Typography>
                {fixChecks && (
                  <Button
                    size="small"
                    fullWidth
                    variant="contained"
                    color="primary"
                    onClick={fixChecks}
                  >
                    {fixLabel}
                  </Button>
                )}
              </>
            )}
            <Tooltip
              arrow
              title={
                running
                  ? "Available once every call is read — the improvement searches over the full diagnosis."
                  : included.length
                    ? ""
                    : "The diagnosis found no change to try."
              }
              placement="top"
            >
              <Box>
                <Button
                  size="small"
                  fullWidth
                  variant={checksFirst && !running ? "outlined" : "contained"}
                  color={checksFirst && !running ? "inherit" : "primary"}
                  disabled={running || !included.length}
                  onClick={onOptimize}
                  startIcon={
                    <Iconify icon="solar:magic-stick-3-bold" width={16} />
                  }
                >
                  {checksFirst && !running ? "Improve anyway" : "Improve"}
                </Button>
              </Box>
            </Tooltip>
            {/*
              Fallback link, not a co-equal button. Optimize is the
              default; hand-off is for teams that would rather see the
              diff in a review tool before it becomes a version. The
              earlier full-width outlined button read as an equal
              alternative, which it isn't.
            */}
            <Box sx={{ textAlign: "center", pt: 0.25 }}>
              <Button
                size="small"
                disabled={running || !included.length}
                onClick={() => setHandoff((v) => !v)}
                sx={{
                  color: "text.tertiary",
                  "&:hover": { bgcolor: "transparent", color: "text.primary" },
                }}
              >
                {handoff
                  ? "Hide hand-off"
                  : "or hand these changes over without a search"}
              </Button>
            </Box>
          </Stack>
        </>
      )}
    </Stack>
  );
}

DiagnosisPane.propTypes = {
  /* Handed to a coding agent: starts the improvement its attempts land on. */
  onHandToAgent: PropTypes.func,
  tasks: PropTypes.array,
  report: PropTypes.array,
  trace: PropTypes.array,
  proposals: PropTypes.array,
  verdict: PropTypes.string,
  /* Debug selected: { count, leftOut }. */
  picked: PropTypes.object,
  /* (proposalId, lines | null) — a person's wording for a change. */
  onEditChange: PropTypes.func,
  applied: PropTypes.object,
  setApplied: PropTypes.func,
  current: PropTypes.number,
  projected: PropTypes.number,
  willFix: PropTypes.number,
  measured: PropTypes.array,
  failing: PropTypes.array,
  onOpenTask: PropTypes.func,
  onViewIssue: PropTypes.func,
  runId: PropTypes.string,
  onOptimize: PropTypes.func,
  onClose: PropTypes.func,
  env: PropTypes.object,
  envState: PropTypes.object,
  patch: PropTypes.func,
  handoff: PropTypes.object,
  previous: PropTypes.array,
  onOpenPrevious: PropTypes.func,
};
