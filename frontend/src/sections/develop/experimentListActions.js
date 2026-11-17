import { useNavigate } from "react-router";
import { isSurfaceShown } from "src/launch/launchSwitch";

/** Experiment navigation is admitted independently of the list's write actions. */
export function useExperimentListActions(actions) {
  const navigate = useNavigate();
  return {
    ...actions,
    ...(isSurfaceShown("exp.classic") && !actions.openExperiments
      ? {
          openExperiments: (row) =>
            navigate(`/dashboard/develop/${row.id}?tab=experiments`),
        }
      : {}),
    ...(isSurfaceShown("exp.classic") && !actions.newExperiment
      ? {
          newExperiment: (row) =>
            navigate(`/dashboard/develop/${row.id}?tab=data&new_experiment=1`),
        }
      : {}),
  };
}
