import { useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { handleExperimentRerunError } from "src/utils/experimentRerunError";

export const useStopExperiment = (experimentId, onSuccess = null) => {
  const { mutate, isPending } = useMutation({
    mutationFn: async () => {
      // The stop body is ModelHubEmptyRequest (an object); posting no body
      // fails the request contract check in non-production builds.
      return axios.post(endpoints.develop.experiment.stop(experimentId), {});
    },
    onSuccess: () => {
      if (onSuccess) {
        onSuccess();
      }
    },
  });
  return { stopExperiment: mutate, isStoppingExperiment: isPending };
};

// `_selectAll` is kept for call-site compatibility only: the re-run view
// (ExperimentRerunRequestSerializer, reject_unknown_fields=True) accepts
// experiment_ids / max_concurrent_rows / use_temporal and answers 400 to any
// other key, so it is not sent.
export const useReRunExperiment = (
  experimentIds,
  _selectAll,
  onSuccess = null,
) => {
  const { mutate, isPending } = useMutation({
    meta: { errorHandled: true },
    onError: handleExperimentRerunError,
    mutationFn: async () => {
      return axios.post(endpoints.develop.experiment.rerun, {
        experiment_ids: experimentIds,
      });
    },
    onSuccess: () => {
      if (onSuccess) {
        onSuccess?.();
      }
    },
  });
  return { reRunExperiment: mutate, isReRunningExperiment: isPending };
};
