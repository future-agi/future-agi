import { LoadingButton } from "@mui/lab";
import { Box, Collapse, useTheme } from "@mui/material";
import React, { useEffect, useMemo, useState } from "react";
import { ConfirmDialog } from "../../../components/custom-dialog";
import PropTypes from "prop-types";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useParams } from "react-router";
import { useKnowledgeBaseList } from "src/api/knowledge-base/files";
import { useEvaluationContext } from "./context/EvaluationContext";
import { allowedColumnFilter } from "src/sections/develop-detail/Common/EvaluationConfigure/common";
import EvaluationTest from "./EvaluationTest";
import { enqueueSnackbar } from "notistack";
import { FUTUREAGI_LLM_MODELS } from "./validation";
import { useWorkbenchEvaluationContext } from "src/sections/workbench/createPrompt/Evaluation/context/WorkbenchEvaluationContext";
import { useAuthContext } from "src/auth/hooks";
import TooltipForEvals from "./TooltipForEvalPopover";
import {
  EvalTypeId,
  EVALUATION_PAGE_ID_MAPPER,
  getCommonFutureEvalModels,
  getUpdatedRequiredKeys,
  groupEvalsByRequiredKeys,
  getEvalBaseName,
  DEFAULT_EVAL_MODEL,
  validateRequiredColumnMapping,
} from "./common";

import { useRunEvalMutation } from "./getEvalsList";
import { format } from "date-fns";
import logger from "src/utils/logger";
import EvaluationMappingFormContent from "./EvaluationMappingFormContent";
import { sanitizeEvalMapping } from "src/sections/common/EvalPicker/serializeEvalConfig";

function removeElements(array1, array2) {
  return array1.filter((element) => !array2.includes(element));
}

function getFunctionParamsSchemaFromEvalConfig(evalConfig) {
  if (!evalConfig || typeof evalConfig !== "object") return {};
  return (
    evalConfig?.functionParamsSchema ||
    evalConfig?.config?.functionParamsSchema ||
    {}
  );
}

function getGroupFunctionParamsRequirements(groupData) {
  return groupData?.result?.functionParamsRequirements || {};
}

function getDefaultValues(
  evalsData,
  isEditMode = false,
  selectedEval = null,
  preserveName,
) {
  // Auto-select turing_large as default model if not already set
  const defaultModel = evalsData?.selected_model || DEFAULT_EVAL_MODEL;

  const defaultName =
    isEditMode || preserveName
      ? evalsData?.name ?? ""
      : `${getEvalBaseName(evalsData || selectedEval)}_${format(new Date(), "dd_MMM_yyyy")}`;

  return {
    template_id: evalsData?.template_id ?? "",
    model: defaultModel,
    name: defaultName,
    config: {
      mapping: Object.entries(evalsData?.mapping ?? {}).reduce(
        (acc, [key, value]) => {
          acc[key.replace(/\./g, "_")] = value;
          return acc;
        },
        {},
      ),
      config: {},
      params:
        evalsData?.params ??
        evalsData?.config?.params ??
        evalsData?.config?.config?.params ??
        {},
      reasonColumn: true,
    },
    // Auto-enable error localization by default
    errorLocalizer: evalsData?.errorLocalizer ?? true,
    kbId: evalsData?.kbId ?? "",
    run: false,
  };
}

const buildFormSchema = (required_keys, { isNameRequired = true } = {}) => {
  return z.object({
    templateId: z.string(),
    model: z.string(),
    name: isNameRequired
      ? z.string().min(1, "This field is required")
      : z.string().optional(),
    config: z.object({
      mapping: z
        .record(z.string().optional())
        .refine(
          (val) =>
            required_keys.every((key) => val[key] && val[key].trim() !== ""),
          {
            message: "Required input mappings must be filled",
          },
        ),
      // Function eval params can be numeric/boolean/list/object (e.g. k=5).
      config: z.record(z.any()),
      params: z.record(z.any()).optional(),
      reasonColumn: z.boolean(),
    }),
    errorLocalizer: z.boolean(),
    kbId: z.string().optional(),
    run: z.boolean(),
  });
};

const EvaluationMappingFormChild = ({
  allColumns,
  onClose,
  onBack,
  onSubmit: handleClose,
  handleTest,
  isTesting,
  refreshGrid,
  onFormSave,
  requiredColumnIds = "",
  hideBackButtons = false,
  evalsData = null,
  hideTitle = false,
  hideFieldColumns = false,
  isEvalConfig = false,
  isViewMode = false,
  disableSaveButton,
  hideModel,
  isEditMode = false,
  hideKnowledgeBase,
  preserveName,
  ...rest
}) => {
  const { role } = useAuthContext();
  const theme = useTheme();

  // Get the id for fetching JSON schemas
  const {
    actionButtonConfig: { id: contextId },
  } = useEvaluationContext();
  const datasetId = rest?.id ?? contextId;

  const {
    selectedEval = {},
    setIsDirty,
    module,
    setVisibleSection,
    selectedColumn,
    formValues,
    setFormValues,
    actionButtonConfig: {
      id,
      showAdd,
      showTest,
      testLabel,
      runLabel,
      handleRun,
      saveLabel,
      showDefaultButton = true,
    },
    setCurrentTab,
  } = useEvaluationContext();

  const [removedEvals, setRemovedEvals] = useState([]);
  const { versions } = useWorkbenchEvaluationContext();
  const _id = rest?.id ?? id;
  // currently is prompt id
  const {
    id: pageId,
    executionId,
    testId: testIdParam,
    experimentId,
  } = useParams();
  const testId = rest.testId || testIdParam;

  // Get module for conditional logic
  const _module = rest?.module ?? module;

  const { data: evalConfiguration, isPending } = useQuery({
    queryKey: ["develop", "eval-template-config", selectedEval?.id],
    queryFn: () =>
      axios.get(
        endpoints.develop.eval.getPreviouslyConfiguredEvalTemplateConfig(
          _id,
          selectedEval?.id,
        ),
        { params: { eval_type: "preset" } },
      ),
    select: (d) => d.data?.result?.eval,
    enabled:
      !!selectedEval?.id && selectedEval?.isGroupEvals !== true && !isEditMode,
  });

  const { data: groupData } = useQuery({
    queryKey: ["group-evals", selectedEval?.id],
    queryFn: () =>
      axios.get(`${endpoints.develop.eval.groupEvals}${selectedEval?.id}/`),
    enabled: Boolean(selectedEval?.isGroupEvals && selectedEval?.id),
    select: (d) => d.data,
  });
  const isSampleFutureAgiBuilt = groupData?.result?.evalGroup?.isSample;
  const [showAll, setShowAll] = useState(false);
  const members = groupData?.result?.members || [];

  const visibleItems = showAll ? members : members.slice(0, 10);
  const filteredVisibleItems = visibleItems?.filter(
    (item) => !removedEvals.includes(item?.eval_template_id),
  );
  const defaultValues = getDefaultValues(
    evalsData,
    isEditMode,
    selectedEval,
    preserveName,
  );

  // Fetch JSON schemas for JSON-type columns
  logger.debug(module);

  const evalConfig = useMemo(() => {
    return evalsData ? evalsData : evalConfiguration;
  }, [evalsData, evalConfiguration]);

  const modeHelpData = useMemo(() => {
    if (!selectedEval?.isGroupEvals) return null;

    const members =
      groupData?.result?.members?.filter(
        (item) => !removedEvals.includes(item?.eval_template_id),
      ) ?? [];
    const futureEvals = members.filter(
      (item) => Array.isArray(item?.tags) && item.tags.includes("FUTURE_EVALS"),
    );
    if (futureEvals.length === 0) return null;

    const names = futureEvals.map((item) => item.name);
    const visible = names.slice(0, 2); // show first 3
    const hidden = names.slice(2); // rest

    return {
      visible,
      hidden,
      total: names.length,
    };
  }, [groupData?.result?.members, selectedEval?.isGroupEvals, removedEvals]);

  const modeHelpMessage = useMemo(() => {
    if (!modeHelpData) return null;

    return (
      <>
        Language model needed for the following evals –{" "}
        {modeHelpData?.visible.join(", ")}
        {modeHelpData?.hidden.length > 0 && (
          <>
            {" "}
            <TooltipForEvals selectedEvalItem={modeHelpData?.hidden}>
              <Box
                component="span"
                sx={{
                  cursor: "default",
                  color: "text.primary",
                  userSelect: "none",
                  ml: 1,
                }}
              >
                +{modeHelpData.hidden.length} more
              </Box>
            </TooltipForEvals>
          </>
        )}
      </>
    );
  }, [modeHelpData]);

  const isFutureagiBuilt = useMemo(() => {
    if (selectedEval?.isGroupEvals && selectedEval?.id) {
      return (
        groupData?.result?.members
          ?.filter((item) => !removedEvals.includes(item?.eval_template_id))
          ?.some((item) => item?.tags?.includes("FUTURE_EVALS")) || false
      );
    }

    const tags =
      evalsData?.eval_template_tags ?? selectedEval?.eval_template_tags;
    return (
      (tags?.includes("FUTUREAGI_BUILT") ||
        evalsData?.type === "futureagi_built") ??
      false
    );
  }, [
    evalsData?.eval_template_tags,
    groupData?.result?.members,
    selectedEval?.eval_template_tags,
    selectedEval?.id,
    selectedEval?.isGroupEvals,
    removedEvals,
    evalsData?.type,
  ]);

  const required_keys = useMemo(() => {
    if (selectedEval?.isGroupEvals && groupData) {
      return getUpdatedRequiredKeys(groupData, removedEvals);
    } else {
      return evalConfig?.required_keys ?? [];
    }
  }, [
    evalConfig?.required_keys,
    groupData,
    selectedEval?.isGroupEvals,
    removedEvals,
  ]);

  const transformedRequiredKeys = required_keys.map((key) =>
    key.replace(/\./g, "_"),
  );
  const required_keysMappingObject = required_keys.reduce((acc, curr) => {
    return {
      ...acc,
      [curr.replace(/\./g, "_")]: curr,
    };
  }, {});

  const optionalKeys = useMemo(() => {
    if (selectedEval?.isGroupEvals) {
      return members
        ?.filter((item) => !removedEvals.includes(item?.eval_template_id))
        ?.filter((item) => item?.optionalKeys)
        .flatMap((item) => item?.optionalKeys);
    } else {
      return evalConfig?.optionalKeys ?? [];
    }
  }, [
    selectedEval?.isGroupEvals,
    members,
    evalConfig?.optionalKeys,
    removedEvals,
  ]);

  const transformedOptionalKeys = optionalKeys.map((key) =>
    key.replace(/\./g, "_"),
  );
  const filteredRequiredKeys = useMemo(
    () =>
      removeElements(transformedRequiredKeys, transformedOptionalKeys ?? []),
    [transformedOptionalKeys, transformedRequiredKeys],
  );
  const groupedRequiredKeys = useMemo(() => {
    if (!selectedEval?.isGroupEvals) return [];
    return groupEvalsByRequiredKeys(
      groupData?.result?.members?.filter(
        (item) => !removedEvals.includes(item?.eval_template_id),
      ),
    );
  }, [groupData?.result?.members, selectedEval?.isGroupEvals, removedEvals]);

  const allowedModels = useMemo(() => {
    if (selectedEval?.isGroupEvals) {
      // return groupData?.result?.models ?? [];
      return getCommonFutureEvalModels(groupData, removedEvals);
    } else {
      return evalConfig?.models ?? evalsData?.models ?? [];
    }
  }, [
    evalConfig?.models,
    groupData,
    selectedEval?.isGroupEvals,
    removedEvals,
    evalsData?.models,
  ]);

  const formSchema = useMemo(
    () =>
      buildFormSchema(filteredRequiredKeys, {
        isNameRequired: !selectedEval?.isGroupEvals,
      }),
    [filteredRequiredKeys, selectedEval?.isGroupEvals],
  );

  const {
    control,
    formState,
    watch,
    setValue,
    getValues,
    handleSubmit,
    unregister,
  } = useForm({
    defaultValues: defaultValues,
    resolver: zodResolver(formSchema),
    mode: "onChange",
  });

  // Initialize defaults for preset function-eval config fields (e.g., k for @k metrics).
  useEffect(() => {
    const schema = getFunctionParamsSchemaFromEvalConfig(evalConfig);

    if (
      !schema ||
      typeof schema !== "object" ||
      Object.keys(schema).length === 0
    ) {
      return;
    }

    const existingParams = getValues("config.params") || {};
    if (Object.keys(existingParams).length > 0) {
      return;
    }

    const defaults = {};
    Object.entries(schema).forEach(([key, value]) => {
      if (value && typeof value === "object" && "default" in value) {
        defaults[key] = value.default;
      }
    });

    const existingFromEval =
      evalConfig?.params || evalConfig?.config?.params || {};
    const mergedDefaults = {
      ...defaults,
      ...(existingFromEval && typeof existingFromEval === "object"
        ? existingFromEval
        : {}),
    };

    if (Object.keys(mergedDefaults).length > 0) {
      setValue("config.params", mergedDefaults, {
        shouldDirty: false,
        shouldValidate: true,
      });
    }
  }, [evalConfig, getValues, selectedEval, setValue]);

  const { data: knowledgeBaseList } = useKnowledgeBaseList("", null, {
    status: true,
  });

  const formData = watch();
  useMemo(() => {
    if (
      isEvalConfig &&
      formData &&
      JSON.stringify(formData) !== JSON.stringify(formValues)
    ) {
      setFormValues(formData);
    }
  }, [formData, isEvalConfig]);

  const model = watch("model");
  const queryClient = useQueryClient();
  const [mappingError, setMappingError] = useState(null);
  const allowedColumns = useMemo(() => {
    const templist = allowedColumnFilter(evalConfig, allColumns);
    if (selectedColumn === "") {
      return templist;
    } else {
      return templist.filter((col) => col.field === selectedColumn);
    }
  }, [allColumns, evalConfig, selectedColumn]);
  const knowledgeBaseOptions = useMemo(
    () =>
      (knowledgeBaseList || []).map(({ id, name }) => ({
        label: name,
        value: id,
      })),
    [knowledgeBaseList],
  );

  const { mutate: updateEval } = useMutation({
    /**
     *
     * @param {Object} data
     * @returns
     */
    mutationFn: (data) =>
      axios.post(endpoints.develop.eval.editEval(_id, evalsData?.id), data),
  });

  const { mutate: updateSimulateEval } = useMutation({
    mutationFn: (data) =>
      axios.post(
        endpoints.runTests.updateSimulateEval(testId, evalsData?.id),
        data,
      ),
  });

  const transformMapping = (mapping) => {
    const newMapping = {};
    for (let i = 0; i < transformedRequiredKeys.length; i++) {
      const element = transformedRequiredKeys[i];
      newMapping[required_keysMappingObject[element]] = mapping[element];
    }

    const keysToBeRemoved = [];

    Object.entries(newMapping).forEach(([key, val]) => {
      if (val === "") {
        keysToBeRemoved.push(key);
      }
    });
    for (let i = 0; i < keysToBeRemoved.length; ++i) {
      delete newMapping[keysToBeRemoved[i]];
    }
    return newMapping;
  };

  useEffect(() => {
    setIsDirty(formState.isDirty);
  }, [formState.isDirty, setIsDirty]);

  const { mutate: runEval } = useRunEvalMutation(
    experimentId,
    () => {
      refreshGrid();
      onClose();
    },
    "experiment",
  );

  const onSubmit = (run) => {
    return (data) => {
      const mappingValidationError = validateRequiredColumnMapping({
        requiredColumnIds,
        allColumns,
        mapping: data.config.mapping,
        isGroupEvals: selectedEval?.isGroupEvals,
        groupedRequiredKeys,
        filteredRequiredKeys,
      });
      if (mappingValidationError) {
        setMappingError(mappingValidationError);
        return;
      }
      setMappingError(null);

      // Rename camelCase form keys to snake_case for API
      const { errorLocalizer, kbId, templateId: _tid, ...restData } = data;
      let payload = {
        ...restData,
        run:
          _module === "run-experiment" || _module === "run-optimization"
            ? false
            : run,
        template_id: selectedEval.id,
        error_localizer: errorLocalizer,
        ...(kbId ? { kb_id: kbId } : {}),
      };
      if (payload.kb_id === "") {
        delete payload["kb_id"];
      }
      if (payload.model === "" || !isFutureagiBuilt) {
        delete payload["model"];
      }
      if (payload.config?.mapping) {
        if (!selectedEval?.isGroupEvals) {
          payload.config.mapping = transformMapping(payload.config.mapping);
        }
      }
      if (_module === "task") {
        // if(payload.kb_id) {
        //   taskPayload['kbId'] = payload.kb_id;
        // }
        if (selectedEval?.isGroupEvals) {
          const groupEvalPayload = {
            eval_group_id: selectedEval?.id,
            filters: {
              project_id: id,
              error_localizer: payload?.error_localizer,
              ...(payload?.model ? { model: payload.model } : {}),
              ...(payload?.kb_id ? { kb_id: payload.kb_id } : {}),
            },
            page_id: EVALUATION_PAGE_ID_MAPPER[module],
            mapping: sanitizeEvalMapping(payload.config?.mapping),
            params: payload.config?.params || {},
            ...(removedEvals?.length > 0 && { deselected_evals: removedEvals }),
          };
          handleRun(groupEvalPayload, () => {}, {
            isGrouped: true,
          });
        } else {
          const taskPayload = {
            project: id,
            name: payload.name,
            eval_template: payload.template_id,
            mapping: sanitizeEvalMapping(payload.config?.mapping),
            config: {
              ...(payload.config?.config || {}),
              params: payload.config?.params || {},
            },
            filters: {
              project_id: null,
            },
            error_localizer: payload.error_localizer,
            ...(payload?.model ? { model: payload.model } : {}),
            ...(payload?.kb_id ? { kb_id: payload.kb_id } : {}),
          };
          handleRun(taskPayload);
        }
        setVisibleSection("list");
        return;
      } else if (
        _module === "dataset" ||
        _module === "run-experiment" ||
        _module === "run-optimization" ||
        _module === "create-simulate"
      ) {
        if (selectedEval?.isGroupEvals) {
          payload = {
            eval_group_id: selectedEval?.id,
            filters: {
              error_localizer: payload?.error_localizer,
              ...(payload?.model ? { model: payload.model } : {}),
              ...(payload?.kb_id ? { kb_id: payload.kb_id } : {}),
            },
            page_id: EVALUATION_PAGE_ID_MAPPER[module],
            mapping: payload.config?.mapping,
            params: payload.config?.params || {},
            ...(removedEvals?.length > 0 && { deselected_evals: removedEvals }),
          };
        }
        handleRun(
          payload,
          () => {
            if (run) {
              refreshGrid?.(null, true);
            }
            queryClient.invalidateQueries({
              queryKey: ["develop", "user-eval-list", _id],
            });
            queryClient.invalidateQueries({
              queryKey: ["optimize-develop-column-info"],
            });
            // show evaluation list after success if is group or not run
            if (selectedEval?.isGroupEvals || !run) {
              setVisibleSection("list");
            } else {
              handleClose();
            }
            enqueueSnackbar({
              message: "Evaluation added to dataset",
            });
          },
          { isGrouped: selectedEval?.isGroupEvals },
          setVisibleSection,
        );
      } else if (_module === "experiment") {
        if (selectedEval?.isGroupEvals) {
          payload = {
            eval_group_id: selectedEval?.id,
            filters: {
              error_localizer: payload?.error_localizer,
              ...(payload?.model ? { model: payload.model } : {}),
              ...(payload?.kb_id ? { kb_id: payload.kb_id } : {}),
            },
            page_id: EVALUATION_PAGE_ID_MAPPER[module],
            mapping: payload.config?.mapping,
            params: payload.config?.params || {},
            ...(removedEvals?.length > 0 && { deselected_evals: removedEvals }),
          };
        }
        handleRun(
          payload,
          () => {
            queryClient.invalidateQueries({
              queryKey: ["develop", "user-eval-list", _id],
            });
            queryClient.invalidateQueries({
              queryKey: ["optimize-develop-column-info", _id],
            });
            setVisibleSection("list");
            enqueueSnackbar({
              message: "Evaluation added",
            });
          },
          { isGrouped: selectedEval?.isGroupEvals },
        );
      } else if (_module === "workbench") {
        if (selectedEval?.isGroupEvals) {
          const groupEvalPayload = {
            eval_group_id: selectedEval?.id,
            filters: {
              prompt_template_id: pageId,
              kb_id: payload.kb_id,
              model: payload.model,
              error_localizer: payload.error_localizer,
            },
            mapping: payload.config.mapping,
            params: payload.config?.params || {},
            page_id: EVALUATION_PAGE_ID_MAPPER[module],
            ...(removedEvals?.length > 0 && { deselected_evals: removedEvals }),
          };

          handleRun(
            groupEvalPayload,
            () => {
              setVisibleSection("list");
            },
            { isGrouped: true },
          );
          return;
        }
        handleRun(
          {
            config: {
              params: payload.config?.params || {},
            },
            mapping: payload.config.mapping,
            params: payload.config?.params || {},
            id: payload.template_id,
            name: payload.name,
            kb_id: payload.kb_id,
            model: payload.model,
            error_localizer: payload.error_localizer,
            is_run: run,
            version_to_run: versions,
          },
          () => {
            if (run) {
              handleClose();
              refreshGrid?.();
            } else {
              setVisibleSection("list");
            }
          },
          // () => {
          //   if (run) {
          //     runEval({ versionToRun: versions, evalId: [selectedEval.id] });
          //   } else {
          //     setVisibleSection("list");
          //   }
          // },
        );
      } else if (_module === "dataset-update") {
        delete payload["templateId"];
        updateEval(
          {
            ...payload,
            save_as_template: false,
          },
          {
            onSuccess: () => {
              if (run) {
                refreshGrid?.(null, true);
              }
              queryClient.invalidateQueries({
                queryKey: ["develop", "user-eval-list", _id],
              });
              queryClient.invalidateQueries({
                queryKey: ["optimize-develop-column-info"],
              });
              queryClient.invalidateQueries({
                queryKey: ["develop", "eval-template-config", evalsData?.id],
              });
              handleClose();
              enqueueSnackbar({
                message: "Evaluation edited successfully",
              });
            },
          },
        );
      } else if (_module === "update-experiment") {
        delete payload["templateId"];
        updateEval(
          {
            ...payload,
            save_as_template: false,
          },
          {
            onSuccess: () => {
              if (run) {
                const payload = {
                  eval_template_ids: rest?.templateTags,
                };
                runEval(payload);
              }
              queryClient.invalidateQueries({
                queryKey: ["develop", "user-eval-list", _id],
              });
              queryClient.invalidateQueries({
                queryKey: ["develop", "eval-template-config", evalsData?.id],
              });
              handleClose();
              enqueueSnackbar({
                message: "Evaluation edited successfully",
              });
            },
          },
        );
        // }
      } else if (_module === "simulate-eval-update") {
        delete payload["templateId"];
        updateSimulateEval(
          {
            ...payload,
            mapping: payload?.config?.mapping,
            save_as_template: false,
            test_execution_id: executionId,
          },
          {
            onSuccess: () => {
              if (run) {
                refreshGrid?.();
              }
              handleClose();
              enqueueSnackbar({
                message: "Evaluation edited successfully",
              });
              queryClient.invalidateQueries({
                queryKey: ["simulate", "eval-template-config", evalsData?.id],
              });
            },
          },
        );
      }
    };
  };

  const onTest = () => {
    if (testLabel === "Cancel") {
      onBack();
    } else {
      handleSubmit(handleTest)();
      // trackEvent(Events.datasetAddevalsClicked, {
      //   [PropertyName.type]: "test",
      //   [PropertyName.rowCount]: total_count,
      // });
    }
  };

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

  const filteredColumns = useMemo(() => {
    const isDeterministicEvaluator =
      evalConfig?.evalTypeId == EvalTypeId.DETERMINISTIC;
    const cols = [
      ...allowedColumns,
      ...(isDeterministicEvaluator ? knowledgeBaseOptions : []).map((kb) => ({
        headerName: `KB_${kb.label}`,
        field: kb.value,
        dataType: "text",
      })),
    ].filter((col) => {
      if (isFutureagiBuilt && modelsToShow.length > 0) {
        if (model !== "") {
          let i = 0;
          modelsToShow.forEach((llm, index) => {
            if (llm.value === model) {
              i = index;
            }
          });
          return (
            modelsToShow[i].allowedDataTypes.includes(col.dataType) ||
            _module === "task" ||
            _module === "experiment" ||
            _module === "workbench"
          );
        } else {
          const mapping = getValues("config.mapping");
          Object.keys(mapping).forEach((key) => {
            mapping[key] = "";
          });
          const newMapping = {
            ...mapping,
          };
          setValue("config.mapping", newMapping);
          return false;
        }
      }
      return true;
    });

    return cols;
  }, [
    model,
    isFutureagiBuilt,
    _module,
    allowedColumns,
    getValues,
    modelsToShow,
    setValue,
    evalConfig,
    knowledgeBaseOptions,
  ]);

  // const { mutate: updateEvalList } = useMutation({
  //   mutationFn: async (payload) => {
  //     return axios.post(endpoints.develop.eval.editGroupEvalList, payload);
  //   },
  //   onSuccess: () => {
  //     refetchGroupData();
  //   },
  // });

  const handleEvalDelete = (itemId) => {
    if (!selectedEval?.id || !itemId) {
      return;
    }
    setRemovedEvals((prev) => [...prev, itemId]);
    // const payload = {
    //   deleted_template_ids: [itemId],
    //   eval_group_id: selectedEval?.id,
    // };

    // updateEvalList(payload);
  };

  useEffect(() => {
    if (selectedEval?.isGroupEvals) {
      unregister("name");
    }
  }, [selectedEval?.isGroupEvals, unregister]);

  useEffect(() => {
    const currentModel = getValues("model");

    if (modelsToShow?.length > 0) {
      // Check if current model is valid (exists in modelsToShow)
      const isCurrentModelValid = modelsToShow?.find(
        (m) => m?.value === currentModel,
      );

      if (!currentModel || !isCurrentModelValid) {
        // Prefer turing_large if available, otherwise use first model
        const turingLarge = modelsToShow.find(
          (m) => m?.value === DEFAULT_EVAL_MODEL,
        );
        const modelToSet = turingLarge?.value || modelsToShow[0]?.value;

        if (modelToSet) {
          setValue("model", modelToSet, {
            shouldValidate: true,
            shouldDirty: false,
          });
        }
      }
    } else if (modelsToShow?.length === 0) {
      setValue("model", "", {
        shouldValidate: true,
        shouldDirty: false,
      });
    }
  }, [modelsToShow, setValue, getValues]);

  const contentProps = {
    hideBackButtons,
    isSampleFutureAgiBuilt,
    setCurrentTab,
    setVisibleSection,
    selectedEval,
    onBack,
    hideTitle,
    onClose,
    isEditMode,
    evalsData,
    evalConfig,
    members,
    filteredVisibleItems,
    handleEvalDelete,
    setShowAll,
    showAll,
    control,
    disabledName: rest?.disabledName,
    isViewMode,
    isFutureagiBuilt,
    modelsToShow,
    hideModel,
    modeHelpMessage,
    hideKnowledgeBase,
    knowledgeBaseOptions,
    isEvalConfig,
    filteredRequiredKeys,
    filteredColumns,
    datasetId,
    _module,
    transformedOptionalKeys,
    hideFieldColumns,
    groupedRequiredKeys,
    groupFunctionParamsRequirements:
      getGroupFunctionParamsRequirements(groupData),
    mappingError,
    isPending,
    onFormSave,
    handleSubmit,
    evalConfiguration,
    role,
    saveButtonTitle: rest?.saveButtonTitle,
    showTest,
    onTest,
    isTesting,
    testLabel,
    showAdd,
    setIsDirty,
    onSubmit,
    saveLabel,
    runLabel,
    showDefaultButton,
    saveGroup: rest?.saveGroup,
    removedEvals,
    formState,
  };
  return (
    <form
      style={{
        background: theme.palette.background.paper,
        width: rest.fullWidth ? "100%" : "37.5vw",
        display: "flex",
        flexDirection: "column",
        height: "100%",
        gap: theme.spacing(2),
      }}
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        setIsDirty(false);
        setTimeout(() => {
          handleSubmit(onSubmit(true))();
        }, 10);
      }}
    >
      <EvaluationMappingFormContent {...contentProps} />
    </form>
  );
};

EvaluationMappingFormChild.propTypes = {
  handleTest: PropTypes.func,
  isTesting: PropTypes.bool,
  onClose: PropTypes.func,
  onSubmit: PropTypes.func,
  onBack: PropTypes.func,
  allColumns: PropTypes.array,
  refreshGrid: PropTypes.func,
  onFormSave: PropTypes.func,
  requiredColumnIds: PropTypes.string,
  hideBackButtons: PropTypes.bool,
  evalsData: PropTypes.object,
  hideTitle: PropTypes.bool,
  hideFieldColumns: PropTypes.bool,
  isEvalConfig: PropTypes.bool,
  isViewMode: PropTypes.bool,
  disableSaveButton: PropTypes.bool,
  hideModel: PropTypes.bool,
  hideKnowledgeBase: PropTypes.bool,
  isEditMode: PropTypes.bool,
  existingEvalsProp: PropTypes.array,
  preserveName: PropTypes.bool,
};

const EvaluationMappingForm = ({
  onClose,
  allColumns,
  onBack,
  refreshGrid,
  onFormSave,
  requiredColumnIds,
  saveGroup,
  selectedEvalItem = null,
  preserveName,
  ...rest
}) => {
  const theme = useTheme();
  const [openConfirmDialog, setOpenConfirmDialog] = useState(false);

  const [showTestCollapse, setShowTestCollapse] = useState(false);
  const [action, setAction] = useState("close");
  const {
    setVisibleSection,
    isDirty,
    actionButtonConfig,
    selectedEval,
    module,
  } = useEvaluationContext();
  const _id = rest?.id ?? actionButtonConfig.id;
  const {
    mutate: testEval,
    isPending: testingEvalLoading,
    isSuccess: testingEvalSuccess,
    data: testingEvalData,
  } = useMutation({
    mutationFn: (d) => axios.post(endpoints.develop.eval.testEval(_id), d),
  });

  const onTest = (data) => {
    const payload = {
      ...data,
      run: false,
      template_id: selectedEval.id ?? rest?.evalsData?.templateId,
    };
    testEval(payload);
  };
  useEffect(() => {
    if (testingEvalSuccess && testingEvalData?.data?.result?.responses) {
      setShowTestCollapse(true);
    }
  }, [testingEvalSuccess, testingEvalData]);

  const handleClose = (action) => {
    if (action === "submit") {
      if (module !== "run-experiment" && module !== "run-optimization") {
        // closes the main drawer after evaluation has been added
        onClose();
      }
      setVisibleSection("list");
    } else if (isDirty) {
      setOpenConfirmDialog(true);
    } else {
      if (action === "back") {
        onBack();
      } else {
        onClose();
        setVisibleSection("list");
      }
    }
  };
  return (
    <Box display="flex" flexDirection="row" height="100%" width="100%">
      <Collapse
        in={showTestCollapse}
        orientation="horizontal"
        unmountOnExit
        sx={{ marginRight: theme.spacing(2) }}
      >
        <EvaluationTest
          onClose={() => {
            setShowTestCollapse(false);
          }}
          testingEvalData={testingEvalData}
          testingEvalLoading={testingEvalLoading}
        />
      </Collapse>
      <Box
        display="flex"
        flexDirection="column"
        gap={theme.spacing(2)}
        height="100%"
        flex={1}
      >
        <EvaluationMappingFormChild
          onClose={() => handleClose("close")}
          onBack={() => {
            handleClose("back");
            setAction("back");
          }}
          refreshGrid={refreshGrid}
          onSubmit={() => handleClose("submit")}
          allColumns={allColumns}
          handleTest={onTest}
          isTesting={testingEvalLoading}
          onFormSave={onFormSave}
          requiredColumnIds={requiredColumnIds}
          saveGroup={saveGroup}
          isEditMode={!!selectedEvalItem}
          evalsData={selectedEvalItem}
          preserveName={preserveName}
          {...rest}
        />
        <ConfirmDialog
          open={openConfirmDialog}
          onClose={() => setOpenConfirmDialog(false)}
          title="Confirm Action"
          content="Any progress will be lost. Are you sure you want to leave?"
          action={
            <LoadingButton
              variant="contained"
              size="small"
              color="error"
              sx={{
                paddingX: theme.spacing(3),
              }}
              onClick={() => {
                setOpenConfirmDialog(false);
                if (action === "back") {
                  onBack();
                } else {
                  onClose();
                  setVisibleSection("list");
                }
              }}
            >
              Close
            </LoadingButton>
          }
        />
      </Box>
    </Box>
  );
};

export default EvaluationMappingForm;

EvaluationMappingForm.propTypes = {
  onBack: PropTypes.func,
  id: PropTypes.string,
  onClose: PropTypes.func,
  allColumns: PropTypes.array,
  refreshGrid: PropTypes.func,
  onFormSave: PropTypes.func,
  requiredColumnIds: PropTypes.string,
  disableSaveButton: PropTypes.bool,
  saveGroup: PropTypes.bool,
  selectedEvalItem: PropTypes.object,
  existingEvalsProp: PropTypes.array,
  preserveName: PropTypes.bool,
};
