import {
  DialogContent,
  FormControl,
  FormControlLabel,
  Radio,
  RadioGroup,
  Stack,
  Typography,
  useTheme,
  Box,
} from "@mui/material";
import { z } from "zod";
import { zodResolver } from "@hookform/resolvers/zod";
import React, { useEffect } from "react";
import PromptModalWrapper from "./PromptModalWrapper";
import { Controller, useForm } from "react-hook-form";
import PropTypes from "prop-types";
import { useRunPromptOptions } from "src/api/develop/develop-detail";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "notistack";
import _ from "lodash";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { arrayToLabelValueMap } from "../common";

const EXCLUDED_CONFIG_KEYS = new Set([
  "messages",
  "run_prompt_config",
  "runType",
  "concurrency",
]);

const savePromptSchema = z
  .object({
    saveType: z.enum(["new_version", "new_prompt"]),
    promptName: z.string().optional(),
  })
  .superRefine((data, ctx) => {
    if (data.saveType === "new_prompt" && !data.promptName?.trim()) {
      ctx.addIssue({
        path: ["promptName"],
        code: z.ZodIssueCode.custom,
        message: "Prompt name is required",
      });
    }
  });

export default function SavePromptModal({
  open,
  onClose,
  promptData,
  promptId,
  saveAndRunMode,
  onSaveSuccess,
  handleUpdateImportedPrompt,
  modelParameters,
  reasoningState,
  setInitialImportedData,
}) {
  const theme = useTheme();
  const queryClient = useQueryClient();
  const {
    control,
    handleSubmit,
    watch,
    formState: { isValid },
    setValue,
    reset,
  } = useForm({
    defaultValues: {
      saveType: "new_version",
      promptName: "",
    },
    resolver: zodResolver(savePromptSchema),
    mode: "onChange",
  });
  const { data: runPromptOptions } = useRunPromptOptions();
  const availableTools = runPromptOptions?.available_tools;

  const saveType = watch("saveType");

  useEffect(() => {
    if (saveType === "new_version") {
      setValue("promptName", "");
    }
  }, [saveType, setValue]);
  const isNewPrompt = saveType === "new_prompt";

  const { mutate: handleSaveAsNewPrompt, isPending: isSavingNewPrompt } =
    useMutation({
      mutationFn: ({ id, data }) => {
        return axios.post(
          endpoints.develop.runPrompt.runTemplatePrompt(id),
          data,
        );
      },
      onSuccess: (data) => {
        const promptName = data?.data?.result?.promptName;
        const templateVersion = data?.data?.result?.createdVersion;
        const promptConfigData = data?.data?.result?.promptConfig?.[0];

        enqueueSnackbar("New version created successfully.", {
          variant: "success",
        });
        onClose();
        reset();
        // update prompt versions list
        queryClient.invalidateQueries({
          queryKey: ["versions", promptId],
        });

        // Reset dirty baseline to current form state after successful save
        if (typeof setInitialImportedData === "function") {
          const copy = _.cloneDeep(promptData);
          delete copy?.name;
          delete copy?.config?.concurrency;
          setInitialImportedData(copy);
        }

        if (saveAndRunMode && typeof onSaveSuccess === "function") {
          onSaveSuccess();
        } else {
          // update prompt selected prompt
          if (promptName && templateVersion && promptId && promptConfigData) {
            handleUpdateImportedPrompt({
              promptName,
              templateVersion,
              promptId,
              promptConfigData,
            });
          }
        }
      },
      onError: () => {},
    });

  const { mutate: createDraft, isPending: isLoadingCreate } = useMutation({
    mutationFn: (body) =>
      axios.post(endpoints.develop.runPrompt.createPromptDraft, {
        ...body,
        is_draft: false,
      }),
    onSuccess: (data) => {
      const newPromptId = data?.data?.result?.id;
      const promptName = data?.data?.result?.name;
      const templateVersion = data?.data?.result?.createdVersion;
      const promptConfigData = data?.data?.result?.promptConfig?.[0];
      enqueueSnackbar("Prompt created successfully.", {
        variant: "success",
      });
      onClose();
      reset();
      // update prompt list
      queryClient.invalidateQueries({
        queryKey: ["prompts"],
      });
      // update prompt versions list
      // need new prompt id to update
      if (newPromptId) {
        queryClient.invalidateQueries({
          queryKey: ["versions", newPromptId],
        });
      }

      // Reset dirty baseline to current form state after successful save
      if (typeof setInitialImportedData === "function") {
        const copy = _.cloneDeep(promptData);
        delete copy?.name;
        delete copy?.config?.concurrency;
        setInitialImportedData(copy);
      }

      if (saveAndRunMode && typeof onSaveSuccess === "function") {
        onSaveSuccess();
      } else {
        // update prompt selected prompt
        if (promptName && templateVersion && newPromptId && promptConfigData) {
          handleUpdateImportedPrompt({
            promptName,
            templateVersion,
            promptId: newPromptId,
            promptConfigData,
          });
        }
      }
    },
  });

  const handleSavePrompt = (data) => {
    const tools = availableTools?.filter((tool) =>
      promptData?.config?.tools?.includes(tool.id),
    );

    // Extract model parameters values from sliders, booleans, and dropdowns
    const modelParametersOBJ = modelParameters?.sliders?.reduce((acc, item) => {
      acc[item.label] = item.value;
      return acc;
    }, {});

    const voiceId = promptData?.config?.run_prompt_config?.voiceId;
    const runPromptConfig = { ...promptData?.config?.run_prompt_config };
    delete runPromptConfig.voiceId;

    const savePromptData = {
      is_run: null,
      name: promptData?.config?.prompt,
      variable_names: {},
      placeholders: [],
      evaluation_configs: [],
      source: "dataset",
      prompt_config: [
        {
          configuration: {
            ..._.omit(promptData?.config, Array.from(EXCLUDED_CONFIG_KEYS)),
            ...modelParametersOBJ,
            ...(modelParameters?.booleans && {
              booleans: modelParameters.booleans.reduce(
                (acc, item) => ({
                  ...acc,
                  [item.label]: item.value,
                }),
                {},
              ),
            }),
            ...(modelParameters?.dropdowns && {
              dropdowns: modelParameters.dropdowns?.reduce(
                (acc, item) => ({
                  ...acc,
                  [item?.label]: item?.value,
                }),
                {},
              ),
            }),
            output_format: (() => {
              if (promptData?.config?.modelType === "tts") {
                return "audio";
              } else if (promptData?.config?.modelType === "image") {
                return "image";
              }
              return "string";
            })(),
            ...(reasoningState && {
              reasoning: {
                sliders: arrayToLabelValueMap(reasoningState.sliders),
                dropdowns: arrayToLabelValueMap(reasoningState.dropdowns),
                show_reasoning_process: reasoningState.showReasoningProcess,
              },
            }),
            model: runPromptConfig?.model_name,
            model_detail: runPromptConfig,
            providers: runPromptConfig?.providers,
            response_format: promptData?.config?.responseFormat,
            ...(voiceId && { voice_id: voiceId }),
            tools: tools,
          },
          messages: promptData?.config?.messages?.map((message) => ({
            role: message?.role,
            content: message?.content,
          })),
          placeholders: [...(promptData?.config?.placeholders || [])],
        },
      ],
    };

    if (tools?.length > 0) {
      savePromptData.prompt_config[0].configuration["tool_choice"] = "auto";
    }

    if (isNewPrompt) {
      savePromptData["name"] = data?.promptName;
      createDraft(savePromptData);
    } else if (saveType === "new_version" && promptId) {
      handleSaveAsNewPrompt({ id: promptId, data: savePromptData });
    }
  };
  return (
    <PromptModalWrapper
      open={open}
      onSubmit={handleSubmit(handleSavePrompt)}
      isValid={isValid}
      title={
        saveAndRunMode ? "Save the prompt before executing " : "Save prompt"
      }
      onClose={onClose}
      isLoading={isSavingNewPrompt || isLoadingCreate}
      actionBtnTitle={saveAndRunMode ? "Save and run" : "Save"}
    >
      <DialogContent sx={{ padding: 0 }}>
        <FormControl>
          <Controller
            name="saveType"
            control={control}
            render={({ field }) => (
              <RadioGroup
                {...field}
                sx={{
                  display: "flex",
                  flexDirection: "column",
                  gap: theme.spacing(3),
                }}
              >
                <FormControlLabel
                  value="new_version"
                  sx={{ padding: 0, alignItems: "flex-start" }}
                  control={
                    <Radio
                      sx={{
                        padding: 0,
                        paddingLeft: "10px",
                        paddingTop: "4px",
                      }}
                    />
                  }
                  label={
                    <Stack sx={{ pl: "12px" }} direction="column" gap="2px">
                      <Typography
                        variant="s1"
                        color="text.primary"
                        fontWeight="fontWeightMedium"
                      >
                        Save as a new version
                      </Typography>
                      <Typography
                        variant="s2"
                        fontWeight="fontWeightRegular"
                        color="text.secondary"
                      >
                        Save your current changes as a new version without
                        affecting the original
                      </Typography>
                    </Stack>
                  }
                />

                <FormControlLabel
                  value="new_prompt"
                  sx={{ padding: 0, alignItems: "flex-start" }}
                  control={
                    <Radio
                      sx={{
                        padding: 0,
                        paddingLeft: "10px",
                        paddingTop: "4px",
                      }}
                    />
                  }
                  label={
                    <Stack sx={{ pl: "12px" }} direction="column" gap="2px">
                      <Typography
                        variant="s1"
                        color="text.primary"
                        fontWeight="fontWeightMedium"
                      >
                        Save as a new prompt
                      </Typography>
                      <Typography
                        variant="s2"
                        fontWeight="fontWeightRegular"
                        color="text.secondary"
                      >
                        Store this as a separate prompt so you can reuse or
                        modify it later
                      </Typography>
                    </Stack>
                  }
                />
              </RadioGroup>
            )}
          />
        </FormControl>
        {isNewPrompt && (
          <Box
            sx={{
              ml: "32px",
            }}
          >
            <FormTextFieldV2
              fullWidth
              required
              label={"Add prompt name"}
              fieldName={"promptName"}
              control={control}
              placeholder="Prompt Name"
              size="small"
              sx={{
                mt: "24px",
              }}
            />
          </Box>
        )}
      </DialogContent>
    </PromptModalWrapper>
  );
}

SavePromptModal.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  promptData: PropTypes.object,
  promptId: PropTypes.string,
  selectedModelProvider: PropTypes.func,
  setInitialImportedData: PropTypes.func,
  saveAndRunMode: PropTypes.bool,
  setsaveAndRunMode: PropTypes.func,
  onSaveSuccess: PropTypes.func,
  handleUpdateImportedPrompt: PropTypes.func,
  modelParameters: PropTypes.object,
  reasoningState: PropTypes.object,
};
