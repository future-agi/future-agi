/*
  What an epoch hands a coding agent, and how its proof reads — the same
  brief and proof the full record built, now read by the epoch page's ⋯
  menu and its Ship drawer. Only what the search could see goes in: the
  held-back set never leaves here.
*/
import { diagnosisSummaryOf } from "../_mock/optimizer";
import { improvementOutcome } from "../_mock/optimizationRuns";
import { proofKindOf } from "../_mock/rsiModel";
import {
  membersChanged,
  uncheckedChannelNotes,
} from "../_mock/improvementPolicy";
import { briefBreakdownOf, codingAgentBrief } from "../_mock/codingAgentBrief";
import { agentDefinitionOf, agentSourceOf } from "../_mock/agentRegistry";
import {
  connectedCodingAgent,
  startImproverSession,
} from "../_mock/integrations";
import {
  briefEvidenceOf,
  visibleFailingOf,
} from "../run/fixmyagent/improvementView";
import { proofDisplay, proofMarkdown } from "../run/fixmyagent/proofBlock";
import { improvementIdentityOf } from "./improvementRows";

/** The model the agent runs on, when its definition shows one. */
export const agentModelOf = (env, envState) => {
  if (!envState?.agent) return null;
  const def = agentDefinitionOf({ agent: envState.agent, env, envState });
  return def.model?.visible ? def.model.name : null;
};

/* Where candidates ran and how far the proof can be trusted. */
export const epochProofOf = (record, agent) => {
  if (record?.proof) return record.proof;
  if (!agent) return null;
  const p = proofKindOf(agent);
  return { kind: p.kind, label: p.label, tier: p.tier, tierLabel: p.tierLabel };
};

/* Which members the change touches, and the channels it wasn't checked on. */
const scopeOf = (record, env, state) => {
  const agent = state?.byEnv?.[env?.id]?.agent || null;
  const winner = record?.result?.winner || null;
  const scope = membersChanged(winner?.proposals || [], {
    agent,
    limits: record?.limits,
    target: record?.targetMember,
  });
  const surfaces = [
    ...new Set(
      [
        env?.surface,
        ...(record?.regressionEnvIds || []).map(
          (id) =>
            (state?.myEnvironments || []).find((e) => e.id === id)?.surface,
        ),
      ].filter(Boolean),
    ),
  ];
  return { scope, channelNotes: uncheckedChannelNotes(agent, surfaces) };
};

/** The proof block a hand-off carries (markdown). */
export const epochProofMarkdown = ({ record, env, envState, state, name }) => {
  const agent = envState?.agent || null;
  const outcome = improvementOutcome(record, {
    model: agentModelOf(env, envState),
  });
  const { scope, channelNotes } = scopeOf(record, env, state);
  return proofMarkdown({
    record,
    outcome,
    name,
    agent,
    members: scope.names,
    channelNotes: scope.shared || !scope.hasMembers ? channelNotes : [],
    identity: improvementIdentityOf(record, env, envState),
  });
};

/**
 * The brief for a coding agent. Handing it over also starts that agent's
 * session on this epoch, so its attempts land here as candidates.
 */
export const epochBriefOf = ({
  record,
  env,
  envState,
  state,
  name,
  tasks = [],
  start = true,
}) => {
  const result = record?.result || null;
  const winner = result?.winner || null;
  const agentModel = agentModelOf(env, envState);
  const outcome = improvementOutcome(record, { model: agentModel });
  const diag = result ? diagnosisSummaryOf(record, result) : null;
  const { scope } = scopeOf(record, env, state);
  if (start && env?.id && result) {
    const deployed = ["endpoint", "framework"].includes(
      agentSourceOf(envState?.agent).kind,
    );
    startImproverSession({
      agentId: connectedCodingAgent()?.id || null,
      envId: env.id,
      optId: record.id,
      where: deployed ? "deployed" : "future-agi",
    });
  }
  const proposals = winner?.proposals || result?.pool || [];
  return codingAgentBrief({
    agent: {
      name: envState?.agent?.name || env?.name,
      key: envState?.agent?.key,
    },
    env,
    pairing: improvementIdentityOf(record, env, envState),
    issue:
      record.trigger?.kind === "error-feed" ||
      record.trigger?.source === "run-insights"
        ? {
            id: record.trigger.issueId,
            title: record.trigger.issueTitle,
            severity: record.trigger.severity,
          }
        : record.trigger?.goal
          ? `Raise ${record.trigger.goal.label || "the pass rate"}`
          : name || record.name,
    rootCause:
      [
        record.snapshot?.rootCause ||
          (diag?.code ? null : diag?.cause) ||
          (record.diagnosis || [])[0]?.headline ||
          record.trigger?.issueRoot?.subGoal ||
          null,
        outcome.headline ? `What the search found: ${outcome.headline}` : null,
      ]
        .filter(Boolean)
        .join("\n\n") || null,
    suggestedFix: (outcome.suggestion || proposals).slice(0, 4),
    evidence: briefEvidenceOf({ record, result, winner, tasks }),
    failingScenarios: visibleFailingOf(result, winner, tasks),
    diagnosis: diag?.code || null,
    breakdown: briefBreakdownOf(record),
    limits: record.limits,
    model: agentModel,
    proof: proofDisplay(epochProofOf(record, envState?.agent || null)),
    members: scope.names,
  });
};
