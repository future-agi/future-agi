import { useMemo, useState } from "react";
import PropTypes from "prop-types";
import { useNavigate } from "react-router-dom";
import { useSnackbar } from "notistack";
import { paths } from "src/routes/paths";
import { useConnectedAgents } from "../store";
import { envAgentKey, agentIdOf, sourceKindOf } from "../_mock/agentRegistry";
import { buildableAgentsOf } from "../environments/worldSource";
import {
  PAIR_CHECK_LIMIT_MIN,
  compatibilityOf,
  pairingStampOf,
} from "../_mock/compatibility";
import {
  checkSavedPatch,
  checksConnection,
  checksFor,
  connectionSpecOf,
  runConnectionCheck,
} from "../_mock/connectionCheck";
import { foundValuesOf } from "../agents/foundFacts";
import { withPinnedWorld } from "../_mock/toolFit";
import { runPlan } from "../_mock/proofs";
import { scopeBreakdownOf, runScopeOf } from "../_mock/runScope";
import { setupGaps } from "../_mock/setupGaps";
import {
  agentVersions,
  currentAgentVersion,
  currentEnvVersion,
  environmentVersions,
} from "../_mock/versions";
import { versionOptionsOf } from "../_mock/pricing";
import { resolveEval, suggestedEvalsOf } from "../_mock/evals";
import { parallelOf } from "../_mock/runStream";
import { groupsOf as orgGroupsOf } from "../_mock/scenarioOrg";
import { protoRunId } from "../_mock/executionAdapter";
import { launchUrlOf } from "../_mock/runPlanScope";
import { useAppliedEvals } from "../workspace/evals/useAppliedEvals";
import RunPlanDialog from "../workspace/RunPlanDialog";

/*
  The run plan, opened over the run results (a centred dialog) so a
  re-run of the selected, flaky or failing rows never leaves the results.
  It is the same RunPlanDialog the environment page opens — scope, agent,
  environment, trials and the readiness check — fed from the environment's
  state here. Closing it leaves the results as they were; starting from it
  starts the new run exactly as the environment page does.

  request: { scenarioIds, trials, agent, snapshot, envVersion, toolReplay }
  sourceRun: the run being repeated (its pairing needs no new check).
*/
export default function RerunPlanDrawer({
  open,
  onClose,
  env,
  envState,
  patch,
  request,
  sourceRun,
  scenarioStatus,
  onGradeSource,
}) {
  const navigate = useNavigate();
  const { enqueueSnackbar } = useSnackbar();
  const envId = env?.id;
  const { agents: connectedAgents, setAgentValues } = useConnectedAgents();
  const { add: addEvals } = useAppliedEvals(
    envState.evals ? envState : { ...envState, evals: [] },
    patch,
  );
  const [capsChecking, setCapsChecking] = useState(false);
  const worldEnv = useMemo(
    () => withPinnedWorld(env, envState),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [env, envState.envVersions, envState.activeEnvVersion],
  );
  const agentKey = envAgentKey(envState.agent, envId);
  const connectedAgent =
    connectedAgents.find((a) => a.key === agentKey) || envState.agent;
  const pairCheckable = (a) =>
    checksConnection(a) || sourceKindOf(a) === "platform";
  const fixedPair = !!request?.continues;

  /* The plan's pairing check: the same check as Agent → Capabilities, saved
     on the agent, so Ready to run updates where it is. */
  const runCapsCheck = (agentNow = connectedAgent, key = agentKey) => {
    if (capsChecking || !agentNow || !pairCheckable(agentNow)) return;
    const stamp = pairingStampOf(worldEnv, envState);
    if (!checksConnection(agentNow)) {
      setCapsChecking(true);
      setTimeout(() => {
        setAgentValues(key, {
          platformChecks: {
            ...(agentNow.values?.platformChecks || {}),
            [env.id]: { at: new Date().toISOString() },
          },
          checkUnavailable: null,
          checkedOn: { ...(agentNow.values?.checkedOn || {}), [env.id]: stamp },
        });
        setCapsChecking(false);
      }, 1200);
      return;
    }
    const found = foundValuesOf(agentNow);
    const spec = connectionSpecOf(agentNow, found);
    setCapsChecking(true);
    setTimeout(
      () => {
        const knob = new URLSearchParams(window.location.search).get("check");
        if (knob === "unavailable" || knob === "timeout") {
          const timedOut = knob === "timeout";
          setAgentValues(key, {
            checkUnavailable: {
              at: new Date().toISOString(),
              timedOut,
              reason: timedOut
                ? `It didn't finish within ${PAIR_CHECK_LIMIT_MIN} minutes`
                : "The test world couldn't be prepared on our side",
            },
          });
          setCapsChecking(false);
          return;
        }
        const out = runConnectionCheck(agentNow, spec, found.tools);
        setAgentValues(key, {
          ...checkSavedPatch(agentNow.values, out),
          checkUnavailable: null,
          checkedOn: { ...(agentNow.values?.checkedOn || {}), [env.id]: stamp },
        });
        setCapsChecking(false);
      },
      450 * (checksFor(spec).length + 1),
    );
  };

  const choices = (() => {
    const typeId = envState.agent?.typeId;
    const others = buildableAgentsOf(connectedAgents).filter(
      (a) => a.key !== agentKey && typeId && a.typeId === typeId,
    );
    return [
      {
        key: agentKey,
        name: connectedAgent?.name || "the agent",
        version: currentAgentVersion(envState)?.label,
        current: true,
      },
      ...others.map((a) => ({
        key: a.key,
        name: a.name,
        version: a.production || a.latest || "v1",
        versions: [
          ...new Set(
            [
              a.production || a.latest || "v1",
              ...(a.versions || []).map((v) => v.label).reverse(),
            ].filter(Boolean),
          ),
        ],
        current: false,
      })),
    ];
  })();

  const ids = request?.scenarioIds || [];
  const gaps = open ? setupGaps(worldEnv, envState) : [];
  const plan = open
    ? runPlan(env, envState, { only: ids.length ? ids : null })
    : null;
  const scope = open
    ? runScopeOf(worldEnv, envState, { plan: runPlan(env, envState), gaps })
    : null;

  const start = ({
    attempts,
    versions,
    candidate,
    cap,
    alertAt,
    limits,
    compatSubset,
    withAgent,
    suiteIds,
    addEvals: picked,
    envVersion: pickedEnv,
  }) => {
    onClose();
    if (picked?.length) {
      addEvals(picked);
      /* Same test on both sides: the run it repeats is graded with them too. */
      if (sourceRun && !request?.fresh) onGradeSource?.(picked);
    }
    const current = currentAgentVersion(envState)?.label;
    const runId = protoRunId(envId, Date.now().toString(36));
    navigate(
      launchUrlOf(
        envId,
        runId,
        compatSubset ? compatSubset.ids : suiteIds || request?.scenarioIds,
        attempts,
        {
          agent:
            versions[0] && versions[0] !== current ? versions[0] : undefined,
          cap,
          alertAt,
          continues: request?.continues,
          snapshot: request?.snapshot,
          envVersion: pickedEnv || request?.envVersion,
          candidate,
          limits,
          toolReplay: request?.toolReplay,
          compatSubset,
          withAgent,
          /* Same version as the run it repeats: play everything it played. */
          rerunOf:
            sourceRun &&
            !request?.fresh &&
            (pickedEnv || request?.envVersion || sourceRun.envVersion) ===
              sourceRun.envVersion
              ? sourceRun.id
              : undefined,
        },
      ),
    );
  };

  if (!open) return null;
  return (
    <RunPlanDialog
      open={open}
      onClose={onClose}
      onStart={start}
      env={env}
      envState={envState}
      patch={patch}
      agentChoices={fixedPair ? [] : choices}
      suites={
        fixedPair ? [] : orgGroupsOf(envState).filter((g) => g.ids?.length)
      }
      envLabel={currentEnvVersion(env, envState)?.label}
      scenarioCount={ids.length || scope?.count || 0}
      scopeNote={`${ids.length || scope?.count || 0} ${ids.length ? "selected on the results" : "ready to run"}`}
      initialScenarioIds={ids}
      scenarioStatus={scenarioStatus}
      baseline={
        sourceRun && !request?.fresh
          ? {
              agentKey: sourceRun.agentKey || null,
              agentVersion: sourceRun.agentVersion,
              envVersion: sourceRun.envVersion,
              label: sourceRun.ordinal ? `run ${sourceRun.ordinal}` : "the run",
              /* What the run ran with — a re-run keeps it (25 Nov). */
              limits: sourceRun.limits || null,
              turnLimit: sourceRun.turnLimit || null,
            }
          : null
      }
      initialEnvVersion={request?.envVersion || null}
      initialAttempts={Number(request?.trials) || 3}
      versionOptions={versionOptionsOf(
        envState,
        agentVersions(envState),
        envId,
      )}
      initialVersion={
        request?.agent ||
        (request?.fresh
          ? currentAgentVersion(envState)?.label
          : sourceRun?.agentVersion)
      }
      /* The run's own evaluations: a re-run grades the same way. */
      evals={((!request?.fresh && sourceRun?.evals) || envState.evals || [])
        .map(resolveEval)
        .filter(Boolean)}
      suggestedEvals={suggestedEvalsOf(env, envState)}
      canAddEvals
      runLocation={envState?.agent?.values?.runLocation || null}
      parallel={parallelOf(envState)}
      envVersionOptions={
        fixedPair
          ? []
          : environmentVersions(env, envState).map((v) => ({
              label: v.label,
              current: v.current,
            }))
      }
      breakdownOf={(v) =>
        scopeBreakdownOf(
          worldEnv,
          { ...envState, activeEnvVersion: v },
          {
            plan: runPlan(worldEnv, envState, { envVersion: v }),
            gaps,
            agentLabel: request?.agent || null,
          },
        )
      }
      breakdown={
        fixedPair
          ? null
          : scopeBreakdownOf(worldEnv, envState, {
              plan,
              gaps,
              only: ids.length ? ids : null,
              agentLabel: request?.agent || null,
            })
      }
      compatOf={
        fixedPair
          ? null
          : (
              key,
              { version = null, ids: picked = null, envVersion = null } = {},
            ) => {
              const other =
                key && key !== agentKey
                  ? connectedAgents.find((a) => a.key === key)
                  : null;
              return compatibilityOf({
                env: worldEnv,
                envState: envVersion
                  ? { ...envState, activeEnvVersion: envVersion }
                  : envState,
                agent: other || {
                  ...envState.agent,
                  values: {
                    ...(envState.agent?.values || {}),
                    ...(connectedAgent?.values || {}),
                  },
                },
                scenarioIds: picked || ids,
                agentVersion: version,
                ownAgent: !other,
              });
            }
      }
      canCheckCompat={(key) => {
        const a =
          key && key !== agentKey
            ? connectedAgents.find((x) => x.key === key)
            : connectedAgent;
        return !!a && pairCheckable(a);
      }}
      onCheckCompat={(key) =>
        key && key !== agentKey
          ? runCapsCheck(
              connectedAgents.find((x) => x.key === key),
              key,
            )
          : runCapsCheck()
      }
      checkingCompat={capsChecking}
      onRepairCompat={(row, key = agentKey) => {
        enqueueSnackbar("Opening the agent to fix it — come back to re-run.", {
          variant: "info",
        });
        navigate(
          `${paths.dashboard.agents}/connected/${agentIdOf(key)}?tab=${row?.repair === "agent" ? "definition" : "connections"}`,
          {
            state: {
              from: "environment",
              envId,
              envName: env?.name,
              back: window.location.pathname + window.location.search,
              backLabel: "Back to run results",
            },
          },
        );
      }}
      seedFrom={
        request?.snapshot && sourceRun?.ordinal
          ? `run ${sourceRun.ordinal}`
          : null
      }
    />
  );
}

RerunPlanDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  env: PropTypes.object,
  envState: PropTypes.object,
  patch: PropTypes.func,
  request: PropTypes.object,
  sourceRun: PropTypes.object,
  onGradeSource: PropTypes.func,
  scenarioStatus: PropTypes.object,
};
