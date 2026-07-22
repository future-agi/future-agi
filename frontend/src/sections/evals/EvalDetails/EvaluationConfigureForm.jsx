import {
  Box,
  Button,
  Divider,
  FormHelperText,
  LinearProgress,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useMemo, useState, useEffect } from "react";
import Iconify from "src/components/iconify";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useForm } from "react-hook-form";

import { ShowComponent } from "src/components/show";
import { z } from "zod";
import { zodResolver } from "@hookform/resolvers/zod";
import {
  camelCaseToTitleCase,
  canonicalEntries,
  canonicalKeys,
  canonicalValues,
  getRandomId,
  interpolateColorBasedOnScore,
} from "src/utils/utils";
import { LoadingButton } from "@mui/lab";
import { enqueueSnackbar } from "src/components/snackbar";
import { HtmlPromptValidationSchema } from "src/utils/validation";
import ChoicesInput from "src/components/ChoiceInput/ChoicesInput";
import {
  allowedColumnFilter,
  keyWiseAllowedColumnFilter,
} from "src/sections/develop-detail/Common/EvaluationConfigure/common";
import HelperText from "src/sections/develop-detail/Common/HelperText";
import ModelSelectInput from "src/sections/develop-detail/Common/EvaluationConfigure/ModelSelectInput";
import StringInput from "src/sections/develop-detail/Common/EvaluationConfigure/StringInput";
import ListInput from "src/sections/develop-detail/Common/EvaluationConfigure/ListInput";
import BooleanInput from "src/sections/develop-detail/Common/EvaluationConfigure/BooleanInput";
import OptionInput from "src/sections/develop-detail/Common/EvaluationConfigure/OptionInput";
import NumberInput from "src/sections/develop-detail/Common/EvaluationConfigure/NumberInput";
import CodeInput from "src/sections/develop-detail/Common/EvaluationConfigure/CodeInput";
import PromptInput from "src/sections/develop-detail/Common/EvaluationConfigure/PromptInput";
import DictInput from "src/sections/develop-detail/Common/EvaluationConfigure/DictInput";
import { RuleStringInputVariable } from "src/sections/develop-detail/Common/EvaluationConfigure/RuleStringInput";
import RulePromptInput from "src/sections/develop-detail/Common/EvaluationConfigure/RulePromptInput";
import EvalAccordion from "../EvalsAccordions/EvalAccordion";
import FieldSelection from "../Helpers/FieldSelection";
import { encloseString } from "src/sections/develop-detail/Common/EvaluationConfigure/validation";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
} from "src/sections/develop-detail/AccordianElements";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import {
  getEvalBaseName,
  DEFAULT_EVAL_MODEL,
} from "src/sections/common/EvaluationDrawer/common";

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

  // Add validation for config fields. canonicalEntries drops the
  // camelCase aliases that may exist in legacy objects alongside snake_case
  // keys so each parameter is only validated once.
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

const getDefaultValues = (evalConfig, editMode, allColumns) => {
  const mapping = {};
  const config = {};
  const allColumnsMap = allColumns?.reduce((acc, col) => {
    acc[col.field] = col.headerName;
    return acc;
  }, {});
  for (const key of evalConfig?.required_keys || []) {
    const isMultipleColumn = evalConfig?.variable_keys?.includes(key);
    const defaultValue = isMultipleColumn ? [] : "";
    mapping[key] = editMode
      ? evalConfig?.mapping[key] || defaultValue
      : defaultValue;
  }
  for (const key of canonicalKeys(evalConfig?.config || {})) {
    const defaultVal = evalConfig?.config[key]?.default;
    // Auto-select preferred model (turing_large) for model field
    if (key === "model") {
      const modelOptions =
        evalConfig?.configParamsOption?.[key] ||
        evalConfig?.config_params_option?.[key] ||
        [];
      const hasTuringLarge = modelOptions.includes(DEFAULT_EVAL_MODEL);
      config[key] = editMode
        ? defaultVal ||
          (hasTuringLarge ? DEFAULT_EVAL_MODEL : modelOptions[0] || "")
        : hasTuringLarge
          ? DEFAULT_EVAL_MODEL
          : defaultVal || modelOptions[0] || "";
      continue;
    }
    if (evalConfig?.config[key]?.default !== undefined) {
      switch (evalConfig?.config[key]?.type) {
        case "code": {
          const defaultCode = `# Function name should be main only. You can access variables using {{
def main():
    return True
`;
          config[key] = defaultVal
            ? allColumns?.reduce((acc, col) => {
                const newVal = `{{${col.headerName}}}`;
                return acc.replace(new RegExp(`{{${col.field}}}`, "g"), newVal);
              }, defaultVal)
            : defaultCode;
          break;
        }
        case "rule_string":
          config[key] = defaultVal?.map((ruleId) => {
            return {
              value: ruleId.replace("{{", "").replace("}}", ""),
              id: getRandomId(),
            };
          });
          break;
        case "choices":
          config[key] = defaultVal?.map((choice) => ({
            value: choice,
            id: getRandomId(),
          }));
          break;
        case "rule_prompt": {
          let newRulePrompt = defaultVal;
          const ruleStringConfig =
            canonicalValues(evalConfig?.config || {}).find((fc) => {
              return fc.type === "rule_string";
            })?.default || [];
          for (const [idx, ruleId] of ruleStringConfig.entries()) {
            const newRuleId = ruleId?.replace("{{", "").replace("}}", "");
            newRulePrompt = newRulePrompt.replace(
              `{{variable_${idx + 1}}}`,
              `{{${allColumnsMap[newRuleId]}}}`,
            );
          }
          config[key] = newRulePrompt;
          break;
        }
        case "boolean":
          config[key] = defaultVal;
          break;
        default:
          config[key] =
            typeof defaultVal === "number"
              ? defaultVal?.toString()
              : defaultVal || "";
          break;
      }
    } else {
      config[key] = "";
    }
  }
  return {
    // Auto-generate name from eval template
    name: editMode ? evalConfig?.name : getEvalBaseName(evalConfig),
    save_as_template: true,
    config: {
      mapping,
      config,
      // Error localization is enabled by default
      reasonColumn: true,
    },
  };
};

const EvaluationConfigureFormChild = ({
  selectedEval,
  evalConfig,
  allColumns,
  onClose,
  refreshGrid,
  datasetId,
  hideSaveAndRun,
  onSubmitComplete,
  setFormIsDirty,
  setConfirmationModalOpen,
  inputVariables,
  control,
  handleSubmit,
  setLogIds,
  logIds,
  allInputValues,
  setRefresh,
  refresh,
}) => {
  const allowedColumns = useMemo(
    () => allowedColumnFilter(evalConfig, allColumns),
    [allColumns, evalConfig],
  );
  const [rulePromptData, setRulePromptData] = useState("");
  const [isRulePrompt, setIsRulePrompt] = useState(false);

  const queryClient = useQueryClient();

  const evalName = selectedEval?.name || evalConfig?.name;

  const { mutate: addEval, isPending: addingDatasetEvalLoading } = useMutation({
    mutationFn: (data) => axios.post(endpoints.develop.eval.runEval, data),
  });

  const onAddEval = (run) => (data) => {
    if (setFormIsDirty) setFormIsDirty(false);

    addEval(
      {
        template_id: evalConfig?.id,
        is_run: false,
        save_as_template: true,
        log_ids: logIds,
        ...data,
      },
      {
        onSuccess: () => {
          enqueueSnackbar("Evaluation added successfully", {
            variant: "success",
          });
          setRefresh(refresh + 1);
          queryClient.invalidateQueries({
            queryKey: ["develop", "user-eval-list", datasetId],
          });
          queryClient.invalidateQueries({
            queryKey: ["optimize-develop-column-info"],
          });
          queryClient.invalidateQueries({
            queryKey: ["develop", "previously_configured-eval-list", datasetId],
          });
          refreshGrid();
          onClose();
          if (run) {
            if (setConfirmationModalOpen) setConfirmationModalOpen(false);
            onSubmitComplete?.();
          }
          setLogIds([]);
        },
      },
    );
  };

  const requiredKeys =
    evalConfig?.required_keys?.filter(
      (key) => !evalConfig?.optional_keys?.includes(key),
    ) || [];

  const optionalKeys = evalConfig?.optional_keys || [];

  return (
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
        fieldName="name"
        size="small"
        control={control}
        label="Name"
        placeholder="Enter name"
      />

      <ShowComponent
        condition={requiredKeys?.length > 0 || optionalKeys?.length > 0}
      >
        {requiredKeys?.length > 0 && (
          <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
            <Typography color="text.disabled" fontWeight={600} fontSize="12px">
              Required Inputs
            </Typography>
            {requiredKeys?.map((field) => {
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
            <Typography color="text.disabled" fontWeight={600} fontSize="12px">
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
        <ShowComponent condition={Object.keys(evalConfig?.config)?.length > 0}>
          <Accordion defaultExpanded>
            <AccordionSummary>Configuration Parameters</AccordionSummary>
            <AccordionDetails sx={{ p: 0 }}>
              <Box
                sx={{
                  display: "flex",
                  flexDirection: "column",
                  gap: 2,
                  padding: "20px",
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
                          allInputColumns={allInputValues}
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
      </Box>

      <Box
        sx={{
          display: "flex",
          gap: 1,
          width: "100%",
          flexDirection: "column",
        }}
      >
        <Box
          sx={{
            display: "flex",
            gap: 2,
            width: "100%",
            // marginBottom:1
          }}
        >
          <ShowComponent condition={!hideSaveAndRun}>
            <LoadingButton
              fullWidth
              size="small"
              variant="contained"
              color="primary"
              onClick={() =>
                // editMode
                //   ? handleSubmit(onEditEval(true))()
                handleSubmit(onAddEval(true))()
              }
              loading={addingDatasetEvalLoading}
            >
              Add Eval
            </LoadingButton>
          </ShowComponent>
        </Box>
      </Box>
    </form>
  );
};

EvaluationConfigureFormChild.propTypes = {
  selectedEval: PropTypes.object,
  evalConfig: PropTypes.object,
  allColumns: PropTypes.array,
  module: PropTypes.string,
  evalsConfigs: PropTypes.array,
  setEvalsConfigs: PropTypes.func,
  handleLabelsAdd: PropTypes.func,
  onClose: PropTypes.func,
  requiredColumnIds: PropTypes.array,
  editMode: PropTypes.bool,
  testEval: PropTypes.func,
  testingEvalLoading: PropTypes.bool,
  refreshGrid: PropTypes.func,
  datasetId: PropTypes.string,
  experimentEval: PropTypes.object,
  hideSaveAndRun: PropTypes.bool,
  onSubmitComplete: PropTypes.func,
  setFormIsDirty: PropTypes.func,
  setConfirmationModalOpen: PropTypes.func,
  onFormSave: PropTypes.func,
  setInputVariables: PropTypes.func,
  inputVariables: PropTypes.array,
  handleDeleteInput: PropTypes.func,
  addInputVariable: PropTypes.func,
  setController: PropTypes.func,
  control: PropTypes.any,
  handleSubmit: PropTypes.any,
  logIds: PropTypes.array,
  setLogIds: PropTypes.func,
  allInputValues: PropTypes.array,
  refresh: PropTypes.any,
  setRefresh: PropTypes.func,
};

const EvaluationConfigureForm = ({
  onClose,
  onBackClick,
  selectedEval,
  allColumns,
  module,
  evalsConfigs,
  setEvalsConfigs,
  requiredColumnIds,
  refreshGrid,
  datasetId,
  experimentEval,
  hideSaveAndRun,
  onSubmitComplete,
  setConfirmationModalOpen,
  handleLabelsAdd,
  setFormIsDirty,
  onFormSave,
  setRefresh,
  refresh,
}) => {
  const isPreviouslyConfigured =
    selectedEval?.eval_type === "previouslyConfigured";

  const [testData, setTestData] = useState(null);

  const isUserEval = selectedEval?.eval_type === "user";
  const [inputVariables, setInputVariables] = useState([]);
  const [logIds, setLogIds] = useState([]);

  const addInputVariable = () => {
    const newInput = {
      id: inputVariables.length + 1,
      name: `variable_${inputVariables.length + 1}`,
      type: "text",
      value: "",
    };
    setInputVariables((prev) => [...prev, newInput]);
  };

  const handleDeleteInput = (index) => {
    setInputVariables((prev) => prev.filter((_, i) => i !== index));
  };

  const { mutate: testEval, isPending: testingEvalLoading } = useMutation({
    mutationFn: (d) => {
      const modifiedData = {
        ...d,
        template_id: selectedEval?.id,
        save_as_template: false,
      };
      return axios.post(endpoints.develop.eval.runEval, modifiedData);
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

  const {
    data: evalConfig,
    isSuccess,
    isFetching,
  } = useQuery({
    queryKey: ["develop", "eval-template-config", selectedEval?.id],
    queryFn: () =>
      axios.get(
        endpoints.develop.eval.getPreviouslyConfiguredEvalTemplateConfig(
          datasetId,
          selectedEval?.id,
        ),
        {
          params: {
            eval_type: isUserEval
              ? "user"
              : isPreviouslyConfigured
                ? "previously_configured"
                : "preset",
          },
        },
      ),
    select: (d) => d.data?.result?.eval,
    enabled: !!selectedEval?.id,
  });

  const allowedColumns = useMemo(
    () => allowedColumnFilter(evalConfig, allColumns),
    [allColumns, evalConfig],
  );

  const requiredKeys = evalConfig?.required_keys || [];

  const validationSchema = useMemo(
    () =>
      generateValidationSchema(evalConfig, requiredColumnIds, allowedColumns, {
        rulePromptStartEnclosures: ["prompt", "observe"].includes(module)
          ? ""
          : "{{",
        rulePromptEndEnclosures: ["prompt", "observe"].includes(module)
          ? ""
          : "}}",
      }),
    [evalConfig, allowedColumns, requiredColumnIds, module],
  );

  const {
    control,
    handleSubmit,
    formState: { isDirty },
    getValues,
    setValue,
  } = useForm({
    defaultValues: getDefaultValues(evalConfig, isUserEval, allowedColumns),
    resolver: zodResolver(validationSchema),
  });
  const allValues = getValues();

  useEffect(() => {
    if (setFormIsDirty) setFormIsDirty(isDirty);
  }, [isDirty, setFormIsDirty]);

  const getScorePercentage = (s, decimalPlaces = 0) => {
    if (s <= 0) s = 0;
    const score = s * 100;
    return Number(score.toFixed(decimalPlaces));
  };

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
  }, [evalConfig, allValues?.config?.config["input"]?.length]);

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
    <Box sx={{ display: "flex", height: "100%" }}>
      <Box
        sx={{
          width: "35vw",
          height: "100vh",
          borderRight: "1px solid",
          borderColor: "divider",
          paddingTop: "20px",
          paddingX: "20px",
          paddingBottom: "5px",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-around",
          gap: "25px",
          overflowY: "auto",
        }}
      >
        {isFetching && <LinearProgress />}

        <Box
          sx={{
            height: "100%",
            position: "relative",
          }}
        >
          <Typography
            color={"text.primary"}
            fontSize="14px"
            fontWeight={700}
            sx={{ mt: 1 }}
          >
            {"Evaluation Test"}
          </Typography>
          <Typography
            fontSize="14px"
            fontWeight={700}
            sx={{ mb: "1.5rem", mt: "0.2rem" }}
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

        <Box
          sx={{
            // position: "sticky",
            bottom: 0,
          }}
        >
          <Typography fontSize="14px" fontWeight={700} sx={{ mb: 1 }}>
            <Box
              sx={{
                display: "flex",
                gap: 1,
                width: "100%",
                flexDirection: "column",
                marginY: 1,
              }}
            >
              <Divider />
            </Box>
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
                  minHeight: "100px",
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
          <LoadingButton
            size="small"
            fullWidth
            variant="outlined"
            sx={{
              marginY: "15px",
            }}
            onClick={handleSubmit(testEval)}
            loading={testingEvalLoading}
          >
            Test
          </LoadingButton>
        </Box>
      </Box>
      <Box
        sx={{
          width: "35vw",
          height: "100vh",
          borderRight: "1px solid",
          borderColor: "divider",
          padding: "20px",
          display: "flex",
          flexDirection: "column",
          gap: "20px",
          overflowY: "auto",
        }}
      >
        <Box
          sx={{
            display: "flex",
            justifyContent: "space-between",
            position: "relative",
            top: 0,
            zIndex: 1,
          }}
        >
          <Button
            size="small"
            startIcon={
              <Iconify
                icon="octicon:chevron-left-24"
                width="24px"
                sx={{ color: "primary.main" }}
              />
            }
            onClick={() => {
              setLogIds([]);
              onBackClick(isPreviouslyConfigured);
            }}
          >
            Back
          </Button>
          <Iconify
            icon="flowbite:x-outline"
            sx={{ cursor: "pointer" }}
            onClick={() => {
              setLogIds([]);
              if (setFormIsDirty) setFormIsDirty(false);
              onClose();
            }}
          />
        </Box>

        <Typography color={"text.primary"} fontSize="14px" fontWeight={700}>
          {selectedEval?.name || evalConfig?.name}
          <Typography fontSize="14px" fontWeight={700} sx={{ mt: "0.2rem" }}>
            <HelperText text={selectedEval?.description} />
          </Typography>
        </Typography>

        {isSuccess && (
          <EvaluationConfigureFormChild
            selectedEval={selectedEval}
            evalConfig={evalConfig}
            allColumns={allColumns}
            module={module}
            onClose={onClose}
            evalsConfigs={evalsConfigs}
            setEvalsConfigs={setEvalsConfigs}
            requiredColumnIds={requiredColumnIds}
            editMode={isUserEval}
            testEval={testEval}
            testingEvalLoading={testingEvalLoading}
            refreshGrid={refreshGrid}
            datasetId={datasetId}
            experimentEval={experimentEval}
            hideSaveAndRun={hideSaveAndRun}
            onSubmitComplete={onSubmitComplete}
            handleLabelsAdd={handleLabelsAdd}
            setFormIsDirty={setFormIsDirty}
            setConfirmationModalOpen={setConfirmationModalOpen}
            onFormSave={onFormSave}
            setInputVariables={setInputVariables}
            inputVariables={inputVariables}
            handleDeleteInput={handleDeleteInput}
            addInputVariable={addInputVariable}
            control={control}
            handleSubmit={handleSubmit}
            setLogIds={setLogIds}
            logIds={logIds}
            allInputValues={allValues?.config?.config["input"]}
            setRefresh={setRefresh}
            refresh={refresh}
          />
        )}
      </Box>
    </Box>
  );
};

EvaluationConfigureForm.propTypes = {
  onClose: PropTypes.func,
  onBackClick: PropTypes.func,
  selectedEval: PropTypes.object,
  module: PropTypes.string,
  evalsConfigs: PropTypes.array,
  setEvalsConfigs: PropTypes.func,
  allColumns: PropTypes.array,
  requiredColumnIds: PropTypes.array,
  refreshGrid: PropTypes.func,
  datasetId: PropTypes.string,
  experimentEval: PropTypes.object,
  hideSaveAndRun: PropTypes.bool,
  handleLabelsAdd: PropTypes.func,
  onSubmitComplete: PropTypes.func,
  setFormIsDirty: PropTypes.func,
  setConfirmationModalOpen: PropTypes.func,
  onFormSave: PropTypes.func,
  refresh: PropTypes.any,
  setRefresh: PropTypes.func,
};

export default EvaluationConfigureForm;
