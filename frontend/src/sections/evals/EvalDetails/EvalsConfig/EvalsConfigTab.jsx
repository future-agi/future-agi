import {
  Box,
  Divider,
  InputAdornment,
  Typography,
  useTheme,
} from "@mui/material";
import React, { useEffect, useMemo, useState } from "react";
import EvalsOutput from "./EvalsOutput";
import { extractVariables } from "src/utils/utils";
import { useNavigate, useParams } from "react-router";
import axios, { endpoints } from "src/utils/axios";
import { FUTUREAGI_LLM_MODELS } from "src/sections/common/EvaluationDrawer/validation";
import { ShowComponent } from "src/components/show";
import CustomEvalsForm from "src/sections/common/EvaluationDrawer/CustomEvalsForm";
import EvaluationMappingForm from "src/sections/common/EvaluationDrawer/EvaluationMappingForm";
import { useEvaluationContext } from "src/sections/common/EvaluationDrawer/context/EvaluationContext";
import PlaygroundInput from "src/components/PlaygroundInput/PlaygroundInput";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LoadingButton } from "@mui/lab";
import { enqueueSnackbar } from "notistack";
import HeadingAndSubHeading from "src/components/HeadingAndSubheading/HeadingAndSubheading";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import { paths } from "src/routes/paths";
import { useKnowledgeBaseList } from "src/api/knowledge-base/files";
import { useForm } from "react-hook-form";
import EvalsConfigLoading from "./EvalsConfigLoading";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { useAuthContext } from "src/auth/hooks";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";

const transformChoices = (choices) => {
  const choicesCopy = {};
  choices?.forEach((choice) => {
    choicesCopy[choice.key] = choice.value;
  });
  return choicesCopy;
};

const transformCriteria = (formValues) => {
  let criteriaCopy = formValues.criteria;
  const extractedKeys = extractVariables(
    criteriaCopy,
    formValues?.template_format || "mustache",
  );
  extractedKeys.forEach((key, index) => {
    const regex = new RegExp(`\\{\\{${key}\\}\\}`, "g");
    criteriaCopy = criteriaCopy.replace(regex, `{{variable_${index + 1}}}`);
  });
  return criteriaCopy;
};

const getCustomEvalPayload = (oldData, newData) => {
  const payload = {
    eval_template_id: oldData?.template_id || "",
  };

  if (oldData?.name != newData.name) {
    payload.name = newData.name || "";
  }
  if (oldData?.description != newData.description) {
    payload.description = newData.description || "";
  }
  if (oldData?.criteria != newData.criteria) {
    payload.criteria = transformCriteria(newData || "");
    payload.required_keys = extractVariables(
      newData.criteria,
      newData?.template_format || "mustache",
    );
  }
  if (oldData?.multi_choice != newData.multiChoice) {
    payload.multi_choice = newData.multiChoice || false;
  }
  if (oldData?.config.model != newData.config.model) {
    payload.model = newData.config.model || "";
  }
  if (JSON.stringify(oldData?.tags) != JSON.stringify(newData.tags)) {
    payload.eval_tags = newData?.tags?.map((tag) => tag.value);
  }
  if (oldData?.check_internet != newData.checkInternet) {
    payload.check_internet = newData.checkInternet || false;
  }
  if (oldData?.error_localizer_enabled != newData.errorLocalizerEnabled) {
    payload.error_localizer_enabled = newData.errorLocalizerEnabled || false;
  }
  if (
    newData.output === "choices" &&
    JSON.stringify(oldData?.choices) != JSON.stringify(newData.choices)
  ) {
    payload.choices_map = transformChoices(newData.choices);
  }

  return payload;
};

const defaultValues = {
  templateType: "Futureagi",
  name: "",
  criteria: "",
  outputType: "Pass/Fail",
  config: {
    model: "",
    reverseOutput: false,
  },
  choices: [],
  multiChoice: false,
  tags: [],
  description: "",
  checkInternet: false,
  errorLocalizerEnabled: false,
};

const transformCriteriaToKey = (data) => {
  let criteriaCopy = data.criteria;
  data.requiredKeys.forEach((key, index) => {
    const regex = new RegExp(`\\{\\{variable_${index + 1}\\}\\}`, "g");
    criteriaCopy = criteriaCopy.replace(regex, `{{${key}}}`);
  });
  return criteriaCopy;
};

const generateConfigData = (data) => {
  if (!data) {
    return defaultValues;
  }
  const templateType = FUTUREAGI_LLM_MODELS.some(
    (item) => item.value === data.model,
  );
  const choicesMap = data?.config?.choices_map || {};
  const functionEval = data.function_eval;
  let defaultValue = {
    ...data,
    templateType: templateType ? "Futureagi" : "Llm",
    name: data.name,
    criteria: templateType ? transformCriteriaToKey(data) : data.criteria || "",
    outputType: data.output,
    config: {
      model: data.model,
      reverseOutput: false,
    },
    choices: Object.entries(choicesMap).map(([key, value]) => ({
      key,
      value,
    })),
    multiChoice: data.multi_choice || false,
    tags: data.eval_tags.map((tag) => ({
      key: tag,
      value: tag,
    })),
    description: data.description,
    checkInternet: data.check_internet || false,
    errorLocalizerEnabled: data.error_localizer_enabled || false,
  };
  if (functionEval) {
    defaultValue = {
      ...defaultValue,
      templateType: "Function",
      config: {
        config: data.config,
      },
      evalTypeId: data.eval_type_id,
    };
  }
  return defaultValue;
};

const modelSchema = (isFutureagiBuilt, modelsToShow, evalConfig) => {
  const requiredKeys = evalConfig?.required_keys || [];
  const optionalKeys = evalConfig?.optional_keys || [];
  const keysRequired = requiredKeys.filter(
    (key) => !optionalKeys.includes(key),
  );

  return z
    .object({
      model: z.string().optional(),
      mapping: z.record(z.string()),
    })
    .superRefine((data, ctx) => {
      const mapping = data.mapping || {};
      keysRequired?.forEach((key) => {
        const value = mapping[key];
        if (!value || value.trim() === "") {
          ctx.addIssue({
            path: ["mapping", key],
            message: `${key} is required`,
            code: z.ZodIssueCode.custom,
          });
        }
      });

      if (
        isFutureagiBuilt &&
        modelsToShow.length > 0 &&
        (!data.model || data.model.trim() === "")
      ) {
        ctx.addIssue({
          path: ["model"],
          message: "Model is required",
          code: z.ZodIssueCode.custom,
        });
      }
    });
};

const EvalsConfigTab = () => {
  const { role } = useAuthContext();
  const { evalId } = useParams();
  const [evalsFormData, setEvalsFormData] = useState({});
  const [results, setResults] = useState(null);
  const theme = useTheme();
  const navigate = useNavigate();

  const {
    setSelectedEval,
    formValues,
    actionButtonConfig: { id },
  } = useEvaluationContext();

  const { data: configData, isPending } = useQuery({
    queryKey: ["evalsConfig", evalId],
    queryFn: () => {
      return axios.get(endpoints.develop.eval.getEvalConfigs, {
        params: { eval_id: evalId },
      });
    },
    enabled: !!evalId,
    select: (data) => {
      const result = data?.data;
      setSelectedEval(result?.result?.eval);
      if (result?.result?.owner === "user") {
        trackEvent(Events.usageConfigPageLoaded, {
          [PropertyName.evalId]: evalId,
          [PropertyName.evalType]: "user_built",
        });
        const generated = generateConfigData(result?.result?.eval);
        return { ...generated, owner: result?.result?.owner };
      }
      trackEvent(Events.usageConfigPageLoaded, {
        [PropertyName.evalId]: evalId,
        [PropertyName.evalType]: "futureagi_built",
      });
      return {
        ...(result?.result?.eval && { ...result?.result?.eval }),
        evalTemplateTags: result?.result?.type?.toUpperCase?.(),
      };
    },
    staleTime: 1000,
  });

  const { data: evalConfig } = useQuery({
    queryKey: ["develop", "eval-template-config", evalId],
    queryFn: () =>
      axios.get(
        endpoints.develop.eval.getPreviouslyConfiguredEvalTemplateConfig(
          /* Here we have to send dataset id if the eval type is not preset but as the eval type
          is preset whatever we send here doesn't matter but should be a uuid
          so we are just sending the evaluation id here */
          evalId,
          evalId,
        ),
        { params: { eval_type: "preset" } },
      ),
    select: (d) => d.data?.result?.eval,
    enabled: !!evalId,
  });

  const { data: knowledgeBaseList } = useKnowledgeBaseList("", null, {
    status: true,
  });
  const knowledgeBaseOptions = useMemo(
    () =>
      (knowledgeBaseList || []).map(({ id, name }) => ({
        label: name,
        value: id,
      })),
    [knowledgeBaseList],
  );

  const isFutureagiBuilt = configData?.eval_template_tags == "FUTUREAGI_BUILT";
  const functionParamsSchema = useMemo(() => {
    return (
      evalConfig?.functionParamsSchema ||
      evalConfig?.function_params_schema ||
      evalConfig?.config?.functionParamsSchema ||
      evalConfig?.config?.function_params_schema ||
      configData?.functionParamsSchema ||
      configData?.function_params_schema ||
      configData?.config?.functionParamsSchema ||
      configData?.config?.function_params_schema ||
      {}
    );
  }, [configData, evalConfig]);
  const functionParamKeys = useMemo(
    () => Object.keys(functionParamsSchema || {}),
    [functionParamsSchema],
  );
  const allowedModels = useMemo(() => evalConfig?.models ?? [], [evalConfig]);

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

  const { control, formState, setValue, getValues, handleSubmit, watch } =
    useForm({
      defaultValues: {
        mapping: {},
        model: "",
        kbId: null,
        config: {
          params: {},
        },
      },
      resolver: zodResolver(
        modelSchema(isFutureagiBuilt, modelsToShow, evalConfig),
      ),
    });

  const { variables, variableConfig } = useMemo(() => {
    const allVariable = configData?.required_keys || [];
    const allVariableConfig = configData?.input_data_types || {};
    const mapping = {};
    allVariable?.forEach((item) => {
      mapping[item] = "";
    });
    setValue("mapping", mapping);
    if (!configData?.input_data_types) {
      allVariable?.forEach((item) => {
        allVariableConfig[item] = "text";
      });
    }
    return { variables: allVariable, variableConfig: allVariableConfig };
  }, [configData]);

  useEffect(() => {
    if (!functionParamKeys.length) return;

    const currentParams = getValues("config.params") || {};
    if (Object.keys(currentParams).length > 0) return;

    const defaults = {};
    const existingParams =
      evalConfig?.params ||
      evalConfig?.config?.params ||
      configData?.params ||
      configData?.config?.params ||
      {};
    functionParamKeys.forEach((key) => {
      const schema = functionParamsSchema[key] || {};
      if (Object.prototype.hasOwnProperty.call(existingParams, key)) {
        defaults[key] = existingParams[key];
      } else if (Object.prototype.hasOwnProperty.call(schema, "default")) {
        defaults[key] = schema.default;
      }
    });

    if (Object.keys(defaults).length > 0) {
      setValue("config.params", defaults, {
        shouldDirty: false,
        shouldValidate: false,
      });
    }
  }, [
    configData,
    evalConfig,
    functionParamKeys,
    functionParamsSchema,
    getValues,
    setValue,
  ]);

  const handleEvaluate = () => {
    if (!RolePermission.EVALS[PERMISSIONS.EVALUATE][role]) {
      return;
    }
    const { mapping, model, kbId } = getValues();
    const config = formValues["config"] || {};
    const modelValue = model || config?.model || "";

    const latestParams = getValues("config.params") || {};

    const payload = {
      error_localizer:
        formValues.errorLocalizer ??
        formValues.errorLocalizerEnabled ??
        configData?.error_localizer_enabled ??
        false,
      mapping: mapping,
      config: {
        params: latestParams,
      },
      kb_id: configData?.owner !== "user" ? kbId : formValues.kbId || undefined,
      ...(modelValue && { model: modelValue }),
      template_id: evalId,
    };

    evaluateMutate(payload);
  };

  const { mutate: evaluateMutate, isPending: isEvaluating } = useMutation({
    /**
     *
     * @param {Object} data
     * @returns
     */
    mutationFn: (data) =>
      axios.post(endpoints.develop.eval.evalPlayground, data),
    onSuccess: (data, variable) => {
      const { result } = data?.data || {};
      setResults({ output: result.output, reason: result.reason });
      trackEvent(Events.usageConfigEvaluateClicked, {
        [PropertyName.evalId]: variable?.templateId,
        [PropertyName.evalType]:
          configData?.owner === "user" ? "user_built" : "futureagi_built",
        [PropertyName.errorLocalizerState]: variable?.errorLocalizer,
        [PropertyName.model]: variable?.model,
      });
    },
  });
  const queryClient = useQueryClient();
  const { mutate: saveCustomEvalMutate, isPending: isCustomEvalSaving } =
    useMutation({
      /**
       *
       * @param {Object} data
       * @returns
       */
      mutationFn: (data) =>
        axios.post(endpoints.develop.eval.updateEvalsTemplate, data),
      onSuccess: (data) => {
        data?.data?.result &&
          enqueueSnackbar(data.data.result, { variant: "success" });
        queryClient.invalidateQueries({
          queryKey: ["evalsConfig", evalId],
        });
        trackEvent(Events.usageSaveConfigClicked, {
          [PropertyName.click]: true,
        });
      },
    });

  useMemo(() => {
    if (
      formValues &&
      JSON.stringify(formValues) !== JSON.stringify(evalsFormData)
    ) {
      setEvalsFormData(formValues);
    }
  }, [formValues]);

  const saveCustomEvals = () => {
    if (!RolePermission.EVALS[PERMISSIONS.UPDATE][role]) {
      return;
    }
    const payload = getCustomEvalPayload(configData, formValues);
    if (Object.keys(payload).length > 1) saveCustomEvalMutate(payload);
  };

  const keysRequired = useMemo(() => {
    const requiredKeys = configData?.required_keys || [];
    const optionalKeys = configData?.optional_keys || [];
    return requiredKeys.filter((key) => !optionalKeys.includes(key));
  }, [configData]);

  if (isPending) {
    return <EvalsConfigLoading />;
  }

  return (
    <Box sx={{ paddingTop: 1, height: "100%" }}>
      <Box
        sx={{
          paddingTop: 1,
          width: "100%",
          display: "flex",
          height: "100%",
          gap: "16px",
          overflow: "hidden",
        }}
      >
        {/* Left Section */}
        <Box
          sx={{
            width: "100%",
            display: "flex",
            flexDirection: "column",
            gap: "16px",
            overflowY: "auto",
          }}
        >
          <Box sx={{ display: "flex", flexDirection: "column", gap: "4px" }}>
            <Typography
              typography="s1"
              fontWeight={"fontWeightMedium"}
              color="text.primary"
            >
              Playground
            </Typography>
            <Typography
              typography="s1"
              fontWeight={"fontWeightRegular"}
              color="text.primary"
            >
              Test your evaluations on our playground
            </Typography>
          </Box>
          <ShowComponent
            condition={isFutureagiBuilt && modelsToShow.length > 0}
          >
            <HeadingAndSubHeading
              heading={
                <FormSearchSelectFieldControl
                  control={control}
                  options={modelsToShow.map((model) => {
                    return {
                      ...model,
                      component: (
                        <Box>
                          <Box
                            display={"flex"}
                            flexDirection={"row"}
                            alignItems={"center"}
                            gap={"8px"}
                          >
                            <img
                              src={"/favicon/logo.svg"}
                              style={{
                                height: theme.spacing(2),
                                width: theme.spacing(2),
                              }}
                            />
                            <Typography
                              typography="s1"
                              fontWeight={"fontWeightMedium"}
                              color={"text.primary"}
                            >
                              {model.label}
                            </Typography>
                          </Box>
                          <Typography
                            typography={"s2"}
                            sx={{
                              marginLeft: theme.spacing(3),
                              wordWrap: "break-word",
                              whiteSpace: "normal",
                            }}
                            color={"text.primary"}
                          >
                            {model.description}
                          </Typography>
                        </Box>
                      ),
                    };
                  })}
                  InputProps={{
                    startAdornment: (
                      <InputAdornment position="start">
                        <img
                          src={"/favicon/logo.svg"}
                          style={{
                            height: theme.spacing(2),
                            width: theme.spacing(2),
                          }}
                        />
                      </InputAdornment>
                    ),
                  }}
                  fieldName={"model"}
                  label={"Language Model"}
                  size={"small"}
                  fullWidth
                  required
                />
              }
              subHeading="The model to use for evaluation"
            />
          </ShowComponent>
          <ShowComponent condition={configData?.owner !== "user"}>
            <HeadingAndSubHeading
              heading={
                <FormSearchSelectFieldControl
                  disabled={false}
                  label="Knowledge base"
                  placeholder="Choose knowledge base"
                  size="small"
                  control={control}
                  fieldName={`kbId`}
                  fullWidth
                  createLabel="Create knowledge base"
                  handleCreateLabel={() =>
                    navigate(paths.dashboard.knowledge_base)
                  }
                  options={knowledgeBaseOptions}
                  emptyMessage={"No knowledge base has been added"}
                />
              }
              subHeading="Allows the LLM to leverage domain-specific or specialized information when evaluating"
            />
          </ShowComponent>
          {variables?.map((item, index) => {
            const model = watch("model");
            const config = formValues["config"] || {};
            const modelValue = model || config?.model || "";

            return (
              <PlaygroundInput
                key={index}
                showTabs={true}
                fieldTitle={item}
                setValue={setValue}
                control={control}
                typeFieldName={`inputDataTypes.${item}`}
                valueFieldName={`mapping.${item}`}
                inputType={variableConfig[item] || "text"}
                errorMessage={formState.errors?.mapping?.[item]?.message}
                required={keysRequired?.includes(item)}
                hideAudio={modelValue !== "turing_large"}
              />
            );
          })}
          <ShowComponent condition={functionParamKeys.length > 0}>
            <Box
              display={"flex"}
              py={theme.spacing(2)}
              px={theme.spacing(1.5)}
              border={`1px solid`}
              borderColor={"divider"}
              borderRadius={theme.spacing(0.5)}
              flexDirection={"column"}
              gap={theme.spacing(1.5)}
            >
              <HeadingAndSubHeading
                heading="Function Parameters"
                subHeading="Set metric-specific parameters"
              />
              {functionParamKeys.map((key) => {
                const schema = functionParamsSchema[key] || {};
                return (
                  <FormTextFieldV2
                    key={key}
                    control={control}
                    fieldName={`config.params.${key}`}
                    fieldType={schema?.type === "integer" ? "number" : "text"}
                    label={key}
                    placeholder={`Enter ${key}`}
                    size="small"
                    required={Boolean(schema?.required)}
                    helperText={
                      evalConfig?.configParamsDesc?.[key] ||
                      evalConfig?.config_params_desc?.[key] ||
                      evalConfig?.config?.configParamsDesc?.[key] ||
                      evalConfig?.config?.config_params_desc?.[key]
                    }
                  />
                );
              })}
            </Box>
          </ShowComponent>
          <Box display={"flex"} justifyContent={"flex-end"}>
            <LoadingButton
              variant="contained"
              color="primary"
              size="small"
              type="button"
              onClick={handleSubmit(handleEvaluate)}
              loading={isEvaluating}
            >
              Evaluate
            </LoadingButton>
          </Box>
          <Divider orientation="horizontal" />
          <EvalsOutput results={results} />
        </Box>

        {/* Divider */}
        <Divider orientation="vertical" />

        {/* Right Section */}
        <Box
          sx={{
            width: "100%",
            display: "flex",
            flexDirection: "column",
            gap: "16px",
          }}
        >
          <Box sx={{ overflowY: "auto" }}>
            <ShowComponent condition={configData?.owner === "user"}>
              <CustomEvalsForm
                onClose={() => {}}
                showTest={false}
                evalsData={configData}
                onFormSave={saveCustomEvals}
                saveButtonTitle="Save Changes"
                hideTitle
                hideBackButtons
                disableOutputType
                hideModelType
                disabledName
                fullWidth
                formHeight="100%"
                defaultCriteria={configData?.criteria}
                isEvalConfig
                loadingSaveButton={isCustomEvalSaving}
                titleComponent={
                  <Box
                    sx={{
                      display: "flex",
                      gap: "4px",
                      marginBottom: 2,
                      justifyContent: "space-between",
                    }}
                  >
                    <Box
                      sx={{
                        display: "flex",
                        flexDirection: "column",
                        gap: "4px",
                      }}
                    >
                      <Typography
                        typography="s1"
                        fontWeight={"fontWeightMedium"}
                        color="text.primary"
                      >
                        Configure
                      </Typography>
                      <Typography
                        typography="s1"
                        fontWeight={"fontWeightRegular"}
                        color="text.primary"
                      >
                        Configure your own evaluation using custom metrics and
                        test cases.
                      </Typography>
                    </Box>
                  </Box>
                }
              />
            </ShowComponent>
            <ShowComponent
              condition={configData?.owner !== "user" && configData}
            >
              <Box sx={{ paddingTop: "5px" }}>
                <EvaluationMappingForm
                  id={id}
                  onClose={() => {}}
                  allColumns={[]}
                  refreshGrid={() => {}}
                  onBack={() => {}}
                  evalsData={configData}
                  onFormSave={() => {}}
                  saveButtonTitle="Save Changes"
                  hideBackButtons
                  hideTitle
                  hideFieldColumns
                  disabledName
                  fullWidth
                  isEvalConfig
                  disableSaveButton
                  hideModel
                  hideKnowledgeBase
                />
              </Box>
            </ShowComponent>
          </Box>
        </Box>
      </Box>
    </Box>
  );
};

export default EvalsConfigTab;
