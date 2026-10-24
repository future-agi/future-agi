import { zodResolver } from "@hookform/resolvers/zod";
import {
  Box,
  Button,
  Checkbox,
  IconButton,
  Modal,
  Radio,
  Stack,
  Switch,
  TextField,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useMemo, useState } from "react";
import { Controller, useFieldArray, useForm } from "react-hook-form";
import { useOrganization } from "src/contexts/OrganizationContext";
import { FormCheckboxField } from "src/components/FormCheckboxField";
import { FormSelectField } from "src/components/FormSelectField";
import Iconify from "src/components/iconify";
import SlideNumber from "src/sections/common/SliderRow/SlideNumber";
import { z } from "zod";
import RulePromptInput from "../Common/EvaluationConfigure/RulePromptInput";
import {
  ModalBody,
  ModalHeader,
  ModalTitle,
  ModalWrap,
  PreviewWrap,
} from "./AnnotationStyle";
import { trackEvent, Events, PropertyName } from "src/utils/Mixpanel";
import FormTextLabelField from "src/components/FormTextLabelField/FormTextLabelField";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";

const getValidationSchema = () => {
  let isAutoAnnotated = false;
  let isAnnoType = "numeric";
  let inputs = [];
  return z.object({
    addLabelFields: z
      .object({
        type: z.string().min(1, "Type is required"),
        auto_annotate: z.boolean().default(false),
        name: z.string().min(1, "Label name is required"),
        description: z.string().min(1, "Description is required"),
        displayOption: z.string().optional(),

        min: z
          .string()
          .transform((val) => Number(val))
          .optional(),

        max: z
          .string()
          .transform((val) => Number(val))
          .optional(),

        stepSize: z
          .string()
          .optional()
          .transform((val) => Number(val)),
        promptDesc: z.string().optional(),
        multiChoice: z.boolean().default(false),
        placeholderText: z.string().optional(),
      })
      .superRefine((data, ctx) => {
        const type = data.type;
        isAutoAnnotated = data.auto_annotate;
        isAnnoType = data.type;

        if (type === "text" && !data.placeholderText) {
          ctx.addIssue({
            path: ["placeholderText"],
            message: "Placeholder text is required",
          });
        }

        if (type !== "categorical" && !data.min) {
          ctx.addIssue({
            path: ["min"],
            message: "Min value is required",
          });
        }
        if (type !== "categorical" && !data.max) {
          ctx.addIssue({
            path: ["max"],
            message: "Max value is required",
          });
        }

        if (type === "numeric" && !data.displayOption) {
          ctx.addIssue({
            path: ["displayOption"],
            message: "Display Option is required",
          });
        }

        if (type === "numeric" && !data.stepSize) {
          ctx.addIssue({
            path: ["stepSize"],
            message: "Step size is required",
          });
        }

        if (type === "numeric") {
          const minVal = data.min;
          const maxVal = data.max;
          const stepSize = data.stepSize;

          if (data.min && (isNaN(data.min) || data.min <= 0)) {
            ctx.addIssue({
              path: ["min"],
              message: `Min value must be a non-negative number`,
            });
          }

          if (maxVal && isNaN(maxVal)) {
            ctx.addIssue({
              path: ["max"],
              message: `Max value must be a valid number`,
            });
          }

          if (stepSize && (isNaN(stepSize) || stepSize <= 0)) {
            ctx.addIssue({
              path: ["stepSize"],
              message: `Step size must be a positive number`,
            });
          }

          if (maxVal <= minVal) {
            ctx.addIssue({
              path: ["max"],
              message: "Max value must be greater than Min",
            });
          }

          if (stepSize <= 0 || stepSize > maxVal - minVal) {
            ctx.addIssue({
              path: ["stepSize"],
              message: `Step size must lie between 0 and (Max - Min)`,
            });
          }
        } else if (type === "text") {
          const minVal = data.min;
          const maxVal = data.max;

          if (minVal && (isNaN(minVal) || minVal <= 0)) {
            ctx.addIssue({
              path: ["min"],
              message: `Min value must be a non-negative number`,
            });
          }

          if (maxVal && isNaN(maxVal)) {
            ctx.addIssue({
              path: ["max"],
              message: `Max value must be a valid number`,
            });
          }

          if (maxVal <= minVal) {
            ctx.addIssue({
              path: ["max"],
              message: "Max value must be greater than Min",
            });
          }
        }
      }),
    addLabelInputOptions: z
      .array(
        z.object({
          label: z.string().min(1, "Column is required"),
        }),
      )
      .superRefine((data, ctx) => {
        inputs = data;
        if (isAutoAnnotated && data.length == 0) {
          ctx.addIssue({
            path: [""],
            message: "Inputs are required",
          });
        }
      }),
    addLabelOptions: z
      .array(
        z.object({
          label: z.string().min(1, "Label is required"),
        }),
      )
      .superRefine((data, ctx) => {
        if (isAnnoType == "categorical" && data.length <= 1) {
          ctx.addIssue({
            path: [""],
            message: "Minimum 2 choices are required",
          });
        }
      }),
    config: z.object({
      config: z
        .object({
          RulePrompt: z.string(),
          ruleStringValue: z.array(
            z.object({
              value: z.string().min(1, "Value is required"),
            }),
          ),
        })
        .superRefine((data, ctx) => {
          if (isAutoAnnotated && data?.RulePrompt?.length == 0) {
            ctx.addIssue({
              path: ["RulePrompt"],
              message: "Rule Prompt is required",
            });
          }
          for (let i = 0; i < inputs?.length; i++) {
            const placeholder = `{{${inputs[i]?.label}}}`;

            if (!data?.RulePrompt.includes(placeholder)) {
              ctx.addIssue({
                path: ["RulePrompt"],
                message: "Variables added should be used in the rule prompt",
              });
            }
          }
        }),
    }),
  });
};

const LabelModal = ({
  open,
  onClose,
  columnData,
  modalLabel,
  handleLabels,
}) => {
  const theme = useTheme();
  const { currentOrganizationId } = useOrganization();

  const schema = useMemo(() => getValidationSchema(), []);

  const {
    control,
    watch: isWatch,
    handleSubmit,
    reset,
    setValue,
    formState: { isDirty, errors },
  } = useForm({
    defaultValues: {
      addLabelFields: {
        name: "",
        type: "numeric",
        displayOption: "",
        min: "",
        max: "",
        stepSize: "",
        placeholderText: "",
        description: "",
        promptDesc: "",
        multiChoice: false,
        auto_annotate: false,
      },
      addLabelOptions: [],
      addLabelInputOptions: [],
      config: {
        config: {
          RulePrompt: "",
          ruleStringValue: [],
        },
      },
    },
    resolver: zodResolver(schema),
  });

  const {
    fields: addLabelOptions,
    append: addLabelAppend,
    remove: addLabelRemove,
    update: updateLabel,
  } = useFieldArray({
    control,
    name: "addLabelOptions",
  });
  const {
    fields: addLabelInputOptions,
    append: addLabelInputAppend,
    remove: addLabelInputRemove,
    update: updateInputLabel,
  } = useFieldArray({
    control,
    name: "addLabelInputOptions",
  });

  const formHandle = {
    addLabelOptions: addLabelOptions,
    addLabelAppend: addLabelAppend,
    updateLabel: updateLabel,
    addLabelRemove: addLabelRemove,
    addLabelItems: "addLabelOptions",
    addLabelInputOptions: addLabelInputOptions,
    addLabelInputAppend: addLabelInputAppend,
    addLabelInputRemove: addLabelInputRemove,
    updateInputLabel: updateInputLabel,
    addLabelInputItems: "addLabelInputOptions",
    setValue: setValue,
    reset: reset,
  };
  const [step, setStep] = useState(1);
  const [, setIsAutoAnnotate] = useState(false);
  const [rulePromptData, setRulePromptData] = useState("");
  const [renderInput, setRenderInput] = useState({});
  const labelType = isWatch("addLabelFields.type");
  const labelHeader = isWatch("addLabelFields.name");
  const displayOpt = isWatch("addLabelFields.displayOption");
  const placeholderText = isWatch("addLabelFields.placeholderText");
  const minVal = isWatch("addLabelFields.min");
  const maxVal = isWatch("addLabelFields.max");
  const labelOpts = isWatch("addLabelOptions");
  const autoAnnotateValue = isWatch("addLabelFields.auto_annotate");
  const [isRulePrompt, setIsRulePrompt] = useState(false);
  const multiChoice = isWatch("addLabelFields.multiChoice");
  const addLabelInputitemsWatch = isWatch("addLabelInputOptions");
  const configPropt = isWatch("config.config.RulePrompt");

  const [currentLabel, setCurrentLabel] = useState("");

  const handleSaveLabel = () => {
    if (currentLabel.trim() !== "") {
      formHandle.addLabelAppend({ label: currentLabel, selected: false });
      setCurrentLabel("");
    }
  };

  // Checkbox Change Handler
  const handleOptionChange = (index, checked) => {
    formHandle.updateLabel(index, {
      ...formHandle.addLabelOptions[index],
      selected: checked,
    });
  };

  // Radio Change Handler
  const handleRadioChange = (index) => {
    const updatedOptions = formHandle.addLabelOptions.map((option, idx) => ({
      ...option,
      selected: idx === index,
    }));
    updatedOptions.forEach((option, idx) => {
      formHandle.updateLabel(idx, option);
    });
  };

  const allColumns = useMemo(() => {
    return addLabelInputitemsWatch?.map((item) => ({
      field: item.label,
      headerName: item.label,
    }));
  }, [addLabelInputitemsWatch, renderInput]);

  const fieldConfig = {};
  const config = {
    configParamsDesc: {
      RulePrompt: "The rule prompt to be evaluated",
    },
  };
  const configKey = "RulePrompt";
  const ruleStringKey = "ruleStringValue";

  const [, setChoiceValidate] = useState({ validate: false });

  const handleLabel = (payload) => {
    const {
      type,
      name,
      placeholderText,
      max,
      min,
      stepSize,
      displayOption,
      multiChoice,
      auto_annotate,
    } = payload.addLabelFields;

    if (type === "text") {
      const textData = {
        name,
        type,
        organization: currentOrganizationId,
        settings: {
          placeholder: placeholderText.slice(0, +max),
          max_length: +max,
          min_length: +min,
        },
      };

      handleLabels(textData);
      trackEvent(Events.annNewLabelCreate, {
        [PropertyName.type]: "Text Label",
      });
      // trackEvent(Events.annTextLabelCreate, {
      //   [PropertyName.click]: textData,
      // });
    } else if (type === "numeric") {
      const numericData = {
        name,
        type,
        organization: currentOrganizationId,
        settings: {
          display_type: displayOption,
          max: +max,
          min: +min,
          step_size: +stepSize,
        },
      };

      handleLabels(numericData);
      trackEvent(Events.annNewLabelCreate, {
        [PropertyName.type]: "Numerice Label",
      });
      // trackEvent(Events.annNumLabelCreate, {
      //   [PropertyName.click]: numericData,
      // });
    } else if (type === "categorical") {
      const placeholderRegex = /{{(.*?)}}/g;
      const result = configPropt.replace(placeholderRegex, (_, label) => {
        const column = columnData[0].staticFieldColumn.find(
          (col) => col.label === label.trim(),
        );
        return column ? `{{${column.value}}}` : `{{${label}}}`;
      });

      const categoryData = {
        name,
        type,
        organization: currentOrganizationId,
        settings: {
          rule_prompt: result || "",
          multi_choice: multiChoice || false,
          few_shot: [],
          options: addLabelOptions
            ?.filter((i) => i.label && i.label.trim() !== "")
            .map((i) => ({ label: i.label })),
          strategy: "Rag",
          query: "Query for RAG",
          query_col: "",
          auto_annotate: auto_annotate || false,
          inputs: isWatch("addLabelInputOptions").map((i) => {
            const column = columnData[0].staticFieldColumn.find(
              (col) => col.label === i.label,
            );
            return column ? column.value : i.label;
          }),
        },
      };

      handleLabels(categoryData);
      trackEvent(Events.annNewLabelCreate, {
        [PropertyName.type]: "Categorical Label",
      });
      // trackEvent(Events.annCatLabelCreate, {
      //   [PropertyName.click]: categoryData,
      // });
    }
    setChoiceValidate({ validate: false });
    reset();
    onClose();
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      sx={{
        "& .MuiModal-backdrop": {
          backgroundColor: "rgba(0, 0, 0, 0.25)",
        },
      }}
    >
      <Box sx={style.wrapper}>
        <ModalWrap>
          <ModalHeader>
            <ModalTitle color="text.primary">{modalLabel}</ModalTitle>
            <IconButton
              sx={{ p: "4px" }}
              onClick={() => {
                reset();
                onClose();
              }}
            >
              <Iconify icon="solar:close-linear" color="text.secondary" />
            </IconButton>
          </ModalHeader>
          <ModalBody>
            <form
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                }
              }}
              onSubmit={(e) => {
                handleSubmit(handleLabel)(e);
              }}
            >
              <Box sx={{ marginBottom: "25px" }}>
                <FormTextFieldV2
                  control={control}
                  fieldName="addLabelFields.name"
                  label="Name"
                  size="small"
                  sx={{
                    fontSize: "12px",
                  }}
                  // variant=""
                  fullWidth
                  placeholder="Type here..."
                  helperText={null}
                />
              </Box>
              <Stack
                direction="row"
                flexWrap="wrap"
                rowGap="20px"
                columnGap="13px"
              >
                <Box sx={{ flex: "0 0 100%" }}>
                  <FormSelectField
                    control={control}
                    fieldName={`addLabelFields.type`}
                    label="Annotation Type"
                    size="small"
                    options={[
                      { value: "numeric", label: "Numeric" },
                      { value: "text", label: "Text" },
                      { value: "categorical", label: "Categorical" },
                    ]}
                    fullWidth
                  />
                  {
                    <Box sx={{ marginTop: "10px" }}>
                      <FormTextLabelField
                        label={"Description"}
                        labelStyle={{
                          fontSize: "12px",
                          color: "text.secondary",
                          marginBottom: "7px",
                          marginTop: "20px",
                        }}
                        control={control}
                        fieldName={`addLabelFields.description`}
                        multiline={true}
                        rows={3}
                        sx={{
                          ...style.description,
                          ...style.hoverOutline,
                        }}
                        placeholder="Enter description here"
                      />
                    </Box>
                  }
                </Box>

                {labelType === "numeric" && (
                  <Box sx={{ flex: "0 0 100%" }}>
                    <FormSelectField
                      control={control}
                      fieldName={`addLabelFields.displayOption`}
                      label="Display Options"
                      size="small"
                      options={[{ value: "slider", label: "Slider" }]}
                      fullWidth
                    />
                  </Box>
                )}
                {labelType === "text" && (
                  <Box sx={{ flex: "0 0 100%" }}>
                    <FormTextFieldV2
                      control={control}
                      fieldName={`addLabelFields.placeholderText`}
                      label="Type placeholder text here"
                      size="small"
                      fullWidth
                      helperText={null}
                    />
                  </Box>
                )}
                {labelType === "categorical" ? (
                  <Box
                    sx={{
                      flex: "0 0 100%",
                      border: "1px solid gray",
                      borderColor: "divider",
                      padding: "10px 0px",
                      borderRadius: "6px",
                    }}
                  >
                    <Box
                      display={"flex"}
                      justifyItems={"center"}
                      alignItems={"center"}
                    >
                      <Stack
                        direction="row"
                        spacing={1}
                        sx={{ alignItems: "center" }}
                      >
                        <Controller
                          name="addLabelFields.auto_annotate"
                          control={control}
                          render={({ field }) => (
                            <Switch
                              {...field}
                              checked={autoAnnotateValue}
                              inputProps={{ "aria-label": "controlled" }}
                              sx={{ ...style.switch }}
                            />
                          )}
                        />
                      </Stack>
                      <Typography
                        fontSize={12}
                        color="text.primary"
                        fontWeight={500}
                      >
                        Auto Annotate
                      </Typography>
                      <Typography
                        fontSize={12}
                        color="text.primary"
                        fontWeight={500}
                      >
                        &nbsp;(automate your data labelling process)
                      </Typography>
                    </Box>

                    <Box sx={{ padding: "15px 15px" }}>
                      <Stack
                        direction="row"
                        alignItems="center"
                        justifyContent="space-between"
                        // mb="16px"
                      >
                        {autoAnnotateValue && (
                          <Box width="100%" marginBottom={2}>
                            <Box>
                              <Box
                                display={"flex"}
                                justifyContent={"space-between"}
                              >
                                <Typography
                                  color="text.primary"
                                  sx={{
                                    fontWeight:
                                      theme.typography.fontWeightMedium,
                                    fontSize: "13px",
                                  }}
                                >
                                  Input*
                                </Typography>
                              </Box>
                              <Box
                                display={"flex"}
                                justifyItems={"center"}
                                alignItems={"center"}
                                my={0.5}
                              >
                                <Typography
                                  fontSize={12}
                                  color="text.secondary"
                                >
                                  These are the scores or annotation labels that
                                  an annotator will be able to assign to the
                                  responses
                                </Typography>
                              </Box>
                            </Box>

                            <Box sx={{ maxHeight: "180px", overflow: "auto" }}>
                              {formHandle.addLabelInputOptions.map(
                                (field, idx) => (
                                  <Box
                                    key={field?.id}
                                    display="flex"
                                    gap={1}
                                    alignItems="center"
                                    sx={{ marginBottom: 1, marginTop: 0.8 }}
                                    justifyContent={"space-between"}
                                  >
                                    <Typography
                                      fontSize={13}
                                      color="text.primary"
                                    >
                                      Variable_{idx + 1}
                                    </Typography>

                                    <Box
                                      display={"flex"}
                                      gap={1}
                                      alignItems={"center"}
                                      width={"35%"}
                                    >
                                      <FormSelectField
                                        control={control}
                                        fieldName={`addLabelInputOptions.${idx}.label`} // Path to each field's 'label'
                                        label="Response"
                                        size="small"
                                        options={columnData[0]?.staticFieldColumn.map(
                                          (i) => ({
                                            label: i.label,
                                            value: i.label,
                                          }),
                                        )}
                                        fullWidth
                                        onChange={(e) => {
                                          setIsAutoAnnotate(false);
                                          setRenderInput(e);
                                        }}
                                      />
                                      <IconButton
                                        onClick={() =>
                                          formHandle.addLabelInputRemove(idx)
                                        }
                                      >
                                        <Iconify
                                          icon="solar:trash-bin-trash-linear"
                                          color="text.secondary"
                                        />
                                      </IconButton>
                                    </Box>
                                  </Box>
                                ),
                              )}
                            </Box>

                            <Button
                              color="primary"
                              variant="contained"
                              sx={{
                                display: "flex",
                                gap: 0.2,
                              }}
                              onClick={() =>
                                formHandle.addLabelInputAppend({ label: "" })
                              }
                            >
                              <Iconify icon="solar:add-linear" />
                              <Typography
                                fontSize={12}
                                sx={{
                                  fontWeight:
                                    theme.typography.fontWeightRegular,
                                  textAlign: "center",
                                  verticalAlign: "center",
                                  mt: 0.4,
                                }}
                              >
                                Add Input
                              </Typography>
                            </Button>

                            <Typography
                              fontSize={12}
                              color="status.danger.fg"
                              marginLeft="7px"
                              marginTop="5px"
                            >
                              {errors?.addLabelInputOptions?.message}
                            </Typography>
                          </Box>
                        )}
                      </Stack>

                      <Stack
                        direction="row"
                        alignItems="center"
                        justifyContent="space-between"
                        // mb="16px"
                      >
                        <Box>
                          <Typography
                            color="text.primary"
                            sx={{
                              fontWeight: theme.typography.fontWeightMedium,
                              fontSize: "13px",
                            }}
                          >
                            Choices*
                          </Typography>
                          <Box
                            display={"flex"}
                            justifyItems={"center"}
                            alignItems={"center"}
                          >
                            <Typography
                              mt={0.3}
                              fontSize={12}
                              color="text.secondary"
                            >
                              The Choices for the multiple choice question
                            </Typography>
                          </Box>
                        </Box>
                      </Stack>

                      {/*input horizontal */}
                      <Box sx={{ padding: "15px 0px" }}>
                        <Stack
                          direction="row"
                          alignItems="flex-start"
                          justifyContent="space-between"
                          mb="16px"
                          gap={1}
                        >
                          <TextField
                            value={currentLabel}
                            onChange={(e) => setCurrentLabel(e.target.value)} // Handle input change
                            sx={{ width: "100%" }}
                            placeholder="Type of choice and press enter"
                            size="small"
                            onKeyDown={(e) => {
                              if (e.key === "Enter") {
                                handleSaveLabel();
                              }
                            }}
                          />

                          <Button
                            color="primary"
                            variant="contained"
                            onClick={handleSaveLabel}
                          >
                            Add
                          </Button>
                        </Stack>
                        <Typography
                          fontSize={12}
                          color="status.danger.fg"
                          marginLeft="7px"
                          marginTop="5px"
                        >
                          {errors?.addLabelOptions?.message}
                        </Typography>
                        {/* Render Labels with Delete Option */}
                        {formHandle.addLabelOptions.map((field, index) => {
                          if (field?.label !== "") {
                            return (
                              <Box
                                key={field?.id}
                                display={"flex"}
                                justifyContent={"space-between"}
                                alignItems={"center"}
                              >
                                <Typography
                                  sx={{
                                    fontWeight:
                                      theme.typography.fontWeightRegular,
                                  }}
                                  fontSize={13}
                                >
                                  {field?.label}
                                </Typography>

                                <IconButton
                                  onClick={() =>
                                    formHandle.addLabelRemove(index)
                                  }
                                >
                                  <Iconify
                                    icon="solar:trash-bin-trash-linear"
                                    color="text.secondary"
                                  />
                                </IconButton>
                              </Box>
                            );
                          }
                        })}
                      </Box>

                      {autoAnnotateValue && (
                        <div
                          style={{
                            position: "relative",
                            maxWidth: "100%",
                            margin: "0 auto",
                          }}
                        >
                          <RulePromptInput
                            setRulePromptData={setRulePromptData}
                            rulePromptData={rulePromptData}
                            isRulePrompt={isRulePrompt}
                            setIsRulePrompt={setIsRulePrompt}
                            control={control}
                            fieldConfig={fieldConfig}
                            config={config}
                            configKey={configKey}
                            ruleStringKey={ruleStringKey}
                            allColumns={allColumns}
                            isAnnotation={true}
                          />
                        </div>
                      )}

                      <Stack
                        direction="row"
                        alignItems="center"
                        justifyContent="space-between"
                        mb="5px"
                        mt="10px"
                      >
                        <Box>
                          <Box>
                            <FormCheckboxField
                              control={control}
                              fieldName={"addLabelFields.multiChoice"}
                              helperText={""}
                              label={
                                <Typography
                                  fontSize={13}
                                  sx={{
                                    fontWeight:
                                      theme.typography.fontWeightMedium,
                                  }}
                                >
                                  Multi Choice*
                                </Typography>
                              }
                            />
                          </Box>
                          <Box
                            display={"flex"}
                            justifyItems={"center"}
                            alignItems={"center"}
                          >
                            <Typography fontSize={12} color="text.secondary">
                              Whether the output is a multiple choice question
                              or not
                            </Typography>
                          </Box>
                        </Box>
                      </Stack>
                    </Box>
                  </Box>
                ) : (
                  <>
                    {labelType && (
                      <>
                        <Box sx={{ flex: 1 }}>
                          <FormTextFieldV2
                            control={control}
                            placeholder="Enter min value"
                            fieldName={`addLabelFields.min`}
                            label="Min"
                            defaultValue={0}
                            size="small"
                            type="number"
                            helperText={null}
                            fullWidth
                          />
                        </Box>
                        <Box sx={{ flex: 1 }}>
                          <FormTextFieldV2
                            control={control}
                            fieldName={`addLabelFields.max`}
                            placeholder="Enter max value"
                            helperText={null}
                            label="Max"
                            size="small"
                            type="number"
                            fullWidth
                          />
                        </Box>
                      </>
                    )}
                  </>
                )}
                {displayOpt === "slider" && labelType === "numeric" && (
                  <Box sx={{ flex: "0 0 100%" }}>
                    <FormTextFieldV2
                      control={control}
                      fieldName={`addLabelFields.stepSize`}
                      placeholder="Enter stepsize"
                      label="Step Size"
                      helperText={null}
                      size="small"
                      type="number"
                      fullWidth
                      onChange={(e) => setStep(e?.target?.value)}
                    />
                  </Box>
                )}
              </Stack>
              <PreviewWrap>
                <Box
                  sx={{
                    px: "20px",
                    py: "14px",
                    borderBottom: `1px solid ${theme.palette.background.neutral}`,
                  }}
                >
                  <Typography
                    color="text.primary"
                    sx={{
                      fontSize: "13px",
                      fontWeight: theme.typography.fontWeightBold,
                    }}
                  >
                    Preview
                  </Typography>
                </Box>
                <Box sx={{ p: "24px", minHeight: "164px" }}>
                  {displayOpt === "slider" && labelType === "numeric" && (
                    <SlideNumber
                      label={labelHeader || ""}
                      // control={control}
                      // fieldName={`addLabelFields.stepSize`}
                      min={minVal}
                      max={maxVal}
                      step={step}
                    />
                  )}
                  {labelType === "text" && (
                    <Stack direction="row" gap="20px">
                      <Box flexShrink={0}>
                        <Typography
                          color="text.primary"
                          sx={{
                            fontSize: "13px",
                            fontWeight: theme.typography.fontWeightRegular,
                          }}
                        >
                          {labelHeader ? `${labelHeader} :` : "Test Label :"}
                        </Typography>
                      </Box>
                      <Box flexGrow={1}>
                        <Box
                          sx={{
                            width: "100%",
                            minHeight: "89px",
                            backgroundColor: "action.hover",
                            borderRadius: "8px",
                            p: "8px",
                            mb: "6px",
                          }}
                        >
                          <Typography
                            color="text.primary"
                            sx={{
                              fontSize: "13px",
                              fontWeight: theme.typography.fontWeightRegular,
                            }}
                          >
                            {placeholderText.slice(
                              0,
                              maxVal || placeholderText.length,
                            )}
                          </Typography>
                        </Box>
                        {minVal && (
                          <Typography
                            color="text.secondary"
                            sx={{
                              pl: "7px",
                              fontSize: "12px",
                              fontWeight: theme.typography.fontWeightRegular,
                            }}
                          >
                            {minVal}/{maxVal}
                          </Typography>
                        )}
                      </Box>
                    </Stack>
                  )}
                  {labelType === "categorical" && (
                    <Stack gap="10px">
                      <Box flexGrow={1}>
                        <Stack borderRadius="8px">
                          {(labelOpts || []).map((field, index) => {
                            return (
                              <>
                                {field?.label !== "" && (
                                  <Box key={field?.label} display={"flex"}>
                                    {/* Conditionally Render Checkbox or Radio */}
                                    {multiChoice ? (
                                      <Checkbox
                                        checked={field?.selected}
                                        onChange={(e) =>
                                          handleOptionChange(
                                            index,
                                            e.target.checked,
                                          )
                                        }
                                      />
                                    ) : (
                                      <Radio
                                        checked={field?.selected}
                                        onChange={() =>
                                          handleRadioChange(index)
                                        }
                                      />
                                    )}
                                    <Typography
                                      color="text.primary"
                                      sx={{
                                        px: "16px",
                                        py: "6px",
                                        fontSize: "13px",
                                      }}
                                      fontWeight={400}
                                    >
                                      {field?.label}
                                    </Typography>
                                  </Box>
                                )}
                              </>
                            );
                          })}
                        </Stack>
                      </Box>
                    </Stack>
                  )}
                </Box>
              </PreviewWrap>
              <Box
                sx={{
                  display: "flex",
                  justifyContent: "flex-end",
                  gap: "12px",
                  pt: "22px",
                }}
              >
                <Button
                  onClick={() => {
                    reset();
                    onClose();
                  }}
                  size="small"
                  variant="outlined"
                  sx={{
                    width: "100%",
                  }}
                >
                  Cancel
                </Button>
                <Button
                  type="submit"
                  size="small"
                  variant="contained"
                  color="primary"
                  sx={{
                    width: "100%",
                  }}
                  disabled={!isDirty}
                >
                  Save
                </Button>
              </Box>
            </form>
          </ModalBody>
        </ModalWrap>
      </Box>
    </Modal>
  );
};

LabelModal.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  columnData: PropTypes.any,
  modalLabel: PropTypes.any,
  handleLabels: PropTypes.any,
};

export default LabelModal;

const style = {
  wrapper: {
    display: "flex",
    flexWrap: "wrap",
    alignItems: "center",
    justifyContent: "center",
    overflow: "auto",
    height: "100%",
    padding: "15px",
  },
  description: {
    backgroundColor: "action.hover",
    overflow: "hidden",
    borderRadius: "6px",
    width: "100%",
    // border: "1px solid gray",
    // borderColor: grey["300"],
  },
  hoverOutline: {
    "& .MuiOutlinedInput-root": {
      "&:hover .MuiOutlinedInput-notchedOutline": {
        borderColor: "transparent",
      },
      "&.Mui-focused .MuiOutlinedInput-notchedOutline": {
        borderColor: "transparent",
      },
      "& .MuiOutlinedInput-notchedOutline": {
        borderColor: "transparent",
      },
    },
  },
  switch: {
    "& .MuiSwitch-switchBase.Mui-checked": {
      color: "primary.main",
    },
    "& .MuiSwitch-switchBase.Mui-checked + .MuiSwitch-track": {
      backgroundColor: "success.main",
    },
    "& .MuiSwitch-switchBase": {
      color: "text.disabled",
    },
    "& .MuiSwitch-track": {
      backgroundColor: "action.disabledBackground",
    },
  },
};
