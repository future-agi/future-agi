import {
  Box,
  Button,
  IconButton,
  InputAdornment,
  Link,
  Skeleton,
  TextField,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useEffect, useMemo, useState } from "react";
import { useFieldArray, useForm } from "react-hook-form";
import { ConfirmDialog } from "../../../components/custom-dialog";
import { LoadingButton } from "@mui/lab";
import Iconify from "../../../components/iconify";
import StepsBox from "../../../components/steps-box/StepsBox";
import FormTextFieldV2 from "../../../components/FormTextField/FormTextFieldV2";
import RadioField from "../../../components/RadioField/RadioField";
import { zodResolver } from "@hookform/resolvers/zod";
import { FormCheckboxField } from "../../../components/FormCheckboxField";
import { ShowComponent } from "../../../components/show";
import SvgColor from "../../../components/svg-color";
import { FormSelectField } from "../../../components/FormSelectField";
import {
  camelCaseToTitleCase,
  extractVariables,
  snakeCaseToTitleCase,
} from "src/utils/utils";
import ExpandedRulePrompt from "./ExpandedRulePrompt";
import HeadingAndSubHeading from "../../../components/HeadingAndSubheading/HeadingAndSubheading";
import { FUTUREAGI_LLM_MODELS, useCases } from "./validation";
import { useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "notistack";
import { useEvaluationContext } from "./context/EvaluationContext";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import logger from "src/utils/logger";
import CustomEvalsPlayground from "./CustomEvalsPlayground";
import CustomModelDropdownControl from "src/components/custom-model-dropdown/CustomModelDropdownControl";
import ConfigureKeysModal from "src/components/ConfigureApiKeysModal/ConfigureKeysModal";
import ConfigRenderer from "./ConfigRenderer";
import { buildFormSchema, useFunctionEvalsList } from "./common";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";

const defaultValues = {
  templateType: "Futureagi",
  name: "",
  criteria: "",
  outputType: "Pass/Fail",
  config: {
    model: "turing_large",
    config: {},
    reverseOutput: false,
  },
  evalTypeId: "",
  templateId: "",
  requiredKeys: [],
  choices: [],
  multiChoice: false,
  tags: [],
  description: "",
  // checkInternet: false,
};

const CustomEvalsFormChild = ({
  onClose,
  handleCloseForm,
  onBack,
  showTest = false,
  hideBackButtons = false,
  hideTitle = false,
  evalsData = null,
  disableOutputType = false,
  disableVariableEdit = false,
  defaultCriteria = "",
  onFormSave,
  saveButtonTitle,
  isEvalConfig = false,
  loadingSaveButton,
  openConfirmDialog,
  setOpenConfirmDialog,
  action,
  setAction,
  isViewMode,
  formHeight = "100vh",
  titleComponent = <></>,
  deleteButon = <></>,
  ...rest
}) => {
  const { role } = useAuthContext();
  const [isApiConfigurationOpen, setIsApiConfigurationOpen] = useState(null);
  const [openPlayground, setOpenPlayground] = useState(false);
  const { data: functionEvalsList, isPending: isFunctionEvalsListPending } =
    useFunctionEvalsList();
  const { control, handleSubmit, watch, formState, setValue, setError, reset } =
    useForm({
      resolver: zodResolver(buildFormSchema(functionEvalsList ?? [])),
      defaultValues: evalsData ? evalsData : defaultValues,
    });
  const [choice, setChoice] = useState("");
  const [expandRulePrompt, setExpandRulePrompt] = useState(false);
  const theme = useTheme();
  const {
    fields: tags,
    append: appendTags,
    remove: removeTag,
  } = useFieldArray({
    control,
    name: "tags",
  });
  const {
    fields: choices,
    append: appendChoices,
    remove: removeChoices,
  } = useFieldArray({
    control,
    name: "choices",
  });
  const criteria = watch("criteria");
  const templateType = watch("templateType");
  const outputType = watch("outputType");
  const config = watch("config.config");
  const evalTypeId = watch("evalTypeId");
  const extractedKeys = useMemo(() => {
    return extractVariables(disableVariableEdit ? defaultCriteria : criteria);
  }, [criteria]);
  const [_, setOpenTest] = useState(false);
  const {
    setVisibleSection,
    setIsDirty,
    setSelectedEval,
    formValues,
    setFormValues,
    setPlaygroundEvaluation,
  } = useEvaluationContext();

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

  const resetForm = () => {
    setIsDirty(false);
    setOpenPlayground(false);
    reset();
  };

  const { mutate: createCustomEval, isPending } = useMutation({
    /**
     *
     * @param {Object} d
     */
    mutationFn: (d) => axios.post(endpoints.develop.eval.createCustomEval, d),
    onSuccess: (data, variables) => {
      enqueueSnackbar({
        message: "Eval added successfully",
        variant: "success",
      });

      const selectedEval = {
        description: variables.description,
        eval_required_keys: variables.required_keys,
        eval_template_name: variables.name,
        eval_template_tags: variables.tags,
        id: data.data.result.eval_template_id,
        is_model_required: false,
        name: variables.name,
        type: "user_built",
      };
      if (rest?.isEvalsView) {
        resetForm();
        handleCloseForm();
        setPlaygroundEvaluation({ ...variables, ...selectedEval });
      } else {
        setSelectedEval(selectedEval);
        setVisibleSection("mapping");
      }
    },
  });

  useEffect(() => {
    setIsDirty(formState.isDirty);
  }, [formState.isDirty]);

  const addChoice = (key, value) => {
    appendChoices({
      key: key,
      value: value,
    });
  };

  const deleteChoice = (index) => {
    removeChoices(index);
  };

  const transformCriteria = () => {
    let criteriaCopy = criteria;
    extractedKeys.forEach((key, index) => {
      const regex = new RegExp(`\\{\\{${key}\\}\\}`, "g");
      criteriaCopy = criteriaCopy.replace(regex, `{{variable_${index + 1}}}`);
    });
    return criteriaCopy;
  };

  const transformChoices = (choices) => {
    const choicesCopy = {};
    choices.forEach((choice) => {
      choicesCopy[choice.key] = choice.value;
    });
    return choicesCopy;
  };

  const transformPayloadKeys = (obj) => {
    const keyMap = {
      templateType: "template_type",
      outputType: "output_type",
      evalTypeId: "eval_type_id",
      templateId: "template_id",
      requiredKeys: "required_keys",
      multiChoice: "multi_choice",
      reverseOutput: "reverse_output",
      checkInternet: "check_internet",
    };
    const result = {};
    Object.keys(obj).forEach((key) => {
      const newKey = keyMap[key] || key;
      result[newKey] = obj[key];
    });
    return result;
  };

  const onSubmit = (data) => {
    try {
      let payload = {};
      if (templateType === "Function") {
        payload = transformPayloadKeys({
          ...data,
          tags: tags.map((tag) => tag.value),
        });
        delete payload["criteria"];
        delete payload["choices"];
      } else {
        const transformedCritera =
          data.templateType === "Futureagi"
            ? transformCriteria()
            : data.criteria;
        payload = transformPayloadKeys({
          ...data,
          requiredKeys: extractedKeys,
          choices: transformChoices(data.choices),
          criteria: transformedCritera,
          tags: tags.map((tag) => tag.value),
        });
      }
      // const filteredPayload = {}  // do not remove these comments
      // Object.keys(payload).forEach((key)=>{
      //   if(Array.isArray(payload[key])) {
      //     if(payload[key].length) {
      //       filteredPayload[key] = payload[key];
      //     }
      //   } else if (typeof payload[key] === 'string') {
      //     if(payload[key] !== ''){
      //       filteredPayload[key] = payload[key];
      //     }
      //   } else {
      //     filteredPayload[key] = payload[key];
      //   }
      // })
      createCustomEval(payload);
    } catch (error) {
      logger.error("Failed to create custom eval", error);
    }
  };

  const onTest = () => {
    if (rest.isEvalsView) {
      setOpenPlayground(true);
    } else {
      setOpenTest(true);
    }
  };

  const handleNameTransformation = (event) => {
    const originalValue = event.target.value;
    const transformedValue = originalValue
      .toLowerCase()
      .replace(/[^a-z0-9-]/g, "_");

    setValue("name", transformedValue);
  };

  const handleAddChoice = () => {
    if (choice !== "") {
      addChoice(choice, "neutral");
      setChoice("");
    }
  };

  const buttonStyles = (isSelected) => {
    const baseStyle = {
      px: 1,
      border: "1px solid",
      borderRadius: theme.spacing(0.5),
    };
    if (isSelected)
      return {
        ...baseStyle,
        color: "primary.main",
        backgroundColor: "action.hover",
        px: 1,
        borderColor: "primary.lighter",
        "&:hover": {
          backgroundColor: "action.hover",
        },
      };
    return {
      ...baseStyle,
      color: "text.primary",
      backgroundColor: "transparent",
      px: 1,
      borderColor: "divider",
      "&:hover": {
        backgroundColor: theme.palette.action.hover,
      },
    };
  };

  const iconColor = (isSelected) =>
    isSelected ? "primary.main" : "text.primary";

  return (
    <React.Fragment>
      <ConfirmDialog
        open={openConfirmDialog}
        onClose={() => {
          setOpenConfirmDialog(false);
          setAction("close");
        }}
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
                setVisibleSection("config");
              } else {
                resetForm();
                handleCloseForm();
                setVisibleSection("list");
              }
            }}
          >
            Close
          </LoadingButton>
        }
      />
      <CustomEvalsPlayground
        open={openPlayground}
        onClose={() => setOpenPlayground(false)}
        formData={formData}
      />
      <form
        style={{
          background: theme.palette.background.paper,
          display: "flex",
          height: `calc(${formHeight}- 30px)`,
          flexDirection: "column",
          paddingBottom: 16,
          gap: theme.spacing(2),
          ...(!rest.fullWidth && { width: "35vw" }),
        }}
        noValidate
        onSubmit={(e) => {
          e.preventDefault();
          handleSubmit(onSubmit)();
        }}
      >
        <ShowComponent condition={!hideBackButtons}>
          <Box display={"flex"} justifyContent={"space-between"}>
            <Button
              onClick={onBack}
              variant="outlined"
              sx={{
                borderRadius: theme.spacing(0.5),
                paddingX: theme.spacing(1.5),
                paddingY: theme.spacing(0.5),
                borderColor: "action.hover",
              }}
              startIcon={
                <Iconify
                  icon="lucide:chevron-left"
                  sx={{
                    width: theme.spacing(2),
                    height: theme.spacing(2),
                    marginRight: "-4px",
                    // background:'red',
                  }}
                />
              }
            >
              <Typography
                typography="s1"
                fontWeight={"fontWeightMedium"}
                sx={
                  {
                    // background:'yellow'
                  }
                }
              >
                Back
              </Typography>
            </Button>
            <IconButton
              onClick={onClose}
              sx={{
                padding: 0,
                paddingY: 0,
              }}
            >
              <Iconify
                icon="line-md:close"
                sx={{
                  width: theme.spacing(4),
                  height: theme.spacing(2),
                  color: "text.primary",
                }}
              />
            </IconButton>
          </Box>
        </ShowComponent>
        {titleComponent}
        <ShowComponent condition={!hideTitle}>
          <Box display="flex" gap={0.5}>
            <Box sx={{ gap: theme.spacing(0.5) }}>
              <Typography
                typography={"m3"}
                color="text.primary"
                fontWeight={"fontWeightSemiBold"}
              >
                Custom Evaluations
              </Typography>
              <Typography
                typography={"s1"}
                marginTop={theme.spacing(0.5)}
                color="text.primary"
              >
                {`Configure your own evaluation using custom metrics and test cases. View `}
                <Link
                  href="https://docs.futureagi.com/docs/cookbook/quickstart/custom-eval-metrics"
                  target="_blank"
                  underline="always"
                >
                  {"docs"}
                </Link>
                {` for more info`}
              </Typography>
            </Box>
            <ShowComponent condition={isViewMode}>
              <IconButton
                onClick={onClose}
                sx={{
                  padding: 0,
                  paddingY: 0,
                  height: theme.spacing(4),
                }}
              >
                <Iconify
                  icon="line-md:close"
                  sx={{
                    width: theme.spacing(4),
                    height: theme.spacing(2),
                    color: "text.primary",
                  }}
                />
              </IconButton>
            </ShowComponent>
          </Box>
        </ShowComponent>
        <Box
          sx={{
            overflow: "auto",
            display: "flex",
            flexDirection: "column",
            gap: theme.spacing(2),
            scrollbarWidth: "none",
            msOverflowStyle: "none",
            "&::-webkit-scrollbar": {
              display: "none",
            },
          }}
        >
          <StepsBox title="Add Details" stepNumber={1}>
            <Box
              paddingX={theme.spacing(1.5)}
              paddingY={theme.spacing(2.25)}
              display={"flex"}
              flexDirection={"column"}
              gap={theme.spacing(2)}
            >
              <FormTextFieldV2
                control={control}
                fullWidth
                size="small"
                fieldName={"name"}
                error={formState.errors.name}
                disabled={rest.disabledName || isViewMode}
                helperText={""}
                required
                label={<Typography typography={"s1"}>Name</Typography>}
                inputProps={{
                  style: {
                    fontSize: theme.typography.s2.fontSize,
                    textTransform: "lowercase",
                  },
                }}
                onChange={handleNameTransformation}
                placeholder={"eg: output-context-context-similarity"}
                defaultValue={undefined}
                onBlur={undefined}
              />
              <ShowComponent condition={!rest.hideModelType}>
                <RadioField
                  control={control}
                  fieldName={"templateType"}
                  label={""}
                  required={true}
                  disabled={isViewMode}
                  optionDirection="horizontal"
                  optionVariant="s1"
                  optionColor="text.primary"
                  options={[
                    { label: "Use Future AGI Agents", value: "Futureagi" },
                    { label: "Use other LLMs", value: "Llm" },
                    { label: "Function based", value: "Function" },
                  ]}
                  groupSx={{
                    padding: 0,
                    margin: 0,
                    gap: theme.spacing(2),
                  }}
                  radioSx={{ padding: 0 }}
                  optionSx={{ gap: theme.spacing(1) }}
                  onChange={() => {
                    setValue("config.model", "");
                    setValue("evalTypeId", "");
                    setValue("templateId", "");
                    setValue("config.config", {});
                  }}
                />
              </ShowComponent>
              {(() => {
                switch (templateType) {
                  case "Futureagi":
                    return (
                      <HeadingAndSubHeading
                        heading={
                          <FormSearchSelectFieldControl
                            control={control}
                            disabled={isViewMode}
                            options={FUTUREAGI_LLM_MODELS.filter(
                              (model) => model.label !== "PROTECT_FLASH",
                            ).map((model) => {
                              return {
                                ...model,
                                component: (
                                  <Box
                                    sx={{
                                      padding: (theme) =>
                                        theme.spacing(0.75, 1),
                                    }}
                                  >
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
                            error={
                              formState.errors?.config?.["model"] &&
                              formState.errors.config["model"].message
                            }
                            fieldName={"config.model"}
                            label={"Language Model"}
                            size={"small"}
                            fullWidth
                            required
                          />
                        }
                        subHeading="The model used for evaluation"
                      />
                    );
                  case "Llm":
                    return (
                      <HeadingAndSubHeading
                        heading={
                          <Box
                            sx={{
                              width: "100%",
                            }}
                          >
                            <ConfigureKeysModal
                              open={Boolean(isApiConfigurationOpen)}
                              selectedModel={isApiConfigurationOpen}
                              onClose={() => setIsApiConfigurationOpen(null)}
                            />
                            <CustomModelDropdownControl
                              control={control}
                              fieldName="config.model"
                              label="Language Model"
                              searchDropdown
                              modelObjectKey={null}
                              size="small"
                              fullWidth
                              excludeCustomProviders
                              onModelConfigOpen={(selectedModel) => {
                                setIsApiConfigurationOpen(selectedModel);
                              }}
                              required
                              inputSx={{
                                "&.MuiInputLabel-root, .MuiInputLabel-shrink": {
                                  fontWeight: "fontWeightMedium",
                                  color: "text.disabled",
                                },
                                "&.Mui-focused.MuiInputLabel-shrink": {
                                  color: "text.disabled",
                                },
                                "& .MuiInputLabel-root.Mui-focused": {
                                  color: "text.secondary",
                                },
                              }}
                              showIcon
                            />
                          </Box>
                        }
                        subHeading="The model used for evaluation"
                      />
                    );
                  default:
                    return (
                      <HeadingAndSubHeading
                        heading={
                          <FormSearchSelectFieldControl
                            control={control}
                            disabled={isViewMode}
                            options={
                              !isFunctionEvalsListPending
                                ? functionEvalsList.map((item) => ({
                                    label: snakeCaseToTitleCase(item.name),
                                    value: item.config.evalTypeId,
                                    component: (
                                      <Box
                                        sx={{
                                          padding: (theme) =>
                                            theme.spacing(0.75, 1),
                                        }}
                                      >
                                        <Typography
                                          typography="s1"
                                          fontWeight={"fontWeightMedium"}
                                          color={"text.primary"}
                                        >
                                          {snakeCaseToTitleCase(item.name)}
                                        </Typography>
                                        <Typography
                                          typography={"s2"}
                                          sx={{
                                            wordWrap: "break-word",
                                            whiteSpace: "normal",
                                          }}
                                          color={"text.primary"}
                                        >
                                          {item.description}
                                        </Typography>
                                      </Box>
                                    ),
                                  }))
                                : [
                                    {
                                      label: "",
                                      value: "",
                                      disabled: true,
                                      component: (
                                        <Skeleton
                                          sx={{ marginY: 1 }}
                                          width={"60%"}
                                        />
                                      ),
                                    },
                                    {
                                      label: "",
                                      value: "",
                                      disabled: true,
                                      component: (
                                        <Skeleton
                                          sx={{ marginY: 1 }}
                                          width={"60%"}
                                        />
                                      ),
                                    },
                                    {
                                      label: "",
                                      value: "",
                                      disabled: true,
                                      component: (
                                        <Skeleton
                                          sx={{ marginY: 1 }}
                                          width={"60%"}
                                        />
                                      ),
                                    },
                                  ]
                            }
                            onChange={(e) => {
                              const evalTypeId = e.target.value;
                              // console.log(functionEvalsList);
                              const selectedFunctionEval =
                                functionEvalsList.filter(
                                  (item) =>
                                    item.config.evalTypeId === evalTypeId,
                                )[0];
                              const config = selectedFunctionEval.config.config;
                              const keys = Object.keys(config);
                              const mapping = {};
                              for (let i = 0; i < keys.length; i++) {
                                const defaultValue = config[keys[i]].default;
                                if (
                                  !!defaultValue &&
                                  defaultValue?.constructor === Object
                                ) {
                                  mapping[keys[i]] =
                                    JSON.stringify(defaultValue);
                                } else {
                                  mapping[keys[i]] = defaultValue;
                                }
                              }
                              setValue(
                                "requiredKeys",
                                selectedFunctionEval.config.required_keys,
                              );
                              setValue("templateId", selectedFunctionEval.id);
                              setValue("config.config", mapping);
                            }}
                            error={
                              formState.errors?.["evalTypeId"] &&
                              formState.errors?.["evalTypeId"].message
                            }
                            fieldName={"evalTypeId"}
                            label={"Function Evals"}
                            size={"small"}
                            fullWidth
                            required
                          />
                        }
                        subHeading={
                          "Choose from our list of function based evaluations"
                        }
                      ></HeadingAndSubHeading>
                    );
                }
              })()}
            </Box>
            {/* <Box></Box> */}
            {/* <DropdownWithSearch></DropdownWithSearch> */}
          </StepsBox>
          <StepsBox title="Configure Parameters" stepNumber={2}>
            <Box
              paddingX={theme.spacing(1.5)}
              paddingY={theme.spacing(2.25)}
              display={"flex"}
              flexDirection={"column"}
              gap={theme.spacing(2)}
              flexWrap={"wrap"}
            >
              {templateType === "Function" ? (
                evalTypeId === "" ? (
                  <HeadingAndSubHeading
                    heading={"No eval selected"}
                    subHeading={"Choose an eval to modify the relevant configs"}
                  />
                ) : (
                  <Box display={"flex"} flexDirection={"column"} gap={2}>
                    {Object.keys(config).map((key, index) => {
                      const selectedEvalDetail = functionEvalsList.filter(
                        (item) => item.config.evalTypeId === evalTypeId,
                      )[0].config;
                      return (
                        <ConfigRenderer
                          key={index}
                          control={control}
                          viewMode={isViewMode}
                          fieldName={`config.config.${key}`}
                          label={camelCaseToTitleCase(key)}
                          type={selectedEvalDetail.config[key]["type"]}
                          description={selectedEvalDetail.configParamsDesc[key]}
                        />
                      );
                    })}
                  </Box>
                )
              ) : (
                <>
                  <HeadingAndSubHeading
                    heading="Rule Prompt"
                    subHeading="Write a prompt defining the specific rules, patterns, or criteria the input must adhere to"
                    required
                  />
                  <FormTextFieldV2
                    control={control}
                    size="medium"
                    fieldName={"criteria"}
                    disabled={isViewMode}
                    error={formState.errors.criteria}
                    label={<Typography typography={"s1"}>Prompt</Typography>}
                    maxRows={7}
                    sx={{
                      height: "150px",
                      "& .MuiInputBase-root": {
                        "& textarea": {
                          overflow: "auto !important",
                        },
                        overflow: "auto",
                      },
                      "& .MuiOutlinedInput-root": {
                        minHeight: "100%",
                      },
                      display: "flex",
                    }}
                    InputProps={{
                      style: {
                        height: "100%",
                        paddingX: theme.spacing(2),
                        paddingY: theme.spacing(1),
                      },
                      inputProps: {
                        style: {
                          minHeight: "100%",
                        },
                      },
                      endAdornment: (
                        <IconButton
                          onClick={() => setExpandRulePrompt(true)}
                          disabled={isViewMode}
                          sx={{
                            position: "absolute",
                            right: 0,
                            bottom: 0,
                          }}
                        >
                          <Iconify
                            icon={"mi:expand"}
                            sx={{
                              width: "16px",
                              height: "16px",
                            }}
                          />
                        </IconButton>
                      ),
                    }}
                    multiline
                    fullWidth
                    helperText={undefined}
                    defaultValue={undefined}
                    onBlur={undefined}
                    showExpand
                    placeholder={
                      "Write a prompt here. Use {{ to create a new variable"
                    }
                  />
                  {formState.errors.criteria && (
                    <Box height={theme.spacing(1)} />
                  )}
                  {extractedKeys.length > 0 && (
                    <Box
                      display={"flex"}
                      gap={theme.spacing(1.5)}
                      alignItems={"center"}
                      flexWrap={"wrap"}
                    >
                      <Typography typography={"s2"}>Variables:</Typography>
                      {extractedKeys.map((key, index) => {
                        return (
                          <Typography
                            key={index}
                            typography={"s2"}
                            sx={{
                              bgcolor: "action.hover",
                              color: "primary.main",
                              fontWeight: "fontWeightMedium",
                              borderRadius: theme.spacing(0.5),
                              paddingY: theme.spacing(0.5),
                              paddingX: theme.spacing(1),
                            }}
                          >
                            {`{{${key}}}`}
                          </Typography>
                        );
                      })}
                    </Box>
                  )}
                  {disableVariableEdit && (
                    <Box
                      sx={{
                        backgroundColor: "blue.o10",
                        padding: "6px 12px",
                        borderRadius: "8px",
                        display: "flex",
                        alignItems: "center",
                        gap: 1.5,
                      }}
                    >
                      <Iconify icon="tabler:info-circle" color="blue.500" />
                      <Typography typography={"s2"}>
                        Rule prompt can be re-written but variables cannot be
                        changed
                      </Typography>
                    </Box>
                  )}
                </>
              )}
            </Box>
          </StepsBox>
          <StepsBox title="Choose Output Type" stepNumber={3}>
            <Box
              paddingX={theme.spacing(1.5)}
              paddingY={theme.spacing(2.25)}
              display={"flex"}
              flexDirection={"column"}
              gap={theme.spacing(2)}
            >
              <HeadingAndSubHeading
                heading="Output Type"
                subHeading="Select your preferred evaluation format"
                required
              />
              <RadioField
                control={control}
                required={true}
                fieldName={"outputType"}
                label={""}
                optionDirection="horizontal"
                optionVariant="s1"
                optionColor="text.primary"
                disabled={disableOutputType || isViewMode}
                options={[
                  { label: "Pass/Fail", value: "Pass/Fail" },
                  {
                    label: "Percentage",
                    value: "score",
                    disabled: templateType === "Function",
                  },
                  {
                    label: "Choices",
                    value: "choices",
                    disabled: templateType === "Function",
                  },
                ]}
                groupSx={{
                  padding: 0,
                  margin: 0,
                  gap: theme.spacing(2),
                }}
                radioSx={{
                  padding: 0,
                }}
                optionSx={{
                  gap: theme.spacing(0.5),
                }}
              />
              <ShowComponent condition={outputType === "score"}>
                <Box
                  display={"flex"}
                  flexDirection={"row"}
                  alignItems={"center"}
                  width={"70%"}
                  gap={theme.spacing(0.5)}
                >
                  <Typography
                    sx={{
                      width: "100%",
                    }}
                    typography={"s1"}
                  >
                    0% will be shown as
                  </Typography>
                  <FormSelectField
                    control={control}
                    fullWidth
                    fieldName={`config.reverseOutput`}
                    size="small"
                    defaultValue={false}
                    disabled={disableOutputType || isViewMode}
                    options={[
                      { label: "Pass", value: true },
                      { label: "Fail", value: false },
                    ]}
                  />
                </Box>
              </ShowComponent>
              <ShowComponent condition={outputType === "choices"}>
                <HeadingAndSubHeading
                  heading="Choices"
                  subHeading="Create a list of predefined options or categories. Used when multi_choice is true."
                />
              </ShowComponent>
              <ShowComponent condition={outputType === "choices"}>
                <Box
                  display={"flex"}
                  justifyContent={"space-between"}
                  gap={theme.spacing(1.5)}
                  alignItems={"center"}
                >
                  <TextField
                    value={choice}
                    fullWidth
                    size="small"
                    label="Choice"
                    disabled={isViewMode}
                    error={!!formState.errors.choices}
                    required={outputType === "choices" && choices.length === 0}
                    placeholder="Type a choice and press enter"
                    onChange={(e) => {
                      setChoice(e.target.value);
                    }}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        handleAddChoice();
                      }
                    }}
                  />
                  <Button
                    variant="outlined"
                    color="primary"
                    disabled={isViewMode}
                    onClick={handleAddChoice}
                    sx={{
                      width: "190px",
                      color: "primary.main",
                      "& .MuiButton-startIcon": {
                        margin: 0,
                        paddingRight: theme.spacing(1),
                      },
                    }}
                    startIcon={
                      <SvgColor
                        src="/assets/icons/action_buttons/ic_add.svg"
                        sx={{
                          height: 20,
                          width: 20,
                          color: "primary.main",
                        }}
                      />
                    }
                  >
                    <Typography
                      typography={"s2"}
                      fontWeight={"fontWeightMedium"}
                    >
                      Add Choice
                    </Typography>
                  </Button>
                </Box>
                {!!formState.errors.choices && (
                  <Typography
                    typography={"s2"}
                    color={"error"}
                    sx={{
                      marginLeft: theme.spacing(1.5),
                      marginTop: theme.spacing(-1),
                    }}
                  >
                    {formState.errors.choices.message}
                  </Typography>
                )}
              </ShowComponent>
              <ShowComponent condition={outputType === "choices"}>
                {choices.map((item, index) => {
                  const currentValue = watch(`choices.${index}.value`);
                  const oldChoice = evalsData?.choices?.some(
                    (choice) => choice.key === item.key,
                  );
                  const isDisabled =
                    (disableOutputType && oldChoice) || isViewMode;

                  return (
                    <Box
                      key={index}
                      alignItems={"center"}
                      gap={theme.spacing(1)}
                      display={"flex"}
                    >
                      <TextField
                        size="small"
                        value={item.key}
                        disabled={isViewMode}
                        fullWidth
                        sx={{
                          color: "text.primary",
                          pointerEvents: "none",
                        }}
                      />
                      <Typography typography="s1" sx={{ minWidth: "105px" }}>
                        Will be shown as
                      </Typography>
                      <FormSearchSelectFieldControl
                        fieldName={`choices.${index}.value`}
                        control={control}
                        fullWidth
                        disabled={isViewMode}
                        size="small"
                        showClear={false}
                        options={[
                          {
                            label: "Neutral",
                            value: "neutral",
                            component: (
                              <Box
                                display={"flex"}
                                flexDirection={"row"}
                                alignItems={"center"}
                                gap={"8px"}
                                sx={{
                                  padding: (theme) => theme.spacing(0.75, 1),
                                }}
                              >
                                <Box
                                  bgcolor={"primary.light"}
                                  sx={{
                                    height: "12px",
                                    width: "12px",
                                    borderRadius: "2px",
                                  }}
                                />
                                <Typography typography="s1" noWrap>
                                  Neutral
                                </Typography>
                              </Box>
                            ),
                          },
                          {
                            label: "Pass",
                            value: "pass",
                            component: (
                              <Box
                                display={"flex"}
                                flexDirection={"row"}
                                alignItems={"center"}
                                gap={"8px"}
                                sx={{
                                  padding: (theme) => theme.spacing(0.75, 1),
                                }}
                              >
                                <Box
                                  bgcolor={"green.200"}
                                  sx={{
                                    height: "12px",
                                    width: "12px",
                                    borderRadius: "2px",
                                  }}
                                />
                                <Typography typography="s1" noWrap>
                                  Pass
                                </Typography>
                              </Box>
                            ),
                          },
                          {
                            label: "Fail",
                            value: "fail",
                            component: (
                              <Box
                                display={"flex"}
                                flexDirection={"row"}
                                alignItems={"center"}
                                gap={"8px"}
                                sx={{
                                  padding: (theme) => theme.spacing(0.75, 1),
                                }}
                              >
                                <Box
                                  bgcolor={"red.200"}
                                  sx={{
                                    height: "12px",
                                    width: "12px",
                                    borderRadius: "2px",
                                  }}
                                />
                                <Typography typography="s1" noWrap>
                                  Fail
                                </Typography>
                              </Box>
                            ),
                          },
                        ]}
                        InputProps={{
                          startAdornment: (
                            <InputAdornment position="start">
                              {currentValue !== "" && (
                                <Box
                                  bgcolor={
                                    currentValue === "neutral"
                                      ? "primary.light"
                                      : currentValue === "pass"
                                        ? "green.200"
                                        : "red.200"
                                  }
                                  sx={{
                                    height: "12px",
                                    width: "12px",
                                    borderRadius: "2px",
                                  }}
                                />
                              )}
                            </InputAdornment>
                          ),
                        }}
                      />
                      <IconButton
                        disabled={isDisabled}
                        onClick={() => deleteChoice(index)}
                      >
                        <SvgColor
                          src="/assets/icons/ic_delete.svg"
                          sx={{
                            height: 20,
                            width: 20,
                            color: isDisabled
                              ? "text.disabled"
                              : "text.primary",
                          }}
                        />
                      </IconButton>
                      {/* <DropdownWithSearch/> */}
                    </Box>
                  );
                })}
                <HeadingAndSubHeading
                  heading={
                    <FormCheckboxField
                      control={control}
                      disabled={isViewMode}
                      label={"Multi choice"}
                      fieldName={"multiChoice"}
                      helperText={undefined}
                      defaultValue={false}
                      labelProps={{
                        gap: theme.spacing(1),
                      }}
                      checkboxSx={{
                        padding: 0,
                        "&.Mui-checked": {
                          color: "primary.light",
                        },
                      }}
                      labelPlacement="end"
                    />
                  }
                  subHeading="Create a list of predefined options or categories. Used when multi_choice is true."
                />
                <Typography
                  typography={"s2"}
                  sx={{ mt: -1 }}
                  color={"text.secondary"}
                  fontStyle={"italic"}
                >
                  <Typography
                    typography={"s2"}
                    fontStyle={"normal"}
                    component={"span"}
                  >
                    NOTE:
                  </Typography>{" "}
                  Evals returns percentages when all deterministic values are
                  between 0 and 1.
                </Typography>
              </ShowComponent>
            </Box>
          </StepsBox>
          <StepsBox title="Optional Fields" stepNumber={4}>
            <Box
              paddingX={theme.spacing(1.5)}
              paddingY={theme.spacing(2.25)}
              display={"flex"}
              flexDirection={"column"}
              gap={theme.spacing(2)}
            >
              <HeadingAndSubHeading
                heading="Tags"
                subHeading="Add a tag to your evaluation"
              />
              <Box display="flex" flexWrap="wrap" gap={theme.spacing(1)}>
                {useCases.map((button, ind) => {
                  const index = tags.findIndex(
                    (tag) => tag.value === button.value,
                  );
                  const isSelected = index !== -1;
                  return (
                    <Button
                      key={ind}
                      size="small"
                      disabled={isViewMode}
                      onClick={() => {
                        if (isSelected) {
                          removeTag(index);
                        } else {
                          appendTags({
                            key: button.title,
                            value: button.value,
                          });
                        }
                      }}
                      startIcon={
                        <SvgColor
                          src={`/assets/icons/evals_use_case/${button.icon}.svg`}
                          sx={{
                            width: "16px",
                            height: "16px",
                            color: iconColor(isSelected),
                          }}
                        />
                      }
                      sx={buttonStyles(isSelected)}
                    >
                      <Typography
                        typography={"s2"}
                        fontWeight={"fontWeightMedium"}
                      >
                        {button.title}
                      </Typography>
                    </Button>
                  );
                })}
              </Box>

              <Box
                gap={theme.spacing(2)}
                display={"flex"}
                flexDirection={"column"}
              >
                <HeadingAndSubHeading
                  heading="Description"
                  subHeading="Enter a description to your evaluation"
                />
                <FormTextFieldV2
                  control={control}
                  size="medium"
                  fieldName={"description"}
                  label={<Typography typography={"s1"}>Description</Typography>}
                  sx={{
                    "& .MuiOutlinedInput-root": {
                      minHeight: "100%",
                    },
                  }}
                  InputProps={{
                    style: {
                      height: "100%",
                      paddingX: theme.spacing(2),
                      paddingY: theme.spacing(1),
                    },
                    inputProps: {
                      style: {
                        minHeight: "100%",
                      },
                    },
                  }}
                  multiline
                  disabled={isViewMode || rest?.disabledDescription}
                  fullWidth
                  rows={5}
                  helperText={undefined}
                  defaultValue={undefined}
                  onBlur={undefined}
                  placeholder={"Enter a description here"}
                />
                {/* <Box
                  gap={theme.spacing(0.5)}
                  display={"flex"}
                  flexDirection={"column"}
                >
                  <FormCheckboxField
                    control={control}
                    fieldName={"checkInternet"}
                    label={"Check Internet"}
                    labelPlacement="end"
                    disabled={isViewMode}
                    helperText={undefined}
                    defaultValue={false}
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
                  <Typography typography={"s2"} color={"text.disabled"}>
                    Whether to check internet for evaluation
                  </Typography>
                </Box> */}
              </Box>
            </Box>
          </StepsBox>
        </Box>
        <Box
          display={"flex"}
          flexDirection={"row"}
          justifyContent={"flex-end"}
          gap={theme.spacing(2)}
        >
          {showTest && (
            <LoadingButton
              sx={{ width: "160px" }}
              variant="outlined"
              onClick={handleSubmit(onTest)}
            >
              <Typography typography={"s2"} fontWeight={"fontWeightMedium"}>
                Test
              </Typography>
            </LoadingButton>
          )}
          {deleteButon}
          {onFormSave ? (
            <LoadingButton
              color="primary"
              variant="contained"
              onClick={(e) => {
                e.preventDefault();
                if (disableVariableEdit) {
                  const newVariable = extractVariables(criteria);
                  const oldCriteria = extractVariables(defaultCriteria);
                  if (
                    JSON.stringify(newVariable) !== JSON.stringify(oldCriteria)
                  ) {
                    setError("criteria", {
                      message: "Please use all varibales in the prompt",
                    });
                    return;
                  }
                }
                handleSubmit(onFormSave)();
              }}
              loading={loadingSaveButton}
              disabled={
                !RolePermission.EVALS[PERMISSIONS.EDIT_CREATE_DELETE_EVALS][
                  role
                ] || !formState.isValid
              }
            >
              <Typography typography="s2" fontWeight="fontWeightMedium">
                {saveButtonTitle || "Save Eval"}
              </Typography>
            </LoadingButton>
          ) : (
            <LoadingButton
              sx={{
                ...(isViewMode && { display: "none" }),
                width: "160px",
                bgcolor: "primary.main",
                ":hover": {
                  bgcolor: "primary.dark",
                },
              }}
              type="submit"
              variant="contained"
              loading={isPending}
            >
              <Typography typography={"s2"} fontWeight={"fontWeightMedium"}>
                Create Evaluation
              </Typography>
            </LoadingButton>
          )}
        </Box>
        <ExpandedRulePrompt
          open={expandRulePrompt}
          control={control}
          onClose={() => setExpandRulePrompt(false)}
          handleSave={() => setExpandRulePrompt(false)}
          fieldName="criteria"
        />
      </form>
    </React.Fragment>
  );
};

CustomEvalsFormChild.propTypes = {
  onClose: PropTypes.func,
  onBack: PropTypes.func,
  showTest: PropTypes.bool,
  hideBackButtons: PropTypes.bool,
  hideTitle: PropTypes.bool,
  evalsData: PropTypes.object,
  disableOutputType: PropTypes.bool,
  onFormSave: PropTypes.func,
  saveButtonTitle: PropTypes.string,
  disableVariableEdit: PropTypes.bool,
  defaultCriteria: PropTypes.string,
  isEvalConfig: PropTypes.bool,
  loadingSaveButton: PropTypes.bool,
  openConfirmDialog: PropTypes.bool,
  setOpenConfirmDialog: PropTypes.func,
  action: PropTypes.string,
  setAction: PropTypes.func,
  handleCloseForm: PropTypes.func,
  isViewMode: PropTypes.bool,
  titleComponent: PropTypes.node,
  deleteButon: PropTypes.node,
  formHeight: PropTypes.string,
};

const CustomEvalsForm = ({ onClose, ...rest }) => {
  const { setVisibleSection, isDirty } = useEvaluationContext();
  const theme = useTheme();
  const [openConfirmDialog, setOpenConfirmDialog] = useState(false);
  const [action, setAction] = useState("close");
  const handleClose = (action) => {
    if (isDirty) {
      setOpenConfirmDialog(true);
    } else {
      if (action === "back") {
        setVisibleSection("config");
      } else {
        onClose();
        setVisibleSection("list");
      }
    }
  };
  return (
    <Box
      display={"flex"}
      flexDirection={rest.isEvalsView ? "row" : "column"}
      gap={theme.spacing(2)}
    >
      <CustomEvalsFormChild
        onClose={() => handleClose("close")}
        handleCloseForm={onClose}
        openConfirmDialog={openConfirmDialog}
        setOpenConfirmDialog={setOpenConfirmDialog}
        action={action}
        setAction={setAction}
        onBack={() => {
          handleClose("back");
          setAction("back");
        }}
        {...rest}
      />
    </Box>
  );
};

export default CustomEvalsForm;

CustomEvalsForm.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
};
