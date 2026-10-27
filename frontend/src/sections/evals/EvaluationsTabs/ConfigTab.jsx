import React, { useEffect, useMemo, useState } from "react";
import { Box, FormHelperText, LinearProgress, Typography } from "@mui/material";
import { useParams } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios from "axios";
import { endpoints } from "src/utils/axios";
import { HOST_API } from "src/config-global";
import { useForm } from "react-hook-form";
import {
  camelCaseToTitleCase,
  canonicalEntries,
  canonicalKeys,
  canonicalValues,
  getRandomId,
  interpolateColorBasedOnScore,
} from "src/utils/utils";
import {
  allowedColumnFilter,
  keyWiseAllowedColumnFilter,
} from "src/sections/develop-detail/Common/EvaluationConfigure/common";
import { zodResolver } from "@hookform/resolvers/zod";
import { LoadingButton } from "@mui/lab";
import EvalAccordion from "../EvalsAccordions/EvalAccordion";
import HelperText from "src/sections/develop-detail/Common/HelperText";
import { enqueueSnackbar } from "notistack";
import { ShowComponent } from "src/components/show";
import RulePromptInput from "src/sections/develop-detail/Common/EvaluationConfigure/RulePromptInput";
import ChoicesInput from "src/components/ChoiceInput/ChoicesInput";
import { RuleStringInputVariable } from "src/sections/develop-detail/Common/EvaluationConfigure/RuleStringInput";
import DictInput from "src/sections/develop-detail/Common/EvaluationConfigure/DictInput";
import PromptInput from "src/sections/develop-detail/Common/EvaluationConfigure/PromptInput";
import CodeInput from "src/sections/develop-detail/Common/EvaluationConfigure/CodeInput";
import NumberInput from "src/sections/develop-detail/Common/EvaluationConfigure/NumberInput";
import OptionInput from "src/sections/develop-detail/Common/EvaluationConfigure/OptionInput";
import BooleanInput from "src/sections/develop-detail/Common/EvaluationConfigure/BooleanInput";
import ListInput from "src/sections/develop-detail/Common/EvaluationConfigure/ListInput";
import StringInput from "src/sections/develop-detail/Common/EvaluationConfigure/StringInput";
import ModelSelectInput from "src/sections/develop-detail/Common/EvaluationConfigure/ModelSelectInput";
import FieldSelection from "../Helpers/FieldSelection";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
} from "src/sections/develop-detail/AccordianElements";
import { z } from "zod";
import { encloseString } from "src/sections/develop-detail/Common/EvaluationConfigure/validation";
import { HtmlPromptValidationSchema } from "src/utils/validation";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";

const generateValidationSchema = (
  evalConfig,
  requiredColumnIds = [],
  allColumns,
  options = { rulePromptStartEnclosures: "{{", rulePromptEndEnclosures: "}}" },
) => {
  const mappingSchema = {};
  const configSchema = {};

  const optionalKeys = evalConfig?.optional_keys || [];
  const variableKeys = evalConfig?.variable_keys || [];

  // Add validation for required keys
  for (const key of evalConfig?.required_keys || []) {
    const isMultipleColumn = variableKeys.includes(key);
    mappingSchema[key] = isMultipleColumn ? z.array(z.string()) : z.string();
    if (optionalKeys.includes(key)) {
      mappingSchema[key] = mappingSchema[key].transform((val) =>
        val.length ? val : null,
      );
    } else {
      mappingSchema[key] = mappingSchema[key].min(1, `Required`);
    }
  }

  let isRulePrompt = false;

  // Add validation for config fields. canonicalEntries drops legacy
  // camelCase aliases beside snake_case parameter names, otherwise every
  // config field would be validated twice under two different keys.
  for (const [key, field] of canonicalEntries(evalConfig?.config || {})) {
    switch (field.type) {
      case "string":
        configSchema[key] = z
          .string({
            invalid_type_error: `${camelCaseToTitleCase(key)} is required.`,
          })
          .min(1, `${camelCaseToTitleCase(key)} is required.`);
        break;
      case "integer":
        configSchema[key] = z
          .string()
          .min(1, `${camelCaseToTitleCase(key)} is required.`)
          .transform((val) => Number.parseInt(val))
          .pipe(
            z
              .number({
                invalid_type_error: `${camelCaseToTitleCase(key)} is required.`,
              })
              .int(),
          );
        break;
      case "float":
        configSchema[key] = z
          .string()
          .min(1, `${camelCaseToTitleCase(key)} is required.`)
          .transform((val) => Number.parseFloat(val))
          .pipe(
            z.number({
              invalid_type_error: `${camelCaseToTitleCase(key)} is required.`,
            }),
          );
        break;
      case "boolean":
        configSchema[key] = z.boolean();
        break;
      case "option":
        configSchema[key] = z
          .string({
            invalid_type_error: `${camelCaseToTitleCase(key)} is required.`,
          })
          .min(1, `${camelCaseToTitleCase(key)} is required.`);
        break;
      case "dict":
        configSchema[key] = z.union([
          z
            .array(
              z.object({
                key: z.string().min(1, "Key is required"),
                value: z.string().min(1, "Value is required"),
              }),
            )
            .transform((arr) => {
              return arr.reduce(
                (acc, { key, value }) => ({
                  ...acc,
                  [key]: value,
                }),
                {},
              );
            }),
          z.record(z.string(), z.string()),
        ]);
        break;
      case "prompt":
        configSchema[key] = HtmlPromptValidationSchema.refine(
          (str) => str.length > 1,
          "Prompt is required",
        ).transform((str) => {
          if (str) {
            return allColumns?.reduce((acc, col) => {
              return acc.replace(
                new RegExp(`{{${col.headerName}}}`, "g"),
                `{{${col.field}}}`,
              );
            }, str);
          } else {
            return str;
          }
        });

        break;
      case "list":
        configSchema[key] = z
          .string()
          .min(1, `${camelCaseToTitleCase(key)} is required.`)
          .transform((val) => val.split(",").map((item) => item.trim()))
          .pipe(
            z
              .array(z.string())
              .min(
                1,
                `${camelCaseToTitleCase(key)} must have at least one item`,
              ),
          );
        break;
      case "code":
        configSchema[key] = z
          .string({ invalid_type_error: "Code is required" })
          .min(1, "Code is required")
          .transform((val) => {
            return allColumns?.reduce((acc, col) => {
              const newVal = `{{${col.field}}}`;
              // if (
              //   !col?.dataType ||
              //   col?.dataType === "boolean" ||
              //   col?.dataType === "float" ||
              //   col?.dataType === "integer"
              // ) {
              //   newVal = `{{${col.field}}}`;
              // } else {
              //   newVal = `'{{${col.field}}}'`;
              // }
              return acc.replace(
                new RegExp(`{{${col.headerName}}}`, "g"),
                newVal,
              );
            }, val);
          });
        break;
      case "rule_string":
        configSchema[key] = z
          .array(
            z.object({
              id: z.string(),
              value: z.string().min(1, "Required"),
            }),
          )
          .min(1, "Inputs is required")
          .transform((arr) =>
            arr.map((item) => encloseString(item.value, options)),
          )
          .superRefine((mapping, ctx) => {
            requiredColumnIds.forEach((columnId) => {
              if (!mapping.includes(`{{${columnId}}}`)) {
                ctx.addIssue({
                  code: z.ZodIssueCode.custom,
                  message: `Fill the fileds`,
                  path: [],
                });
              }
            });
          });
        break;
      case "choices":
        configSchema[key] = z
          .array(
            z.object({
              id: z.string(),
              value: z.string(),
            }),
          )
          .min(1, "Choices are required")
          .transform((arr) => arr.map((item) => item.value));
        break;
      case "rule_prompt": {
        configSchema[key] = HtmlPromptValidationSchema.refine(
          (str) => str.length > 1,
          "Rule Prompt is required",
        ).transform((str) => {
          return allColumns?.reduce((acc, col) => {
            return acc.replace(
              new RegExp(`{{${col.headerName}}}`, "g"),
              `{{${col.field}}}`,
            );
          }, str);
        });
        isRulePrompt = true;
        break;
      }
      default:
        configSchema[key] = z.any();
    }
  }

  let config = z.object(configSchema).superRefine((mapping, ctx) => {
    const keys = Object.keys(mapping);
    if (
      keys.length > 0 &&
      keys.some((key) => key === "evalPrompt" || key === "systemPrompt") &&
      requiredColumnIds.length > 0
    ) {
      let matched = false;
      Object.entries(mapping).forEach(([key, value]) => {
        if (key === "evalPrompt" || key === "systemPrompt") {
          const newVal = allColumns?.reduce((acc, col) => {
            return acc.replace(
              new RegExp(`{{${col.headerName}}}`, "g"),
              `{{${col.field}}}`,
            );
          }, value);
          requiredColumnIds.forEach((columnId) => {
            if (newVal.includes(columnId)) {
              matched = true;
            }
          });
        }
      });
      if (!matched && keys.length > 0) {
        Object.keys(mapping).forEach((key) => {
          if (key === "evalPrompt" || key === "systemPrompt") {
            ctx.addIssue({
              code: z.ZodIssueCode.custom,
              message: `The config column must be one of chosen values`,
              path: [key],
            });
          }
        });
      }
    }
  });

  if (isRulePrompt) {
    const ruleStringConfig = canonicalEntries(evalConfig?.config).find(
      ([_, fc]) => {
        return fc.type === "rule_string";
      },
    );
    const rulePromptConfig = canonicalEntries(evalConfig?.config).find(
      ([_, fc]) => {
        return fc.type === "rule_prompt";
      },
    );
    config = config
      .superRefine((obj, ctx) => {
        const rulePromptKey = rulePromptConfig?.[0];
        const ruleStringArray = obj[ruleStringConfig?.[0]];
        const rulePromptString = obj[rulePromptKey];
        const rulePromptVariables = ruleStringArray.map(
          (_, index) => `variable_${index + 1}`,
        );

        rulePromptVariables?.forEach((ruleId) => {
          if (!rulePromptString?.includes(ruleId)) {
            ctx.addIssue({
              code: z.ZodIssueCode.custom,
              message: `Rule prompt must contain all selected variables`,
              path: [rulePromptKey],
            });
          }
        });
      })
      .transform((obj) => {
        if (!rulePromptConfig || !rulePromptConfig) return obj;

        const rulePromptKey = rulePromptConfig?.[0];
        const ruleStringKey = ruleStringConfig?.[0];
        const ruleStringArray = obj[ruleStringKey];
        const rulePromptString = obj[rulePromptKey];

        const newRulePromptString = ruleStringArray.reduce(
          (acc, curr, index) => {
            const id = curr.replace("{{", "").replace("}}", "");
            acc = acc.replaceAll(`{{${id}}}`, `{{variable_${index + 1}}}`);
            return acc;
          },
          rulePromptString,
        );

        return { ...obj, [rulePromptKey]: newRulePromptString };
      });
  }

  return z.object({
    name: z.string().min(1, "Name is required"),
    save_as_template: z.boolean(),
    config: z.object({
      mapping: z.object(mappingSchema).superRefine((mapping, ctx) => {
        requiredColumnIds.forEach((columnId) => {
          if (!Object.values(mapping).includes(columnId)) {
            for (const key in mapping) {
              ctx.addIssue({
                code: z.ZodIssueCode.custom,
                message: `The config column must be one of chosen values`,
                path: [key],
              });
            }
          }
        });
      }),
      config,
      reasonColumn: z.boolean(),
    }),
  });
};

const getDefaultValues = (evalConfig, editMode) => {
  const mapping = {};
  const config = {};
  for (const key of evalConfig?.required_keys || []) {
    const isMultipleColumn = evalConfig?.variable_keys?.includes(key);
    const defaultValue = isMultipleColumn ? [] : "";
    mapping[key] = editMode
      ? evalConfig?.mapping[key] || defaultValue
      : defaultValue;
  }
  for (const key of canonicalKeys(evalConfig?.config || {})) {
    const defaultVal = evalConfig?.config[key]?.default;

    if (evalConfig?.config[key]?.default !== undefined) {
      switch (evalConfig?.config[key]?.type) {
        case "rule_string":
          if (defaultVal[key] && defaultVal[key].length > 0) {
            config[key] = defaultVal[key]?.map((ruleId) => {
              return {
                value: ruleId.replace("{{", "").replace("}}", ""),
                id: getRandomId(),
              };
            });
          } else {
            config[key] = defaultVal?.map((ruleId) => {
              return {
                value: ruleId.replace("{{", "").replace("}}", ""),
                id: getRandomId(),
              };
            });
          }
          break;
        case "choices":
          if (defaultVal[key] && defaultVal[key].length > 0) {
            config[key] = defaultVal[key]?.map((choice) => {
              return {
                value: choice,
                id: getRandomId(),
              };
            });
          } else {
            config[key] = defaultVal?.map((choice) => ({
              value: choice,
              id: getRandomId(),
            }));
          }
          break;
        case "rule_prompt": {
          let newRulePrompt = defaultVal;
          let ruleStringConfig =
            canonicalValues(evalConfig?.config || {}).find((fc) => {
              return fc.type === "rule_string";
            })?.default || [];
          if (defaultVal[key] && defaultVal[key].length > 0) {
            newRulePrompt = defaultVal[key];
            ruleStringConfig =
              canonicalValues(evalConfig?.config || {}).find((fc) => {
                return fc.type === "rule_string";
              })?.default["input"] || [];
          }
          const rulePromptVariables = ruleStringConfig.map(
            (_, index) => `variable_${index + 1}`,
          );

          for (const [idx, ruleId] of rulePromptVariables.entries()) {
            const newRuleId = ruleId?.replace("{{", "").replace("}}", "");
            newRulePrompt = newRulePrompt.replace(
              `{{variable_${idx + 1}}}`,
              `{{${newRuleId}}}`,
            );
          }
          config[key] = newRulePrompt;
          break;
        }
        case "list":
          if (defaultVal && defaultVal.length > 0) {
            config[key] = defaultVal.join(", ");
          } else if (defaultVal[key] && defaultVal[key].length > 0) {
            config[key] = defaultVal[key].join(", ");
          } else {
            config[key] = "";
          }
          break;

        case "boolean":
          config[key] = defaultVal[key];
          break;

        default:
          if (defaultVal) {
            config[key] =
              typeof defaultVal === "number"
                ? defaultVal?.toString()
                : typeof defaultVal == "object"
                  ? defaultVal[key] || {}
                  : defaultVal || "";
            break;
          }
      }
    } else {
      config[key] = "";
    }
  }

  return {
    name: editMode ? evalConfig?.name : "",
    save_as_template: true,
    config: {
      mapping,
      config,
      reasonColumn: true,
    },
  };
};

const EvaluationConfig = () => {
  const { evalId } = useParams();
  const datasetId = "6c081a28-2c7e-44f2-8c21-a02d8e33527e";
  const [inputVariables, setInputVariables] = useState([]);
  const [testData, setTestData] = useState(null);
  const [rulePromptData, setRulePromptData] = useState("");
  const [isRulePrompt, setIsRulePrompt] = useState(false);
  const queryClient = useQueryClient();
  const [logIds, setLogIds] = useState([]);

  const { data: evalConfig, isLoading } = useQuery({
    queryKey: ["eval-template-config", evalId],
    queryFn: () =>
      axios.get(HOST_API + endpoints.develop.eval.getEvalConfigs, {
        params: { eval_id: evalId },
        headers: {
          Authorization: "Bearer " + localStorage.getItem("accessToken"),
        },
      }),
    select: (d) => d.data?.result,
    enabled: !!evalId,
  });

  const evalName = evalConfig?.name;

  const allowedColumns = useMemo(
    () => allowedColumnFilter(evalConfig, []),
    [evalConfig],
  );

  const validationSchema = useMemo(
    () =>
      generateValidationSchema(evalConfig, [], allowedColumns, {
        rulePromptStartEnclosures: ["prompt", "observe"].includes("")
          ? ""
          : "{{",
        rulePromptEndEnclosures: ["prompt", "observe"].includes("") ? "" : "}}",
      }),
    [evalConfig, allowedColumns],
  );

  const isUserEval = evalConfig?.eval_type === "user";

  const requiredKeys = evalConfig?.required_keys || [];

  const optionalKeys = evalConfig?.optional_keys || [];

  const getScorePercentage = (s, decimalPlaces = 0) => {
    if (s <= 0) s = 0;
    const score = s * 100;
    return Number(score.toFixed(decimalPlaces));
  };

  const { control, handleSubmit, reset, getValues, setValue } = useForm({
    defaultValues: getDefaultValues(evalConfig, true),
    resolver: zodResolver(validationSchema),
  });

  const allValues = getValues();

  useEffect(() => {
    if (evalConfig) {
      reset(getDefaultValues(evalConfig, true));
      setTestData(null);
    }
  }, [evalConfig, isUserEval, allowedColumns, reset]);

  useEffect(() => {
    const allValues = getValues();
    if (requiredKeys) {
      const array = requiredKeys.map((i, index) => {
        return {
          id: index + 1,
          name: i,
          type: "text",
          value: allValues?.config?.mapping[i] || "",
        };
      });
      setInputVariables(array);
    }
    if (allValues?.config?.config && allValues?.config?.config["input"]) {
      const array = allValues?.config?.config["input"].map((i, index) => {
        return {
          id: index + 1,
          name: `variable_${index + 1}`,
          type: "text",
          map: "input",
          value: i?.value || "",
        };
      });
      setInputVariables((prevState) => {
        return [...prevState, ...array];
      });
    }
  }, [
    requiredKeys.length,
    evalConfig,
    allValues?.config?.config["input"]?.length,
  ]);

  const { mutate: testEval, isPending: testingEvalLoading } = useMutation({
    mutationFn: (d) => {
      const modifiedData = {
        ...d,
        template_id: evalConfig?.id,
        save_as_template: false,
      };
      return axios.post(
        HOST_API + endpoints.develop.eval.runEval,
        modifiedData,
        {
          headers: {
            Authorization: "Bearer " + localStorage.getItem("accessToken"),
          },
        },
      );
    },
    onSuccess: (data) => {
      const responseData = data?.data;
      setTestData(responseData?.result);
      const newLogId = responseData?.result?.responses[0]?.log_id;
      if (newLogId) {
        setLogIds((prevLogIds) => [...prevLogIds, newLogId]);
      }
    },
  });

  const { mutate: addEval } = useMutation({
    mutationFn: (data) =>
      axios.post(HOST_API + endpoints.develop.eval.runEval, data, {
        headers: {
          Authorization: "Bearer " + localStorage.getItem("accessToken"),
        },
      }),
  });

  const onAddEval = () => (data) => {
    addEval(
      {
        template_id: evalConfig?.id,
        is_run: false,
        update: evalConfig?.owner ? true : false,
        log_ids: logIds,
        save_as_template: true,
        ...data,
      },
      {
        onSuccess: () => {
          enqueueSnackbar("Evaluation updated successfully", {
            variant: "success",
          });
          queryClient.invalidateQueries({
            queryKey: ["develop", "user-eval-list", datasetId],
          });
          queryClient.invalidateQueries({
            queryKey: ["optimize-develop-column-info"],
          });
          queryClient.invalidateQueries({
            queryKey: ["develop", "previously_configured-eval-list", datasetId],
          });
          setLogIds([]);
        },
      },
    );
  };

  const showTestResultMessage = () => {
    if (testData) {
      if (typeof testData?.responses == "string") {
        return testData?.responses;
      } else if (typeof testData?.responses == "object") {
        if (testData?.responses?.length > 0) {
          return testData?.responses[0]?.reason;
        } else {
          return "Test the eval to see the result";
        }
      }
    } else {
      return "Test the eval to see the result";
    }
  };

  const showTestResultData = () => {
    if (testData) {
      if (typeof testData?.responses == "object") {
        if (testData?.responses?.length > 0) {
          if (testData?.responses[0]?.output_type == "score") {
            return getScorePercentage(testData?.responses[0]?.output) + "%";
          } else if (testData?.responses[0]?.output_type == "Pass/Fail") {
            return testData?.responses[0]?.output;
          } else {
            return "";
          }
        } else {
          return "";
        }
      }
    } else {
      return "";
    }
  };

  return (
    <Box
      sx={{
        mt: 1,
        padding: 1,
        width: "100%",
        display: "flex",
        height: "100vh",
        justifyContent: "space-around",
        overflow: "hidden",
      }}
    >
      <Box
        sx={{
          width: "100%",
          height: "100%",
          borderRight: "1px solid",
          borderColor: "divider",
          paddingX: "15px",
          paddingTop: "10px",
          paddingBottom: "20px",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-around",
          gap: "25px",
          overflowY: "auto",
        }}
      >
        <Typography
          color={"text.primary"}
          fontSize="14px"
          fontWeight={700}
          sx={{ mt: 0 }}
        >
          {evalConfig?.name}
          <Typography
            fontSize="14px"
            fontWeight={700}
            sx={{ mb: "1.5rem", mt: "0.2rem" }}
          >
            <HelperText text={evalConfig?.description} />
          </Typography>
        </Typography>

        <form
          style={{
            display: "flex",
            flexDirection: "column",
            gap: "20px",
            height: "100%",
            width: "100%",
          }}
        >
          <FormTextFieldV2
            size="small"
            fieldName="name"
            control={control}
            label="Name"
            placeholder="Enter name"
          />

          <ShowComponent
            condition={requiredKeys?.length > 0 || optionalKeys?.length > 0}
          >
            {requiredKeys?.length > 0 && (
              <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
                <Typography
                  color="text.disabled"
                  fontWeight={600}
                  fontSize="12px"
                >
                  Required Inputs
                </Typography>
                {(
                  evalConfig?.required_keys?.filter(
                    (key) => !evalConfig?.optional_keys?.includes(key),
                  ) || []
                )?.map((field) => {
                  const isMultipleColumn =
                    evalConfig?.variable_keys?.includes(field);
                  const keyWiseAllowedColumns = keyWiseAllowedColumnFilter(
                    field,
                    evalConfig,
                    allowedColumns,
                  );
                  return (
                    <React.Fragment key={field}>
                      {evalName === "Prompt/Instruction Adherence" &&
                        keyWiseAllowedColumns.length === 0 && (
                          <FormHelperText error={true}>
                            Please perform <b>Eval run</b> on <b>run_prompt</b>{" "}
                            column to check adherence
                          </FormHelperText>
                        )}
                      <FieldSelection
                        field={field}
                        key={field}
                        allColumns={keyWiseAllowedColumns}
                        control={control}
                        isMultipleColumn={isMultipleColumn}
                      />
                    </React.Fragment>
                  );
                })}
              </Box>
            )}
            {optionalKeys?.length > 0 && (
              <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
                <Typography
                  color="text.disabled"
                  fontWeight={600}
                  fontSize="12px"
                >
                  Optional Inputs
                </Typography>
                {optionalKeys?.map((field) => {
                  const isMultipleColumn =
                    evalConfig?.variable_keys?.includes(field);
                  const keyWiseAllowedColumns = keyWiseAllowedColumnFilter(
                    field,
                    evalConfig,
                    allowedColumns,
                  );
                  return (
                    <React.Fragment key={field}>
                      <FieldSelection
                        field={field}
                        key={field}
                        allColumns={keyWiseAllowedColumns}
                        control={control}
                        isMultipleColumn={isMultipleColumn}
                      />
                    </React.Fragment>
                  );
                })}
              </Box>
            )}
          </ShowComponent>

          <Box sx={{ flex: 1 }}>
            {evalConfig && (
              <ShowComponent
                condition={Object?.keys(evalConfig?.config)?.length > 0}
              >
                <Accordion defaultExpanded>
                  <AccordionSummary>Configuration Parameters</AccordionSummary>
                  <AccordionDetails sx={{ p: 0 }}>
                    <Box
                      sx={{
                        display: "flex",
                        flexDirection: "column",
                        gap: 2,
                        paddingX: "40px",
                        paddingY: "15px",
                      }}
                    >
                      {canonicalEntries(evalConfig?.config).map(
                        ([key, fieldConfig]) => {
                          if (key === "model") {
                            return (
                              <ModelSelectInput
                                key={key}
                                control={control}
                                fieldConfig={fieldConfig}
                                config={evalConfig}
                                configKey={key}
                              />
                            );
                          } else if (fieldConfig.type === "string") {
                            return (
                              <StringInput
                                key={key}
                                control={control}
                                fieldConfig={fieldConfig}
                                config={evalConfig}
                                configKey={key}
                              />
                            );
                          } else if (fieldConfig.type === "list") {
                            return (
                              <ListInput
                                key={key}
                                control={control}
                                fieldConfig={fieldConfig}
                                config={evalConfig}
                                configKey={key}
                              />
                            );
                          } else if (fieldConfig.type === "boolean") {
                            return (
                              <BooleanInput
                                key={key}
                                control={control}
                                fieldConfig={fieldConfig}
                                config={evalConfig}
                                configKey={key}
                              />
                            );
                          } else if (fieldConfig.type === "option") {
                            return (
                              <OptionInput
                                key={key}
                                control={control}
                                fieldConfig={fieldConfig}
                                config={evalConfig}
                                configKey={key}
                              />
                            );
                          } else if (
                            fieldConfig.type === "float" ||
                            fieldConfig.type === "integer"
                          ) {
                            return (
                              <NumberInput
                                key={key}
                                control={control}
                                fieldConfig={fieldConfig}
                                config={evalConfig}
                                configKey={key}
                              />
                            );
                          } else if (fieldConfig.type === "code") {
                            return (
                              <CodeInput
                                key={key}
                                control={control}
                                fieldConfig={fieldConfig}
                                config={evalConfig}
                                configKey={key}
                              />
                            );
                          } else if (fieldConfig.type === "prompt") {
                            return (
                              <PromptInput
                                key={key}
                                control={control}
                                fieldConfig={fieldConfig}
                                config={evalConfig}
                                configKey={key}
                                allColumns={allowedColumns}
                              />
                            );
                          } else if (fieldConfig.type === "dict") {
                            return (
                              <DictInput
                                key={key}
                                control={control}
                                fieldConfig={fieldConfig}
                                config={evalConfig}
                                configKey={key}
                              />
                            );
                          } else if (fieldConfig.type === "rule_string") {
                            return (
                              <RuleStringInputVariable
                                key={key}
                                control={control}
                                fieldConfig={fieldConfig}
                                config={evalConfig}
                                configKey={key}
                                allColumns={allowedColumns}
                              />
                            );
                          } else if (fieldConfig.type === "choices") {
                            return (
                              <ChoicesInput
                                key={key}
                                control={control}
                                config={evalConfig}
                                configKey={key}
                              />
                            );
                          } else if (fieldConfig.type === "rule_prompt") {
                            const ruleStringConfig = canonicalEntries(
                              evalConfig?.config,
                            ).find(([_, fc]) => {
                              return fc.type === "rule_string";
                            });
                            return (
                              <RulePromptInput
                                rulePromptData={rulePromptData}
                                setRulePromptData={setRulePromptData}
                                isRulePrompt={isRulePrompt}
                                setIsRulePrompt={setIsRulePrompt}
                                key={key}
                                control={control}
                                fieldConfig={fieldConfig}
                                config={evalConfig}
                                configKey={key}
                                ruleStringKey={ruleStringConfig?.[0]}
                                allColumns={inputVariables}
                                isEvaluatation={true}
                                allInputColumns={
                                  allValues?.config?.config["input"]
                                }
                              />
                            );
                          }
                          return <div key={key}>{fieldConfig.type}</div>;
                        },
                      )}
                    </Box>
                  </AccordionDetails>
                </Accordion>
              </ShowComponent>
            )}
          </Box>
        </form>
      </Box>
      <Box
        sx={{
          width: "100%",
          height: "100%",
          borderColor: "divider",
          paddingX: "15px",
          paddingTop: "10px",
          paddingBottom: "20px",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          gap: "25px",
          overflowY: "auto",
        }}
      >
        {isLoading && <LinearProgress />}

        <Box>
          <Typography
            color={"text.primary"}
            fontSize="14px"
            fontWeight={700}
            sx={{ mt: 0 }}
          >
            {"Evaluation Test"}
          </Typography>
          <Typography
            fontSize="14px"
            fontWeight={700}
            sx={{ mb: "2rem", mt: "0.2rem" }}
          >
            <HelperText text="Run a sample evals to see how it works" />
          </Typography>

          {inputVariables && inputVariables.length > 0 && (
            <EvalAccordion
              data={inputVariables}
              setData={setInputVariables}
              column={{ dataType: "text", headerName: "Inputs" }}
              showTabs={evalConfig?.eval_tags?.includes("IMAGE")}
              controller={control}
              setValue={setValue}
            />
          )}
        </Box>

        <Box>
          <Typography fontSize="14px" fontWeight={700} sx={{ mb: 1 }}>
            <Box
              sx={{
                display: "flex",
                gap: 1,
                width: "100%",
                flexDirection: "column",
                marginY: 1,
              }}
            ></Box>
            {"Result"}
          </Typography>
          <Box
            sx={{
              paddingX: "16px",
              paddingY: "12px",
              backgroundColor: `${testData?.responses[0]?.output_type == "score" ? interpolateColorBasedOnScore(testData?.responses[0]?.output, 1) : testData?.responses[0]?.output_type == "Pass/Fail" ? (testData?.responses[0]?.output == "Passed" ? interpolateColorBasedOnScore(1, 1) : interpolateColorBasedOnScore(0, 1)) : "background.neutral"}`,
              overflowWrap: "break-word",
              borderRadius: "10px",
            }}
          >
            {testData && testData?.responses?.length > 0 ? (
              <Typography variant="body2" sx={{ minHeight: "100px" }}>
                {showTestResultData()}
                <Typography variant="body2">
                  {showTestResultMessage()}
                </Typography>
              </Typography>
            ) : (
              <Box
                sx={{
                  display: "flex",
                  justifyContent: "center",
                  alignItems: "center",
                  textAlign: "center",
                  minHeight: "80px",
                }}
              >
                <Typography
                  variant="body2"
                  sx={{
                    color: "text.secondary",
                  }}
                >
                  Test the eval to see the result
                </Typography>
              </Box>
            )}
          </Box>

          <Box
            sx={{
              display: "flex",
              mt: 2,
              width: "100%",
              flexDirection: "column",
            }}
          >
            <Box
              sx={{
                display: "flex",
                gap: 2,
                width: "100%",
              }}
            >
              <LoadingButton
                fullWidth
                variant="outlined"
                sx={
                  {
                    // marginY: "15px"
                  }
                }
                onClick={handleSubmit(testEval)}
                loading={testingEvalLoading}
              >
                Test
              </LoadingButton>
              <LoadingButton
                fullWidth
                variant="contained"
                color="primary"
                onClick={() =>
                  // editMode
                  //   ? handleSubmit(onEditEval(true))()
                  handleSubmit(onAddEval(true))()
                }
                // loading={addingEvalLoading}
              >
                Save changes
              </LoadingButton>
            </Box>
          </Box>
        </Box>
      </Box>
    </Box>
  );
};

export default EvaluationConfig;
