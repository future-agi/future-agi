import { useCallback, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { paths } from "src/routes/paths";
import {
  groupCandidates,
  planRunGroup,
  planVersionGroup,
} from "../_mock/runGroup";
import { useSimStore } from "../store";

/**
 * Start one agent version in several environments at once.
 *
 *   const group = useRunGroup(env.id);
 *   group.candidates              // other environments testing the same agent
 *   group.start(["env-b"], { trials: 3 })
 *
 * `start` records one pending run per environment — this one first — and opens
 * this environment's. With no other environment picked, callers keep their own
 * single-run path; a group of one is not a group.
 */
export default function useRunGroup(envId) {
  const { state, commit } = useSimStore();
  const navigate = useNavigate();
  const candidates = useMemo(
    () => groupCandidates(state, envId),
    [state, envId],
  );

  /* The run plan's limit and warning ride on the leader's link, like trials. */
  const limitParams = (q, { cap, alertAt } = {}) => {
    if (cap) q.set("cap", String(cap));
    if (alertAt) q.set("warn", String(Math.round(alertAt * 100)));
    return q;
  };
  const start = useCallback(
    (otherEnvIds, { trials = 3, cap, alertAt } = {}) => {
      const { records, leader, group } = planRunGroup(
        state,
        envId,
        otherEnvIds,
        { trials },
      );
      /* Saved before navigating, so every member is in history even if the
       page that started them unmounts first. */
      /* The run plan priced every environment together, so each one's run
         holds its share of the limit. */
      const share = cap ? Math.floor(cap / records.length) : null;
      if (share)
        records.forEach(({ run }) => {
          run.budget = { cap: share, alertAt, groupCap: cap };
        });
      records.forEach(({ envId: id, run }) =>
        commit({ type: "recordRun", envId: id, run }),
      );
      const q = limitParams(new URLSearchParams({ trials: String(trials) }), {
        cap: share,
        alertAt,
      });
      if (group.agentVersion) q.set("agent", group.agentVersion);
      navigate(
        `${paths.dashboard.simulate.simulationRun(leader.envId, leader.run.id)}?${q.toString()}`,
      );
    },
    [state, envId, commit, navigate],
  );

  /* One environment, several agent versions — one run each, as a group. */
  const startVersions = useCallback(
    (versions, { trials = 3, cap, alertAt } = {}) => {
      const { records, leader } = planVersionGroup(state, envId, versions, {
        trials,
      });
      /* The run plan's limit covers every version together (its estimate
         does), so each version's run holds its share — never the whole
         limit each, which would let the group spend it several times. */
      const share = cap ? Math.floor(cap / records.length) : null;
      if (share)
        records.forEach(({ run }) => {
          run.budget = { cap: share, alertAt, groupCap: cap };
        });
      records.forEach(({ envId: id, run }) =>
        commit({ type: "recordRun", envId: id, run }),
      );
      const q = limitParams(new URLSearchParams({ trials: String(trials) }), {
        cap: share,
        alertAt,
      });
      q.set("agent", leader.run.agentVersion);
      navigate(
        `${paths.dashboard.simulate.simulationRun(leader.envId, leader.run.id)}?${q.toString()}`,
      );
    },
    [state, envId, commit, navigate],
  );

  return { candidates, start, startVersions };
}
