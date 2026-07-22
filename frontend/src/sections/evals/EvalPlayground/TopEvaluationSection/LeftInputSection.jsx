import { Box, InputAdornment, Typography, useTheme } from "@mui/material";
import React, { useEffect, useMemo } from "react";
import FormSearchSelectFieldControl from "src/components/FromSearchSelectField/FormSearchSelectFieldControl";
import HeadingAndSubHeading from "src/components/HeadingAndSubheading/HeadingAndSubheading";
import { useKnowledgeBaseList } from "src/api/knowledge-base/files";
import { useForm } from "react-hook-form";
import { useNavigate } from "react-router";
import { paths } from "src/routes/paths";
import { ShowComponent } from "src/components/show";
import { FUTUREAGI_LLM_MODELS } from "../../../common/EvaluationDrawer/validation";
import { useMutation, useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import PropTypes from "prop-types";
import PlaygroundInput from "src/components/PlaygroundInput/PlaygroundInput";
import { FormCheckboxField } from "src/components/FormCheckboxField";
import { LoadingButton } from "@mui/lab";
import { extractVariables } from "src/utils/utils";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { useAuthContext } from "src/auth/hooks";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { enqueueSnackbar } from "notistack";
import { useCreditExhaustion } from "src/hooks/use-credit-exhaustion";
import { CreditExhaustionBanner } from "src/components/CreditExhaustionBanner";
import { useExecuteCompositeEval } from "../../hooks/useCompositeEval";

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
};

const generateConfigData = (data) => {
  if (!data) {
    return defaultValues;
  }
  const templateType = FUTUREAGI_LLM_MODELS.some(
    (item) => item.value === data.model,
  );
  const transformCriteria = (data) => {
    let criteriaCopy = data.criteria;
    data?.requiredKeys?.forEach((key, index) => {
      const regex = new RegExp(`\\{\\{variable_${index + 1}\\}\\}`, "g");
      criteriaCopy = criteriaCopy.replace(regex, `{{${key}}}`);
    });
    return criteriaCopy;
  };
  const choicesMap = data?.config?.choicesMap || {};
  const defaultValue = {
    ...data,
    templateType: templateType ? "Futureagi" : "Llm",
    name: data.name,
    criteria: templateType ? transformCriteria(data) : data.criteria || "",
    outputType: data.output,
    config: {
      model: data.model,
      reverseOutput: false,
    },
    choices: Object.entries(choicesMap).map(([key, value]) => ({
      key,
      value,
    })),
    multiChoice: data.multiChoice || false,
    tags: data.evalTags.map((tag) => ({
      key: tag,
      value: tag,
    })),
    description: data.description,
    checkInternet: data.checkInternet || false,
  };
  return defaultValue;
};

const getDefaultValue = (evaluation) => {
  const inputDataTypes = {};
  const mapping = {};
  evaluation?.evalRequiredKeys?.forEach((item) => {
    inputDataTypes[item] = "text";
    mapping[item] = "";
  });
  return {
    ...evaluation,
    errorLocalizer: false,
    inputDataTypes,
    mapping,
    config: {
      params: {},
    },
  };
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
        modelsToShow?.length > 0 &&
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

const LeftInputSection = ({
  evaluation,
  setResults,
  refreshGrid,
  setSelectedData,
}) => {
  const navigate = useNavigate();
  const { role } = useAuthContext();
  const theme = useTheme();
  const evalId = evaluation?.id;
  const {
    exhaustionError,
    handleError: handleCreditError,
    handleUpgradeClick,
    handleDismiss,
  } = useCreditExhaustion({ feature: "evals" });

  const { data: evalConfig } = useQuery({
    queryKey: ["develop", "eval-template-config", evaluation?.id],
    queryFn: () =>
      axios.get(
        endpoints.develop.eval.getPreviouslyConfiguredEvalTemplateConfig(
          /* Here we have to send dataset id if the eval type is not preset but as the eval type
          is preset whatever we send here doesn't matter but should be a uuid
          so we are just sending the evaluation id here */
          evaluation?.id,
          evaluation?.id,
        ),
        { params: { eval_type: "preset" } },
      ),
    select: (d) => d.data?.result?.eval,
    enabled: !!evaluation?.id,
  });

  const { data: configData } = useQuery({
    queryKey: ["evalsConfig", evalId],
    queryFn: () => {
      return axios.get(endpoints.develop.eval.getEvalConfigs, {
        params: { eval_id: evaluation?.id },
      });
    },
    enabled: !!evaluation?.id,
    select: (data) => {
      const result = data?.data;
      if (result?.result?.owner === "user") {
        const generated = generateConfigData(result?.result?.eval);
        return { ...generated, owner: result?.result?.owner };
      }
      return result?.result?.eval;
    },
    staleTime: 1000 * 10,
  });

  const isFutureagiBuilt =
    evaluation?.eval_template_tags?.includes("FUTUREAGI_BUILT");
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
      evaluation?.functionParamsSchema ||
      evaluation?.function_params_schema ||
      {}
    );
  }, [configData, evalConfig, evaluation]);
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

  const {
    control,
    formState,
    watch,
    setError,
    setValue,
    getValues,
    handleSubmit,
  } = useForm({
    defaultValues: getDefaultValue(evaluation),
    resolver: zodResolver(
      modelSchema(isFutureagiBuilt, modelsToShow, configData),
    ),
  });
  const formData = watch();

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
    evalConfig,
    functionParamKeys,
    functionParamsSchema,
    getValues,
    setValue,
  ]);

  useEffect(() => {
    if (Object.keys(formData.mapping).length > 0) {
      setSelectedData((pre) => ({
        ...pre,
        mapping: JSON.stringify(formData.mapping),
      }));
    } else if (evaluation?.evalRequiredKeys?.length > 0) {
      const obj = {};
      evaluation?.evalRequiredKey?.forEach((item) => {
        obj[item] = "";
      });
      setSelectedData((pre) => ({
        ...pre,
        mapping: JSON.stringify(obj),
      }));
    }
  }, [
    evaluation?.evalRequiredKey,
    evaluation?.evalRequiredKeys?.length,
    formData.mapping,
    setSelectedData,
  ]);

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

  const transformCriteria = () => {
    let criteriaCopy = evaluation.criteria;
    const extractedKeys = extractVariables(
      criteriaCopy,
      evaluation?.template_format || "mustache",
    );
    extractedKeys?.forEach((key, index) => {
      const regex = new RegExp(`\\{\\{${key}\\}\\}`, "g");
      criteriaCopy = criteriaCopy.replace(regex, `{{variable_${index + 1}}}`);
    });
    return criteriaCopy;
  };

  const isComposite = evaluation?.template_type === "composite";

  const handleEvaluate = async () => {
    if (!RolePermission.EVALS[PERMISSIONS.EVALUATE][role]) {
      return;
    }
    if (isFutureagiBuilt && modelsToShow.length > 0 && !formData.model) {
      setError("model", { message: "model is required" });
      return;
    }

    // Composite evals go through a dedicated endpoint that runs every child
    // and optionally aggregates the results.
    if (isComposite) {
      const compositePayload = {
        mapping: formData.mapping || {},
        model: formData.model || configData.model || undefined,
        config: {
          params: formData?.config?.params || formData?.params || {},
        },
        error_localizer: formData.errorLocalizer || false,
        input_data_types: formData?.inputDataTypes || {},
      };
      try {
        const compositeResult = await executeComposite.mutateAsync({
          templateId: evalId,
          payload: compositePayload,
        });
        setResults({
          output:
            compositeResult?.aggregation_enabled &&
            compositeResult?.aggregate_score != null
              ? compositeResult.aggregate_score
              : null,
          reason: compositeResult?.summary || "",
          compositeResult,
        });
        refreshGrid();
      } catch (error) {
        if (!handleCreditError(error)) {
          enqueueSnackbar(
            error?.result ||
              error?.detail ||
              error?.message ||
              "Failed to run composite evaluation",
            { variant: "error" },
          );
        }
      }
      return;
    }

    // const { inputDataTypes, mapping } = getInputs(value, variables);
    const userPayload = {};
    if (configData.owner === "user") {
      const transformedCritera =
        formData.templateType === "Futureagi"
          ? transformCriteria()
          : formData.criteria;
      userPayload["kbId"] = formData.kbId || undefined;
      userPayload["evalTags"] = formData.evalTemplateTags || [];
      userPayload["tags"] = formData.evalTemplateTags || [];
      userPayload["name"] = formData?.name || "";
      userPayload["criteria"] = transformedCritera || "";
      userPayload["multi_choice"] = formData?.multiChoice;
    } else {
      userPayload["required_keys"] = formData.evalRequiredKeys;
      userPayload["template_name"] = formData.evalTemplateName;
      userPayload["name"] = formData?.name || "";
      userPayload["kb_id"] = formData.kbId || undefined;
      userPayload["output_type"] = formData.output || evalConfig.output;
      userPayload["error_localizer"] = formData.errorLocalizer || false;
    }
    const payload = {
      template_id: evalId,
      model: formData.model || configData.model,
      kb_id: formData.kbId || undefined,
      mapping: formData.mapping || {},
      config: {
        params: formData?.config?.params || formData?.params || {},
      },
      error_localizer: formData.errorLocalizer || false,
      input_data_types: formData?.inputDataTypes,
    };

    evaluateMutate(payload);
  };

  const { mutate: evaluateMutate, isPending: isEvaluating } = useMutation({
    mutationFn: (data) =>
      axios.post(endpoints.develop.eval.evalPlayground, data),
    onSuccess: (data, variable) => {
      const { result } = data?.data || {};
      setResults({ output: result.output, reason: result.reason });
      refreshGrid();
      trackEvent(Events.evalsPlaygroundEvaluateClicked, {
        [PropertyName.evalId]: variable?.templateId,
        [PropertyName.evalType]:
          configData?.owner === "user" ? "user_built" : "futureagi_built",
        [PropertyName.errorLocalizerState]: variable?.errorLocalizer,
        [PropertyName.model]: variable?.model,
      });
    },
    onError: (error) => {
      if (!handleCreditError(error)) {
        enqueueSnackbar(
          error?.result ||
            error?.detail ||
            error?.message ||
            "Failed to run evaluation",
          { variant: "error" },
        );
      }
    },
  });

  const executeComposite = useExecuteCompositeEval();

  return (
    <Box
      sx={{
        flex: 1,
        paddingTop: 1,
        display: "flex",
        flexDirection: "column",
        gap: 2,
        overflowY: "auto",
        height: "100%",
      }}
    >
      <ShowComponent condition={isFutureagiBuilt && modelsToShow.length > 0}>
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
              // error={formState.errors?.model && formState.errors.model.message}
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
            handleCreateLabel={() => navigate(paths.dashboard.knowledge_base)}
            options={knowledgeBaseOptions}
            emptyMessage={"No knowledge base has been added"}
          />
        }
        subHeading="Allows the LLM to leverage domain-specific or specialized information when evaluating"
      />
      {evaluation?.evalRequiredKeys?.map((item, index) => {
        const inputDataTypes = getValues("inputDataTypes");
        const requiredKeys = evalConfig?.required_keys || [];
        const optionalKeys = evalConfig?.optional_keys || [];
        const keysRequired = requiredKeys.filter(
          (key) => !optionalKeys.includes(key),
        );
        return (
          <PlaygroundInput
            key={index}
            showTabs={true}
            fieldTitle={item}
            setValue={setValue}
            control={control}
            typeFieldName={`inputDataTypes.${item}`}
            valueFieldName={`mapping.${item}`}
            inputType={inputDataTypes?.[item] || "text"}
            errorMessage={formState.errors?.mapping?.[item]?.message}
            required={keysRequired.includes(item)}
            hideAudio={
              isFutureagiBuilt &&
              modelsToShow.length > 0 &&
              formData?.model !== "turing_large"
            }
          />
        );
      })}

      <ShowComponent condition={functionParamKeys.length > 0}>
        <Box
          display={"flex"}
          py={theme.spacing(2)}
          px={theme.spacing(1.5)}
          border={`1px solid`}
          borderColor={"black.50"}
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
      <Box
        display={"flex"}
        alignItems={"center"}
        justifyContent={"space-between"}
        position="sticky"
        bottom={0}
        sx={{ backgroundColor: theme.palette.background.paper }}
        gap={theme.spacing(1.5)}
        // borderRadius={theme.spacing(0.5)}
        paddingTop={0.5}
      >
        <HeadingAndSubHeading
          heading={
            <FormCheckboxField
              control={control}
              fieldName={"errorLocalizer"}
              label={"Error Localization"}
              helperText={undefined}
              labelPlacement="end"
              defaultValue={formState?.defaultValues?.errorLocalizer}
              labelProps={{
                gap: theme.spacing(1),
              }}
              checkboxSx={{
                padding: 0,
                "&.Mui-checked": {
                  color: "primary.light",
                },
              }}
            />
          }
          subHeading="Pinpoints the errors in your LLM output"
        />
        <CreditExhaustionBanner
          error={exhaustionError}
          onUpgrade={handleUpgradeClick}
          onDismiss={handleDismiss}
        />
        <LoadingButton
          variant="contained"
          color="primary"
          size="small"
          type="button"
          onClick={handleSubmit(handleEvaluate)}
          loading={isEvaluating || executeComposite.isPending}
          // disabled={disableEvaluate}
        >
          Evaluate
        </LoadingButton>
      </Box>
    </Box>
  );
};

LeftInputSection.propTypes = {
  evaluation: PropTypes.object,
  setResults: PropTypes.func,
  refreshGrid: PropTypes.func,
  setSelectedData: PropTypes.func,
};

export default LeftInputSection;
