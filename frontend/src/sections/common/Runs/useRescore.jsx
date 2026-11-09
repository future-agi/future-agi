// W1-2: the one re-score door on the frontend. Every screen that scores
// stored outputs again (leaderboard Re-score, the results eval column menu,
// the analysis "Re-score #2A on vN", Score with… on a selection, and the
// experiment's Evaluations drawer: Run, Run all and Update) asks here: a
// confirm that names what runs, what it costs (the server's plan, a
// dry_run of the very request, priced by the one estimator,
// estimateInputs.judgeCallUsd) and that a new run is made; then ONE request
// per experiment carrying every eval, and one toast with "Open #2A·2" and
// "See the 7 rows". Copy: rescoreCopyModel.
import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Button, Stack, Typography } from "@mui/material";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { getRequestErrorMessage } from "src/utils/errorUtils";
import { notify } from "src/sections/common/ux/toast";
import ConfirmDialog from "src/sections/common/ux/ConfirmDialog";
import { judgeCallUsd } from "src/sections/develop-detail/Experiment/estimateInputs";
import {
  compareUrl,
  experimentUrl,
} from "src/sections/develop-detail/ExperimentTab/leaderboardModel";
import {
  mergeRescorePlans,
  versionPlanGuard,
  rescoreActions,
  rescoreConfirmBody,
  rescoreCostLine,
  rescoreMessage,
  mergeRescoreResults,
  rescoreTitle,
} from "./rescoreCopyModel";

/**
 * One rerun-cells body per experiment: the evals each run names
 * (`evalIdsOf(run)`), the runs, and the rows when it is a selection.
 */
export function rescoreBodies({
  runs = [],
  evalIdsOf,
  rowIds,
  errorPolicy,
  datasetVersion,
}) {
  const byExp = new Map();
  runs.forEach((r) => {
    if (!r?.experiment_id) return;
    const ids = (evalIdsOf?.(r) ?? []).filter(Boolean);
    // W1-7: a policy change alone names no eval (every eval of the run).
    if (!ids.length && !errorPolicy) return;
    const cur = byExp.get(r.experiment_id) ?? {
      user_eval_metric_ids: new Set(),
      run_ids: [],
    };
    ids.forEach((id) => cur.user_eval_metric_ids.add(id));
    cur.run_ids.push(r.run_id);
    byExp.set(r.experiment_id, cur);
  });
  return [...byExp.entries()].map(([experimentId, b]) => ({
    experimentId,
    body: errorPolicy
      ? {
          eval_ids: [...b.user_eval_metric_ids],
          run_ids: b.run_ids,
          error_policy: errorPolicy,
        }
      : {
          user_eval_metric_ids: [...b.user_eval_metric_ids],
          run_ids: b.run_ids,
          ...(rowIds?.length ? { row_ids: rowIds } : {}),
          // HO R6: score on the rows of a newer dataset version.
          ...(Number.isInteger(datasetVersion)
            ? { dataset_version: datasetVersion }
            : {}),
        },
    // A policy change goes to run-evaluations (rerun-cells needs eval ids).
    path: errorPolicy ? "runEvaluation" : "reRunExperimentCell",
  }));
}

const post = (list, extra = {}) =>
  Promise.all(
    list.map(({ experimentId, body, path = "reRunExperimentCell", url }) =>
      axios.post(url ?? endpoints.develop.experiment[path](experimentId), {
        ...body,
        ...extra,
      }),
    ),
  );

/**
 * The confirm's plan for a request: the very request with `dry_run` (the
 * server counts the judge calls, writes nothing), merged per experiment; a
 * re-score on vN goes ahead only when the plan is vN's (HO R6). A door that
 * must know first whether the server would do it (Re-score on vN) reads
 * the same plan.
 */
export const planRescore = (r) =>
  post(r.posts ?? rescoreBodies(r), { dry_run: true }).then((res) =>
    versionPlanGuard(
      mergeRescorePlans(res.map((x) => x?.data?.result?.plan ?? null)),
      r.datasetVersion,
    ),
  );

/**
 * @param {{datasetId: string, onDone?: Function}} opts
 * @returns {{request: Function, dialog: React.ReactNode, pending: boolean}}
 *   request({runs, evalIdsOf, evalNames, rowIds?}) opens the confirm; or
 *   request({posts: [{url, body}], evalNames, experimentId, title?,
 *   confirmLabel?}) for a door with its own endpoint (start_evals_process,
 *   edit_and_run_user_eval): the confirm's plan is that request with
 *   `dry_run`, and the runs it names come from the plan; `adding` words
 *   it as Add evaluation (the eval joins, then scores); `title(runs,
 *   {nothing})` and `confirmLabel({nothing})` may follow the plan (nothing:
 *   it found nothing to score). Returns a promise
 *   of the merged result, or {cancelled: true}.
 */
export function useRescore({ datasetId, onDone } = {}) {
  const [req, setReq] = useState(null);
  const [plan, setPlan] = useState(null);
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const doneRef = useRef(onDone);
  doneRef.current = onDone;
  const settleRef = useRef(null);
  const bodies = useMemo(
    () => (req ? req.posts ?? rescoreBodies(req) : []),
    [req],
  );
  // The runs the confirm names: the caller's, else the plan's; once the
  // plan answers, each carries the label its new run will get.
  const shownRuns = useMemo(() => {
    if (!req) return null;
    if (!req.runs) return plan?.runs ?? null;
    const planned = new Map(
      (plan && !plan.error ? plan.runs ?? [] : []).map((x) => [x.run_id, x]),
    );
    return planned.size
      ? req.runs.map((x) =>
          planned.has(x.run_id)
            ? { ...x, next_label: planned.get(x.run_id).next_label ?? null }
            : x,
        )
      : req.runs;
  }, [req, plan]);

  // The confirm's plan: the server counts the judge calls, writes nothing.
  useEffect(() => {
    if (!req) return undefined;
    let live = true;
    setPlan(null);
    planRescore({ ...req, posts: bodies })
      .then((next) => {
        if (live) setPlan(next);
      })
      // The server's own reason (an eval already added, a template not
      // found) is what the confirm shows.
      .catch(
        (e) =>
          live &&
          setPlan({ error: true, reason: getRequestErrorMessage(e, "") }),
      );
    return () => {
      live = false;
    };
  }, [req, bodies]);

  const settle = useCallback((value) => {
    const fn = settleRef.current;
    settleRef.current = null;
    fn?.(value);
  }, []);

  const request = useCallback(
    (r) => {
      if (!r?.runs?.length && !r?.posts?.length)
        return Promise.resolve({ cancelled: true });
      settle({ cancelled: true });
      return new Promise((resolve) => {
        settleRef.current = resolve;
        setReq(r);
      });
    },
    [settle],
  );

  const toast = useCallback(
    (merged, runs, fallbackExpId) => {
      const expOf = (runId) =>
        (runs ?? []).find((x) => x.run_id === runId)?.experiment_id ??
        fallbackExpId ??
        null;
      const acts = rescoreActions(merged);
      const first = merged?.runs?.[0];
      const expId = first ? expOf(first.from_run_id) : null;
      notify(rescoreMessage(merged), {
        variant: merged?.unchanged ? "info" : "success",
        key: "rescore",
        ...(acts.open && {
          action: () => (
            <Stack direction="row" spacing={0.5}>
              <Button
                size="small"
                color="inherit"
                data-testid="rescore-open"
                onClick={() =>
                  navigate(
                    experimentUrl(datasetId, expId, {
                      runId: acts.open.runId,
                    }),
                  )
                }
              >
                {acts.open.label}
              </Button>
              {acts.rows && (
                <Button
                  size="small"
                  color="inherit"
                  data-testid="rescore-see-rows"
                  onClick={() =>
                    navigate(
                      compareUrl(datasetId, {
                        runIds: [acts.rows.baselineId, acts.rows.runId],
                        baselineId: acts.rows.baselineId,
                        pills: [
                          {
                            type: "rows",
                            ids: acts.rows.rowIds,
                            label: acts.rows.pillLabel,
                          },
                        ],
                      }),
                    )
                  }
                >
                  {acts.rows.label}
                </Button>
              )}
            </Stack>
          ),
        }),
      });
    },
    [datasetId, navigate],
  );

  const confirm = useCallback(async () => {
    if (!req) return;
    setBusy(true);
    try {
      const res = await post(bodies);
      const merged = mergeRescoreResults(res);
      queryClient.invalidateQueries({ queryKey: ["develop"] });
      queryClient.invalidateQueries({ queryKey: ["experiment"] });
      queryClient.invalidateQueries({ queryKey: ["experiment-list"] });
      queryClient.invalidateQueries({ queryKey: ["datasets", "list"] });
      toast(merged, req.runs ?? plan?.runs, req.experimentId);
      doneRef.current?.(merged);
      setReq(null);
      settle(merged ?? {});
    } catch (e) {
      notify(getRequestErrorMessage(e, "Could not re-score"), {
        variant: "error",
      });
    } finally {
      setBusy(false);
    }
  }, [req, bodies, plan, queryClient, toast, settle]);

  const cost = plan && !plan.error ? rescoreCostLine(plan, judgeCallUsd) : null;
  // The plan counted nothing to score (a policy change always rebuilds its
  // errored tries): the confirm promises no new run.
  const nothing =
    Boolean(plan) && !plan.error && !plan.cells && !plan.policy_tries;
  const titleRuns = shownRuns ?? [];
  const dialog = (
    <ConfirmDialog
      open={Boolean(req)}
      title={
        req
          ? req.title?.(titleRuns, { nothing }) ??
            rescoreTitle({ ...req, runs: titleRuns })
          : ""
      }
      body={
        req ? (
          <Stack spacing={1} data-testid="rescore-confirm">
            <Typography typography="s1" color="text.secondary">
              {shownRuns
                ? rescoreConfirmBody({
                    runs: shownRuns,
                    evalNames: req.evalNames ?? [],
                    rowCount: req.rowIds?.length,
                    errorPolicy: req.errorPolicy,
                    adding: Boolean(req.adding),
                    nothing,
                    datasetVersion: req.datasetVersion,
                    runOnlyEvals: req.runOnlyEvals ?? [],
                  })
                : plan?.error
                  ? "Could not read which runs this re-scores"
                  : "Reading which runs this re-scores…"}
            </Typography>
            <Typography
              typography="s2"
              fontWeight="fontWeightMedium"
              data-testid="rescore-cost"
            >
              {plan?.error
                ? plan.reason || "Could not read what this costs"
                : cost
                  ? cost.text
                  : "Counting judge calls…"}
            </Typography>
          </Stack>
        ) : null
      }
      confirmLabel={
        (typeof req?.confirmLabel === "function"
          ? req.confirmLabel({ nothing })
          : req?.confirmLabel) ?? (req?.rowIds?.length ? "Score" : "Re-score")
      }
      cancelLabel="Cancel"
      danger={false}
      busy={busy || !plan || Boolean(plan?.error)}
      onConfirm={confirm}
      onClose={() => {
        if (busy) return;
        setReq(null);
        settle({ cancelled: true });
      }}
    />
  );
  return { request, dialog, pending: busy };
}
