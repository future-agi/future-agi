import PropTypes from "prop-types";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { paths } from "src/routes/paths";
import SideDrawer from "../../components/SideDrawer";
import { omegaReport, omegaVerdict, diagnosisTrace } from "../../_mock/omega";
import {
  proposalsFor, projectedRate, addressedCount, optimisable, verifierFixes, projectedWithGeneralisation,
} from "../../_mock/optimize";
import { runSearch, splitScenarios } from "../../_mock/optimizer";
import { nextEnvVersion, environmentVersions, nextAgentVersion } from "../../_mock/versions";
import { optimizationId, OPT_STATUS } from "../../_mock/optimizationRuns";
import { protoRunId } from "../../_mock/executionAdapter";
import DiagnosisPane from "./DiagnosisPane";
import OptimizationRunView from "./OptimizationRunView";
import CreateOptimizationModal from "./CreateOptimizationModal";

/**
 * Fix my agent.
 *
 * One drawer, two widths. Narrow while you are reading what went wrong, wide
 * once a search is running — because a diagnosis is a column of text and an
 * optimization is a graph, a grid of trials and a scenario matrix, and forcing
 * the second into the width of the first is how the old screen ended up
 * pushing its results into an 80vw drawer anyway.
 *
 * The drawer itself is the shared `SideDrawer`, so the page behind stays
 * readable and it looks like every other drawer in the product rather than a
 * bespoke persistent one.
 */

export default function FixMyAgentDrawer({
  open, onClose, env, envState, patch, addAgentVersion, tasks, stats, runId, onOpenTask, openOptimizationId,
}) {
  const navigate = useNavigate();
  /* Opening a row in the runs list lands on that run, not on the diagnosis —
     the list already told you which one you wanted. */
  const [view, setView] = useState(openOptimizationId ? "optimization" : "diagnosis");
  const [activeId, setActiveId] = useState(openOptimizationId || null);

  useEffect(() => {
    if (!open) return;
    setView(openOptimizationId ? "optimization" : "diagnosis");
    setActiveId(openOptimizationId || null);
  }, [open, openOptimizationId]);
  const [modal, setModal] = useState(false);
  /*
    Every proposed change starts checked so the primary "Create agent
    version" button is armed the moment the diagnosis lands — the earlier
    empty-by-default meant a first-time reader had a disabled button
    staring at them with no cue that the tick boxes above were the
    unlock. Users still uncheck anything they don't want to bundle.
  */
  const [applied, setApplied] = useState({});
  const [checksApplied, setChecksApplied] = useState(null);

  const measured = useMemo(() => tasks.filter((t) => t.status !== "unmeasured"), [tasks]);
  const failing = useMemo(() => optimisable(tasks), [tasks]);

  const report = useMemo(
    () => omegaReport({ env, summary: { tasks }, baseline: null }),
    [env, tasks],
  );
  const proposals = useMemo(() => proposalsFor(env, failing, measured), [env, failing, measured]);
  const checks = useMemo(() => verifierFixes(report), [report]);

  /*
    Seed `applied` with every proposal id checked, so the primary CTA is
    armed as soon as the drawer opens. Runs when the set of proposals
    changes (new run, different failures); a `seenRef` prevents the effect
    from resetting the map every time a user unchecks a row and the
    dependency identity happens to shift.
  */
  const seenRef = useRef(new Set());
  useEffect(() => {
    setApplied((prev) => {
      let changed = false;
      const next = { ...prev };
      proposals.forEach((p) => {
        if (seenRef.current.has(p.id)) return;
        seenRef.current.add(p.id);
        next[p.id] = true;
        changed = true;
      });
      return changed ? next : prev;
    });
  }, [proposals]);
  const trace = useMemo(
    () => diagnosisTrace({ report, tasks, proposals, checks }),
    [report, tasks, proposals, checks],
  );

  const included = proposals.filter((p) => applied[p.id]);
  const projected = projectedRate(measured, included);
  const willFix = addressedCount(included);
  const current = Math.round((stats?.passRate || 0) * 100);

  /* The split is shown before the run starts, so the modal needs it too. */
  const split = useMemo(() => {
    const { train, held } = splitScenarios(tasks, `${env?.id}-split`);
    return {
      trainMeasured: train.filter((t) => t.status !== "unmeasured").length,
      heldMeasured: held.filter((t) => t.status !== "unmeasured").length,
    };
  }, [tasks, env]);

  const records = envState?.optimizations || [];
  const active = records.find((o) => o.id === activeId) || null;

  /*
    Starting a run writes the record first and marks it running.

    The search is deterministic and returns instantly, but the record has to
    exist in a running state before the trace plays — otherwise closing the
    drawer mid-search would lose a run that, in a real system, is already
    burning episodes on somebody's infrastructure.
  */
  const start = ({ name, optimizerId, model }) => {
    const result = runSearch({ env, tasks, optimizerId, seed: `${env?.id}-${optimizerId}-${records.length}` });
    const record = {
      id: optimizationId(envState),
      name,
      optimizerId,
      model,
      status: OPT_STATUS.RUNNING,
      createdAt: new Date().toISOString(),
      fromRunId: runId,
      includedIds: included.map((p) => p.id),
      /* The diagnosis this attempt was made against, kept with it — six weeks
         on, "why did we try that" is only answerable if the evidence travelled
         with the attempt. */
      diagnosis: report.map((a) => ({ id: a.id, label: a.label, headline: a.headline, severity: a.severity })),
      result,
    };
    patch({ optimizations: [...records, record] });
    setActiveId(record.id);
    setView("optimization");
    setModal(false);
  };

  const finish = () => {
    patch({
      optimizations: records.map((o) => (
        o.id === activeId
          ? { ...o, status: OPT_STATUS.COMPLETED, completedAt: new Date().toISOString() }
          : o
      )),
    });
  };

  const applyChecks = () => {
    const list = envState?.envVersions?.length
      ? envState.envVersions
      : [...environmentVersions(env, envState)].reverse();
    const version = nextEnvVersion(env, envState, {
      changed: ["evals"],
      note: checks.length === 1 ? checks[0].title : `${checks.length} verifier fixes`,
    });
    patch({ envVersions: [...list, version] });
    setChecksApplied(version.label);
  };

  /*
    Deployed, so re-run — the loop this whole surface exists to close.

    The refactor into a drawer left this button wired to `onClose`: it said "it
    is deployed, re-run", closed, and did nothing. Worse, the banner on the runs
    list that holds a projection against what actually happened reads a record
    nothing was writing any more, so the verification half of the feature was
    silently dead on both ends.

    The version is minted here rather than at hand-off because this is the first
    moment the agent might actually be different.
  */
  const rerun = (changes, expected) => {
    const note = changes.length === 1
      ? changes[0].title
      : `${changes.length} changes: ${[...new Set(changes.map((c) => c.kind.toLowerCase()))].join(", ")}`;
    const version = nextAgentVersion(envState, { note });
    addAgentVersion?.(version);
    patch({
      omegaExpectation: {
        version: version.label,
        fromRun: runId || null,
        projected: expected,
        scenarios: tasks.length,
        addresses: [...new Set(changes.flatMap((c) => c.addresses))],
        at: new Date().toISOString(),
      },
    });
    const url = paths.dashboard.simulate.simulationRun(env.id, protoRunId(env.id, Date.now().toString(36)));
    const ids = tasks.map((t) => t.id);
    const subset = ids.length < (envState?.scenarios?.length || 0);
    onClose();
    navigate(subset ? `${url}?only=${ids.join(",")}` : url);
  };

  /*
    Create the next agent version from the accepted changes — the new
    primary path. Same version-minting the hand-off used to do at re-run
    time, but done here explicitly: the accepted diffs travel on the
    version, tied to the run that produced them. The drawer stays open so
    the NewAgentVersion component can show its success state; the user
    then closes and clicks "Run again" on the parent, which will target
    the new version because it is now the current one.
  */
  /*
    After a new agent version has been minted, "Run in simulation" starts
    a fresh run against the same environment. The store already treats the
    newest version as current, so the run lands as a new row in the
    simulation runs table tagged with the new label — no per-run version
    override needed. Compare v1 v v2 stays on the existing compare flow.
  */
  const runNewVersion = () => {
    onClose();
    navigate(paths.dashboard.simulate.simulationRun(env.id, protoRunId(env.id, Date.now().toString(36))));
  };

  const createAgentVersion = (changes, expected, noteOverride) => {
    const note = noteOverride
      || (changes.length === 1
        ? changes[0].title
        : `${changes.length} changes: ${[...new Set(changes.map((c) => c.kind.toLowerCase()))].join(", ")}`);
    const version = nextAgentVersion(envState, {
      note,
      applied: changes,
      fromRunId: runId || null,
    });
    addAgentVersion?.(version);
    patch({
      omegaExpectation: {
        version: version.label,
        fromRun: runId || null,
        projected: expected,
        scenarios: tasks.length,
        addresses: [...new Set(changes.flatMap((c) => c.addresses))],
        at: new Date().toISOString(),
      },
    });
  };

  const close = () => {
    setView("diagnosis");
    setActiveId(null);
    onClose();
  };

  return (
    <>
      <SideDrawer
        open={open}
        onClose={close}
        /*
          Wide enough for the widest thing it holds, and no wider. 86vw stretched
          the trials table across a metre of screen so each row was a score at one
          end and a sentence at the other; the scenario grid — the widest content
          here at roughly 950px — sets the real requirement.
        */
        width={view === "optimization" ? { xs: "100%", md: 1060 } : { xs: "100%", sm: 570 }}
      >
        {view === "diagnosis" && (
          <DiagnosisPane
            tasks={tasks}
            report={report}
            trace={trace}
            proposals={proposals}
            checks={checks}
            verdict={omegaVerdict(report, failing)}
            applied={applied}
            setApplied={setApplied}
            current={current}
            projected={projected}
            willFix={willFix}
            measured={measured}
            failing={failing}
            onOpenTask={onOpenTask}
            onOptimize={() => setModal(true)}
            onHandOff={rerun}
            onCreateAgentVersion={createAgentVersion}
            onRunNewVersion={runNewVersion}
            env={env}
            envState={envState}
            patch={patch}
            onApplyChecks={applyChecks}
            checksApplied={checksApplied}
            onClose={close}
          />
        )}

        {view === "optimization" && active && (
          <OptimizationRunView
            record={active}
            env={env}
            envState={envState}
            patch={patch}
            tasks={tasks}
            onOpenTask={onOpenTask}
            onRerun={(changes) => rerun(changes, projectedWithGeneralisation(measured, changes))}
            onBack={() => { setView("diagnosis"); setActiveId(null); }}
            onClose={close}
            onDone={finish}
          />
        )}
      </SideDrawer>

      <CreateOptimizationModal
        open={modal}
        envState={envState}
        included={included}
        split={split}
        onClose={() => setModal(false)}
        onStart={start}
      />
    </>
  );
}

FixMyAgentDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  env: PropTypes.object,
  envState: PropTypes.object,
  patch: PropTypes.func,
  addAgentVersion: PropTypes.func,
  tasks: PropTypes.array,
  stats: PropTypes.object,
  runId: PropTypes.string,
  onOpenTask: PropTypes.func,
  openOptimizationId: PropTypes.string,
};
