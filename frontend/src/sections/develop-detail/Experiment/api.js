import { useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

export const useStopExperiment = (experimentId, onSuccess = null) => {
  const { mutate, isPending } = useMutation({
    mutationFn: async () => {
      return axios.post(endpoints.develop.experiment.stop(experimentId));
    },
    onSuccess: () => {
      if (onSuccess) {
        onSuccess();
      }
    },
  });
  return { stopExperiment: mutate, isStoppingExperiment: isPending };
};

export const useReRunExperiment = (
  experimentIds,
  _selectAll,
  onSuccess = null,
) => {
  const { mutate, isPending } = useMutation({
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
