import PropTypes from "prop-types";
import { Fragment, useEffect, useMemo, useState } from "react";
import { alpha, useTheme } from "@mui/material/styles";
import { Box, Stack, Typography, Button, Chip, Tooltip, IconButton, Tabs, Tab, Dialog, Checkbox, TextField, Select, MenuItem } from "@mui/material";
import Iconify from "src/components/iconify";
import { clickable, focusRing } from "../../components/clickable";
import { RunTraceLog } from "../../components/RunTrace";
import ImprovementProgress from "./ImprovementProgress";
import { searchTrace, blockingVerdict, KIND_SURFACE, rejectedFor, compatFailed, staticFailed, diagnosisSummaryOf, paretoPool, goalWords, triedLabelOf, noGainRunsOf } from "../../_mock/optimizer";
import { heldSplitText, improverModelsText, staleSince, optimizationVerdict, OPTIMIZER_MODELS, OPT_STATUS, DEFAULT_LIMITS, improvementOutcome, improvementTotalSpend, issueLine, limitRows, searchSpend, judgeFor, ladderOf, improvementPhaseOf, TRACE_STEP_MS, statusOf, settlePending, reproduceStepsOf, reproduceMsOf } from "../../_mock/optimizationRuns";
import { proofKindOf, fixDepthOf } from "../../_mock/rsiModel";
import { membersChanged, proposalMembers, uncheckedChannelNotes } from "../../_mock/improvementPolicy";
import { improvementScenariosNotIn, addImprovementScenariosPatch } from "../../_mock/improvementScenarios";
import { currentEnvVersion } from "../../_mock/versions";
import { useConnectedAgents, useSimStore } from "../../store";
import { FinalistsTable, MustPassNote, ContractNotice, IssueScenariosNotice, ChangeScope, UnfitNotice, OtherEnvironments, StaticNotice } from "./ImprovementPanels";
import { improvementIdentityOf } from "../../improvements/improvementRows";
import { proofDisplay } from "./proofBlock";
import { snapshotOfImprovement } from "../../_mock/envSnapshot";
import { envLabelOf } from "../../_mock/envVersionKinds";
import { briefEvidenceOf, contractApprovalPatch, issueDraftsOf, methodLabelOf, nextContractVersion, reproduceAgainPatch, visibleFailingOf } from "./improvementView";
import { agentDefinitionOf, agentSourceOf, envAgentKey } from "../../_mock/agentRegistry";
import { budgetsWithin } from "../../_mock/improvementStart";
import { limitsOf } from "../../agents/agentLimits";
import { connectedAgentPath } from "../../improvements/improvementNav";
import { useNavigate } from "react-router-dom";
import { paths } from "src/routes/paths";
import { changeFileFor } from "../../_mock/optimize";
import WinnerChanges from "./WinnerChanges";
import ScenarioOutcomes from "./ScenarioOutcomes";
import OmegaHandoff from "../OmegaHandoff";
import TaskLinks from "./TaskLinks";
import CopyBriefButton from "../../components/CopyBriefButton";
import { briefBreakdownOf, codingAgentBrief } from "../../_mock/codingAgentBrief";
import { proofMarkdown } from "./proofBlock";
import ImproverAttempts from "./ImproverAttempts";
import TradeoffView from "./TradeoffView";
import { shippedCandidate, chosenVerified } from "./tradeoffs";
import { CheckGroup, FrozenInputs, ReceiptButton, VerificationLadder } from "./ProofParts";
import { codingAgentById, connectedCodingAgent, improverSessionFor, startImproverSession, useIntegrations } from "../../_mock/integrations";
import { shippedStateOf } from "../../errorFeed/issueFixModel";
import { attemptsOf, sessionState } from "../../_mock/improverAttempts";
import { candidateSourceOf, takesSnapshot } from "../../_mock/candidateSource";
import CandidateSourcePicker from "../CandidateSourcePicker";
import NotReproducedBody from "./NotReproducedBody";
import { creditsLabel } from "../../_mock/metering";
import { envVersionNameOf } from "../../_mock/codeRefs";

/**
 * One improvement run.
 *
 * The old screen had the right bones — a header with the run's identity, the
 * steps it took, a graph, a bar of results and a grid of trials — so those are
 * kept. What changes is what a trial means. Against a dataset a trial is a
 * scored string; here it is a full sweep of the scenarios through real tools,
 * which makes three things worth showing that a dataset optimizer never had to.
 *
 * The winner is checked for gaming before it can leave. An optimizer pointed at
 * a scoring function will find whatever that function rewards, and the cheapest
 * way to pass a check that reads words is better words — so the same analyzer
 * that flagged the agent's own inflated scores re-reads the winner's. Shipping
 * a prompt that learned to say "I have processed your refund" is the failure
 * this whole product exists to prevent; it would be strange to build the
 * machine that produces it and not look.
 *
 * The winner is the best candidate that did not break a critical scenario, not
 * the best candidate — and when every candidate broke one, there is no winner.
 * The grid shows which scenarios each trial moved, because two candidates a
 * point apart are usually fixing different things.
 */

export default function OptimizationRunView({
  record, name, env, envState, patch, tasks, onOpenTask, onBack, backLabel, onClose, onDone, onRunWinner, handoff, decisionOnly = false,
  shipOnly = false, onOpenDecision,
}) {
  const theme = useTheme();
  const navigate = useNavigate();
  /* A git agent's version reads as its commit ("main @ 3f9a2c1"), never vN. */
  const versionName = (label) => envVersionNameOf(envState, label, env?.id).main;
  const openTrialRun = (trialN) => {
    if (!env?.id || !record?.id) return;
    navigate(paths.dashboard.simulate.simulationRun(env.id, `${record.id}-t${trialN}`));
    onClose?.();
  };
  /* Opened beside the improvement's runs list, it's the decision only — the
     runs and their chart are already on the page. */
  const [tab, setTab] = useState(decisionOnly ? "changes" : "trials");
  const [openTrial, setOpenTrial] = useState(null);
  /* Running for over a day without finishing means it was timed out — the
     same reading the Improvements list and the Error Feed chip use. */
  /* An ending decided before searching reads as running until its
     Reproduce has played, then as that ending (statusOf). */
  const status = statusOf(record);
  const timedOut = status === "running" && staleSince(record);
  const running = status === "running" && !timedOut;
  /* Started from an Error Feed issue whose failure didn't happen — stops here. */
  const notReproduced = status === OPT_STATUS.NOT_REPRODUCED;
  const failedToRun = status === OPT_STATUS.FAILED || timedOut;
  /* A new goal improvement where nothing fell short of the goal. */
  const nothing = status === OPT_STATUS.NOTHING;
  /* Checking the finalists alone would have gone over the budget. */
  const overBudget = status === OPT_STATUS.OVER_BUDGET;
  const [editingScenarios, setEditingScenarios] = useState(false);
  const result = record.result;
  /* Reproducing on the way to an ending already known — no search to show. */
  const pending = running && !result && !!record.pendingEnding;
  /* Where a running improvement is — Reproducing, Searching, Verifying —
     re-read while the trace plays. */
  const [phaseNow, setPhaseNow] = useState(Date.now());
  useEffect(() => {
    if (!running) return undefined;
    const t = setInterval(() => setPhaseNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [running]);
  const phase = running ? improvementPhaseOf(record, phaseNow) : null;
  /* "agent v3 × env v2" — the same pairing every run is named by. */
  const identity = improvementIdentityOf(record, env, envState);
  const diag = result ? diagnosisSummaryOf(record, result) : null;
  const winner = result?.winner || null;
  /* The model the agent runs on — the one a fix must leave alone. */
  const agentModel = useMemo(() => {
    if (!envState?.agent) return null;
    const def = agentDefinitionOf({ agent: envState.agent, env, envState });
    return def.model?.visible ? def.model.name : null;
  }, [env, envState]);
  /* One reading of the run: its ending, the plain-language verdict, the limits. */
  const outcome = improvementOutcome(record, { model: agentModel });
  const ending = outcome.ending.key;
  const shippable = ending === "ready" || ending === "stopped-early" || ending === "budget";
  /* Cautions the verdict sentence doesn't already carry (a large overfit gap,
     a held-out set that couldn't show a gain). */
  /* A fix that fixed its issue isn't cautioned for the rest of the suite
     standing still — that's the issue line's job. */
  const soft = optimizationVerdict(record);
  const caution = shippable ? (blockingVerdict(result) || (outcome.issue?.fixed && soft?.tone === "#DC2626" ? null : soft)) : null;
  const blockedBy = shippable ? null : outcome.headline;
  const model = OPTIMIZER_MODELS.find((m) => m.id === record.model);
  /* What ships — the search's pick, or another trade-off someone chose.
     Only a verified change ships: a chosen run is verified first. */
  const shipped = shippedCandidate(record);
  const verified = chosenVerified(record);
  const choose = (n) => patch({
    optimizations: (envState?.optimizations || []).map((o) => (o.id === record.id ? { ...o, preferredTrial: n || undefined } : o)),
  });
  const winnerFiles = (shipped?.proposals || [])
    .map((p) => changeFileFor(env, p)?.path)
    .filter(Boolean);
  const fromRun = (envState?.runs || []).find((r) => r.id === record.fromRunId);
  const { state: simState } = useSimStore();
  const agent = envState?.agent || null;
  /* Where candidates ran and how far the proof can be trusted. */
  const proof = record.proof || (agent ? (() => { const p = proofKindOf(agent); return { kind: p.kind, label: p.label, tier: p.tier, tierLabel: p.tierLabel }; })() : null);
  const suggestion = ending === "suggestion";
  /* Which members the change touches, and the channels it wasn't checked on. */
  const scope = membersChanged(shipped?.proposals || outcome.suggestion || winner?.proposals || [], { agent, limits: record.limits, target: record.targetMember });
  const checkedSurfaces = [...new Set([env?.surface, ...(record.regressionEnvIds || []).map((id) => (simState?.myEnvironments || []).find((e) => e.id === id)?.surface)].filter(Boolean))];
  const channelNotes = uncheckedChannelNotes(agent, checkedSurfaces);
  /* Scenarios this improvement wrote that the environment doesn't have yet. */
  const issueRows = improvementScenariosNotIn(envState, record);
  const addIssueRows = () => patch(addImprovementScenariosPatch(envState, record));
  const envVersion = env && envState ? currentEnvVersion(env, envState)?.label || null : null;
  /* Approving mints the environment version with the new parameter, runs
     the baseline again on it, and only then compares the candidate there. */
  const contractNext = env && envState ? nextContractVersion(env, envState) : null;
  const approveContract = () => patch(contractApprovalPatch({ env, envState, record, from: envVersion }));
  /* Shown as environment versions (snapshots): the one the improvement was
     frozen on, and the one the approval minted. */
  const frozenOn = env && envState ? snapshotOfImprovement(record, env, envState)?.label || null : null;
  const ca = record.contractApproval;
  const shownApproval = ca ? {
    ...ca,
    to: ca.toSnapshot || (env && envState ? envLabelOf(env, envState, ca.to) : ca.to),
    from: ca.fromSnapshot || frozenOn || ca.from,
  } : ca;

  /* The customer's coding agent on this improvement, if one was handed it. */
  const integrations = useIntegrations();
  /* Once the winner ships, the page follows it: Fix in review → Released →
     Watching → Resolved or Reopened (RSI PRD, Part 3) — the same reading the
     Error Feed's chip uses. */
  const lifecycle = shippedStateOf(record, envState, integrations.prs || [], { envId: env?.id });
  const session = env?.id ? improverSessionFor(env.id, record.id, integrations) : null;
  const [, setTick] = useState(0);
  const attemptsNow = session && result
    ? attemptsOf(record, session, { surface: env?.surface, budgetLeft: (record.budget || Infinity) - searchSpend(result, env?.surface) })
    : [];
  const sessionNow = session && result ? sessionState(session, attemptsNow) : null;
  const improverLabel = codingAgentById(session?.agentId)?.label || "Your coding agent";
  /* Re-read while the coding agent is still submitting. */
  useEffect(() => {
    if (sessionNow?.key !== "waiting") return undefined;
    const t = setInterval(() => setTick((n) => n + 1), 2000);
    return () => clearInterval(t);
  }, [sessionNow?.key]);
  const briefTarget = env?.id ? { kind: "improvement", envId: env.id, optId: record.id } : undefined;
  /* Handing the brief over starts the session: its submissions show here. */
  const handBrief = () => {
    if (env?.id && record.result) {
      const deployed = ["endpoint", "framework"].includes(agentSourceOf(envState?.agent).kind);
      startImproverSession({ agentId: connectedCodingAgent()?.id || null, envId: env.id, optId: record.id, where: deployed ? "deployed" : "local" });
    }
    return brief();
  };

  /* Everything a coding agent needs to take this further — the issue or
     goal, what the search found, what still fails, and the constraints. */
  const brief = () => {
    const proposals = winner?.proposals || result?.pool || [];
    /* Only what the search could see — the held-back set never leaves here. */
    const stillFailing = visibleFailingOf(result, winner, tasks);
    return codingAgentBrief({
      agent: { name: envState?.agent?.name || env?.name, key: envState?.agent?.key },
      env,
      pairing: identity,
      /* An issue fix — from the Error Feed or a run's Insights — hands over the issue itself. */
      issue: record.trigger?.kind === "error-feed" || record.trigger?.source === "run-insights"
        ? { id: record.trigger.issueId, title: record.trigger.issueTitle, severity: record.trigger.severity }
        : record.trigger?.goal ? `Raise ${record.trigger.goal.label || "the pass rate"}` : name || record.name,
      /* The cause, not the verdict — what the search found is its own line. */
      rootCause: [
        record.snapshot?.rootCause || (diag?.code ? null : diag?.cause) || (record.diagnosis || [])[0]?.headline || record.trigger?.issueRoot?.subGoal || null,
        outcome.headline ? `What the search found: ${outcome.headline}` : null,
      ].filter(Boolean).join("\n\n") || null,
      suggestedFix: (outcome.suggestion || proposals).slice(0, 4),
      /* Production-derived: scrubbed of personal data inside the brief. */
      evidence: briefEvidenceOf({ record, result, winner, tasks }),
      failingScenarios: stillFailing,
      /* Where it goes wrong and what each target scenario did — never the held-back set. */
      diagnosis: diag?.code || null,
      breakdown: briefBreakdownOf(record),
      limits: record.limits,
      model: agentModel,
      proof: proofDisplay(proof),
      members: scope.names,
    });
  };

  /* Searches anyway — or tries a failed run again — from now. */
  const continueAnyway = () => patch({
    optimizations: (envState?.optimizations || []).map((o) => (o.id === record.id
      ? { ...o, status: OPT_STATUS.RUNNING, startedAt: new Date().toISOString(), result: o.result || o.pendingResult, pendingResult: undefined, pendingEnding: undefined, ...(notReproduced && { reproduce: { ...o.reproduce, overridden: true } }) }
      : o)),
  });

  /* Reproduce has played: the ending it was heading for is saved. */
  const settleNow = () => patch?.({
    optimizations: (envState?.optimizations || []).map((o) => (o.id === record.id && o.status === OPT_STATUS.RUNNING && o.pendingEnding ? settlePending(o) : o)),
  });

  /* A finished run that hasn't shipped reads quietly: no stepper (the status
     chip says how it ended), the trigger folded into the subtitle. */
  const quietHeader = !running && !lifecycle && !!result && !notReproduced && !failedToRun;
  const startedFrom = record.trigger?.kind === "error-feed"
    ? `from ${record.trigger.issueId}`
    : fromRun ? `from Run ${fromRun.ordinal || ""}`.trim()
      : record.trigger?.kind === "schedule" ? "scheduled" : record.trigger?.kind === "manual" ? `started by ${record.trigger.by || "you"}` : record.trigger?.kind === "goal" ? "from a goal" : "from Debug failures";

  /* The hand-over, as numbered next steps — in Ship and in the full record. */
  const handoffOf = () => (
    <OmegaHandoff
      env={env}
      envState={envState}
      patch={patch}
      included={shipped?.proposals || winner.proposals || []}
      /* Nothing held back: the re-run is held to the target scenarios instead. */
      projected={result.split?.thin ? (shipped || winner).score : shipped?.held ?? result.heldScore}
      projectedOn={result.split?.thin ? "the scenarios it was tuned on" : "held-back scenarios"}
      current={result.split?.thin ? result.base : result.heldBase}
      willFix={(shipped || winner).fixed || 0}
      handoffKey={handoff?.key}
      landedVersion={handoff?.landedVersion}
      provenance={handoff?.provenance}
      onLand={handoff?.onLand}
      onRerun={handoff?.onRerun}
      brief={handBrief}
      briefTarget={briefTarget}
      proof={proofMarkdown({ record, outcome, name, agent, members: scope.names, channelNotes: scope.shared || !scope.hasMembers ? channelNotes : [], identity })}
    />
  );

  /*
    Ship, on its own: what ships and what happens next — nothing else. The
    verdict, runs and constraints are on the page behind it; the evidence is
    one click away (How it was decided).
  */
  if (shipOnly && result && winner) {
    const ships = shipped || winner;
    const count = (ships.proposals || []).length;
    return (
      <Stack sx={{ height: "100%", minHeight: 0 }}>
        <Stack direction="row" alignItems="center" spacing={1.5} sx={{ px: 2.5, py: 2, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}>
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "s1", fontWeight: 700 }}>{`Ship Run ${ships.n}`}</Typography>
            <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
              {[agent?.name || env?.name, agent ? agentSourceOf(agent).label : null, record.trigger?.issueId].filter(Boolean).join(" · ")}
            </Typography>
          </Box>
          {onOpenDecision && (
            <Button
              size="small" variant="outlined" onClick={onOpenDecision}
              startIcon={<Iconify icon="solar:shield-check-linear" width={15} />}
              sx={{ typography: "s2", fontWeight: 700, color: "text.primary", borderColor: "divider", flexShrink: 0 }}
            >
              How it was decided
            </Button>
          )}
          <IconButton size="small" onClick={onClose}>
            <Iconify icon="eva:close-fill" width={17} />
          </IconButton>
        </Stack>
        <Box sx={{ flex: 1, overflowY: "auto", minHeight: 0, px: 2.5, py: 2.5 }}>
          <Typography sx={{ typography: "s2", fontWeight: 700 }}>What ships</Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle", mb: 1 }}>
            {`${count} ${count === 1 ? "change" : "changes"} · ${result.split?.thin ? "checked on its target and must-pass scenarios" : "verified on held-back and must-pass scenarios"}`}
          </Typography>
          <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1 }}>
            <WinnerChanges env={env} winner={ships} record={record} members={scope.hasMembers ? proposalMembers(ships.proposals || [], { agent, limits: record.limits, target: record.targetMember }) : null} />
          </Box>
          <Typography sx={{ typography: "s2", fontWeight: 700, mt: 3 }}>What happens next</Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle", mb: 1 }}>
            {"Nothing goes live from here — it's released when someone makes it live where the agent runs."}
          </Typography>
          {verified || lifecycle ? handoffOf() : (
            <Button
              size="small" variant="contained" color="primary" onClick={() => onRunWinner?.(null)} disabled={!onRunWinner}
              startIcon={<Iconify icon="solar:play-bold" width={14} />}
              sx={{ typography: "s2", fontWeight: 700 }}
            >
              {`Verify Run ${ships.n}`}
            </Button>
          )}
        </Box>
      </Stack>
    );
  }

  return (
    <Stack sx={{ height: "100%", minHeight: 0 }}>
      {/* ── identity ── */}
      <Stack
        direction="row" alignItems="center" spacing={1.5}
        sx={{ px: 2.5, py: 2, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}
      >
        {backLabel ? (
          /* Where back goes, said — an issue's fix returns to the issue. */
          <Button
            size="small" onClick={onBack}
            startIcon={<Iconify icon="eva:arrow-ios-back-fill" width={15} />}
            sx={{ typography: "s2", fontWeight: 600, color: "text.secondary", flexShrink: 0, ...(/^ISS-/.test(backLabel) && { fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" }) }}
          >
            {backLabel}
          </Button>
        ) : (
          <IconButton size="small" onClick={onBack}>
            <Iconify icon="eva:arrow-ios-back-fill" width={17} />
          </IconButton>
        )}
        <Box flex={1} minWidth={0}>
          <Stack direction="row" alignItems="center" spacing={1} flexWrap="wrap" rowGap={0.5}>
            <Typography sx={{ typography: "s1", fontWeight: 700 }}>{name || record.name}</Typography>
            <Chip
              size="small"
              label={phase?.label || (lifecycle ? `${lifecycle.label}${lifecycle.detail && lifecycle.key === "watching" ? ` · ${lifecycle.detail}` : ""}` : outcome.ending.label)}
              sx={{
                height: 19, borderRadius: 0.5, bgcolor: "transparent", border: "1px solid",
                color: running ? "#2563EB" : lifecycle ? "text.primary" : ending === "ready" ? "#16A34A" : "text.secondary",
                borderColor: running ? alpha("#2563EB", 0.4) : lifecycle ? "divider" : ending === "ready" ? alpha("#16A34A", 0.4) : "divider",
                "& .MuiChip-label": { px: 0.75, typography: "s3", fontWeight: 700 },
              }}
            />
          </Stack>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            {identity ? `${identity} · ` : ""}
            {/* Finished and ready: just where it came from — counts and ids are in the Receipt. */}
            {quietHeader ? startedFrom : pending
              ? "reproducing on today's version first"
              : notReproduced || failedToRun || overBudget || !result
              ? "stopped before searching"
              : suggestion
                ? `${record.reproduce?.total ? `Reproduced on ${record.reproduce.reproduced || 0} of ${record.reproduce.total} scenarios` : `${result.trainMeasured + result.heldMeasured} scenarios measured`} · no change tried`
                : `${record.stoppedAtBudget ? `${result.trials.length} of ${record.stoppedAtBudget.of}` : result.stoppedEarly && !running ? `${result.trials.length} of up to ${result.stoppedEarly.of}` : result.trials.length} runs × ${result.trainMeasured} target ${result.trainMeasured === 1 ? "scenario" : "scenarios"}${result.stoppedEarly && !running ? (result.stoppedEarly.complete ? " · stopped once every failing target was fixed" : ` · stopped early, no gain in the last ${noGainRunsOf(result.stoppedEarly)}`) : ""}`}
          </Typography>
        </Box>
        {!running && result && <ReceiptButton record={record} outcome={outcome} attempts={attemptsNow} identity={identity} envSnapshot={frozenOn} />}
        {!running && result && <CopyBriefButton getBrief={handBrief} size="small" target={briefTarget} />}
        <IconButton size="small" onClick={onClose}>
          <Iconify icon="eva:close-fill" width={17} />
        </IconButton>
      </Stack>

      {!quietHeader && <ImprovementSteps
        shipped={lifecycle}
        record={record}
        fromRun={fromRun}
        versionName={versionName}
        running={running}
        phase={phase}
        diag={diag}
        outcome={outcome}
        onOpenIssue={() => { navigate(paths.dashboard.errorFeed.detail(record.trigger.issueId)); onClose?.(); }}
        onOpenRun={fromRun && env?.id ? () => { navigate(paths.dashboard.simulate.simulationRun(env.id, fromRun.id)); onClose?.(); } : null}
      />}

      {nothing && (
        <Box sx={{ flex: 1, overflowY: "auto", px: 2.5, py: 2.5 }}>
          <Typography sx={{ typography: "s1", fontWeight: 700 }}>{outcome.ending.label}</Typography>
          <Typography sx={{ typography: "s2", color: "text.secondary", mt: 0.5, maxWidth: 640 }}>
            {`${outcome.headline} Nothing was searched and nothing was charged beyond measuring it. Pick another goal, or add harder scenarios for this one.`}
          </Typography>
          <Stack direction="row" spacing={1} sx={{ mt: 2 }}>
            <Button
              size="small" variant="outlined"
              onClick={() => { navigate(paths.dashboard.simulate.environmentStep(env.id, "scenarios")); onClose?.(); }}
              sx={{ typography: "s2", fontWeight: 700, color: "text.primary", borderColor: "divider" }}
            >
              Add scenarios
            </Button>
          </Stack>
        </Box>
      )}

      {overBudget && (
        <OverBudgetBody record={record} outcome={outcome} env={env} envState={envState} patch={patch} onClose={onClose} />
      )}

      {/* Could not reproduce: each scenario and its repeats, and what to do. */}
      {notReproduced && (
        <>
          <NotReproducedBody
            record={record}
            envName={env?.name}
            spent={creditsLabel(improvementTotalSpend(record, env?.surface))}
            onEdit={issueDraftsOf(record).length > 0 ? () => setEditingScenarios(true) : undefined}
            onContinue={continueAnyway}
            onOpenIssue={record.trigger?.issueId ? () => { navigate(paths.dashboard.errorFeed.detail(record.trigger.issueId)); onClose?.(); } : undefined}
          />
          <IssueScenarioEditor
            open={editingScenarios}
            drafts={issueDraftsOf(record)}
            perScenario={record.reproduce?.perScenario}
            onClose={() => setEditingScenarios(false)}
            onReproduce={(edits) => {
              const next = reproduceAgainPatch({ record, env, envState, state: simState, edits });
              if (next) patch(next);
              setEditingScenarios(false);
            }}
          />
        </>
      )}

      {failedToRun && !notReproduced && (
        <Box sx={{ flex: 1, overflowY: "auto", px: 2.5, py: 2.5 }}>
          <Typography sx={{ typography: "s1", fontWeight: 700 }}>{outcome.ending.label}</Typography>
          <Typography sx={{ typography: "s2", color: "text.secondary", mt: 0.5, maxWidth: 640 }}>{outcome.headline}</Typography>
          <Stack direction="row" spacing={1} sx={{ mt: 2 }}>
            <Button size="small" onClick={continueAnyway} sx={{ typography: "s2", fontWeight: 700, color: "text.secondary" }}>
              Try again
            </Button>
          </Stack>
        </Box>
      )}

      {/* Reproducing first, on the way to an ending already known. */}
      {pending && (
        <Box sx={{ flex: 1, overflowY: "auto" }}>
          <RunningConstraints record={record} model={agentModel} />
          <ImprovementProgress
            title="Reproducing"
            subtitle={record.reproduce?.total
              ? `${record.reproduce.total} scenarios from ${record.trigger?.issueId || "the issue"} on ${record.baseAgentVersion ? versionName(record.baseAgentVersion) : "today's version"}`
              : `The environment's scenarios on ${record.baseAgentVersion ? versionName(record.baseAgentVersion) : "today's version"}`}
            steps={reproduceStepsOf(record)}
            trials={[]}
            stepMs={TRACE_STEP_MS}
            onDone={settleNow}
          />
        </Box>
      )}

      {running && result && (
        <Box sx={{ flex: 1, overflowY: "auto" }}>
          {/* What every candidate is held to, visible while they're tried. */}
          <RunningConstraints record={record} model={agentModel} />
          {/* The same shape as Debug failures' read: a strip, the steps one
              line each, and candidate runs arriving one at a time. */}
          <ImprovementProgress
            title={phase?.label || (record.fixDepth === "suggestion" ? "Reproducing" : "Searching")}
            subtitle={record.fixDepth === "suggestion"
              ? `${result.trainMeasured + result.heldMeasured} scenarios on the agent as it runs today`
              : `${result.trials.length} runs × ${result.trainMeasured} target ${result.trainMeasured === 1 ? "scenario" : "scenarios"}${result.textSearch ? " in text" : ""}`}
            steps={searchTrace(result, record)}
            trials={record.fixDepth === "suggestion" ? [] : result.trials}
            stepMs={TRACE_STEP_MS}
            onDone={onDone}
          />
        </Box>
      )}

      {!running && suggestion && result && (
        <SuggestionBody
          record={record} outcome={outcome} env={env} envState={envState} patch={patch} agent={agent}
          scope={scope} channelNotes={channelNotes} issueRows={issueRows} onAddIssueRows={addIssueRows}
          handoff={handoff} brief={handBrief} briefTarget={briefTarget} name={name} agentModel={agentModel} identity={identity} frozenOn={frozenOn}
        />
      )}

      {!running && !suggestion && !notReproduced && !failedToRun && !nothing && !overBudget && result && (
        <Box sx={{ flex: 1, overflowY: "auto", minHeight: 0 }}>
          {/* ── the verdict, in plain words, first ── */}
          <Box sx={{ px: 2.5, pt: 2.25 }}>
            <Stack direction="row" alignItems="flex-start" spacing={1.25}>
              {/* A run someone chose is unproven until it's verified — no green check yet. */}
              <Iconify
                icon={ending === "ready" && !(outcome.chosen && !outcome.chosen.verified) ? "solar:check-circle-bold" : ending === "budget" || ending === "stopped-early" ? "solar:pause-circle-linear" : ending === "ready" ? "solar:clock-circle-linear" : "solar:close-circle-linear"}
                width={20}
                sx={{ color: ending === "ready" && !(outcome.chosen && !outcome.chosen.verified) ? "#16A34A" : "text.secondary", flexShrink: 0, mt: "2px" }}
              />
              <Typography sx={{ typography: "m3", fontWeight: 600, lineHeight: 1.35 }}>{outcome.headline}</Typography>
            </Stack>
            {/* The facts behind the headline, one per row, so they scan. */}
            <Facts
              rows={[
                diag?.surface && ["Changed", `${diag.surface.label.charAt(0).toUpperCase()}${diag.surface.label.slice(1)}`],
                issueLine(outcome.issue, outcome.held, { thin: outcome.thin }) && ["Issue scenarios", issueLine(outcome.issue, outcome.held, { thin: outcome.thin })],
                outcome.strength && ["Strength", `${outcome.strength.label} — ${outcome.strength.note}`],
                ["Proof", proof ? proofDisplay(proof).line : "Run by Future AGI"],
                /* Everything it spent — reproducing, the search, a coding agent's attempts. */
                ["Spent", `${creditsLabel(improvementTotalSpend(record, env?.surface, { attempts: attemptsNow }))}${record.budget ? ` of ${creditsLabel(record.budget)}` : ""}`],
              ]}
            />
            {/* A fix also answers for the issue it was started for. */}
            {/* The smallest change that verifies was kept over a deeper one. */}
            {result.shallower && winner && (
              <Typography sx={{ typography: "s2", color: "text.secondary", mt: 0.5, pl: 4 }}>
                {`Run ${winner.n} was kept over Run ${result.shallower.over} (${result.shallower.gap ? `+${result.shallower.gap} pt${result.shallower.gap === 1 ? "" : "s"}` : "same score"}): it fixes this with a ${surfaceWord(winner, result)} change instead of a ${surfaceWord(result.trials.find((t) => t.n === result.shallower.over), result)} one — the smallest change that verifies wins.`}
              </Typography>
            )}
            {/* Another trade-off was chosen over the search's pick. */}
            {shipped?.chosen && (
              <Typography
                {...clickable(() => setTab("tradeoffs"))}
                sx={{ typography: "s2", color: "text.secondary", mt: 0.5, pl: 4, cursor: "pointer", "&:hover": { color: "text.primary" }, ...focusRing }}
              >
                {`You chose Run ${shipped.n} over the search's pick (Run ${winner.n}): ${winner.score > shipped.score ? `${winner.score - shipped.score} pts lower` : winner.score < shipped.score ? `${shipped.score - winner.score} pts higher` : "the same score"} on the target scenarios, ${shipped.projected ? "a projected " : ""}${shipped.held}% held back. ${verified ? "Verified on the full suite — it ships instead." : "It ships instead once it's verified on the full suite."} See Trade-offs.`}
              </Typography>
            )}
            {/* The coding agent's progress, one click from its attempts. */}
            {sessionNow && (
              /* Beside the runs list there's no Runs tab here — the runs are on the page. */
              <Typography
                {...clickable(decisionOnly ? null : () => setTab("trials"))}
                sx={{ typography: "s2", color: "text.secondary", mt: 0.5, pl: 4, ...(!decisionOnly && { cursor: "pointer", "&:hover": { color: "text.primary" } }), ...focusRing }}
              >
                {sessionNow.key === "accepted"
                  ? shippable
                    ? `${sessionNow.attempt.label} was also accepted by Future AGI — ${decisionOnly ? "see its attempts under the runs" : "see Runs"}.`
                    : `Our own search found no safe change, but ${sessionNow.attempt.label} passed every check — ${decisionOnly ? "its proof is under the runs" : "see Runs for its proof"}.`
                  : sessionNow.key === "budget"
                    ? `${improverLabel} stopped without an accepted run — ${decisionOnly ? "see its attempts under the runs" : "see Runs"}.`
                    : decisionOnly
                      ? `${improverLabel} is working on this — its attempts are listed under the runs.`
                      : `${improverLabel} is working on this — its attempts show in Runs.`}
              </Typography>
            )}
            {/* Re-read is on the stepper; only an outside change needs saying. */}
            {record.reread?.changed && (
              <Typography sx={{ typography: "s2", color: "text.secondary", mt: 0.5, pl: 4 }}>
                {`Changed outside Future AGI, so the baseline ran on ${record.reread.version || "the new version"}.`}
              </Typography>
            )}
            {/* Which members it touches — only when there's something to watch for. */}
            {winner && channelNotes.length > 0 && (
              <Box sx={{ pl: 4, mt: 0.5 }}>
                <ChangeScope scope={scope} channelNotes={channelNotes} target={record.targetMember} />
              </Box>
            )}
            {/* A caution in one line; the why on hover. */}
            {caution && caution.tone !== "#16A34A" && (
              <Tooltip arrow placement="bottom-start" title={caution.body || ""}>
                <Typography sx={{ typography: "s2", color: "text.secondary", mt: 0.75, pl: 4 }}>
                  <Box component="span" sx={{ cursor: "help", borderBottom: "1px dotted", borderColor: "text.disabled" }}>{`${caution.title}.`}</Box>
                </Typography>
              </Tooltip>
            )}
            {!!result.hollow?.length && (
              <Box sx={{ pl: 4 }}><TaskLinks ids={result.hollow.map((h) => h.id)} tasks={tasks} onOpen={onOpenTask} /></Box>
            )}
          </Box>

          {/* ── the numbers behind it ── */}
          {winner && (
            <Box sx={{ px: 2.5, pt: 2.25 }}>
            <Box
              sx={{
                display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
                border: "1px solid", borderColor: "divider", borderRadius: 1, overflow: "hidden",
                "& > *": { px: 1.75, py: 1.25, borderLeft: "1px solid", borderColor: "divider", ml: "-1px" },
              }}
            >
              {/* The numbers of what ships — the search's pick, or the run
                  someone chose (its held-back figure projected until verified). */}
              <Metric
                label={shipped?.projected ? "Held back · projected" : "Held back"} value={result.split?.thin ? "Thin" : `${signedPts(outcome.heldScore - result.heldBase)}`}
                tone={result.split?.thin ? undefined : outcome.heldScore < result.heldBase ? "#DC2626" : outcome.heldScore > result.heldBase ? "#16A34A" : undefined}
                sub={result.split?.thin
                  ? "too few groups reproduced to hold any back"
                  : `${result.heldBase}% → ${outcome.heldScore}%${result.noise ? ` · noise ±${result.noise}` : ""}`}
                hint={`Scenarios the search never saw — ${result.heldMeasured} here${heldSplitText(result.split) ? `: ${heldSplitText(result.split)}` : ""}.${result.confirm ? ` Measured on a re-run after the pick (${result.confirm.selectionScore}% when picked) — the run that picks a winner can't also prove it.` : ""} This is the number worth quoting; the target score is what the search was allowed to fit to. Noise is what two runs of the same agent differ by; a gain has to clear it.`}
              />
              <Metric label="Target" value={signedPts((shipped || winner).score - result.base)} sub={`${result.base}% → ${(shipped || winner).score}%`} hint={`The ${result.trainMeasured} scenarios the search tuned on and saw in full.`} />
              <Metric
                label="Overfit gap"
                value={result.split?.thin || outcome.gap == null ? "—" : `${outcome.gap} pts`}
                tone={!result.split?.thin && outcome.gap > 12 ? "#DC2626" : undefined}
                sub="target minus held back"
                hint="How much more the change gained on the scenarios it was tuned on than on ones it never saw. A large gap means it fitted those scenarios rather than the task."
              />
              <Metric
                label="Cost per conversation"
                value={outcome.costKnown ? signedPct(outcome.effect.cost) : "—"}
                sub={outcome.costKnown ? "vs today" : "not measured"}
                hint={outcome.costKnown ? undefined : "This agent reports no cost: only Vapi and Retell do, or traceAI spans. Turn on production tracing to measure it."}
              />
              <Metric label="Latency" value={signedPct(outcome.effect.latency)} sub="vs today" />
            </Box>
            </Box>
          )}

          {/*
            The winner's next step, right after the verdict: run it on the
            full suite. Hand-off sits at the bottom, once.
          */}
          {/* Only a winner that can ship gets the next step; a run that found
              nothing safe doesn't offer one. */}
          {winner && shippable && (
            <WinnerBar
              winner={shipped || winner} files={winnerFiles} blockedBy={blockedBy} onRun={onRunWinner}
              agent={envState?.agent} agentVersion={record.baseAgentVersion} needsVerify={!verified}
            />
          )}

          {/* ── the limits and the proof: one line each while they all held ── */}
          {(!!outcome.checks?.length || winner) && (
            <Stack spacing={1} sx={{ px: 2.5, pt: 2.25 }}>
              {!!outcome.checks?.length && <LimitsChecklist checks={outcome.checks} />}
              {winner && <VerificationLadder record={record} trial={shipped || winner} footer={<MustPassNote result={result} bare />} />}
            </Stack>
          )}


          {/* Each finalist, once in every other environment picked. */}
          <OtherEnvironments result={result} />

          {/* Candidates stopped at Static checks, before anything ran. */}
          <StaticNotice result={result} />

          {/* A change the frozen environment can't prove. */}
          <ContractNotice contract={result.contract} envVersion={shownApproval?.from || frozenOn} nextVersion={contractNext} approval={shownApproval} onApprove={approveContract} />

          {/* Code candidates the sealed environment can't run. */}
          <UnfitNotice result={result} />

          {/* The scenarios this improvement wrote, until they're added. */}
          <IssueScenariosNotice rows={issueRows} envName={env?.name} onAdd={addIssueRows} />

          {/* ── trials, what changed, and what it did to each scenario ── */}
          <Box sx={{ px: 2.5, pt: 2 }}>
            <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1 }}>
              <Tabs
                value={tab} onChange={(_, v) => setTab(v)}
                sx={{ px: 1.5, minHeight: 40, borderBottom: "1px solid", borderColor: "divider" }}
              >
                {!decisionOnly && <Tab value="trials" label="Runs" sx={{ minHeight: 40 }} />}
                {!!result.finalists?.length && <Tab value="finalists" label="Finalists" sx={{ minHeight: 40 }} />}
                <Tab value="changes" label="Changes" sx={{ minHeight: 40 }} />
                <Tab value="scenarios" label="Per scenario" sx={{ minHeight: 40 }} />
                <Tab value="tradeoffs" label="Trade-offs" sx={{ minHeight: 40 }} />
              </Tabs>

              {tab === "trials" && (
                <>
                  {/* The coding agent's submissions first, judged the same way. */}
                  {session && (
                    <ImproverAttempts
                      record={record} env={env} envState={envState} session={session}
                      name={name || record.name} searchSpent={searchSpend(result, env?.surface)}
                    />
                  )}
                  {session && (
                    <Typography sx={{ px: 2, py: 1, typography: "s3", fontWeight: 700, color: "text.subtitle", textTransform: "uppercase", letterSpacing: 0.5, borderBottom: "1px solid", borderColor: "divider" }}>
                      Future AGI search
                    </Typography>
                  )}
                  {/* Best so far, then every run — open one to see its calls. */}
                  <Box sx={{ px: 2, pt: 1.5, pb: 1, borderBottom: "1px solid", borderColor: "divider" }}>
                    <Growth trials={result.trials} base={result.base} theme={theme} winner={winner} />
                  </Box>
                  <TrialList trials={result.trials} winner={winner} openTrial={openTrial} setOpenTrial={setOpenTrial} onOpenRun={openTrialRun} record={record} />
                </>
              )}
              {tab === "finalists" && <FinalistsTable result={result} bare />}
              {tab === "changes" && <WinnerChanges env={env} winner={shipped || winner} record={record} members={scope.hasMembers ? proposalMembers(shipped?.proposals || winner?.proposals || [], { agent, limits: record.limits, target: record.targetMember }) : null} />}
              {tab === "scenarios" && <ScenarioOutcomes result={result} winner={winner} />}
              {tab === "tradeoffs" && <TradeoffView record={record} chosen={record.preferredTrial} onChoose={choose} />}
            </Box>
          </Box>

          {/* ── the winner still has to reach the agent ── */}
          {winner && shippable && (
            <Box sx={{ px: 2.5, py: 2.5 }}>
              {/* Before it ships: nothing is applied — it goes out as a draft.
                  After: say where it is, never "nothing applied". */}
              <Typography sx={{ typography: "s2", fontWeight: 700 }}>
                {!lifecycle ? (verified ? "Hand the winner over" : "Verify it before it ships") : lifecycle.key === "review" ? "Shipped as a draft" : "Released"}
              </Typography>
              <Typography sx={{ typography: "s3", color: "text.subtitle", mb: 1.5 }}>
                {lifecycle?.key === "review"
                  ? lifecycle.awaitingRelease
                    ? `Landed as ${lifecycle.detail}. Mark it released once it's live — the Error Feed then watches for the issue.`
                    : `${lifecycle.via || "The draft"} is waiting for someone to deploy it. Once it's live, mark it released below — the Error Feed then watches for the issue.`
                  : lifecycle?.key === "watching"
                    ? `Released as ${lifecycle.version} — the new baseline. The Error Feed is watching for the issue · ${lifecycle.detail}.`
                    : lifecycle?.key === "resolved"
                      ? `Released as ${lifecycle.version} — it held through the watch window.`
                      : lifecycle?.key === "reopened"
                        ? `Released, but the issue was ${lifecycle.detail}. Fix again from the issue starts a new fix, linked to this one.`
                        : !verified
                          ? `You chose Run ${shipped?.n} over the search's pick. Only a passing verdict can mark a change verified, so it runs on the full suite — held-back and must-pass scenarios included — before it can be handed over.`
                          : ending === "budget"
                          ? `The best of a partial search — it stopped at the budget after ${record.stoppedAtBudget.trials} of ${record.stoppedAtBudget.of} runs. It passed every check; a bigger budget might find a better change. Nothing has been applied yet — it ships as a draft for someone to approve.`
                          : ending === "stopped-early"
                          ? `The best run, verified — the search stopped early after ${result.stoppedEarly.after} of up to ${result.stoppedEarly.of} runs, once ${noGainRunsOf(result.stoppedEarly)} in a row didn't beat it. It passed every check. Nothing has been applied yet — it ships as a draft for someone to approve.`
                          : "Nothing has been applied yet — it ships as a draft for someone to approve."}
              </Typography>
              {!verified && !lifecycle ? (
                <Button
                  size="small" variant="contained" color="primary" onClick={() => onRunWinner?.(null)} disabled={!onRunWinner}
                  startIcon={<Iconify icon="solar:play-bold" width={14} />}
                  sx={{ typography: "s2", fontWeight: 700 }}
                >
                  {`Verify Run ${shipped?.n}`}
                </Button>
              ) : (
              handoffOf()
              )}
            </Box>
          )}

          {/* ── what it was all measured against ── */}
          <FrozenInputs record={record} model={agentModel} identity={identity} envSnapshot={frozenOn} />

          {/* ── how it searched, for whoever wants it ── */}
          <SearchDetails record={record} result={result} model={model} />
        </Box>
      )}
    </Stack>
  );
}

OptimizationRunView.propTypes = {
  /* Beside the runs list: hide the runs and the search chart. */
  decisionOnly: PropTypes.bool,
  /* Ship's drawer: what ships and the next steps only. */
  shipOnly: PropTypes.bool,
  onOpenDecision: PropTypes.func,
  record: PropTypes.object,
  name: PropTypes.string,
  env: PropTypes.object,
  envState: PropTypes.object,
  patch: PropTypes.func,
  tasks: PropTypes.array,
  onOpenTask: PropTypes.func,
  onBack: PropTypes.func,
  /* Names where back goes, when that isn't obvious (an Error Feed issue id). */
  backLabel: PropTypes.string,
  onClose: PropTypes.func,
  onDone: PropTypes.func,
  onRunWinner: PropTypes.func,
  handoff: PropTypes.object,
};

/*
  Suggestion only — an agent we can only call. The failure was reproduced
  and diagnosed; the fix is a suggestion, not a proof, because no candidate
  can be tried on an agent we don't hold. Once it's applied and the new
  version is marked released, a re-run on the same environment version
  proves it.
*/
function SuggestionBody({ record, outcome, env, envState, patch, agent, scope, channelNotes, issueRows, onAddIssueRows, handoff, brief, briefTarget, agentModel, identity, frozenOn }) {
  const fix = outcome.suggestion || [];
  const depth = fixDepthOf(agent);
  return (
    <Box sx={{ flex: 1, overflowY: "auto", minHeight: 0 }}>
      <Box sx={{ px: 2.5, pt: 2.25 }}>
        <Stack direction="row" alignItems="flex-start" spacing={1.25}>
          <Iconify icon="solar:lightbulb-linear" width={20} sx={{ color: "text.secondary", flexShrink: 0, mt: "2px" }} />
          <Typography sx={{ typography: "m3", fontWeight: 600, lineHeight: 1.35 }}>{outcome.headline}</Typography>
        </Stack>
        <Typography sx={{ typography: "s2", color: "text.secondary", mt: 0.5, pl: 4, maxWidth: 720 }}>{depth.note}</Typography>
        <Box sx={{ pl: 4, mt: 0.5 }}>
          <ChangeScope scope={scope} channelNotes={channelNotes} target={record.targetMember} />
        </Box>
      </Box>

      <Box sx={{ px: 2.5, pt: 2.25 }}>
        <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle", textTransform: "uppercase", letterSpacing: 0.5, mb: 0.75 }}>
          Suggested fix
        </Typography>
        <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1 }}>
          <WinnerChanges env={env} winner={{ proposals: fix }} />
        </Box>
      </Box>

      <IssueScenariosNotice rows={issueRows} envName={env?.name} onAdd={onAddIssueRows} />

      {fix.length > 0 && (
        <Box sx={{ px: 2.5, py: 2.5 }}>
          <Typography sx={{ typography: "s2", fontWeight: 700 }}>Apply it, then prove it</Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle", mb: 1.5 }}>
            {"Nothing has been applied — we can't reach inside this agent. Apply the change where it runs, mark the new version released, and re-run on the same environment version."}
          </Typography>
          <OmegaHandoff
            env={env}
            envState={envState}
            patch={patch}
            included={fix}
            projected={null}
            current={null}
            willFix={0}
            handoffKey={handoff?.key}
            landedVersion={handoff?.landedVersion}
            provenance={handoff?.provenance}
            onLand={handoff?.onLand}
            onRerun={handoff?.onRerun}
            brief={brief}
            briefTarget={briefTarget}
            suggestion
          />
        </Box>
      )}

      <FrozenInputs record={record} model={agentModel} identity={identity} envSnapshot={frozenOn} />
    </Box>
  );
}
SuggestionBody.propTypes = {
  record: PropTypes.object, outcome: PropTypes.object, env: PropTypes.object, envState: PropTypes.object, patch: PropTypes.func, agent: PropTypes.object,
  scope: PropTypes.object, channelNotes: PropTypes.array, issueRows: PropTypes.array, onAddIssueRows: PropTypes.func,
  handoff: PropTypes.object, brief: PropTypes.func, briefTarget: PropTypes.object, agentModel: PropTypes.string, identity: PropTypes.string, frozenOn: PropTypes.string,
};

/*
  Stopped at budget before searching: checking the finalists alone would
  have cost more than the budget, so no change was tried. The way on is a
  bigger budget — picked here, within the agent's run budget, and the same
  improvement searches again (Reproduce already ran, so it isn't repeated).
  Past the agent's run budget, or over the time cap, it's a change on the
  agent's Constraints tab.
*/
function OverBudgetBody({ record, outcome, env, envState, patch, onClose }) {
  const navigate = useNavigate();
  const { agents, state } = useConnectedAgents();
  const key = envAgentKey(envState?.agent, env?.id);
  const agent = agents.find((a) => a.key === key) || null;
  const s = record.stoppedAtBudget || {};
  const plan = record.pendingResult || null;
  /* The whole search, plus what measuring today already spent. */
  const needed = plan ? Math.ceil(searchSpend(plan, env?.surface) + (record.spentFirst || 0)) : null;
  const ceiling = limitsOf(state, key)?.budget?.dollars || record.budget || 0;
  const timeOk = !(record.minutes > (record.limits?.budget?.minutes || Infinity));
  const choices = s.by !== "time" && timeOk && needed != null
    ? budgetsWithin(ceiling).filter((b) => b > (record.budget || 0) && b >= needed)
    : [];
  const [pick, setPick] = useState(null);
  const budget = pick ?? choices[0] ?? null;
  const openConstraints = agent ? () => { navigate(connectedAgentPath(agent.id, "constraints")); onClose?.(); } : null;
  const restart = () => patch?.({
    optimizations: (envState?.optimizations || []).map((o) => (o.id === record.id ? {
      ...o,
      status: OPT_STATUS.RUNNING,
      budget,
      /* Reproduce already ran — the search picks up after it. */
      startedAt: new Date(Date.now() - reproduceMsOf(o)).toISOString(),
      result: o.pendingResult,
      pendingResult: undefined,
      pendingEnding: undefined,
      stoppedAtBudget: undefined,
      completedAt: undefined,
    } : o)),
  });
  const why = s.by === "time"
    ? `Raise the time cap on the agent's Constraints tab, or improve towards a narrower goal.`
    : !timeOk
      ? "A bigger budget isn't enough — the search would also run past the time cap. Raise both on the agent's Constraints tab."
      : choices.length
        ? `The whole search needs about ${creditsLabel(needed)}. Pick a bigger budget and the same improvement searches again.`
        : `The whole search needs about ${creditsLabel(needed || 0)}, more than this agent's run budget of ${creditsLabel(ceiling)}. Raise it on the agent's Constraints tab, or improve towards a narrower goal.`;
  return (
    <Box sx={{ flex: 1, overflowY: "auto", px: 2.5, py: 2.5 }}>
      <Typography sx={{ typography: "s1", fontWeight: 700 }}>{outcome.ending.label}</Typography>
      <Typography sx={{ typography: "s2", color: "text.secondary", mt: 0.5, maxWidth: 640 }}>
        {`${outcome.headline} Nothing ran past the budget and no change was found. ${why}`}
      </Typography>
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mt: 2 }}>
        {choices.length > 0 && (
          <>
            <Select
              size="small" value={budget} onChange={(e) => setPick(e.target.value)}
              inputProps={{ "aria-label": "New budget" }}
              sx={{ height: 32, width: 160, typography: "s2", "& .MuiSelect-select": { typography: "s2", py: 0.75 } }}
            >
              {choices.map((b) => <MenuItem key={b} value={b} sx={{ typography: "s2" }}>{creditsLabel(b)}</MenuItem>)}
            </Select>
            <Button
              size="small" variant="contained" color="primary" onClick={restart} disabled={!patch}
              startIcon={<Iconify icon="solar:play-bold" width={14} />}
              sx={{ typography: "s2", fontWeight: 700 }}
            >
              Raise the budget and try again
            </Button>
          </>
        )}
        {openConstraints && (
          <Button
            size="small" variant={choices.length ? "text" : "outlined"} onClick={openConstraints}
            sx={{ typography: "s2", fontWeight: 700, color: choices.length ? "text.secondary" : "text.primary", ...(!choices.length && { borderColor: "divider" }) }}
          >
            {s.by === "time" || !timeOk ? "Raise the time cap" : choices.length ? "Change the agent's run budget" : "Raise the agent's run budget"}
          </Button>
        )}
      </Stack>
    </Box>
  );
}
OverBudgetBody.propTypes = {
  record: PropTypes.object, outcome: PropTypes.object, env: PropTypes.object, envState: PropTypes.object, patch: PropTypes.func, onClose: PropTypes.func,
};

/*
  The scenarios a fix built from its issue, edited in place: rename one, or
  leave it out, then reproduce again on the same improvement.
*/
const REPRO_WORD = { reproduced: "Reproduced", inconclusive: "Inconclusive", passed: "Not reproduced" };
function IssueScenarioEditor({ open, drafts, perScenario, onClose, onReproduce }) {
  const [edits, setEdits] = useState({});
  useEffect(() => { if (open) setEdits({}); }, [open]);
  const set = (id, patchIn) => setEdits((e) => ({ ...e, [id]: { ...e[id], ...patchIn } }));
  const included = drafts.filter((d) => edits[d.id]?.include !== false).length;
  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth PaperProps={{ sx: { borderRadius: 1.5, backgroundImage: "none" } }}>
      <Box sx={{ px: 2.5, py: 2, borderBottom: "1px solid", borderColor: "divider" }}>
        <Typography sx={{ typography: "s1", fontWeight: 700 }}>Edit this fix&apos;s scenarios</Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          Built from the issue&apos;s traces. They stay with this improvement — the environment&apos;s own scenarios don&apos;t change.
        </Typography>
      </Box>
      <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />} sx={{ maxHeight: "56vh", overflowY: "auto" }}>
        {drafts.map((d) => {
          const on = edits[d.id]?.include !== false;
          const state = perScenario?.[d.id]?.state;
          return (
            <Stack key={d.id} direction="row" alignItems="center" spacing={1.25} sx={{ px: 2, py: 1 }}>
              <Checkbox
                size="small" checked={on} onChange={(e) => set(d.id, { include: e.target.checked })}
                inputProps={{ "aria-label": `Include ${d.title}` }} sx={{ p: 0.5 }}
              />
              <TextField
                size="small" fullWidth disabled={!on}
                value={edits[d.id]?.title ?? d.title ?? ""}
                onChange={(e) => set(d.id, { title: e.target.value })}
                inputProps={{ "aria-label": "Scenario title" }}
                sx={{ "& .MuiInputBase-input": { typography: "s2" } }}
              />
              <Typography noWrap sx={{ typography: "s3", color: "text.subtitle", width: 104, flexShrink: 0, textAlign: "right" }}>
                {REPRO_WORD[state] || "—"}
              </Typography>
            </Stack>
          );
        })}
      </Stack>
      <Stack direction="row" alignItems="center" spacing={1.5} sx={{ px: 2.5, py: 1.75, borderTop: "1px solid", borderColor: "divider" }}>
        <Typography sx={{ typography: "s3", color: "text.subtitle", flex: 1 }}>
          {`${included} of ${drafts.length} included · each runs ${REPEATS_WORD} on the agent as it runs today`}
        </Typography>
        <Button onClick={onClose} sx={{ typography: "s2", fontWeight: 600, color: "text.secondary" }}>Cancel</Button>
        <Button
          variant="contained" color="primary" disabled={!included} onClick={() => onReproduce(edits)}
          startIcon={<Iconify icon="solar:play-bold" width={14} />}
          sx={{ typography: "s2", fontWeight: 700 }}
        >
          Reproduce again
        </Button>
      </Stack>
    </Dialog>
  );
}
IssueScenarioEditor.propTypes = { open: PropTypes.bool, drafts: PropTypes.array, perScenario: PropTypes.object, onClose: PropTypes.func, onReproduce: PropTypes.func };
const REPEATS_WORD = "three times";

/* Label and value rows under the verdict — the reasons, not a paragraph of them. */
function Facts({ rows }) {
  const list = rows.filter(Boolean);
  if (!list.length) return null;
  return (
    <Box sx={{ display: "grid", gridTemplateColumns: "116px minmax(0, 1fr)", columnGap: 2, rowGap: 0.5, mt: 1.25, pl: 4, maxWidth: 980 }}>
      {list.map(([k, v]) => (
        <Fragment key={k}>
          <Typography sx={{ typography: "s2", color: "text.subtitle" }}>{k}</Typography>
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>{v}</Typography>
        </Fragment>
      ))}
    </Box>
  );
}
Facts.propTypes = { rows: PropTypes.array };

function Metric({ label, value, sub, tone, hint }) {
  return (
    <Box>
      <Stack direction="row" alignItems="center" spacing={0.5}>
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{label}</Typography>
        {hint && (
          <Tooltip arrow title={hint}>
            <Box component="span" sx={{ display: "flex" }}>
              <Iconify icon="solar:info-circle-linear" width={12} sx={{ color: "text.disabled" }} />
            </Box>
          </Tooltip>
        )}
      </Stack>
      <Typography sx={{ typography: "m2", fontWeight: 700, color: tone || "text.primary", fontVariantNumeric: "tabular-nums" }}>
        {value}
      </Typography>
      {sub && <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{sub}</Typography>}
    </Box>
  );
}

Metric.propTypes = {
  label: PropTypes.string, value: PropTypes.string, sub: PropTypes.string,
  tone: PropTypes.string, hint: PropTypes.string,
};

/**
 * Best-so-far against trial number.
 *
 * Both series, deliberately. The staircase alone makes every search look like
 * it worked; the scatter under it shows how many candidates came in below where
 * it started, which is what says whether the algorithm searched or got lucky.
 */
function Growth({ trials, base, theme, winner }) {
  const W = 640;
  const H = 150;
  const L = 34;
  const T = 10;
  const B = 20;
  /*
    Monotonic view of the score. Each trial is displayed at the running
    max, not its own raw score — improvement, in the way the user
    reads it, only goes up. The raw scatter of "this candidate lost" is a
    detail the trials grid can carry; the headline chart states the story.
  */
  const running = trials.reduce((acc, t, i) => {
    const prev = i === 0 ? base : acc[i - 1];
    /* A trial that broke a must-pass scenario is rejected — it
       can't raise the best-so-far line, otherwise the peak (and the winner's
       on-chart value) would show a score no shippable candidate reached. */
    acc.push(t.brokeBlocker || t.cheats || compatFailed(t) || staticFailed(t) || t.overLimit ? prev : Math.max(prev, t.score));
    return acc;
  }, []);
  const top = Math.min(100, Math.max(base, ...running) + 6);
  const bottom = Math.max(0, Math.min(base, ...running) - 6);
  const x = (i) => L + (i / Math.max(1, trials.length - 1)) * (W - L - 12);
  const y = (v) => T + (1 - (v - bottom) / Math.max(1, top - bottom)) * (H - T - B);

  const line = trials.map((t, i) => `${i === 0 ? "M" : "L"}${x(i)},${y(running[i])}`).join(" ");
  const area = `${line} L${x(trials.length - 1)},${y(bottom)} L${x(0)},${y(bottom)} Z`;
  const ticks = [bottom, Math.round((bottom + top) / 2), top];
  const winIndex = trials.findIndex((t) => t.n === winner?.n);

  /* Geometry stretches to fill the box (preserveAspectRatio="none"), but text
     and dots are HTML overlays positioned in percentage of the same viewBox
     coordinates — so labels stay crisp and dots stay round at any width, with
     none of the horizontal stretch a full-SVG chart shows on a wide drawer. */
  const pctX = (xv) => (xv / W) * 100;
  const pctY = (yv) => (yv / H) * 100;
  const CHART_H = 190;
  const mono = "ui-monospace, Menlo, monospace";
  const showX = (i) => i === 0 || i === trials.length - 1 || (i + 1) % Math.ceil(trials.length / 6) === 0;

  return (
    <Box sx={{ position: "relative", width: "100%", height: CHART_H }}>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        style={{ display: "block", position: "absolute", inset: 0, width: "100%", height: "100%" }}
      >
        <defs>
          <linearGradient id="opt-growth" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#16A34A" stopOpacity={theme.palette.mode === "dark" ? 0.22 : 0.14} />
            <stop offset="100%" stopColor="#16A34A" stopOpacity={0} />
          </linearGradient>
        </defs>

        {ticks.map((v) => (
          <line
            key={v}
            x1={L} x2={W - 12} y1={y(v)} y2={y(v)}
            stroke={theme.palette.divider} strokeWidth={1}
            vectorEffect="non-scaling-stroke"
          />
        ))}

        <line
          x1={L} x2={W - 12} y1={y(base)} y2={y(base)}
          stroke={theme.palette.text.disabled} strokeWidth={1} strokeDasharray="3 3"
          vectorEffect="non-scaling-stroke"
        />

        <path d={area} fill="url(#opt-growth)" />
        <path
          d={line} fill="none" stroke="#16A34A" strokeWidth={1.75}
          strokeLinejoin="round" vectorEffect="non-scaling-stroke"
        />
      </svg>

      {/* ── crisp HTML overlays ── */}
      {/* y-axis % ticks */}
      {ticks.map((v) => (
        <Box
          key={v}
          sx={{
            position: "absolute", left: 0, top: `${pctY(y(v))}%`,
            width: `${pctX(L)}%`, transform: "translateY(-50%)",
            pr: 0.75, textAlign: "right",
            fontFamily: mono, fontSize: 10, fontWeight: 500,
            color: "text.disabled", lineHeight: 1, whiteSpace: "nowrap",
          }}
        >
          {v}%
        </Box>
      ))}

      {/* starting-prompt marker */}
      <Box
        sx={{
          position: "absolute", right: `${pctX(12)}%`, top: `${pctY(y(base))}%`,
          transform: "translateY(-125%)",
          fontFamily: mono, fontSize: 10, fontWeight: 500,
          color: "text.disabled", lineHeight: 1, whiteSpace: "nowrap",
        }}
      >
        starting prompt
      </Box>

      {/* trial dots */}
      {trials.map((t, i) => {
        const win = i === winIndex;
        const d = win ? 9 : 6;
        return (
          <Box
            key={t.n}
            sx={{
              position: "absolute", left: `${pctX(x(i))}%`, top: `${pctY(y(running[i]))}%`,
              width: d, height: d, transform: "translate(-50%, -50%)",
              borderRadius: "50%", border: "1.5px solid #16A34A",
              bgcolor: win ? "#16A34A" : "background.paper", boxSizing: "border-box",
            }}
          />
        );
      })}

      {/* winner value label */}
      {winIndex >= 0 && (
        <Box
          sx={{
            position: "absolute",
            left: `${pctX(x(winIndex))}%`, top: `${pctY(y(running[winIndex]))}%`,
            transform: `translate(${winIndex === 0 ? "0" : "-50%"}, calc(-100% - 8px))`,
            fontFamily: mono, fontSize: 11, fontWeight: 700, color: "#16A34A",
            lineHeight: 1, whiteSpace: "nowrap",
          }}
        >
          {winner?.score ?? running[winIndex]}%
        </Box>
      )}

      {/* x-axis trial numbers */}
      {trials.map((t, i) => (showX(i) ? (
        <Box
          key={`x${t.n}`}
          sx={{
            position: "absolute", left: `${pctX(x(i))}%`, bottom: 0,
            transform: "translateX(-50%)",
            fontFamily: mono, fontSize: 10, fontWeight: 500,
            color: "text.disabled", lineHeight: 1, whiteSpace: "nowrap",
          }}
        >
          {t.n}
        </Box>
      ) : null))}
    </Box>
  );
}

Growth.propTypes = {
  trials: PropTypes.array, base: PropTypes.number, theme: PropTypes.object, winner: PropTypes.object,
};

/**
 * The winner, and the one thing to do with it next.
 *
 * The search's own pick was already checked on the held-back set and every
 * must-pass scenario, so a full-suite run is optional — a run to keep in
 * the environment's history, not a gate. A run someone chose over it hasn't
 * been checked, so verifying it is the one thing to do. Neither runs when
 * the held-out verdict is red.
 */
function WinnerBar({ winner, files, blockedBy, onRun, agent, agentVersion, needsVerify = false }) {
  const fileCount = new Set(files).size;
  /* A code agent's winner can also run from a snapshot: the commit the
     change was pushed as, or an uploaded archive of it. */
  const [candidate, setCandidate] = useState(null);
  const snapshot = takesSnapshot(agent) ? candidateSourceOf(candidate, agent) : null;
  return (
    <Box sx={{ px: 2.5, pt: 2.5 }}>
      <Box sx={{ borderRadius: 1.25, border: "1px solid", borderColor: "divider" }}>
      <Stack
        direction="row" alignItems="center" spacing={1.5} flexWrap="wrap" rowGap={1.25}
        sx={{ px: 2, py: 1.75 }}
      >
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s2", fontWeight: 700 }}>
            {`Run ${winner.n} wins · ${files.length} ${files.length === 1 ? "change" : "changes"} in ${fileCount} ${fileCount === 1 ? "file" : "files"}`}
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            {needsVerify ? "Chosen over the search's pick — verify it on the full suite before it can ship." : "Verified on the held-back and must-pass scenarios. A full-suite run is optional."}
          </Typography>
        </Box>
        <Tooltip arrow title={blockedBy ? `Blocked — ${blockedBy}` : ""}>
          <Box component="span">
            <Button
              variant={needsVerify ? "contained" : "outlined"} color={needsVerify ? "primary" : "inherit"} size="small"
              disabled={!!blockedBy}
              onClick={() => onRun?.(snapshot?.param || null)}
              startIcon={<Iconify icon="solar:play-bold" width={14} />}
              sx={{ typography: "s2", fontWeight: 700, ...(!needsVerify && { color: "text.primary", borderColor: "divider" }) }}
            >
              {needsVerify ? "Verify it" : "Run on full suite"}
            </Button>
          </Box>
        </Tooltip>
      </Stack>
      {takesSnapshot(agent) && (
        <CandidateSourcePicker agent={agent} agentVersion={agentVersion} current={snapshot} onPick={setCandidate} />
      )}
      </Box>
    </Box>
  );
}

WinnerBar.propTypes = {
  winner: PropTypes.object, files: PropTypes.array, blockedBy: PropTypes.string, onRun: PropTypes.func,
  agent: PropTypes.object, agentVersion: PropTypes.string, needsVerify: PropTypes.bool,
};

const signedPts = (n) => `${n > 0 ? "+" : n < 0 ? "−" : "±"}${Math.abs(n)} pts`;
const signedPct = (n) => `${n > 0 ? "+" : n < 0 ? "−" : "±"}${Math.abs(n)}%`;

/*
  Where the improvement run is, as the steps a fix takes: reproduce the
  failure (or start from the run it came from), search, verify, and how it
  ended. An improvement from the Error Feed also says which issue.
*/
function ImprovementSteps({ record, fromRun, versionName = (l) => l, running, phase, diag, outcome, onOpenIssue, onOpenRun, shipped }) {
  const feed = record.trigger?.kind === "error-feed";
  const r = record.reproduce || {};
  const ending = outcome.ending.key;
  const notReproduced = ending === "not-reproduced";
  /* Stopped before searching: not reproduced, failed, nothing to improve, or over the budget. */
  const beforeSearch = !!outcome.beforeSearch;
  const stopped = notReproduced || ending === "failed" || ending === "nothing" || beforeSearch;
  const finalIcon = { ready: "solar:check-circle-linear", budget: "solar:pause-circle-linear", "stopped-early": "solar:pause-circle-linear", "no-fix": "solar:close-circle-linear", failed: "solar:close-circle-linear", suggestion: "solar:lightbulb-linear", nothing: "solar:check-circle-linear" }[ending];
  /* While it runs, each step reads done, now or to do from the phase the
     trace has reached. */
  const ORDER = ["reproducing", "searching", "verifying"];
  const live = (stepPhase, fallback) => {
    if (!running || !phase) return fallback;
    const a = ORDER.indexOf(stepPhase);
    const b = ORDER.indexOf(phase.key === "diagnosing" ? "searching" : phase.key);
    return a < b ? "done" : a === b ? "now" : "todo";
  };
  /* The PRD's stages — Reproduce, Search, Verify, Ready for review. The
     engine's own steps (re-reading the agent, the baseline twice, the
     diagnosis) are what each stage did, on hover — not stages of their own. */
  const cal = record.calibration || record.result?.calibration || null;
  const reproduceSub = [
    record.reread ? (record.reread.changed ? `changed outside Future AGI → ${record.reread.version || "new version"}` : "agent unchanged since the last run") : null,
    r.overridden && !r.reproduced ? "continued anyway" : null,
    r.inconclusive ? `${r.inconclusive} inconclusive` : null,
    cal ? `measured twice for noise · ${cal.disagreed} of ${cal.scenarios} disagreed` : null,
  ].filter(Boolean).join(" · ") || null;
  /* The count is said once Reproduce has run — never while it's running. */
  const reproducing = running && (!phase || phase.key === "reproducing");
  const reproduced = feed && r.total
    ? { label: (r.overridden && !r.reproduced) || reproducing ? "Reproduce" : `Reproduced on ${r.reproduced || 0} of ${r.total} scenarios`, sub: reproducing ? null : reproduceSub }
    : { label: "Reproduce", sub: [fromRun ? `from Run ${fromRun.ordinal || ""}`.trim() : null, reproduceSub].filter(Boolean).join(" · ") || null };
  const searchSub = [
    diag?.code ? `started from ${diag.code.edited ? "your edited diagnosis" : "the diagnosis"}` : null,
    diag?.surface ? `${diag?.code ? "" : "started from "}the smallest change that fits: ${diag.surface.label}` : null,
    record.stoppedAtBudget ? `${record.stoppedAtBudget.trials} of ${record.stoppedAtBudget.of} runs` : null,
    !running && record.result?.stoppedEarly?.window && !record.result.stoppedEarly.complete ? `${record.result.stoppedEarly.after} of up to ${record.result.stoppedEarly.of} runs · no gain in the last ${noGainRunsOf(record.result.stoppedEarly)}` : null,
  ].filter(Boolean).join(" · ") || null;
  const suggestion = ending === "suggestion" || (running && record.fixDepth === "suggestion");
  const steps = suggestion ? [
    { ...reproduced, state: running ? live("reproducing", "now") : "done" },
    { label: "Search", sub: "no change tried — this agent can only be called", state: running ? live("searching", "todo") : "done" },
    { label: "Suggestion only", sub: running ? null : "no change tried", state: running ? "todo" : "done", icon: finalIcon },
  ] : [
    { ...reproduced, state: notReproduced ? "fail" : running ? live("reproducing", "done") : "done" },
    { label: "Search", sub: searchSub, state: stopped ? "todo" : running ? live("searching", "now") : "done" },
    { label: "Verify", sub: !stopped && !running ? `${record.result?.finalists?.length || 1} ${record.result?.finalists?.length === 1 ? "finalist" : "finalists"} on held-back${record.result?.mustPassMeasured != null ? " and must-pass" : ""} + limits` : null, state: stopped || running ? (running ? live("verifying", "todo") : "todo") : "done" },
    { label: running || (stopped && ending !== "nothing" && !beforeSearch) ? "Ready for review" : outcome.ending.label, state: running || (stopped && ending !== "nothing" && !beforeSearch) ? "todo" : ending === "ready" || ending === "nothing" ? "done" : ending === "budget" || ending === "stopped-early" ? "partial" : "fail", icon: finalIcon },
    /* After review: where the shipped change is now. */
    ...(shipped ? [{ label: shipped.label, sub: shipped.detail || null, state: shipped.key === "reopened" ? "fail" : "done" }] : []),
  ].filter(Boolean);
  return (
    <Box sx={{ px: 2.5, py: 1.25, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}>
      <Stack direction="row" alignItems="center" spacing={0.75} sx={{ minWidth: 0 }}>
        <Iconify icon={feed ? "solar:bug-linear" : "solar:magnifer-linear"} width={14} sx={{ color: "text.subtitle", flexShrink: 0 }} />
        <Typography noWrap sx={{ typography: "s3", color: "text.subtitle", minWidth: 0 }}>
          {feed ? (
            <>
              {"From Error Feed · "}
              <Box component="span" {...clickable(onOpenIssue, { role: "link" })} sx={{ color: "text.primary", fontWeight: 600, cursor: "pointer", "&:hover": { textDecoration: "underline" }, ...focusRing }}>
                {record.trigger.issueId}
              </Box>
              {` · ${record.trigger.issueTitle}`}
            </>
          ) : (
            <>
              {record.trigger?.kind === "goal" ? "Goal" : record.trigger?.kind === "schedule" ? "Scheduled · weekly" : record.trigger?.kind === "manual" ? `Started by ${record.trigger.by || "you"}` : record.trigger?.source === "run-insights" ? `From run insights · ${record.trigger.issueTitle || record.trigger.issueId}` : "From Debug failures"}
              {/* The run it started from — a link to it, not a page behind it. */}
              {fromRun && (
                <>
                  {" · started from "}
                  <Box
                    component="span" {...clickable(onOpenRun, { role: "link" })}
                    sx={{ color: "text.primary", fontWeight: 600, ...(onOpenRun && { cursor: "pointer", "&:hover": { textDecoration: "underline" } }), ...focusRing }}
                  >
                    {`Run ${fromRun.ordinal || ""}`.trim()}
                  </Box>
                </>
              )}
              {[
                record.baseAgentVersion ? `agent ${versionName(record.baseAgentVersion)}` : null,
                record.trigger?.goal ? `Goal: ${record.trigger.goal.id === "pass-rate" ? "raise the pass rate" : `raise ${record.trigger.goal.label}`}` : null,
                record.lineage?.length ? `searched across ${versionName(record.lineage[0])}–${record.baseAgentVersion ? versionName(record.baseAgentVersion) : "now"}` : null,
              ].filter(Boolean).map((part) => ` · ${part}`).join("")}
            </>
          )}
        </Typography>
      </Stack>
      {/* One line: the step names carry the story; each step's detail is on hover. */}
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mt: 0.75 }} flexWrap="wrap" rowGap={0.5}>
        {steps.map((st, i) => {
          const icon = st.state === "fail" ? "solar:close-circle-linear"
            : st.state === "partial" ? "solar:pause-circle-linear"
              : st.state === "done" ? (st.icon || "solar:check-circle-linear")
                : "solar:circle-linear";
          const strong = st.state !== "todo";
          return (
            <Stack key={st.label + i} direction="row" alignItems="center" spacing={0.5}>
              {i > 0 && <Box sx={{ width: 14, height: "1px", bgcolor: "divider", mr: 0.5 }} />}
              <Tooltip arrow disableHoverListener={!st.sub} title={st.sub || ""}>
                <Stack direction="row" alignItems="center" spacing={0.5} sx={{ cursor: st.sub ? "help" : "default" }}>
                  <Iconify icon={icon} width={13} sx={{ color: strong ? "text.primary" : "text.subtitle" }} />
                  <Typography sx={{ typography: "s3", fontWeight: strong ? 600 : 400, color: strong ? "text.primary" : "text.subtitle", whiteSpace: "nowrap" }}>
                    {st.state === "now" ? `${st.label}…` : st.label}
                  </Typography>
                </Stack>
              </Tooltip>
            </Stack>
          );
        })}
      </Stack>
    </Box>
  );
}
ImprovementSteps.propTypes = {
  record: PropTypes.object, fromRun: PropTypes.object, versionName: PropTypes.func, running: PropTypes.bool, phase: PropTypes.object, diag: PropTypes.object, outcome: PropTypes.object, onOpenIssue: PropTypes.func, onOpenRun: PropTypes.func, shipped: PropTypes.object,
};

/* The constraints in force while the search runs — a candidate that breaks one is dropped. */
function RunningConstraints({ record, model }) {
  /* Must-pass across every environment it checks: this one's set, plus each other's. */
  const mustPass = (record.result?.mustPass?.total ?? record.frozen?.mustPass ?? 0)
    + (record.result?.otherEnvs || []).reduce((n, e) => n + (e.mustPass || 0), 0);
  const rows = [
    ...limitRows(record.limits || DEFAULT_LIMITS, model, { mustPass }).filter((c) => !(record.budget && c.id === "budget")),
    ...(record.budget ? [{ id: "spend", label: "Run budget", value: creditsLabel(record.budget) }] : []),
  ];
  return (
    <Stack direction="row" alignItems="baseline" flexWrap="wrap" columnGap={2} rowGap={0.5} sx={{ px: 2.5, py: 1.25, borderBottom: "1px solid", borderColor: "divider" }}>
      <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle", textTransform: "uppercase", letterSpacing: 0.5 }}>
        Held to
      </Typography>
      {rows.map((c) => (
        <Typography key={c.id} sx={{ typography: "s3", color: "text.secondary" }}>
          {`${c.label} · `}
          <Box component="span" sx={{ color: "text.primary", fontWeight: 600 }}>{c.value}</Box>
        </Typography>
      ))}
    </Stack>
  );
}
RunningConstraints.propTypes = { record: PropTypes.object, model: PropTypes.string };

/* The agent's constraints a candidate had to keep, each passed or broken, with the measure. */
function LimitsChecklist({ checks }) {
  const broken = checks.filter((c) => c.pass === false);
  const unknown = checks.filter((c) => c.pass === null).length;
  return (
    <CheckGroup
      title="Constraints"
      failed={broken.length > 0}
      summary={broken.length
        ? `${broken.length} broken — ${broken.map((c) => c.label).join(", ")}`
        : `All ${checks.length - unknown} held${unknown ? ` · ${unknown} not measured` : ""}`}
    >
      {checks.map((c, i) => (
        <Stack key={c.id} direction="row" alignItems="center" spacing={1.25} sx={{ px: 1.5, py: 0.875, borderTop: i ? "1px solid" : "none", borderColor: "divider" }}>
          <Iconify
            icon={c.pass === null ? "solar:question-circle-linear" : c.pass ? "solar:check-circle-linear" : "solar:close-circle-bold"}
            width={15} sx={{ color: c.pass === false ? "#DC2626" : c.pass === null ? "text.disabled" : "text.secondary", flexShrink: 0 }}
          />
          <Typography sx={{ typography: "s2", flex: 1, minWidth: 0 }}>{c.label}</Typography>
          <Typography sx={{ typography: "s2", color: c.pass === false ? "#DC2626" : "text.subtitle", fontVariantNumeric: "tabular-nums" }}>{c.actual}</Typography>
        </Stack>
      ))}
    </CheckGroup>
  );
}
LimitsChecklist.propTypes = { checks: PropTypes.array };

function TrialList({ trials, winner, openTrial, setOpenTrial, onOpenRun, record }) {
  const finalists = new Set((record?.result?.finalists || []).map((f) => f.n));
  return (
    <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
      {trials.map((t) => {
        const win = !!winner && t.n === winner.n;
        const open = openTrial === t.n;
        /* Stopped at Static checks or Fits the environment — no scenario ran, so no score. */
        const unfit = compatFailed(t) || staticFailed(t);
        const stat = staticFailed(t);
        return (
          <Box key={t.n}>
            <Stack
              direction="row" alignItems="center" spacing={1.5}
              {...clickable(() => setOpenTrial(open ? null : t.n), { expanded: open })}
              sx={{
                ...focusRing,
                px: 2, py: 1.25, cursor: "pointer",
                bgcolor: (th) => (win ? alpha("#16A34A", th.palette.mode === "dark" ? 0.08 : 0.04) : "transparent"),
                "&:hover": { bgcolor: "action.hover" },
              }}
            >
              <Typography sx={{ typography: "s3", color: "text.disabled", width: 26, flexShrink: 0, fontVariantNumeric: "tabular-nums" }}>{t.n}</Typography>
              <Typography sx={{ typography: "s2", fontWeight: 700, width: 44, flexShrink: 0, fontVariantNumeric: "tabular-nums", color: unfit ? "text.disabled" : "text.primary" }}>{unfit ? "—" : `${t.score}%`}</Typography>
              <Typography sx={{ typography: "s3", width: 40, flexShrink: 0, fontVariantNumeric: "tabular-nums", color: unfit ? "text.disabled" : t.delta > 0 ? "#16A34A" : t.delta < 0 ? "#DC2626" : "text.disabled" }}>
                {unfit ? "not run" : t.delta > 0 ? `+${t.delta}` : t.delta}
              </Typography>
              <Typography sx={{ typography: "s2", color: "text.subtitle", flex: 1, minWidth: 0 }} noWrap>{triedLabelOf(t, trials)}</Typography>
              {/* Where verification stopped it — the first tier it failed. */}
              {!win && stoppedAt(record, t) && (
                <Typography sx={{ typography: "s3", color: "text.disabled", flexShrink: 0 }}>{`stopped at ${stoppedAt(record, t)}`}</Typography>
              )}
              {rejectedFor(t) && (
                <Tooltip arrow title={`Rejected — ${rejectedFor(t)}`}>
                  <Chip size="small" label={t.brokeBlocker ? "must-pass" : stat ? "static check" : unfit ? "doesn't fit" : t.overLimit ? "over limit" : "tests"} sx={{ height: 18, borderRadius: 0.5, flexShrink: 0, color: "#DC2626", border: "1px solid", borderColor: alpha("#DC2626", 0.4), bgcolor: "transparent", "& .MuiChip-label": { px: 0.625, typography: "s3", fontWeight: 700 } }} />
                </Tooltip>
              )}
              {!win && finalists.has(t.n) && (
                <Typography sx={{ typography: "s3", color: "text.subtitle", flexShrink: 0 }}>finalist</Typography>
              )}
              {win && (
                <Chip size="small" label="winner" sx={{ height: 18, borderRadius: 0.5, flexShrink: 0, color: "#16A34A", border: "1px solid", borderColor: alpha("#16A34A", 0.4), bgcolor: "transparent", "& .MuiChip-label": { px: 0.625, typography: "s3", fontWeight: 700 } }} />
              )}
              {!unfit && (
                <Tooltip arrow title={`Run ${t.n} is its own simulation — open it`}>
                  <Button
                    size="small" onClick={(e) => { e.stopPropagation(); onOpenRun(t.n); }}
                    endIcon={<Iconify icon="solar:arrow-right-up-linear" width={12} />}
                    sx={{ typography: "s3", fontWeight: 600, color: "text.secondary", minWidth: 0, px: 0.75, py: 0, flexShrink: 0 }}
                  >
                    View run
                  </Button>
                </Tooltip>
              )}
              <Iconify icon={open ? "eva:arrow-ios-upward-fill" : "eva:arrow-ios-downward-fill"} width={14} sx={{ color: "text.disabled", flexShrink: 0 }} />
            </Stack>
            {open && (
              <Stack spacing={0.5} sx={{ px: 2, pb: 2, pl: 6 }}>
                {unfit && (
                  <Stack direction="row" spacing={1} alignItems="flex-start" sx={{ px: 1.5, py: 1, borderRadius: 1, border: "1px solid", borderColor: "divider" }}>
                    <Iconify icon="solar:close-circle-bold" width={15} sx={{ color: "#DC2626", flexShrink: 0, mt: "2px" }} />
                    <Box minWidth={0}>
                      <Typography sx={{ typography: "s2", fontWeight: 600 }}>{stat ? "Stopped at Static checks" : "Stopped at Fits the environment"}</Typography>
                      <Typography sx={{ typography: "s2", color: "text.secondary" }}>{stat ? t.static.reason : t.compat.reason}</Typography>
                    </Box>
                  </Stack>
                )}
                {t.lines.length ? t.lines.map((line) => (
                  <Stack key={line} direction="row" spacing={1} sx={{ px: 1.5, py: 1, borderRadius: 1, bgcolor: (th) => alpha("#16A34A", th.palette.mode === "dark" ? 0.1 : 0.05) }}>
                    <Typography sx={{ typography: "s2", fontWeight: 700, color: "#16A34A", flexShrink: 0 }}>+</Typography>
                    <Typography sx={{ typography: "s2", fontFamily: "ui-monospace, Menlo, monospace" }}>{line}</Typography>
                  </Stack>
                )) : (
                  <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
                    This run changed nothing — the score is the starting prompt re-run, which is how much of the spread between runs is noise rather than the search.
                  </Typography>
                )}
              </Stack>
            )}
          </Box>
        );
      })}
    </Stack>
  );
}
TrialList.propTypes = { trials: PropTypes.array, winner: PropTypes.object, openTrial: PropTypes.number, setOpenTrial: PropTypes.func, onOpenRun: PropTypes.func, record: PropTypes.object };

const stoppedAt = (record, t) => {
  const fail = ladderOf(record, t).find((x) => x.state === "fail");
  return fail ? fail.label.toLowerCase() : null;
};

/* The deepest kind of change a trial makes, in a word. */
const SURFACE_WORD = { prompt: "prompt", tools: "tool-description", settings: "settings", model: "model", code: "code", structure: "structural" };
const DEPTH_ORDER = ["prompt", "tools", "settings", "model", "code", "structure"];
const surfaceWord = (trial, result) => {
  const kinds = (result?.pool || []).filter((p) => trial?.picks?.includes(p.id)).map((p) => KIND_SURFACE[p.kind] || "code");
  const deepest = kinds.sort((a, b) => DEPTH_ORDER.indexOf(b) - DEPTH_ORDER.indexOf(a))[0] || "prompt";
  return SURFACE_WORD[deepest] || deepest;
};

/* Which optimizer and model searched, and the full trace — kept out of the way. */
function SearchDetails({ record, result, model }) {
  const [open, setOpen] = useState(false);
  return (
    <Box sx={{ px: 2.5, pb: 2.5 }}>
      <Button
        size="small" onClick={() => setOpen((o) => !o)}
        endIcon={<Iconify icon={open ? "eva:arrow-ios-upward-fill" : "eva:arrow-ios-downward-fill"} width={13} />}
        sx={{ typography: "s3", fontWeight: 700, color: "text.secondary", px: 0.5 }}
      >
        Search details
      </Button>
      {open && (
        <Box sx={{ mt: 0.5 }}>
          <Typography sx={{ typography: "s3", color: "text.subtitle", px: 0.5 }}>
            {`${methodLabelOf(result.optimizer?.id)} search (${result.optimizer?.label}) · changes ${record.writerModel ? improverModelsText(record.model, record.writerModel) : `written by ${model?.label || record.model}`}, judged by ${judgeFor(record.model)} — a different model family, so no model grades its own work · ${result.trials.length}${result.planned && result.planned !== result.trials.length ? ` of ${result.planned}` : ""} runs · ${result.trainMeasured} target / ${result.heldMeasured} held-back${result.mustPassMeasured != null ? ` / ${result.mustPassMeasured} must-pass` : ""} scenarios${result.goal ? ` · targets: scenarios short of ${goalWords(result.goal)}` : ""}`}
          </Typography>
          {/* GEPA keeps the best candidate for each scenario, not one overall best. */}
          {result.optimizer?.id === "gepa" && <ParetoPool result={result} />}
          <Box sx={{ mx: -1 }}>
            <RunTraceLog label={`Searched by ${methodLabelOf(result.optimizer?.id).toLowerCase()} search (${result.optimizer?.label}) · ${result.trials.length} runs`} steps={searchTrace(result)} />
          </Box>
        </Box>
      )}
    </Box>
  );
}
SearchDetails.propTypes = { record: PropTypes.object, result: PropTypes.object, model: PropTypes.object };

/* GEPA's pool: each trial that is the best at fixing at least one scenario. */
function ParetoPool({ result }) {
  const pool = paretoPool(result);
  if (!pool.length) return null;
  return (
    <Box sx={{ mt: 1, mx: 0.5, border: "1px solid", borderColor: "divider", borderRadius: 1 }}>
      <Typography sx={{ px: 1.5, py: 0.75, typography: "s3", fontWeight: 700, color: "text.subtitle", textTransform: "uppercase", letterSpacing: 0.5, borderBottom: "1px solid", borderColor: "divider" }}>
        {`Kept · best per scenario · ${pool.length} ${pool.length === 1 ? "run" : "runs"}`}
      </Typography>
      {pool.map((x, i) => (
        <Stack key={x.n} direction="row" spacing={1.5} sx={{ px: 1.5, py: 0.75, borderTop: i ? "1px solid" : "none", borderColor: "divider" }}>
          <Typography sx={{ typography: "s2", fontWeight: 600, width: 64, flexShrink: 0, fontVariantNumeric: "tabular-nums" }}>{`Run ${x.n}`}</Typography>
          <Typography sx={{ typography: "s2", color: "text.secondary", width: 44, flexShrink: 0, fontVariantNumeric: "tabular-nums" }}>{`${x.score}%`}</Typography>
          <Typography noWrap sx={{ typography: "s2", color: "text.subtitle", flex: 1, minWidth: 0 }}>
            {`best on ${x.scenarios.length}: ${x.scenarios.slice(0, 3).join(", ")}${x.scenarios.length > 3 ? ` +${x.scenarios.length - 3}` : ""}`}
          </Typography>
        </Stack>
      ))}
    </Box>
  );
}
ParetoPool.propTypes = { result: PropTypes.object };
