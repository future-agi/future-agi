import {
  AccordionDetails,
  Box,
  Button,
  Collapse,
  FormHelperText,
  LinearProgress,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useMemo, useState, useEffect } from "react";
import Iconify from "src/components/iconify";
import HelperText from "../HelperText";
import FieldSelection from "../FieldSelection";
import { Accordion, AccordionSummary } from "../../AccordianElements";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useForm } from "react-hook-form";
import StringInput from "./StringInput";
import BooleanInput from "./BooleanInput";
import OptionInput from "./OptionInput";
import NumberInput from "./NumberInput";
import { ShowComponent } from "src/components/show";
import CodeInput from "./CodeInput";
import { zodResolver } from "@hookform/resolvers/zod";
import { getRandomId } from "src/utils/utils";
import { LoadingButton } from "@mui/lab";
import { enqueueSnackbar } from "src/components/snackbar";
import PromptInput from "./PromptInput";
import DictInput from "./DictInput";
import RuleStringInput from "./RuleStringInput";
import RulePromptInput from "./RulePromptInput";
import ListInput from "./ListInput";
import EvaluationTest from "./EvaluationTest";
import { generateValidationSchema } from "./validation";
import ModelSelectInput from "./ModelSelectInput";
import {
  allowedColumnFilter,
  keyWiseAllowedColumnFilter,
  onlyAudioEvalTemplate,
} from "./common";
import ChoicesInput from "src/components/ChoiceInput/ChoicesInput";
import { ConfirmDialog } from "src/components/custom-dialog";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import {
  getEvalBaseName,
  DEFAULT_EVAL_MODEL,
} from "src/sections/common/EvaluationDrawer/common";
import { FAGI_MODEL_VALUES } from "src/sections/evals/components/ModelSelector";
import { useFeatureLocked, CAPABILITY } from "src/hooks/useCapabilities";

const getDefaultValues = (evalConfig, editMode, allColumns, fagiLocked) => {
  const mapping = {};
  const config = {};
  const allColumnsMap = allColumns?.reduce((acc, col) => {
    acc[col.field] = col.headerName;
    return acc;
  }, {});
  for (const key of evalConfig?.requiredKeys || []) {
    const isMultipleColumn = evalConfig?.variableKeys?.includes(key);
    const defaultValue = isMultipleColumn ? [] : "";
    mapping[key] = editMode
      ? evalConfig?.mapping[key] || defaultValue
      : defaultValue;
  }
  for (const key of Object.keys(evalConfig?.config || {})) {
    const defaultVal = evalConfig?.config[key]?.default;
    // Auto-select preferred model (turing_large) for model field. Without
    // Turing access (no license/plan) it starts blank and the user must pick.
    if (key === "model") {
      const modelOptions =
        evalConfig?.configParamsOption?.[key] ||
        evalConfig?.config_params_option?.[key] ||
        [];
      const allowed = modelOptions.filter(
        (m) => !fagiLocked || !FAGI_MODEL_VALUES.has(m),
      );
      const safeDefault =
        fagiLocked && FAGI_MODEL_VALUES.has(defaultVal) ? "" : defaultVal;
      const hasTuringLarge = !fagiLocked && allowed.includes(DEFAULT_EVAL_MODEL);
      config[key] = editMode
        ? safeDefault ||
          (hasTuringLarge ? DEFAULT_EVAL_MODEL : allowed[0] || "")
        : hasTuringLarge
          ? DEFAULT_EVAL_MODEL
          : safeDefault || allowed[0] || "";
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
            Object.values(evalConfig?.config || {}).find((fc) => {
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
    saveAsTemplate: false,
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
  jsonSchemas = {},
  module,
  evalsConfigs,
  setEvalsConfigs,
  handleLabelsAdd,
  onClose,
  requiredColumnIds,
  editMode,
  testEval,
  testingEvalLoading,
  refreshGrid,
  datasetId,
  experimentEval,
  hideSaveAndRun,
  onSubmitComplete,
  setFormIsDirty,
  setConfirmationModalOpen,
  onFormSave,
  loadingStates,
  setIsFormDirty,
}) => {
  const allowedColumns = useMemo(
    () => allowedColumnFilter(evalConfig, allColumns),
    [allColumns, evalConfig],
  );
  // Fail closed while capabilities load (locked===true) so form defaults never
  // seed a Turing model the deployment can't run.
  const { locked: fagiLocked } = useFeatureLocked(CAPABILITY.TURING_MODELS);
  const [rulePromptData, setRulePromptData] = useState("");
  const [isRulePrompt, setIsRulePrompt] = useState(false);

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

  const queryClient = useQueryClient();

  const {
    control,
    handleSubmit,
    formState: { isDirty },
  } = useForm({
    defaultValues: getDefaultValues(
      evalConfig,
      editMode,
      allowedColumns,
      fagiLocked,
    ),
    resolver: zodResolver(validationSchema),
  });

  useEffect(() => {
    if (setFormIsDirty) setFormIsDirty(isDirty);
    setIsFormDirty(isDirty);
  }, [isDirty]);

  const evalName = selectedEval?.name || evalConfig?.name;

  const { mutate: addEval, isPending: addingDatasetEvalLoading } = useMutation({
    mutationFn: (data) =>
      axios.post(endpoints.develop.eval.addEval(datasetId), data),
  });

  const { mutate: addExperimentEval, isPending: addingExperimentEvalLoading } =
    useMutation({
      mutationFn: (data) =>
        axios.post(
          endpoints.develop.experiment.addEval(experimentEval?.experimentId),
          data,
        ),
    });

  const { mutate: editEval, isPending: editingEvalLoading } = useMutation({
    mutationFn: (data) =>
      axios.post(
        endpoints.develop.eval.editEval(datasetId, selectedEval?.id),
        data,
      ),
  });

  const addingEvalLoading =
    addingExperimentEvalLoading ||
    addingDatasetEvalLoading ||
    editingEvalLoading;

  // Backend serializers expect snake_case. The form state (from zod schema)
  // uses camelCase for `saveAsTemplate` and nested `config.reasonColumn`; flip
  // them to snake_case before sending the body.
  const toEvalApiPayload = (data) => {
    const { saveAsTemplate, config: formConfig, ...rest } = data || {};
    const { reasonColumn, ...configRest } = formConfig || {};
    return {
      ...rest,
      ...(saveAsTemplate !== undefined && { save_as_template: saveAsTemplate }),
      config: {
        ...configRest,
        ...(reasonColumn !== undefined && { reason_column: reasonColumn }),
      },
    };
  };

  const onAddEval = (run) => (data) => {
    if (setFormIsDirty) setFormIsDirty(false);
    if (handleLabelsAdd) handleLabelsAdd(null);
    const apiData = toEvalApiPayload(data);
    if (experimentEval) {
      addExperimentEval(
        {
          template_id: selectedEval?.id,
          run: requiredColumnIds?.length > 0 ? false : run,
          ...apiData,
        },
        {
          onSuccess: () => {
            enqueueSnackbar("Evaluation added successfully", {
              variant: "success",
            });
            queryClient.invalidateQueries({
              queryKey: [
                "experiment-column-info",
                experimentEval?.baseColumnId,
              ],
            });
            refreshGrid(null, true);
            onClose();
            if (run) {
              if (setConfirmationModalOpen) setConfirmationModalOpen(false);
              onSubmitComplete?.();
            }
          },
        },
      );
    } else {
      // @ts-ignore
      addEval(
        {
          template_id: selectedEval?.id,
          run: requiredColumnIds?.length > 0 ? false : run,
          ...apiData,
        },
        {
          onSuccess: () => {
            enqueueSnackbar("Evaluation added successfully", {
              variant: "success",
            });
            queryClient.invalidateQueries({
              queryKey: ["develop", "user-eval-list", datasetId],
            });
            queryClient.invalidateQueries({
              queryKey: ["optimize-develop-column-info"],
            });
            queryClient.invalidateQueries({
              queryKey: [
                "develop",
                "previously_configured-eval-list",
                datasetId,
              ],
            });
            refreshGrid(null, true);
            onClose();
            if (run) {
              if (setConfirmationModalOpen) setConfirmationModalOpen(false);
              onSubmitComplete?.();
            }
          },
        },
      );
    }
  };

  const onSaveEval = () => (data) => {
    if (setFormIsDirty) setFormIsDirty(false);
    if (handleLabelsAdd) handleLabelsAdd(null);
    const config = {
      config: data.config.config,
      mapping: data.config.mapping,
      id: selectedEval?.id,
      name: data.name,
    };
    setEvalsConfigs([...evalsConfigs, config]);
    if (handleLabelsAdd) handleLabelsAdd(null);
    onClose();
  };

  const onEditEval = (run) => (data) => {
    // @ts-ignore
    if (setFormIsDirty) setFormIsDirty(false);
    if (handleLabelsAdd) handleLabelsAdd(null);
    const apiData = toEvalApiPayload(data);
    editEval(
      {
        run,
        ...apiData,
      },
      {
        onSuccess: () => {
          enqueueSnackbar("Evaluation Edited successfully", {
            variant: "success",
          });
          queryClient.invalidateQueries({
            queryKey: ["develop", "user-eval-list", datasetId],
          });
          queryClient.invalidateQueries({
            queryKey: ["optimize-develop-column-info"],
          });
          refreshGrid(null, true);
          onClose();
          if (run) {
            if (setConfirmationModalOpen) setConfirmationModalOpen(false);
            onSubmitComplete?.();
          }
        },
      },
    );
  };

  const onTestEval = (data) => {
    // @ts-ignore
    testEval({
      template_id: evalConfig?.templateId,
      ...toEvalApiPayload(data),
    });
  };
  const requiredKeys =
    evalConfig?.requiredKeys?.filter(
      (key) => !evalConfig?.optionalKeys?.includes(key),
    ) || [];

  const optionalKeys = evalConfig?.optionalKeys || [];

  return (
    <form
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "20px",
        height: "100%",
        paddingTop: 0.5,
      }}
    >
      <FormTextFieldV2
        fieldName="name"
        control={control}
        label="Name"
        placeholder="Enter name"
        helperText={
          <HelperText
            text={selectedEval?.description || evalConfig?.description}
          />
        }
      />
      <ShowComponent condition={requiredKeys?.length > 0}>
        <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
          <Typography color="text.secondary" fontWeight={600} fontSize="12px">
            Required Inputs
          </Typography>
          {requiredKeys?.map((field) => {
            const isMultipleColumn = evalConfig?.variableKeys?.includes(field);
            let keyWiseAllowedColumns = keyWiseAllowedColumnFilter(
              field,
              evalConfig,
              allowedColumns,
            );

            // For prompt_instruction_adherence, append -input-prompt to column names for "prompt" key
            if (
              evalConfig?.templateName === "prompt_instruction_adherence" &&
              field === "prompt"
            ) {
              keyWiseAllowedColumns = keyWiseAllowedColumns.map((col) => ({
                ...col,
                headerName: `prompt-${col.headerName}-input`,
              }));
            }

            let placeholder = "";
            if (onlyAudioEvalTemplate.includes(evalConfig?.templateName)) {
              placeholder = "Add audio input";
            }
            return (
              <React.Fragment key={field}>
                {evalName === "Prompt/Instruction Adherence" &&
                  keyWiseAllowedColumns.length === 0 && (
                    <FormHelperText error={true}>
                      Please perform <b>Eval run</b> on <b>run_prompt</b> column
                      to check adherence
                    </FormHelperText>
                  )}
                <FieldSelection
                  field={field}
                  key={field}
                  allColumns={keyWiseAllowedColumns}
                  jsonSchemas={jsonSchemas}
                  control={control}
                  isMultipleColumn={isMultipleColumn}
                  placeholder={placeholder}
                />
              </React.Fragment>
            );
          })}
        </Box>
      </ShowComponent>
      <ShowComponent condition={optionalKeys?.length > 0}>
        <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
          <Typography color="text.secondary" fontWeight={600} fontSize="12px">
            Optional Inputs
          </Typography>
          {optionalKeys?.map((field) => {
            const isMultipleColumn = evalConfig?.variableKeys?.includes(field);
            const keyWiseAllowedColumns = keyWiseAllowedColumnFilter(
              field,
              evalConfig,
              allowedColumns,
            );
            return (
              <FieldSelection
                field={field}
                key={field}
                allColumns={keyWiseAllowedColumns}
                jsonSchemas={jsonSchemas}
                control={control}
                isMultipleColumn={isMultipleColumn}
              />
            );
          })}
        </Box>
      </ShowComponent>

      <Box sx={{ flex: 1 }}>
        <ShowComponent condition={Object.keys(evalConfig?.config).length > 0}>
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
                {Object.entries(evalConfig?.config).map(
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
                          allColumns={allowedColumns}
                          jsonSchemas={jsonSchemas}
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
                          jsonSchemas={jsonSchemas}
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
                        <RuleStringInput
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
                      const ruleStringConfig = Object.entries(
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
                          allColumns={allowedColumns}
                          jsonSchemas={jsonSchemas}
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
          pb: "10px",
          flexDirection: "column",
        }}
      >
        {/* <ShowComponent condition={!hideSaveAndRun}>
          <Box sx={{ display: "flex", gap: 1, alignItems: "center" }}>
            <FormCheckboxField
              control={control}
              fieldName="saveAsTemplate"
              size="small"
              label="Save As Template"
              labelPlacement="end"
            />
          </Box>
        </ShowComponent> */}
        <Box
          sx={{
            display: "flex",
            gap: 2,
            width: "100%",
          }}
        >
          {module !== "prompt" && module !== "observe" && (
            <LoadingButton
              // size="small"
              fullWidth
              variant="outlined"
              onClick={handleSubmit(onTestEval)}
              loading={testingEvalLoading}
            >
              Test
            </LoadingButton>
          )}
          <ShowComponent condition={!experimentEval}>
            <LoadingButton
              fullWidth
              // size="small"
              variant={hideSaveAndRun ? "contained" : "outlined"}
              color="primary"
              onClick={() => {
                if (module === "prompt") {
                  handleSubmit(onSaveEval(false))();
                  return;
                }
                if (module === "observe" && onFormSave) {
                  handleSubmit((formData) =>
                    onFormSave({ ...formData, selectedEval, run: false }),
                  )();
                  return;
                }
                editMode
                  ? handleSubmit(onEditEval(false))()
                  : handleSubmit(onAddEval(false))();
              }}
              loading={addingEvalLoading || loadingStates?.saveLoading}
            >
              Save
            </LoadingButton>
          </ShowComponent>
          <ShowComponent condition={!hideSaveAndRun}>
            <LoadingButton
              fullWidth
              size="small"
              variant="contained"
              color="primary"
              onClick={() => {
                if (module === "observe" && onFormSave) {
                  handleSubmit((formData) =>
                    onFormSave({ ...formData, selectedEval, run: true }),
                  )();
                  return;
                }
                editMode
                  ? handleSubmit(onEditEval(true))()
                  : handleSubmit(onAddEval(true))();
              }}
              loading={addingEvalLoading}
            >
              Save and Run
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
  jsonSchemas: PropTypes.object,
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
  loadingStates: PropTypes.shape({
    saveLoading: PropTypes.bool,
  }),
  setIsFormDirty: PropTypes.func,
};

const EvaluationConfigureForm = ({
  onClose,
  onBackClick,
  selectedEval,
  allColumns,
  jsonSchemas = {},
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
  loadingStates,
}) => {
  const isPreviouslyConfigured =
    selectedEval?.evalType === "previouslyConfigured";

  const isUserEval = selectedEval?.evalType === "user";
  const [isFormDirty, setIsFormDirty] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [pendingAction, setPendingAction] = useState(null);
  const [showEvaluation, setShowEvaluation] = useState(false);

  const handleBackClick = () => {
    if (isFormDirty) {
      setPendingAction("back");
      setConfirmOpen(true);
    } else {
      onBackClick(isPreviouslyConfigured);
    }
  };

  const handleCloseClick = () => {
    if (isFormDirty) {
      setPendingAction("close");
      setConfirmOpen(true);
    } else {
      if (setFormIsDirty) setFormIsDirty(false);
      onClose();
    }
  };

  const handleConfirmAction = () => {
    if (pendingAction === "back") {
      onBackClick(isPreviouslyConfigured);
    } else if (pendingAction === "close") {
      if (setFormIsDirty) setFormIsDirty(false);
      onClose();
    }
    setConfirmOpen(false);
    setPendingAction(null);
  };

  const {
    mutate: testEval,
    isPending: testingEvalLoading,
    data: testingEvalData,
  } = useMutation({
    mutationFn: (d) =>
      axios.post(endpoints.develop.eval.testEval(datasetId), d),
    onSuccess: () => {
      setShowEvaluation(true);
    },
  });

  const {
    data: evalConfig,
    isLoading,
    isSuccess,
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

  return (
    <Box sx={{ display: "flex", height: "100%" }}>
      <Collapse orientation="horizontal" in={showEvaluation}>
        <EvaluationTest
          testingEvalData={testingEvalData}
          onClose={() => setShowEvaluation(false)}
        />
      </Collapse>
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
          paddingTop: "0px",
        }}
      >
        <Box
          sx={{
            display: "flex",
            justifyContent: "space-between",
            position: "sticky",
            top: 0,
            zIndex: 5,
            backgroundColor: "background.paper",
            py: "10px",
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
            onClick={handleBackClick}
          >
            Back
          </Button>
          <Button variant="soft" size="small" onClick={handleCloseClick}>
            Close
          </Button>
        </Box>
        <Typography fontSize="14px" fontWeight={700} color="text.secondary">
          {selectedEval?.name || evalConfig?.name}
        </Typography>
        {isLoading && <LinearProgress />}
        {isSuccess && (
          <EvaluationConfigureFormChild
            selectedEval={selectedEval}
            evalConfig={evalConfig}
            allColumns={allColumns}
            jsonSchemas={jsonSchemas}
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
            loadingStates={loadingStates}
            setIsFormDirty={setIsFormDirty}
          />
        )}
        <ConfirmDialog
          content="Are you sure you want to close? Your work will be lost"
          title="Confirm Action"
          message="Are you sure you want to proceed?"
          open={confirmOpen}
          onClose={() => {
            setConfirmOpen(false);
            setPendingAction(null);
          }}
          action={
            <Button
              variant="contained"
              color="error"
              size="small"
              onClick={handleConfirmAction}
            >
              Confirm
            </Button>
          }
        />
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
  jsonSchemas: PropTypes.object,
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
  loadingStates: PropTypes.shape({
    saveLoading: PropTypes.bool,
  }),
};

export default EvaluationConfigureForm;
