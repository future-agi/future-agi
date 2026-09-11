import { useMemo } from "react";
import { useGetProjectEvalConfigs } from "../../../../api/project/project-detail";
import { formatProjectEvals } from "./common";
import {
  getBundleRequiredKeys,
  getUpdatedRequiredKeys,
  groupEvalsByRequiredKeys,
  getCommonFutureEvalModels,
} from "../../../common/EvaluationDrawer/common";
import { SCENARIO_STATUS } from "src/pages/dashboard/scenarios/common";
import { FUTUREAGI_LLM_MODELS } from "../../../common/EvaluationDrawer/validation";

function removeElements(array1, array2) {
  return array1.filter((element) => !array2.includes(element));
}

export function useEvaluationKeys(
  observeId,
  scenarioDetail,
  currentStep,
  options = {},
) {
  const { alwaysFetch = false, alwaysCompute = false } = options;

  const shouldFetch = alwaysFetch
    ? Boolean(observeId)
    : Boolean(
        observeId && scenarioDetail?.status === SCENARIO_STATUS.COMPLETED,
      );

  const { data: projectEvalsData, isLoading } = useGetProjectEvalConfigs(
    observeId,
    {
      queryKey: [
        "project-detail",
        "eval-configs",
        observeId,
        scenarioDetail?.status,
      ],
      enabled: shouldFetch,
    },
  );

  // Compute evaluation mapping schema
  const projectEvals = useMemo(
    () => formatProjectEvals(projectEvalsData?.eval_configs) || [],
    [projectEvalsData?.eval_configs],
  );

  const shouldCompute = alwaysCompute || currentStep === 2;

  const allRequiredKeys = useMemo(() => {
    if (!shouldCompute) return [];
    return getBundleRequiredKeys(projectEvals);
  }, [projectEvals, shouldCompute]);

  const requiredKeys = useMemo(() => {
    if (!shouldCompute) return [];
    return getUpdatedRequiredKeys({
      result: {
        members: projectEvals,
        requiredKeys: allRequiredKeys,
      },
    });
  }, [allRequiredKeys, projectEvals, shouldCompute]);

  const groupedRequiredKeys = useMemo(() => {
    if (!shouldCompute) return [];
    return groupEvalsByRequiredKeys(projectEvals);
  }, [projectEvals, shouldCompute]);

  const optionalKeys = useMemo(() => {
    if (!shouldCompute) return [];
    const allOptionalKeys = projectEvals
      ?.filter((item) => item?.optionalKeys)
      .flatMap((item) => item?.optionalKeys);

    const filteredOptionalKeys = allOptionalKeys.filter(
      (key) => !requiredKeys.includes(key),
    );

    const groupedOptionalKeys =
      groupedRequiredKeys
        ?.flatMap((group) => {
          return group?.requiredKeys
            ?.filter((item) => String(item).startsWith("OPT+"))
            ?.map((item) => item.slice(4, item?.length));
        })
        ?.filter((key) => !requiredKeys.includes(key)) || [];

    return [...new Set([...filteredOptionalKeys, ...groupedOptionalKeys])];
  }, [projectEvals, groupedRequiredKeys, requiredKeys, shouldCompute]);

  const transformedRequiredKeys = useMemo(
    () =>
      shouldCompute ? requiredKeys.map((key) => key.replace(/\./g, "_")) : [],
    [requiredKeys, shouldCompute],
  );
  const transformedOptionalKeys = useMemo(
    () =>
      shouldCompute ? optionalKeys.map((key) => key.replace(/\./g, "_")) : [],
    [optionalKeys, shouldCompute],
  );

  const filteredRequiredKeys = useMemo(
    () =>
      shouldCompute
        ? removeElements(transformedRequiredKeys, transformedOptionalKeys ?? [])
        : [],
    [transformedOptionalKeys, transformedRequiredKeys, shouldCompute],
  );

  const isFutureagiBuilt = useMemo(() => {
    return projectEvals.some(
      (evalItem) =>
        evalItem.tags?.includes("FUTURE_EVALS") ||
        evalItem.tags?.includes("FUTUREAGI_BUILT"),
    );
  }, [projectEvals]);

  const allowedModels = useMemo(() => {
    return getCommonFutureEvalModels({
      result: { members: projectEvals },
    });
  }, [projectEvals]);

  const modelsToShow = useMemo(() => {
    const allowed = [];
    if (typeof allowedModels === "string") {
      for (let j = 0; j < FUTUREAGI_LLM_MODELS.length; j++) {
        const futureAgiLLm = FUTUREAGI_LLM_MODELS[j];
        if (futureAgiLLm.value === allowedModels) {
          allowed.push(futureAgiLLm);
        }
      }
    } else {
      for (let i = 0; i < allowedModels.length; i++) {
        const allowedModel = allowedModels[i];
        for (let j = 0; j < FUTUREAGI_LLM_MODELS.length; j++) {
          const futureAgiLLm = FUTUREAGI_LLM_MODELS[j];
          if (futureAgiLLm.value === allowedModel) {
            allowed.push(futureAgiLLm);
          }
        }
      }
    }
    return allowed;
  }, [allowedModels]);

  return {
    projectEvals,
    allRequiredKeys,
    requiredKeys,
    groupedRequiredKeys,
    optionalKeys,
    transformedRequiredKeys,
    transformedOptionalKeys,
    filteredRequiredKeys,
    isFutureagiBuilt,
    allowedModels,
    modelsToShow,
    isLoading,
  };
}
