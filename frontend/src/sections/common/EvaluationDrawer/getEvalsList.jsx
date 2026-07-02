import { useMutation, useQuery } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import { useMemo } from "react";
import axios, { endpoints } from "src/utils/axios";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { paramsSerializer } from "src/utils/utils";

// Single source of truth for the user-eval-list query key. Exported so
// callers that invalidate after mutations stay in sync with the query
// definition (previously drifted — invalidations passed `module` literally
// while the query used "develop" / "experiment" / "workbench" prefixes,
// so dataset invalidations were silent no-ops).
const MODULE_PREFIX = {
  dataset: "develop",
  experiment: "experiment",
  workbench: "workbench",
};

export const getUserEvalListKey = (module = "dataset", id, payload) => {
  const prefix = MODULE_PREFIX[module] ?? "workbench";
  const base = [prefix, "user-eval-list", id];
  return payload === undefined ? base : [...base, payload];
};

export const useEvalsList = (id, payload, module = "dataset", experimentId) => {
  const queryKey = getUserEvalListKey(module, id, payload);
  const query = useQuery({
    queryKey: queryKey,
    queryFn: () => {
      if (module === "workbench") {
        return axios.get(endpoints.develop.runPrompt.getEvaluationConfigs(id));
      }
      if (module === "experiment" && experimentId) {
        if (!experimentId) return;
        return axios.get(endpoints.develop.eval.getEvalsList(id), {
          params: { experiment_id: experimentId, ...payload },
          paramsSerializer: paramsSerializer(),
        });
      }
      return axios.get(endpoints.develop.eval.getEvalsList(id), {
        params: { ...payload },
        paramsSerializer: paramsSerializer(),
      });
    },
    select: (res) => {
      const result = res?.data?.result;
      if (module === "workbench") {
        return {
          evals: result.evaluation_configs,
        };
      }
      if (payload?.search_text) {
        trackEvent(Events.evalsSearchSubmitted, {
          [PropertyName.searchTerm]: payload?.search_text,
        });
      }
      return result;
    },
    enabled: !!id,
    // Poll every 5s — keeps status fresh while drawer is open.
    // Stops automatically when component unmounts (drawer closes).
    refetchInterval: 5000,
  });
  return query;
};

export const useRunEvalMutation = (
  id,
  onSuccessCallback,
  module = "dataset",
) => {
  const getEndpoint = useMemo(() => {
    switch (module) {
      case "experiment":
        return (targetId) =>
          endpoints.develop.experiment.runEvaluation(targetId);
      case "workbench":
        return (targetId) =>
          endpoints.develop.runPrompt.runEvalsOnMultipleVersions(targetId);
      default:
        return (targetId) => endpoints.develop.eval.runEvals(targetId);
    }
  }, [module]);
  return useMutation({
    /**
     *
     * @param {Object} payload
     * @returns
     */
    mutationFn: (payload) => {
      if (!id) {
        return Promise.reject(
          new Error("Cannot run evaluations without an id."),
        );
      }
      return axios.post(getEndpoint(id), payload);
    },
    onSuccess: () => {
      enqueueSnackbar("Evaluation started successfully", {
        variant: "success",
      });
      onSuccessCallback?.();
    },
  });
};
