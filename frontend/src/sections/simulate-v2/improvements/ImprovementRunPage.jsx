import { useEffect, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { Helmet } from "react-helmet-async";
import { Box, Button, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { useConnectedAgents, useEnvState, useSimStore } from "../store";
import { behindProductionOf } from "./behindProduction";
import OptimizationRunView from "../run/fixmyagent/OptimizationRunView";
import { shippedCandidate } from "../run/fixmyagent/tradeoffs";
import { improvementRunName } from "../_mock/optimizer";
import {
  OPT_STATUS,
  improvementProgressOf,
  settlePending,
} from "../_mock/optimizationRuns";
import { baseRunOf } from "../_mock/improvementStart";
import { nextAgentVersion, currentEnvVersion } from "../_mock/versions";
import { protoRunId } from "../_mock/executionAdapter";
import SideDrawer from "../components/SideDrawer";
import ImprovementRunsView from "./ImprovementRunsView";
import ImprovementDecision from "./ImprovementDecision";
import CodingAgentImprovementView from "./CodingAgentImprovementView";

/**
 * One improvement, on its own page — `simulate/improvements/:envId/:optId`,
 * level 2 of Improvements. Once its search has tried runs, the page lists
 * them (ImprovementRunsView) and the search itself opens beside them; while
 * it's still searching, or when it stopped before trying any, the page is
 * the search.
 *
 * An improvement belongs to its environment, not to a run, so one started
 * without a run (a goal, a schedule) reopens here rather than on the
 * environment's runs list. The same view as the drawer over a run, with the
 * same hand-off: landing a change saves a version, re-running starts a run.
 *
 * Opened from an Error Feed issue (Fix, View fix, the status chip), its back
 * goes to that issue — the fix is part of working the issue.
 *
 * While it runs it's the same runs page, filling in: the stage it's in, and
 * each run in the table as it finishes. One clock (improvementProgressOf)
 * drives the stage, the runs and the end, so a reload picks up where it was.
 */
export default function ImprovementRunPage() {
  const { envId, optId } = useParams();
  const navigate = useNavigate();
  const { state } = useSimStore();
  const env = (state.myEnvironments || []).find((e) => e.id === envId) || null;
  const { envState, patch, addAgentVersion } = useEnvState(envId);
  const { agents } = useConnectedAgents();
  const record =
    (envState?.optimizations || []).find((o) => o.id === optId) || null;
  const location = useLocation();
  const fromIssue = location.state?.fromIssue || null;
  const from = location.state?.back || null;
  /* The drawer beside the runs: the short decision first; the full record on request. */
  const [searchOpen, setSearchOpen] = useState(false);
  const [fullRecord, setFullRecord] = useState(false);
  /* Ship: what ships and the next steps, on their own. */
  const [shipOpen, setShipOpen] = useState(false);
  /* Back goes to the issue it fixes, else the list it was opened from. */
  const back = () => {
    if (fromIssue) navigate(paths.dashboard.errorFeed.detail(fromIssue));
    else if (from?.to) navigate(from.to);
    else if (location.key !== "default") navigate(-1);
    else navigate(paths.dashboard.simulate.improvements);
  };
  const backLabel = fromIssue || from?.label || "Improvements";
  /* The view closes itself after it navigates somewhere (a trial's run, the
     issue, the scenarios) — only a close that stays here goes back. */
  const here = typeof window !== "undefined" ? window.location.pathname : null;
  const close = () => {
    if (typeof window === "undefined" || window.location.pathname === here)
      back();
  };

  const isRunning = record?.status === OPT_STATUS.RUNNING;
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!isRunning) return undefined;
    const t = setInterval(() => setNow(Date.now()), 400);
    return () => clearInterval(t);
  }, [isRunning]);
  const progress = isRunning ? improvementProgressOf(record, now) : null;
  const finished = !!progress?.done;
  /* The last step has played: it has finished, whoever has it open. An
     ending decided before searching settles to that ending once Reproduce
     has played. */
  const endOf = (o) =>
    o.pendingEnding
      ? settlePending(o)
      : {
          ...o,
          status: OPT_STATUS.COMPLETED,
          completedAt: new Date().toISOString(),
        };
  useEffect(() => {
    if (!finished) return;
    patch({
      optimizations: (envState?.optimizations || []).map((o) =>
        o.id === optId && o.status === OPT_STATUS.RUNNING ? endOf(o) : o,
      ),
    });
  }, [finished]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!env || !record) {
    return (
      <Box sx={{ p: 4, textAlign: "center" }}>
        <Iconify
          icon="solar:question-circle-linear"
          width={32}
          sx={{ color: "text.subtitle" }}
        />
        <Typography sx={{ typography: "s1", color: "text.secondary", mt: 1 }}>
          {env
            ? "Improvement not found — it may have been deleted."
            : "Environment not found."}
        </Typography>
        <Button
          size="small"
          onClick={back}
          startIcon={<Iconify icon="solar:arrow-left-linear" width={14} />}
          sx={{ mt: 2 }}
        >
          Back to Improvements
        </Button>
      </Box>
    );
  }

  const tasks = baseRunOf(env, envState)?.tasks || [];
  const allScenarioIds = (envState?.scenarios || []).map((sc) => sc.id);
  const sourceRun =
    (envState?.runs || []).find((r) => r.id === record.fromRunId) || null;
  const provenance = {
    fromRunId: record.fromRunId || null,
    basedOnVersion: sourceRun?.agentVersion || record.baseAgentVersion || null,
  };
  const landed = envState?.handoffLandings?.[record.id] || null;

  /* The same landing the drawer does: save the version once, with the
     held-back projection beside it. */
  const land = (info = {}) => {
    const changes =
      shippedCandidate(record)?.proposals ||
      record.result?.winner?.proposals ||
      [];
    /* A draft until it's released (see FixMyAgentDrawer). */
    const version = info.version || {
      ...nextAgentVersion(envState, {
        note: info.note,
        applied: changes,
        fromRunId: record.fromRunId || null,
        basedOnVersion: provenance.basedOnVersion,
      }),
      origin: info.origin,
      status: "draft",
    };
    if (!info.version) addAgentVersion?.(version);
    patch({
      omegaExpectation: {
        key: record.id,
        version: version.label,
        fromRun: record.fromRunId || null,
        /* Nothing held back: the target scenarios are the number to hold it to. */
        projected: record.result?.split?.thin
          ? shippedCandidate(record)?.score ?? record.result?.winner?.score
          : shippedCandidate(record)?.held ?? record.result?.heldScore,
        projectedOn: record.result?.split?.thin
          ? "the scenarios it was tuned on"
          : "held-back scenarios",
        envVersion: currentEnvVersion(env, envState)?.label || null,
        scenarios: allScenarioIds,
        addresses: [...new Set(changes.flatMap((c) => c.addresses || []))],
        at: new Date().toISOString(),
      },
      handoffLandings: {
        ...(envState?.handoffLandings || {}),
        [record.id]: version.label,
      },
    });
  };
  /* The landed version by name — landing doesn't change what the environment tests. */
  const newRun = (agent = null) => {
    const url = paths.dashboard.simulate.simulationRun(
      env.id,
      protoRunId(env.id, Date.now().toString(36)),
    );
    const v = agent || landed || record.baseAgentVersion;
    navigate(v ? `${url}?agent=${encodeURIComponent(v)}` : url);
  };
  const runWinner = (candidate = null) => {
    const id = protoRunId(env.id, Date.now().toString(36));
    /* Which run it verified — a winner someone chose is checked before review. */
    patch({
      optimizations: (envState?.optimizations || []).map((o) =>
        o.id === record.id
          ? {
              ...o,
              verifyRunIds: [...(o.verifyRunIds || []), id],
              verifiedTrial: o.preferredTrial || o.result?.winner?.n,
            }
          : o,
      ),
    });
    /* The winner on the version the search started from, named. */
    const url = paths.dashboard.simulate.simulationRun(env.id, id);
    const q = new URLSearchParams();
    if (record.baseAgentVersion) q.set("agent", record.baseAgentVersion);
    if (candidate) q.set("candidate", candidate);
    navigate(q.toString() ? `${url}?${q.toString()}` : url);
  };
  /* Fell behind production while it waited: the kept change once more, on
     the version in production now — Ship waits for it. */
  const recheck = () => {
    const behind = behindProductionOf({ record, env, envState, agents });
    if (!behind) return;
    const id = protoRunId(env.id, Date.now().toString(36));
    patch({
      optimizations: (envState?.optimizations || []).map((o) =>
        o.id === record.id
          ? {
              ...o,
              verifyRunIds: [...(o.verifyRunIds || []), id],
              recheck: {
                on: behind.prodLabel,
                runId: id,
                at: new Date().toISOString(),
              },
            }
          : o,
      ),
    });
    const q = new URLSearchParams({ agent: behind.prodLabel });
    navigate(`${paths.dashboard.simulate.simulationRun(env.id, id)}?${q}`);
  };
  const finish = () =>
    patch({
      optimizations: (envState?.optimizations || []).map((o) =>
        o.id === record.id && o.status === OPT_STATUS.RUNNING ? endOf(o) : o,
      ),
    });

  const search = (inDrawer, { ship = false } = {}) => (
    <OptimizationRunView
      shipOnly={ship}
      onOpenDecision={
        ship
          ? () => {
              setShipOpen(false);
              setFullRecord(false);
              setSearchOpen(true);
            }
          : undefined
      }
      /* In the drawer beside the runs list: the decision, not the runs
           and growth chart the page already shows. */
      decisionOnly={inDrawer}
      record={record}
      name={improvementRunName(envState, record)}
      env={env}
      envState={envState}
      patch={patch}
      tasks={tasks}
      onRunWinner={runWinner}
      handoff={{
        key: record.id,
        landedVersion: landed,
        provenance,
        onLand: land,
        onRerun: newRun,
      }}
      onBack={inDrawer ? () => setFullRecord(false) : back}
      backLabel={inDrawer ? undefined : fromIssue}
      onClose={
        ship
          ? () => setShipOpen(false)
          : inDrawer
            ? () => setSearchOpen(false)
            : close
      }
      onDone={finish}
    />
  );

  /* Handed to the customer's coding agent: its attempts, judged here. */
  if (record.improver) {
    return (
      <Box sx={{ height: "100%", minHeight: 0, bgcolor: "background.paper" }}>
        <Helmet>
          <title>{`${improvementRunName(envState, record)} · Improvements`}</title>
        </Helmet>
        <CodingAgentImprovementView
          env={env}
          envState={envState}
          record={record}
          patch={patch}
          onBack={back}
          backLabel={backLabel}
          handoff={{
            key: record.id,
            landedVersion: landed,
            provenance,
            onLand: land,
            onRerun: newRun,
          }}
        />
      </Box>
    );
  }

  /* Runs to list once the search has tried some — as they finish while it
     runs. A suggestion-only fix tries none, so it stays on the search view.
     One whose ending is already known still shows Reproduce here first. */
  const hasRuns =
    (!!progress?.pending && record.fixDepth !== "suggestion") ||
    ((record.result?.trials || []).length > 0 &&
      (record.status !== OPT_STATUS.RUNNING ||
        (!!progress && record.fixDepth !== "suggestion")));
  if (hasRuns) {
    return (
      <Box sx={{ height: "100%", minHeight: 0, bgcolor: "background.paper" }}>
        <Helmet>
          <title>{`${improvementRunName(envState, record)} · Improvements`}</title>
        </Helmet>
        <ImprovementRunsView
          env={env}
          envState={envState}
          record={record}
          patch={patch}
          onBack={back}
          backLabel={backLabel}
          onOpenSearch={() => {
            setFullRecord(false);
            setSearchOpen(true);
          }}
          progress={progress}
          onOpenIssue={
            record.trigger?.issueId
              ? () =>
                  navigate(
                    paths.dashboard.errorFeed.detail(record.trigger.issueId),
                  )
              : undefined
          }
          onOpenStartRun={
            sourceRun
              ? () =>
                  navigate(
                    paths.dashboard.simulate.simulationRun(
                      env.id,
                      sourceRun.id,
                    ),
                    {
                      state: {
                        back: {
                          to: `${location.pathname}${location.search}`,
                        },
                      },
                    },
                  )
              : undefined
          }
          onVerify={() => runWinner()}
          onRecheck={recheck}
          onShip={() => {
            setSearchOpen(false);
            setShipOpen(true);
          }}
        />
        <SideDrawer
          open={shipOpen}
          onClose={() => setShipOpen(false)}
          width={960}
        >
          {shipOpen && search(true, { ship: true })}
        </SideDrawer>
        <SideDrawer
          open={searchOpen}
          onClose={() => setSearchOpen(false)}
          width={fullRecord ? 960 : 640}
        >
          {searchOpen &&
            (fullRecord ? (
              search(true)
            ) : (
              <ImprovementDecision
                record={record}
                env={env}
                envState={envState}
                onClose={() => setSearchOpen(false)}
                onFullRecord={() => setFullRecord(true)}
              />
            ))}
        </SideDrawer>
      </Box>
    );
  }

  return (
    <Box
      sx={{
        height: "100%",
        minHeight: 0,
        display: "flex",
        flexDirection: "column",
        bgcolor: "background.paper",
      }}
    >
      <Helmet>
        <title>Improvement · Future AGI</title>
      </Helmet>
      {search(false)}
    </Box>
  );
}
