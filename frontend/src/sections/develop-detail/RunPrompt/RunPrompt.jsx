import {
  Box,
  Drawer,
  IconButton,
  Typography,
  Button,
  Collapse,
  Modal,
  FormControlLabel,
  Radio,
  TextField,
  Popover,
  Stack,
} from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import PropTypes from "prop-types";
import React, {
  useImperativeHandle,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { trackEvent, Events, PropertyName } from "src/utils/Mixpanel";
import Iconify from "src/components/iconify";
import { useForm } from "react-hook-form";
import { getRandomId } from "src/utils/utils";
import { zodResolver } from "@hookform/resolvers/zod";
import { RunPromptValidationSchema } from "./validation";
import axios, { endpoints } from "src/utils/axios";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "src/components/snackbar";
import { useParams } from "react-router";
import { LoadingButton } from "@mui/lab";
import TestRunPrompt from "./TestRunPrompt";
import {
  normalizeConfigurationForLoad,
  normalizeConfigurationForSave,
} from "src/sections/workbench/createPrompt/common";
import {
  useDatasetColumnConfig,
  useDatasetDerivedVariables,
  useGetJsonColumnSchema,
  useVoiceOptions,
} from "src/api/develop/develop-detail";
import {
  arrayToLabelValueMap,
  findInvalidVariables,
  getDefaultReasoningState,
  getOutputFormatForModelType,
  MODEL_TYPES,
  replaceVariablesWithFields,
  transformDefaultData,
  transformParameterType,
} from "./common";
import ToolsConfigSection from "./ToolConfig/ToolsConfigSection";
import {
  RunBody,
  RunBtnWrap,
  RunCta,
  RunHeader,
  RunItem,
  RunList,
  RunTitle,
  RunWrapper,
} from "./RunStyle";
import IncrementerButton from "src/components/IncrementerButton/IncrementerButton";
import { ConfirmDialog } from "src/components/custom-dialog";
import { useApiKeysStatus } from "src/api/model/api-keys";
import SavePromptModal from "./Modals/SavePromptModal";
import CustomToolModal from "./Modals/CustomToolModal";
import ImportPrompt from "./Modals/ImportPrompt";
import RunPreviewModal from "./Modals/RunPreviewModal";
import _ from "lodash";
import GeneratePromptDrawer from "src/components/GeneratePromptDrawer";
import { PromptRoles } from "src/utils/constants";
import CustomModelDropdownControl from "src/components/custom-model-dropdown/CustomModelDropdownControl";
import ImprovePromptDrawer from "src/components/ImprovePromptDrawer";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { sanitizeContent } from "src/utils/utils";
import { AudioPlaybackProvider } from "../../../components/custom-audio/context-provider/AudioPlaybackContext";
import logger from "src/utils/logger";
import { useCustomAudioDialog, useRunPromptStore } from "../states";
import { useDevelopDetailContext } from "../Context/DevelopDetailContext";
import { ShowComponent } from "../../../components/show";
import HelperText from "../Common/HelperText";
import SvgColor from "src/components/svg-color";
import PromptTemplateSection from "./Components/PromptTemplateSection";
import PromptTTSInput from "./Components/PromptTTSInput";
import PromptSTTInput from "./Components/PromptSTTInput";
import PromptImageInput from "./Components/PromptImageInput";
import ChooseModelType from "./Components/ChooseModelType";
import ModelOptionsState from "../Common/ModelOptionsState";
import CustomAudioDialog from "../CustomAudioDialog";

const DEFAULT_CONFIG_KEYS = [
  "responseFormat",
  "temperature",
  "topP",
  "model",
  "concurrency",
  "voiceInputColumn",
  "messages",
  "maxTokens",
  "presencePenalty",
  "frequencyPenalty",
  "tools",
  "runType",
  "prompt",
  "promptVersion",
  "run_prompt_config",
];

const buildRunPromptConfig = (
  data,
  modelParameters,
  reasoningState,
  modelType,
) => {
  const submittedKeys = Object.keys(data?.config);
  delete data?.config?.modelType;

  submittedKeys.forEach((key) => {
    if (!DEFAULT_CONFIG_KEYS.includes(key)) {
      const value = data?.config[key] ?? "";
      data.config.run_prompt_config[key] = value;
      delete data.config[key];
    }
  });

  data["config"]["run_prompt_config"]["model_type"] = modelType;
  // arrayToLabelValueMap camelCases every slider label (see common.js),
  // so normalize at the outer boundary after all spreads rather than on
  // the run_prompt_config input alone — otherwise camelCase slider keys
  // land on top of the snake_case rename and the backend (snake_case
  // reader) sees the stale form-state value instead of the user's live
  // slider tweak.
  const builtRunPromptConfig = {
    ...data?.config?.run_prompt_config,
    ...arrayToLabelValueMap(modelParameters?.sliders),
    ...(modelParameters?.booleans && {
      booleans: arrayToLabelValueMap(modelParameters.booleans),
    }),
    ...(modelParameters?.dropdowns && {
      dropdowns: arrayToLabelValueMap(modelParameters.dropdowns),
    }),
  };
  data["config"]["run_prompt_config"] =
    normalizeConfigurationForSave(builtRunPromptConfig);

  if (reasoningState) {
    data["config"]["run_prompt_config"]["reasoning"] = {
      sliders: arrayToLabelValueMap(reasoningState.sliders),
      dropdowns: arrayToLabelValueMap(reasoningState.dropdowns),
      show_reasoning_process: reasoningState.showReasoningProcess,
    };
  }
};

const defaultValues = {
  name: "",
  config: {
    model: "",
    run_prompt_config: {
      modelName: "",
      logoUrl: "",
      providers: "",
      isAvailable: false,
      voice: "",
      voiceId: "",
    },
    modelType: "llm",
    concurrency: 5,
    voiceInputColumn: "",
    messages: [
      {
        id: getRandomId(),
        role: PromptRoles.SYSTEM,
        content: [
          {
            type: "text",
            text: "",
          },
        ],
      },
      {
        id: getRandomId(),
        role: PromptRoles.USER,
        content: [
          {
            type: "text",
            text: "",
          },
        ],
      },
    ],
    responseFormat: "text",
    // temperature: 0.5,
    // topP: 1,
    // maxTokens: 4085,
    // presencePenalty: 1,
    // frequencyPenalty: 1,
    tools: [],
    runType: "",
    prompt: "",
    promptVersion: "",
  },
};

const getDefaultValues = (editConfigData, allColumns) => {
  if (editConfigData) {
    return transformDefaultData(editConfigData, allColumns);
  }
  return defaultValues;
};

function checkIfAllVariablesAreValid(allInvalidVariables) {
  if (allInvalidVariables?.length > 0) {
    const targetElement = document.getElementById(`invalid-variables-message`);
    if (targetElement) {
      targetElement.scrollIntoView({ behavior: "smooth", block: "center" });
    }
    return false;
  }
  return true;
}

function getAllNestedKeys(obj, keys = [], prefix = "") {
  if (typeof obj !== "object" || obj === null) {
    return keys;
  }

  for (const key in obj) {
    if (Object.prototype.hasOwnProperty.call(obj, key)) {
      const newKey = prefix ? `${prefix}.${key}` : key;
      keys.push(newKey);
      if (typeof obj[key] === "object" && obj[key] !== null) {
        getAllNestedKeys(obj[key], keys, newKey);
      }
    }
  }

  return keys;
}

function CustomTabPanel(props) {
  const { children, value, index, ...other } = props;

  return (
    <div
      role="tabpanel"
      hidden={value !== index}
      id={`simple-tabpanel-${index}`}
      aria-labelledby={`simple-tab-${index}`}
      {...other}
    >
      {value === index && (
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            gap: "16px",
            padding: "0 20px 16px",
          }}
        >
          {children}
        </Box>
      )}
    </div>
  );
}
CustomTabPanel.propTypes = {
  children: PropTypes.node,
  index: PropTypes.number.isRequired,
  value: PropTypes.number.isRequired,
};

export const RunPromptForm = React.forwardRef(
  (
    {
      onClose,
      editConfigData,
      isConfirmationModalOpen,
      setConfirmationModalOpen,
      initialData,
      onFormSubmit,
      initialModelParams = {
        sliders: [],
        dropdowns: [],
        booleans: [],
      },
      initialReasoningState = null,
    },
    ref,
  ) => {
    const { dataset } = useParams();
    const [testRun, setTestRun] = useState(Boolean);
    const [, setShowOutput] = useState(Boolean);
    const [selected, setSelected] = useState("firstRows");
    const [rowNum, setRowNum] = useState("1,2,3,4,5");
    const [firstRow, setFirstRow] = useState(5);
    const [anchorEl, setAnchorEl] = useState(null);
    const runList = [
      { label: "First N rows", value: "firstRows" },
      { label: "Row Number", value: "rowNumber" },
    ];
    const allColumns = useDatasetColumnConfig(dataset, true);
    const { data: jsonSchemas = {} } = useGetJsonColumnSchema(dataset);
    const { data: derivedVariablesData } = useDatasetDerivedVariables(dataset);
    const derivedVariables = derivedVariablesData || {};
    const [modelParameters, setModelParameters] = useState(initialModelParams);
    const queryClient = useQueryClient();
    const [reasoningState, setReasoningState] = useState(initialReasoningState);
    const [modelParamsAnchorEl, setModelParamsAnchorEl] = useState(null);
    const [draftModelParameters, setDraftModelParameters] = useState(null);
    const [draftReasoningState, setDraftReasoningState] = useState(null);
    const draftResponseFormatRef = useRef(null);

    const handleOpenModelParams = (e) => {
      setDraftModelParameters(JSON.parse(JSON.stringify(modelParameters)));
      setDraftReasoningState(JSON.parse(JSON.stringify(reasoningState)));
      draftResponseFormatRef.current = getValues("config.responseFormat");
      setModelParamsAnchorEl(e.currentTarget);
    };

    const handleApplyModelParams = () => {
      setModelParameters(draftModelParameters);
      setReasoningState(draftReasoningState);
      draftResponseFormatRef.current = getValues("config.responseFormat");
      setModelParamsAnchorEl(null);
    };

    const handleCloseModelParams = () => {
      setDraftModelParameters(null);
      setDraftReasoningState(null);
      setValue("config.responseFormat", draftResponseFormatRef.current);
      setModelParamsAnchorEl(null);
    };

    const handleClearModelParams = () => {
      setDraftModelParameters({
        ...modelParameters,
        ...(modelParameters?.sliders && {
          sliders: modelParameters.sliders.map((s) => ({
            ...s,
            value: s?.default !== undefined ? s?.default : 0,
          })),
        }),
        ...(modelParameters?.booleans && {
          booleans: modelParameters.booleans.map((b) => ({
            ...b,
            value: b?.default ?? false,
          })),
        }),
        ...(modelParameters?.dropdowns && {
          dropdowns: modelParameters.dropdowns.map((d) => ({
            ...d,
            value: d?.default ?? d?.options?.[0],
          })),
        }),
      });

      setDraftReasoningState(getDefaultReasoningState(modelParams?.reasoning));
      setValue("config.responseFormat", "text");
    };

    const { refreshGrid } = useDevelopDetailContext();

    const { data: apiKeysStatus } = useApiKeysStatus();

    const isEditMode = Boolean(editConfigData);
    const [openImportPromptModal, setOpenImportPromptModal] = useState(false);
    const [importedPrompt, setImportedPrompt] = useState({
      prompt: "",
      promptVersion: "",
    });
    const [openRunPreViewModal, setOpenRunPreViewModal] = useState(false);
    const [openSavePromptModal, setOpenSavePromptModal] = useState(false);
    const [openCustomToolModal, setOpenCustomToolModal] = useState(false);
    const [openGeneratePromptDrawer, setOpenGeneratePromptDrawer] = useState({
      state: false,
      index: null,
    });
    const [openImprovePromptDrawer, setOpenImprovePromptDrawer] = useState({
      state: false,
      index: null,
    });
    const [saveAndRunMode, setSaveAndRunMode] = useState(false);
    const [editTool, setEditTool] = useState(null);
    const formRef = useRef(null);

    const {
      control,
      handleSubmit,
      formState: { isDirty, errors },
      getValues,
      setValue,
      reset,
      watch,
      clearErrors,
      trigger,
      getFieldState,
      setError,
    } = useForm({
      defaultValues: initialData
        ? getDefaultValues(initialData, allColumns)
        : getDefaultValues(editConfigData, allColumns),
      resolver: zodResolver(RunPromptValidationSchema(!!onFormSubmit)),
      mode: "onChange",
    });
    useImperativeHandle(ref, () => ({
      isDirty,
    }));

    const watchedResponseFormat = watch("config.responseFormat");

    const hasModelParamsChanges = useMemo(() => {
      if (!draftModelParameters || !modelParameters) return false;
      return (
        !_.isEqual(draftModelParameters, modelParameters) ||
        !_.isEqual(draftReasoningState, reasoningState) ||
        watchedResponseFormat !== draftResponseFormatRef.current
      );
    }, [
      draftModelParameters,
      modelParameters,
      draftReasoningState,
      reasoningState,
      watchedResponseFormat,
    ]);

    const selectedTools = watch("config.tools");
    const [initialImportedData, setInitialImportedData] = useState(null);
    const watchedValues = watch();
    const watchedMessages = watch("config.messages");
    const watchedModelType = watch("config.modelType");
    const watchedModel = watch("config.model");
    const watchedModelProvider = watch("config.run_prompt_config.providers");
    const watchedVoice = watch("config.run_prompt_config.voiceId");
    const allInvalidVariables = [];
    // Flag to apply voice default only on user-initiated model changes
    const userChangedModelRef = useRef(false);
    // Track which model the current modelParameters state belongs to
    const modelParametersModelRef = useRef(watchedModel);

    watchedMessages?.forEach((message) => {
      if (!message) return;
      const content = message.content;
      if (Array.isArray(content)) {
        content.forEach((part) => {
          if (part.type === "text" && part.text) {
            const invalids = findInvalidVariables(
              part.text,
              allColumns,
              jsonSchemas,
              derivedVariables,
            );
            allInvalidVariables.push(...invalids);
          }
        });
      }
    });

    const validateForm = async () => {
      try {
        const allFields = getAllNestedKeys(defaultValues); // get all field keys
        const nonMessageFields = allFields.filter(
          (key) => key !== "config" && !key.startsWith("config.messages"),
        );

        // Trigger validation for all fields except messages
        const nonMessageFieldsValid = await trigger(nonMessageFields, {
          shouldFocus: false,
        });

        // Determine which message fields to validate
        // For non-LLM models (TTS, STT, IMAGE), only validate the user prompt (messages[1])
        const messageFieldToValidate =
          watchedModelType !== MODEL_TYPES.LLM
            ? "config.messages.1"
            : "config.messages";

        // Trigger validation for messages (to show errors)
        await trigger(messageFieldToValidate, { shouldFocus: false });
        if (
          watchedModelType === MODEL_TYPES.STT &&
          !getValues("config.voiceInputColumn")
        ) {
          setError("config.voiceInputColumn", {
            message: "Select an Audio column",
          });
          return;
        }

        if (!nonMessageFieldsValid) {
          // Focus first invalid non-message field
          await trigger(nonMessageFields, { shouldFocus: true });
          return false;
        }

        // Get message field state (single or all)
        const messagesState = getFieldState(messageFieldToValidate);

        // Handle invalid message(s)
        if (messagesState.invalid) {
          // For non-LLM models, the user prompt is at index 1
          const _idx =
            watchedModelType !== MODEL_TYPES.LLM
              ? 1
              : messagesState.error?.findIndex?.((item) => Boolean(item));

          if (_idx !== -1) {
            const targetElement = document.getElementById(
              `config.messages.${_idx}`,
            );
            if (targetElement) {
              targetElement.scrollIntoView({
                behavior: "smooth",
                block: "center",
              });
            }
          }
          return false;
        }

        // Custom variable validation
        const areCustomVariablesValid =
          checkIfAllVariablesAreValid(allInvalidVariables);

        logger.debug("areCustomVariablesValid", { areCustomVariablesValid });
        if (!areCustomVariablesValid) {
          return false;
        }

        return true;
      } catch (error) {
        return false;
      }
    };

    const isPromptTemplateDirty = useMemo(() => {
      if (initialImportedData === null) return false;
      const excludedKeys = ["name", "config.concurrency"];

      // Omit excluded keys from comparison
      const valuesToCompare = _.omit(watchedValues, excludedKeys);

      return !_.isEqual(valuesToCompare, initialImportedData);
    }, [watchedValues, initialImportedData]);

    const handleRemovePrompt = () => {
      resetPreviewData();
      setImportedPrompt({
        prompt: "",
        promptVersion: "",
      });
      const values = getValues(["name", "config.concurrency"]);
      reset({
        name: values?.[0],
        config: {
          ...defaultValues?.config,
          concurrency: values?.[1],
        },
      });
    };
    const {
      data: previewData,
      isPending: isPreviewLoading,
      mutate: previewRunPrompt,
      isSuccess,
      reset: resetPreviewData,
    } = useMutation({
      mutationFn: (d) => axios.post(endpoints.develop.runPrompt.preview, d),
      onSuccess: () => {
        // setRowNum("1,2,3,4,5");
        // setFirstRow(5);
        setShowOutput(true);
      },
    });

    useEffect(() => {
      resetPreviewData();
    }, [isEditMode, resetPreviewData]);

    const { data: voiceOptions, isLoading: isLoadingVoiceOptions } =
      useVoiceOptions({
        model: watchedModel,
        enabled: !!watchedModel,
      });

    const { mutate: createRunPrompt, isPending: isSubmittingPrompt } =
      useMutation({
        mutationFn: (data) =>
          axios.post(endpoints.develop.runPrompt.create, data),
        onSuccess: () => {
          enqueueSnackbar("Run Prompt created successfully", {
            variant: "success",
          });
          refreshGrid(null, true);
          resetPreviewData();
          onClose();
          reset();
        },
      });

    const { mutate: updateRunPrompt, isPending: isUpdatingPrompt } =
      useMutation({
        mutationFn: (data) =>
          axios.post(endpoints.develop.runPrompt.editRunPrompt(), data),
        onSuccess: () => {
          enqueueSnackbar("Run Prompt updated successfully", {
            variant: "success",
          });
          refreshGrid(null, true);
          resetPreviewData();
          onClose();
          reset();
        },
        onError: () => {
          enqueueSnackbar("Failed to update Run Prompt", {
            variant: "error",
          });
        },
      });

    const { isCustomAudioModalOpen, setIsCustomAudioModalOpen } =
      useCustomAudioDialog();

    // setIsFormLoading(
    //   isSubmittingPrompt || isUpdatingPrompt || isPreviewLoading,
    // );

    const handleClose = () => {
      setAnchorEl(null);
    };

    const onCloseHandler = () => {
      // onFormSubmit is sent in conditional node only we don't need dirty check there
      if (isDirty && !onFormSubmit) {
        setConfirmationModalOpen(true);
      } else {
        onClose();
      }
    };

    // We have put this check here so that user is forced to put api key in case of Sample Dataset where a model is already selected
    const performExtraCheckForSampleDataset = (submitObject) => {
      if (submitObject.config.model === "gpt-4o-mini") {
        if (!apiKeysStatus.find((key) => key.provider === "openai")?.hasKey) {
          enqueueSnackbar("Please configure OpenAI API key", {
            variant: "error",
          });
          return true;
        }
      }
      return false;
    };
    const { data: responseSchema } = useQuery({
      queryKey: ["response-schema"],
      queryFn: () => axios.get(endpoints.develop.runPrompt.responseSchema),
      select: (d) => d.data?.results,
      staleTime: 1 * 60 * 1000, // 1 min stale time
    });
    const { data: modelParams, isLoading: isLoadingModelParams } = useQuery({
      queryKey: [
        "model-params",
        watchedModel,
        watchedModelType,
        watchedModelProvider,
      ],
      queryFn: () =>
        axios.get(endpoints.develop.modelParams, {
          params: {
            model: watchedModel,
            provider: watchedModelProvider,
            model_type: watchedModelType,
          },
        }),
      enabled: !!(watchedModel && watchedModelProvider && watchedModelType),
      select: (d) => d.data?.result,
    });

    const getResponseFormat = (id) => {
      const response = responseSchema.find((item) => item.id === id);
      return response;
    };

    useEffect(() => {
      if (!isDirty) return;
      if (!watchedModel) {
        setModelParameters({
          sliders: [],
          dropdowns: [],
          booleans: [],
        });
        modelParametersModelRef.current = null;
        return;
      }

      // Check if model has changed - compare with the model that current state belongs to
      const modelChanged = modelParametersModelRef.current !== watchedModel;

      // If modelParams is not yet available (query disabled, loading, or error)
      // Only reset if model changed - otherwise keep existing parameters
      if (!modelParams) {
        if (modelChanged) {
          modelParametersModelRef.current = null; // Mark as reset, but not yet populated
          setModelParameters({
            sliders: [],
            dropdowns: [],
            booleans: [],
          });
        }
        return;
      }

      // Process modelParams and update state in a single operation
      setModelParameters((prev) => {
        // If model changed, start fresh; otherwise merge with existing
        const baseParams = modelChanged
          ? { sliders: [], dropdowns: [], booleans: [] }
          : {
              sliders: prev?.sliders || [],
              dropdowns: prev?.dropdowns || [],
              booleans: prev?.booleans || [],
            };

        const result = {
          sliders: [...baseParams.sliders],
          dropdowns: [...baseParams.dropdowns],
          booleans: [...baseParams.booleans],
        };

        // Handle sliders (top level array)
        if (modelParams?.sliders?.length > 0) {
          const existingIds = result?.sliders?.map((p) => p.id) || [];

          const newSliders = modelParams.sliders
            .filter((item) => {
              const id = item.id ?? _.camelCase(item.label);
              return !existingIds.includes(id);
            })
            .map((item) => {
              const id = item.id ?? _.camelCase(item.label);
              const defaultValue =
                item.value !== undefined
                  ? item.value
                  : item.default !== undefined
                    ? item.default
                    : 0;

              return {
                ...item,
                value: defaultValue,
                default: item.default,
                id,
              };
            });

          result.sliders = [...result.sliders, ...newSliders];
        }

        // Handle booleans
        if (modelParams?.booleans?.length > 0) {
          const existingBooleanIds = result?.booleans?.map((p) => p.id) || [];

          const newBooleans = modelParams.booleans
            .filter((item) => {
              const id = item.id ?? _.camelCase(item.label);
              return !existingBooleanIds.includes(id);
            })
            .map((item) => {
              const id = item.id ?? _.camelCase(item.label);
              return {
                ...item,
                value: item.value ?? item.default ?? false,
                id,
              };
            });

          result.booleans = [...result.booleans, ...newBooleans];
        }

        // Handle dropdowns
        if (modelParams?.dropdowns?.length > 0) {
          const existingDropdownIds = result.dropdowns.map((p) => p.id) || [];

          const newDropdowns = modelParams.dropdowns
            .filter((item) => {
              const id = item.id ?? _.camelCase(item.label);
              return !existingDropdownIds.includes(id);
            })
            .map((item) => {
              const id = item.id ?? _.camelCase(item.label);
              return {
                ...item,
                value: item.value ?? item.default ?? item?.options?.[0],
                id,
              };
            });

          result.dropdowns = [...result.dropdowns, ...newDropdowns];
        }

        return result;
      });

      // Handle reasoning — clear if new model doesn't support it
      if (modelChanged) {
        const reasoning = modelParams?.reasoning;
        setReasoningState(
          reasoning ? getDefaultReasoningState(reasoning) : null,
        );
      }

      // Update ref to track that state now belongs to this model (after state update)
      modelParametersModelRef.current = watchedModel;
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [modelParams, watchedModel, isDirty]);

    const validResponseFormats = [
      "text",
      "json_object",
      "none",
      ...(modelParams?.responseFormat?.map((item) => item?.value) ?? []),
    ];

    const onSubmit = (data) => {
      const inValidVariables = [];

      const modelType = data?.config?.modelType;
      const outputFormat = getOutputFormatForModelType(modelType);
      if (outputFormat !== "audio") {
        delete data?.config?.run_prompt_config?.voice;
      }

      // Step 2: Safely assign correct responseFormat
      const originalResponseFormat = data?.config?.responseFormat;
      const finalResponseFormat = validResponseFormats.includes(
        originalResponseFormat,
      )
        ? originalResponseFormat
        : getResponseFormat(originalResponseFormat); // fallback

      buildRunPromptConfig(data, modelParameters, reasoningState, modelType);

      const configEntries = {
        ...data.config,
        outputFormat,
        responseFormat: finalResponseFormat, //  Use validated or fallback
        messages: data?.config?.messages?.map(({ id, ...rest }) => {
          let content = rest.content;

          if (Array.isArray(content)) {
            content = content.map((part) => {
              if (part.type === "text" && part.text) {
                let updatedText = sanitizeContent(part.text);

                // Extract all variables in the format {{ variable }}
                const variablePattern = /{{\s*([^{}]+?)\s*}}/g;
                const matches = [...updatedText.matchAll(variablePattern)];

                const result = replaceVariablesWithFields(
                  updatedText,
                  matches,
                  allColumns,
                  jsonSchemas,
                );
                updatedText = result.text;
                inValidVariables.push(...result.invalidVariables);

                return { ...part, text: updatedText };
              }
              return part;
            });
          }

          return {
            ...rest,
            content,
          };
        }),
      };

      // Rename camelCase config keys to snake_case for the API
      const camelToSnakeMap = {
        responseFormat: "response_format",
        outputFormat: "output_format",
        maxTokens: "max_tokens",
        topP: "top_p",
        presencePenalty: "presence_penalty",
        frequencyPenalty: "frequency_penalty",
        voiceInputColumn: "voice_input_column",
        runType: "run_type",
        promptVersion: "prompt_version",
        modelType: "model_type",
      };
      const renamedConfig = Object.fromEntries(
        Object.entries(configEntries).map(([key, value]) => [
          camelToSnakeMap[key] || key,
          value,
        ]),
      );

      const submitObject = {
        dataset_id: dataset,
        name: data.name,
        config: renamedConfig,
      };

      if (data?.config?.tools?.length > 0) {
        submitObject.config["tool_choice"] = "auto";
      }

      if (inValidVariables?.length > 0) {
        return;
      }
      if (onFormSubmit) {
        onFormSubmit(submitObject);
        return;
      }

      if (isEditMode) {
        if (performExtraCheckForSampleDataset(submitObject)) return;
        // @ts-ignore
        updateRunPrompt({ column_id: editConfigData?.id, ...submitObject });
      } else {
        // @ts-ignore
        createRunPrompt(submitObject);
        trackEvent(Events.datasetRunpromptRunClicked, {
          [PropertyName.id]: dataset,
          [PropertyName.concurrency]: submitObject.config?.concurrency,
          [PropertyName.promptTemplate]:
            submitObject.config?.messages?.map((msg) => msg.content) || [],
          [PropertyName.toolConfig]: {
            toolChoice: submitObject.config?.toolChoice,
            tools: submitObject.config?.tools?.map((tool) => tool.id) || [],
          },
          [PropertyName.modelName]: submitObject.config?.model,
          [PropertyName.modelOptions]: {
            maxTokens: submitObject.config?.maxTokens,
            presencePenalty: submitObject.config?.presencePenalty,
            temperature: submitObject.config?.temperature,
            topP: submitObject.config?.topP,
            responseFormat: submitObject.config?.responseFormat,
          },
        });
      }
    };

    const onSubmitPreview = (data, testData) => {
      const inValidVariables = [];

      const modelType = data?.config?.modelType;
      const outputFormat = getOutputFormatForModelType(modelType);
      if (outputFormat !== "audio") {
        delete data?.config?.run_prompt_config?.voice;
      }

      // Step 2: Safely assign correct responseFormat
      const originalResponseFormat = data?.config?.responseFormat;
      const finalResponseFormat = validResponseFormats.includes(
        originalResponseFormat,
      )
        ? originalResponseFormat
        : getResponseFormat(originalResponseFormat); // fallback

      buildRunPromptConfig(data, modelParameters, reasoningState, modelType);

      const previewConfigEntries = {
        ...data.config,
        outputFormat,
        model: data?.config?.run_prompt_config?.model_name,
        messages: data?.config?.messages?.map(({ id, ...rest }) => {
          let content = rest.content;

          if (Array.isArray(content)) {
            content = content.map((part) => {
              if (part.type === "text" && part.text) {
                let updatedText = sanitizeContent(part.text);

                // Extract all variables in the format {{ variable }}
                const variablePattern = /{{\s*([^{}]+?)\s*}}/g;
                const matches = [...updatedText.matchAll(variablePattern)];

                const result = replaceVariablesWithFields(
                  updatedText,
                  matches,
                  allColumns,
                  jsonSchemas,
                );
                updatedText = result.text;
                inValidVariables.push(...result.invalidVariables);

                return { ...part, text: updatedText };
              }
              return part;
            });
          }

          return {
            ...rest,
            content,
          };
        }),
      };

      // Rename camelCase config keys to snake_case for the API
      const previewCamelToSnakeMap = {
        responseFormat: "response_format",
        outputFormat: "output_format",
        maxTokens: "max_tokens",
        topP: "top_p",
        presencePenalty: "presence_penalty",
        frequencyPenalty: "frequency_penalty",
        voiceInputColumn: "voice_input_column",
        runType: "run_type",
        promptVersion: "prompt_version",
        modelType: "model_type",
      };
      const renamedPreviewConfig = Object.fromEntries(
        Object.entries(previewConfigEntries)
          .filter(([_, value]) => value !== null)
          .map(([key, value]) => [previewCamelToSnakeMap[key] || key, value]),
      );

      const submitObject = {
        dataset_id: dataset,
        name: data.name,
        response_format: finalResponseFormat, //  Use validated or fallback
        config: renamedPreviewConfig,
        ...testData,
        // ...(selected == "rowNumber"
        //   ? { row_indices: rowNum.split(",").map((i) => +i) }
        //   : { first_n_rows: firstRow }),
      };

      if (data?.config?.tools?.length > 0) {
        submitObject.config["tool_choice"] = "auto";
      }
      if (inValidVariables?.length > 0) {
        return;
      }
      if (performExtraCheckForSampleDataset(submitObject)) return;

      // @ts-ignore
      previewRunPrompt(submitObject);
    };
    const handleIncrease = () => {
      setFirstRow(firstRow + 1);
    };

    const handleDecrease = () => {
      setFirstRow(firstRow - 1);
    };
    const onApply = async (testData) => {
      try {
        await handleSubmit(async (data) => {
          await onSubmitPreview(data, testData);
        })();
        setTestRun(false);
        setAnchorEl(false);
        setOpenRunPreViewModal(false);
      } catch (error) {
        enqueueSnackbar("Failed to run prompt", { variant: "error" });
        logger.error("Failed to run prompt", error);
      }
    };

    const handleApplyImportedPrompt = async (data) => {
      if (!data?.prompt?.name && !data?.promptVersion?.templateVersion) return;
      clearErrors("config");
      setImportedPrompt(data);

      setValue("config.prompt", data?.prompt?.name, {
        shouldDirty: true,
      });
      setValue("config.promptVersion", data?.promptVersion?.templateVersion);

      let modelObject = defaultValues.config.run_prompt_config;
      const normalizedSnapshotConfig = normalizeConfigurationForLoad(
        data?.promptVersion?.promptConfigSnapshot?.configuration,
      );
      const modelType = normalizedSnapshotConfig?.modelDetail?.type;
      const importedVoiceId = normalizedSnapshotConfig?.voiceId;

      let internalModelType = "llm";
      if (modelType === "image_generation") {
        internalModelType = "image";
      } else if (modelType === "tts") {
        internalModelType = "tts";
      } else if (modelType === "stt") {
        internalModelType = "stt";
      }
      setValue("config.modelType", internalModelType);

      if (typeof normalizedSnapshotConfig?.model === "string") {
        modelObject = {
          ...normalizedSnapshotConfig?.modelDetail,
          modelName: normalizedSnapshotConfig?.model,
        };
      }
      setValue("config.model", modelObject?.model_name);
      const configWithVoice = importedVoiceId
        ? { ...modelObject, voiceId: importedVoiceId }
        : modelObject;
      setValue("config.run_prompt_config", configWithVoice);

      setValue(
        "config.messages",
        data?.promptVersion?.promptConfigSnapshot?.messages?.map((msg) => ({
          id: getRandomId(),
          role: msg?.role,
          content: msg.content,
        })),
      );

      const importedConfig =
        data?.promptVersion?.promptConfigSnapshot?.configuration;

      setValue(
        "config.responseFormat",
        importedConfig?.responseFormat ?? "text",
      );
      setValue(
        "config.tools",
        importedConfig?.tools?.map((tool) => tool?.id),
      );

      // Fetch model params inline to avoid race condition with the modelParams useEffect
      try {
        if (modelObject?.model_name && modelObject?.providers) {
          const { data: paramsData } = await queryClient.fetchQuery({
            queryKey: [
              "model-params",
              modelObject.model_name,
              modelObject.providers,
              internalModelType,
            ],
            queryFn: () =>
              axios.get(endpoints.develop.modelParams, {
                params: {
                  model: modelObject.model_name,
                  provider: modelObject.providers,
                  model_type: internalModelType,
                },
              }),
          });

          const fetchedParams = paramsData?.result;
          if (fetchedParams) {
            const transformedSliders = (fetchedParams.sliders || []).map(
              (item) => {
                const id = item.id ?? _.camelCase(item.label);
                let defaultValue = item.value ?? item.default ?? 0;

                // Use imported value if available for this param
                if (importedConfig?.[id] !== undefined) {
                  defaultValue = importedConfig[id];
                }

                return {
                  ...item,
                  value: defaultValue,
                  default: item.default,
                  id,
                };
              },
            );

            setModelParameters({
              sliders: transformedSliders,
              ...(fetchedParams.booleans && {
                booleans: transformParameterType(
                  fetchedParams.booleans,
                  importedConfig,
                  "booleans",
                ),
              }),
              ...(fetchedParams.dropdowns && {
                dropdowns: transformParameterType(
                  fetchedParams.dropdowns,
                  importedConfig,
                  "dropdowns",
                ),
              }),
            });

            // Handle reasoning state from imported config
            const reasoning = fetchedParams?.reasoning;
            const savedReasoning = importedConfig?.reasoning;
            if (reasoning) {
              setReasoningState({
                sliders:
                  reasoning?.sliders?.map((item) => ({
                    ...item,
                    id: _.camelCase(item?.label),
                    value:
                      savedReasoning?.sliders?.[_.camelCase(item?.label)] ??
                      item?.default ??
                      null,
                  })) ?? [],
                dropdowns: transformParameterType(
                  reasoning?.dropdowns,
                  savedReasoning,
                  "dropdowns",
                ),
                showReasoningProcess:
                  savedReasoning?.showReasoningProcess ?? true,
              });
            } else {
              setReasoningState(null);
            }

            modelParametersModelRef.current = modelObject.model_name;
          }
        }
      } catch (error) {
        logger.error("Error fetching model params during import", error);
        enqueueSnackbar(
          "Failed to fully import prompt template. Some settings may be missing.",
          { variant: "warning" },
        );
      }

      const copy = _.cloneDeep(getValues());
      delete copy?.name;
      delete copy?.config.concurrency;
      setInitialImportedData(_.cloneDeep(copy));
    };
    const handleSuccessCustomAudio = (res) => {
      const customName = res?.data?.result?.name;
      const customId = res?.data?.result?.id;

      setValue("config.run_prompt_config.voice", customName);
      setValue("config.run_prompt_config.voiceId", customId);
    };

    useEffect(() => {
      if (
        voiceOptions?.default &&
        !watchedVoice &&
        userChangedModelRef.current
      ) {
        setValue("config.run_prompt_config.voiceId", voiceOptions.default);
        userChangedModelRef.current = false;
      }
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [setValue, voiceOptions, watchedModel]);

    const handleApplyPrompt = (index, prompt) => {
      const currentMessages = getValues("config.messages");

      const updatedMessages = currentMessages.map((msg, i) =>
        i === index
          ? {
              ...msg,
              content: [
                {
                  type: "text",
                  text: prompt,
                },
              ],
            }
          : msg,
      );
      setValue("config.messages", updatedMessages, { shouldDirty: true });
    };

    const promptHasBeenImported = Object?.values(importedPrompt).every(
      (item) => item !== "",
    );

    const handleUpdateImportedPrompt = (data) => {
      if (
        !data?.promptName &&
        !data?.templateVersion &&
        !data?.promptId &&
        !data?.promptConfigData
      )
        return;

      setImportedPrompt((prev) => ({
        ...prev,
        prompt: {
          ...prev.prompt,
          id: data?.promptId,
          name: data?.promptName,
        },
        promptVersion: {
          ...prev.promptVersion,
          promptConfigSnapshot: data?.promptConfigData,
          templateVersion: data?.templateVersion,
        },
      }));
    };
    const runPromptForm = () => {
      return (
        <>
          <Box
            sx={{
              display: "flex",
              flexDirection: "column",
              height: "100%",
              // width: "100%",
            }}
          >
            <Stack
              sx={{
                padding: (theme) => theme.spacing(2, 2, 0),
              }}
              direction={"column"}
              gap={0.5}
            >
              <Typography
                typography="m3"
                fontWeight={"fontWeightMedium"}
                color="text.primary"
              >
                Run Prompt
              </Typography>
              <Typography
                typography={"s1"}
                fontWeight="fontWeightRegular"
                color="text.secondary"
              >
                Execute your prompt using this dataset to see how it performs
              </Typography>
            </Stack>
            <form
              ref={formRef}
              style={{
                flex: 1,
                gap: 2,
                display: "flex",
                flexDirection: "column",
                overflow: "hidden",
                width: "650px",
              }}
            >
              <Box
                sx={{
                  flex: 1,
                  gap: (theme) => theme.spacing(2),
                  display: "flex",
                  flexDirection: "column",
                  overflow: "auto",
                  padding: (theme) => theme.spacing(2, 2, 3),
                }}
              >
                <ShowComponent condition={!onFormSubmit}>
                  <FormTextFieldV2
                    control={control}
                    fieldName="name"
                    label="Name"
                    fullWidth
                    placeholder="Prompt Name"
                    required={true}
                    size="small"
                    helperText={undefined}
                    defaultValue={undefined}
                    onBlur={undefined}
                  />
                </ShowComponent>
                <ChooseModelType
                  control={control}
                  label={"Choose a model type to run your prompt"}
                  onChange={() => {
                    userChangedModelRef.current = true;
                    setValue("config.messages", defaultValues?.config.messages);
                    setValue("config.model", "");
                    setValue("config.run_prompt_config", {});
                    setValue("config.voiceInputColumn", "");
                    setValue("config.run_prompt_config.voiceId", "");
                    setModelParameters({
                      sliders: [],
                      dropdowns: [],
                      booleans: [],
                    });
                  }}
                />
                <Stack gap={0.5}>
                  <Stack
                    direction={"row"}
                    sx={{
                      alignItems: "center",
                    }}
                    gap={2}
                  >
                    <Box
                      sx={{
                        flex: 1,
                      }}
                    >
                      <CustomModelDropdownControl
                        control={control}
                        fieldName="model"
                        modelObjectKey={"run_prompt_config"}
                        fieldPrefix={"config"}
                        label="Select Model"
                        searchDropdown
                        size="small"
                        fullWidth
                        hideCreateLabel={
                          watchedModelType === MODEL_TYPES.STT ||
                          watchedModelType === MODEL_TYPES.TTS ||
                          watchedModelType === MODEL_TYPES.IMAGE
                        }
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
                        disabledHover={watchedModelType === MODEL_TYPES.IMAGE}
                        onChange={() => {
                          userChangedModelRef.current = true;
                          // Note: modelParameters reset is handled in useEffect when model changes
                          // to avoid race conditions with async modelParams query
                          if (watchedModelType !== MODEL_TYPES.STT) {
                            setValue("config.voiceInputColumn", "");
                          }

                          // Get the current messages array
                          if (watchedModelType !== MODEL_TYPES.LLM) {
                            const currentMessages =
                              getValues("config.messages") || [];

                            const filteredMessages = currentMessages.slice(
                              0,
                              2,
                            );

                            if (filteredMessages[0]?.role !== "system") {
                              filteredMessages[0] = {
                                ...filteredMessages[0],
                                role: "system",
                                content: [{ type: "text", text: "" }],
                              };
                            }

                            // --- Ensure index 1 is always "user" ---
                            if (filteredMessages[1]) {
                              if (filteredMessages[1].role !== "user") {
                                filteredMessages[1] = {
                                  ...filteredMessages[1],
                                  role: "user",
                                };
                              }
                            }

                            setValue("config.messages", filteredMessages, {
                              shouldDirty: true,
                              shouldValidate: true,
                            });
                          }
                        }}
                        extraParams={{ model_type: watchedModelType }}
                      />
                    </Box>
                    <CustomTooltip
                      show
                      size="small"
                      type="black"
                      title={
                        !watchedModel
                          ? "Select a model first"
                          : isLoadingModelParams
                            ? "Loading model parameters..."
                            : "Model options"
                      }
                    >
                      <span>
                        <IconButton
                          disabled={!watchedModel || isLoadingModelParams}
                          size="small"
                          onClick={handleOpenModelParams}
                          sx={{
                            height: "28px",
                            width: "28px",
                            borderRadius: 0.25,
                            border: "1px solid",
                            borderColor: "divider",
                            padding: "3px",
                            flexShrink: 0,
                            color: "text.primary",
                          }}
                        >
                          <SvgColor src="/assets/prompt/slider-options.svg" />
                        </IconButton>
                      </span>
                    </CustomTooltip>
                  </Stack>
                  <Typography
                    typography={"s3"}
                    color={"text.secondary"}
                    fontWeight={"fontWeightMedium"}
                  >
                    Choose from a list of LLM models or create your own custom
                    models for running prompt
                  </Typography>
                </Stack>

                <ShowComponent condition={watchedModelType === MODEL_TYPES.TTS}>
                  <Stack gap={0.5}>
                    <FormSearchSelectFieldControl
                      control={control}
                      fieldName="config.run_prompt_config.voiceId"
                      size="small"
                      label="Voice"
                      placeholder="Choose a voice"
                      required
                      fullWidth
                      onChange={(e) => {
                        setValue(
                          "config.run_prompt_config.voice",
                          e?.target?.option?.label,
                          {
                            shouldDirty: true,
                            shouldValidate: true,
                          },
                        );
                      }}
                      options={voiceOptions?.voices || []}
                      disabled={
                        watchedModel &&
                        !voiceOptions?.voices?.length &&
                        !isLoadingVoiceOptions
                      }
                      handleCreateLabel={() => {
                        setIsCustomAudioModalOpen(true);
                      }}
                      createLabel={
                        voiceOptions?.isCustomAudio && "Add Custom Voice"
                      }
                    />
                    <Typography
                      typography={"s3"}
                      color={"text.secondary"}
                      fontWeight={"fontWeightMedium"}
                    >
                      Choose from a list of available voices
                    </Typography>
                    <ShowComponent
                      condition={
                        watchedModel &&
                        !voiceOptions?.voices?.length &&
                        !isLoadingVoiceOptions
                      }
                    >
                      <HelperText
                        sx={{
                          color: "red.500",
                          typography: "s2",
                        }}
                        text="Voice support isn't available for the selected model."
                      />
                    </ShowComponent>
                  </Stack>
                </ShowComponent>

                {/* <ConfigureKeys
                open={isApiConfigurationOpen}
                onClose={() => setApiConfigurationOpen(false)}
              /> */}
                <ShowComponent condition={watchedModelType === MODEL_TYPES.LLM}>
                  <PromptTemplateSection
                    allColumns={allColumns}
                    jsonSchemas={jsonSchemas}
                    derivedVariables={derivedVariables}
                    allInvalidVariables={allInvalidVariables}
                    clearErrors={clearErrors}
                    control={control}
                    errors={errors}
                    handleRemovePrompt={handleRemovePrompt}
                    promptHasBeenImported={promptHasBeenImported}
                    setOpenGeneratePromptDrawer={setOpenGeneratePromptDrawer}
                    setOpenImportPromptModal={setOpenImportPromptModal}
                    setOpenImprovePromptDrawer={setOpenImprovePromptDrawer}
                    setValue={setValue}
                    watch={watch}
                  />
                </ShowComponent>
                <ShowComponent condition={watchedModelType === MODEL_TYPES.STT}>
                  <PromptSTTInput
                    control={control}
                    allColumns={allColumns}
                    getValues={getValues}
                    setValue={setValue}
                    currentColId={editConfigData?.id}
                    promptHasBeenImported={promptHasBeenImported}
                    handleRemovePrompt={handleRemovePrompt}
                    setOpenImportPromptModal={setOpenImportPromptModal}
                  />
                </ShowComponent>
                <ShowComponent condition={watchedModelType === MODEL_TYPES.TTS}>
                  <PromptTTSInput
                    allColumns={allColumns}
                    jsonSchemas={jsonSchemas}
                    derivedVariables={derivedVariables}
                    allInvalidVariables={allInvalidVariables}
                    clearErrors={clearErrors}
                    errors={errors}
                    setValue={setValue}
                    watch={watch}
                    control={control}
                    currentColId={editConfigData?.id}
                    promptHasBeenImported={promptHasBeenImported}
                    handleRemovePrompt={handleRemovePrompt}
                    setOpenImportPromptModal={setOpenImportPromptModal}
                  />
                </ShowComponent>
                <ShowComponent
                  condition={watchedModelType === MODEL_TYPES.IMAGE}
                >
                  <PromptImageInput
                    allColumns={allColumns}
                    jsonSchemas={jsonSchemas}
                    derivedVariables={derivedVariables}
                    allInvalidVariables={allInvalidVariables}
                    watch={watch}
                    control={control}
                    currentColId={editConfigData?.id}
                    promptHasBeenImported={promptHasBeenImported}
                    handleRemovePrompt={handleRemovePrompt}
                    setOpenImportPromptModal={setOpenImportPromptModal}
                  />
                </ShowComponent>

                <Popover
                  open={Boolean(modelParamsAnchorEl)}
                  anchorEl={modelParamsAnchorEl}
                  onClose={handleCloseModelParams}
                  anchorOrigin={{
                    vertical: "bottom",
                    horizontal: "right",
                  }}
                  transformOrigin={{
                    vertical: "top",
                    horizontal: "right",
                  }}
                  PaperProps={{
                    sx: {
                      p: 2,
                      width: 500,
                      borderRadius: 1,
                    },
                  }}
                >
                  {draftModelParameters && (
                    <ModelOptionsState
                      setModelParameters={setDraftModelParameters}
                      modelParameters={draftModelParameters}
                      responseSchema={responseSchema}
                      hideTools
                      hideAccordion
                      modelResponseFormat={modelParams?.responseFormat}
                      control={control}
                      fieldNamePrefix="config"
                      reasoning={modelParams?.reasoning}
                      reasoningState={draftReasoningState}
                      setReasoningState={setDraftReasoningState}
                      showActions
                      disableActions={!hasModelParamsChanges}
                      onApply={handleApplyModelParams}
                      onClear={handleClearModelParams}
                    />
                  )}
                </Popover>
                <ToolsConfigSection
                  selectedTools={selectedTools}
                  onOpenCustomToolModal={() => setOpenCustomToolModal(true)}
                  setEditTool={setEditTool}
                  tools={
                    importedPrompt.promptVersion?.promptConfigSnapshot
                      ?.configuration?.tools
                  }
                  control={control}
                  setValue={setValue}
                />
                <Stack gap={0.5}>
                  <FormSearchSelectFieldControl
                    control={control}
                    fieldName="config.concurrency"
                    label="Concurrency"
                    variant="outlined"
                    placeholder="Enter concurrency"
                    fullWidth
                    size="small"
                    fieldType="number"
                    required
                    isSpinnerField
                    options={Array.from({ length: 10 }).map((_, index) => ({
                      label: `${index + 1}`,
                      value: index + 1,
                    }))}
                  />
                  <Typography
                    typography={"s3"}
                    color={"text.secondary"}
                    fontWeight={"fontWeightMedium"}
                  >
                    Choose how many rows the system should process
                    simultaneously.
                  </Typography>
                </Stack>
                <Button
                  disabled={
                    !importedPrompt?.prompt?.id ? true : !isPromptTemplateDirty
                  }
                  onClick={async () => {
                    const isFormValid = await validateForm();
                    if (!isFormValid) return false;
                    setOpenSavePromptModal(true);
                    trackEvent(Events.datasetSaveTemplateClicked);
                  }}
                  color="primary"
                  variant="contained"
                  sx={{
                    borderRadius: "8px",
                    minWidth: "128px",
                    py: "6px",
                    ml: "auto",
                  }}
                >
                  <Typography
                    typography="s2"
                    fontWeight={"fontWeightSemiBold"}
                    color={"white"}
                  >
                    Save Template
                  </Typography>
                </Button>
              </Box>

              <Modal
                open={testRun}
                onClose={() => {
                  setTestRun(false);
                  setShowOutput(false);
                }}
                sx={{
                  display: "flex",
                  flexWrap: "wrap",
                  alignItems: "center",
                  p: "15px",
                  "& .MuiModal-backdrop": {
                    backgroundColor: "#00000040",
                  },
                }}
              >
                <RunWrapper>
                  <RunHeader>
                    <RunTitle>Run Preview on Custom Rows</RunTitle>
                    <IconButton onClick={() => setTestRun(false)}>
                      <Iconify icon="mingcute:close-line" />
                    </IconButton>
                  </RunHeader>
                  <RunBody>
                    <RunList defaultValue={selected} name="radio-buttons-group">
                      {runList.map((item, idx) => (
                        <RunItem key={idx}>
                          <FormControlLabel
                            onClick={() => setSelected(item.value)}
                            value={item.value}
                            control={<Radio />}
                            label={item.label}
                          />
                          {selected === item.value && (
                            <>
                              {selected === "firstRows" && (
                                <IncrementerButton
                                  quantity={firstRow}
                                  onIncrease={handleIncrease}
                                  onDecrease={handleDecrease}
                                  disabledIncrease={firstRow >= 10}
                                  disabledDecrease={firstRow <= 1}
                                />
                              )}
                              {selected === "rowNumber" && (
                                <TextField
                                  defaultValue={rowNum}
                                  onChange={(e) => setRowNum(e.target.value)}
                                  size="small"
                                  variant="outlined"
                                />
                              )}
                            </>
                          )}
                        </RunItem>
                      ))}
                    </RunList>
                    <RunCta>
                      <Button
                        onClick={() => {
                          setTestRun(false);
                          setShowOutput(false);
                        }}
                        type="button"
                        size="small"
                        variant="outlined"
                      >
                        Cancel
                      </Button>
                      <Button
                        onClick={() => {
                          trackEvent(Events.runPromptTestSuccessful, {
                            method:
                              selected === "firstRows"
                                ? "First N rows"
                                : "Custom Row No",
                            custom_row_no:
                              selected === "firstRows" ? firstRow : rowNum,
                          });

                          onApply();
                        }}
                        type="button"
                        size="small"
                        variant="contained"
                        color="primary"
                      >
                        Apply and Run
                      </Button>
                    </RunCta>
                  </RunBody>
                </RunWrapper>
              </Modal>
              <CustomAudioDialog
                open={isCustomAudioModalOpen}
                onClose={() => {
                  setIsCustomAudioModalOpen(false);
                }}
                selectedModel={{
                  value: watchedModel,
                  providers: watchedModelProvider,
                }}
                onSuccess={handleSuccessCustomAudio}
              />
              <Popover
                open={open}
                anchorEl={anchorEl}
                onClose={handleClose}
                anchorOrigin={{
                  vertical: "top",
                  horizontal: "right",
                }}
                transformOrigin={{
                  vertical: "bottom",
                  horizontal: "right",
                }}
                sx={{
                  "& .MuiPaper-root": {
                    padding: 0,
                    marginTop: "-6px",
                  },
                }}
              >
                <Box sx={{ width: "268px" }}>
                  <RunBtnWrap
                    onClick={() => {
                      trackEvent(Events.runPromptTestClicked);
                      setTestRun(!testRun);
                    }}
                  >
                    <Button
                      size="small"
                      fullWidth
                      variant="contained"
                      sx={{
                        borderRadius: "6px",
                        color: "text.primary",
                        boxShadow:
                          "-20px 20px 40px -4px rgba(145, 158, 171, 0.24)",
                        backgroundColor: "action.hover",
                        "&:hover": {
                          backgroundColor: "action.hover",
                        },
                      }}
                    >
                      Test on custom rows
                    </Button>
                  </RunBtnWrap>
                </Box>
              </Popover>
              <Box
                sx={{
                  display: "flex",
                  gap: (theme) => theme.spacing(2),
                  paddingX: (theme) => theme.spacing(2),
                  marginBottom: (theme) => theme.spacing(2.5),
                }}
              >
                <LoadingButton
                  // onClick={handleSubmit(onSubmitPreview)}
                  onClick={async () => {
                    const isFormValid = await validateForm();
                    if (!isFormValid) return false;
                    if (allInvalidVariables.length > 0) return;
                    setOpenRunPreViewModal(true);
                  }}
                  loading={isPreviewLoading || isLoadingModelParams}
                  size="small"
                  fullWidth
                  variant="outlined"
                  sx={{
                    color: "text.secondary",
                    minHeight: (theme) => theme.spacing(38 / 8),
                    "&:hover": {
                      borderColor: "transparent !important",
                    },
                    "&:disabled": {
                      opacity: 0.6,
                    },
                  }}
                >
                  <Typography typography="s1" fontWeight={"fontWeightMedium"}>
                    Test
                  </Typography>
                </LoadingButton>

                <LoadingButton
                  fullWidth
                  variant="contained"
                  color="primary"
                  size="small"
                  onClick={async () => {
                    try {
                      const isFormValid = await validateForm();
                      if (!isFormValid) {
                        return;
                      }
                      if (importedPrompt?.prompt?.id) {
                        // template not saved
                        if (isPromptTemplateDirty) {
                          const isFormValid = await validateForm();
                          if (!isFormValid) return false;

                          setSaveAndRunMode(true);
                          setOpenSavePromptModal(true);
                        } else {
                          handleSubmit(onSubmit)();
                        }
                      } else {
                        handleSubmit(onSubmit)();
                      }
                    } catch (error) {
                      logger.error("[Run Button] Error:", error);
                    }
                  }}
                  loading={
                    isSubmittingPrompt ||
                    isUpdatingPrompt ||
                    isLoadingModelParams
                  }
                  sx={{
                    minHeight: (theme) => theme.spacing(38 / 8),
                  }}
                >
                  <Typography typography="s1" fontWeight={"fontWeightSemiBold"}>
                    {isEditMode ? "Update" : "Run"}
                  </Typography>
                </LoadingButton>
              </Box>
            </form>
          </Box>
          <ImportPrompt
            data={importedPrompt}
            handleApplyImportedPrompt={handleApplyImportedPrompt}
            open={openImportPromptModal}
            onClose={() => {
              setOpenImportPromptModal(false);
            }}
            allColumns={allColumns}
          />
          <RunPreviewModal
            open={openRunPreViewModal}
            onClose={() => setOpenRunPreViewModal(false)}
            onRun={onApply}
          />
          <SavePromptModal
            saveAndRunMode={saveAndRunMode}
            setsaveAndRunMode={setSaveAndRunMode}
            setInitialImportedData={setInitialImportedData}
            promptId={importedPrompt?.prompt?.id}
            promptData={watchedValues}
            modelParameters={modelParameters}
            open={openSavePromptModal}
            onClose={() => {
              setSaveAndRunMode(false);
              setOpenSavePromptModal(false);
            }}
            onSaveSuccess={() => handleSubmit(onSubmit)()}
            handleUpdateImportedPrompt={handleUpdateImportedPrompt}
            reasoningState={reasoningState}
          />
          <CustomToolModal
            editTool={editTool}
            setEditTool={setEditTool}
            open={openCustomToolModal}
            onClose={() => setOpenCustomToolModal(false)}
          />
          <GeneratePromptDrawer
            allColumns={allColumns}
            onApplyPrompt={handleApplyPrompt}
            promptFor={openGeneratePromptDrawer.index}
            open={openGeneratePromptDrawer.state}
            onClose={() =>
              setOpenGeneratePromptDrawer({
                index: null,
                state: false,
              })
            }
          />
          <ImprovePromptDrawer
            open={openImprovePromptDrawer.state}
            onClose={() => {
              setOpenImprovePromptDrawer({
                index: null,
                state: false,
              });
            }}
            variables={allColumns?.map((col) => col.headerName) ?? []}
            existingPrompt={
              watchedMessages?.[openImprovePromptDrawer?.index]?.content ?? ""
            }
            onApplyPrompt={handleApplyPrompt}
            promptFor={openImprovePromptDrawer.index}
          />
        </>
      );
    };

    const handleConfirmClose = () => {
      setConfirmationModalOpen(false);
      onClose();
      resetPreviewData();
      reset();
      setValue("name", "");
      setImportedPrompt({
        prompt: null,
        promptVersion: null,
      });
    };

    const handleCancelClose = () => {
      setConfirmationModalOpen(false);
    };

    const open = Boolean(anchorEl);

    const handleViewDocs = () => {
      window.open(
        "https://docs.futureagi.com/docs/dataset/features/run-prompt",
        "_blank",
      );
    };

    return (
      <AudioPlaybackProvider>
        <Stack
          sx={{
            position: "absolute",
            top: "12px",
            right: "12px",
            flexDirection: "row",
            gap: 1.5,
            alignItems: "center",
          }}
        >
          <Button
            size="small"
            variant="outlined"
            onClick={handleViewDocs}
            startIcon={
              <SvgColor
                sx={{
                  height: "16px",
                  width: "16px",
                }}
                src="/assets/icons/ic_paper.svg"
              />
            }
            sx={{
              px: 1.5,
            }}
          >
            View Docs
          </Button>
          <IconButton
            onClick={onCloseHandler}
            size="small"
            sx={{
              color: "text.primary",
            }}
          >
            <Iconify icon="akar-icons:cross" />
          </IconButton>
        </Stack>
        <Box
          sx={{
            display: "flex",
            height: "100%",
            width: "100%",
            justifyContent: "flex-end",
            backgroundColor: "background.paper",
          }}
        >
          <Collapse in={isSuccess && previewData} orientation="horizontal">
            <TestRunPrompt
              previewData={previewData}
              modelType={watchedModelType}
            />
          </Collapse>
          {/* {showOutput && <PromptCard />} */}
          {runPromptForm()}
          <ConfirmDialog
            content="Are you sure you want to close? Your work will be lost"
            action={
              <Button
                variant="contained"
                color="error"
                size="small"
                onClick={handleConfirmClose}
              >
                Confirm
              </Button>
            }
            open={isConfirmationModalOpen}
            onClose={handleCancelClose}
            title="Confirm Action"
            message="Are you sure you want to proceed?"
          />
        </Box>
      </AudioPlaybackProvider>
    );
  },
);

RunPromptForm.displayName = "RunPromptForm";

RunPromptForm.propTypes = {
  onClose: PropTypes.func,
  editConfigData: PropTypes.object,
  isConfirmationModalOpen: PropTypes.bool,
  setConfirmationModalOpen: PropTypes.func,
  initialData: PropTypes.object,
  onFormSubmit: PropTypes.func,
  initialModelParams: PropTypes.array,
  initialReasoningState: PropTypes.object,
};

const RunPrompt = () => {
  const { openRunPrompt, setOpenRunPrompt } = useRunPromptStore();
  const isEditMode = typeof openRunPrompt === "object";
  const configureRunPrompt =
    typeof openRunPrompt === "object" ? openRunPrompt : null;
  const [isConfirmationModalOpen, setConfirmationModalOpen] = useState(false);
  const queryClient = useQueryClient();
  const onClose = () => {
    queryClient.invalidateQueries({
      queryKey: ["run-prompt-data", configureRunPrompt?.id],
    });
    setOpenRunPrompt(null);
  };

  const formRef = useRef();

  const {
    data: runPromptDataRaw,
    isSuccess,
    isPending,
  } = useQuery({
    queryFn: () =>
      axios.get(endpoints.develop.runPrompt.getRunPrompt(), {
        params: { column_id: configureRunPrompt?.id },
      }),
    queryKey: ["run-prompt-data", configureRunPrompt?.id],
    enabled: Boolean(openRunPrompt && isEditMode && configureRunPrompt?.id),
  });

  // Backend stores both snake_case and camelCase copies of these fields
  // on `run_prompt_config`, and for some records (e.g. TTS configs) the
  // values diverge — `modelType: ""` while `model_type: "tts"`. Read
  // snake_case first with a camelCase fallback so the form hydrates with
  // the real value.
  const runPromptConfig =
    runPromptDataRaw?.data?.result?.config?.run_prompt_config ??
    runPromptDataRaw?.data?.result?.config?.runPromptConfig;
  const model = runPromptDataRaw?.data?.result?.config?.model;
  const provider = runPromptConfig?.providers;
  const modelType = runPromptConfig?.model_type || runPromptConfig?.modelType;

  const modelParamsEnabled = !!(model && provider && modelType);
  const { data: modelParams, isPending: isModelParamsPending } = useQuery({
    queryKey: ["model-params", model],
    queryFn: () =>
      axios.get(endpoints.develop.modelParams, {
        params: {
          model: model,
          provider: provider,
          model_type: modelType,
        },
      }),
    enabled: modelParamsEnabled,
    select: (d) => d.data?.result,
  });

  const runPromptData = runPromptDataRaw?.data?.result?.config;

  const transformedModelParamsSliders = modelParams?.sliders?.map((item) => {
    if (
      runPromptData?.runPromptConfig[_.camelCase(item?.label)] !== undefined
    ) {
      return {
        ...item,
        id: _.camelCase(item?.label),
        value:
          runPromptData?.runPromptConfig[_.camelCase(item?.label)] ??
          item?.default,
      };
    }
    return {
      ...item,
      id: _.camelCase(item?.label),
      value: item?.value !== undefined ? item.value : item?.default,
    };
  });

  const finalTransformation = {
    sliders: transformedModelParamsSliders ?? [],
    ...(modelParams?.booleans && {
      booleans: transformParameterType(
        modelParams?.booleans,
        runPromptData?.runPromptConfig,
        "booleans",
      ),
    }),
    ...(modelParams?.dropdowns && {
      dropdowns: transformParameterType(
        modelParams?.dropdowns,
        runPromptData?.runPromptConfig,
        "dropdowns",
      ),
    }),
  };

  const reasoningConfig = modelParams?.reasoning;
  const savedReasoning = runPromptData?.runPromptConfig?.reasoning;

  const transformedReasoningSliders = reasoningConfig?.sliders?.map((item) => {
    if (savedReasoning?.sliders?.[_.camelCase(item?.label)] !== undefined) {
      return {
        ...item,
        id: _.camelCase(item?.label),
        value:
          savedReasoning?.sliders?.[_.camelCase(item?.label)] ?? item?.default,
      };
    }
    return {
      ...item,
      id: _.camelCase(item?.label),
      value: item?.value !== undefined ? item.value : item?.default,
    };
  });
  const initialReasoningState = reasoningConfig
    ? {
        sliders: transformedReasoningSliders,
        dropdowns: transformParameterType(
          reasoningConfig?.dropdowns,
          savedReasoning,
          "dropdowns",
        ),
        showReasoningProcess: savedReasoning?.showReasoningProcess ?? true,
      }
    : null;

  // A disabled useQuery reports isPending: true indefinitely, so only
  // block the form on modelParams when the query is actually enabled.
  // Otherwise the drawer opens empty whenever the run-prompt config
  // doesn't expose model/provider/modelType.
  const showPromptForm = isEditMode
    ? isSuccess &&
      !isPending &&
      (!modelParamsEnabled || !isModelParamsPending)
    : true;

  const handleClose = () => {
    // Todo add form loading check
    if (formRef.current?.isDirty) {
      setConfirmationModalOpen(true);
    } else {
      onClose();
    }
  };

  return (
    <Drawer
      anchor="right"
      open={openRunPrompt}
      onClose={handleClose}
      PaperProps={{
        sx: {
          height: "100vh",
          position: "fixed",
          zIndex: 2,
          boxShadow: "-10px 0px 100px #00000035",
          borderRadius: "10px",
          overflow: "visible",
          // width: "613px",
        },
      }}
      ModalProps={{
        BackdropProps: {
          style: { backgroundColor: "transparent" },
        },
      }}
      sx={{
        zIndex: 1099,
      }}
    >
      {showPromptForm && (
        <RunPromptForm
          onClose={onClose}
          editConfigData={
            isEditMode
              ? {
                  id: configureRunPrompt?.id,
                  improvePrompt: configureRunPrompt?.improvePrompt,
                  ...runPromptData,
                }
              : null
          }
          initialModelParams={finalTransformation}
          initialReasoningState={initialReasoningState}
          ref={formRef}
          isConfirmationModalOpen={isConfirmationModalOpen}
          setConfirmationModalOpen={setConfirmationModalOpen}
        />
      )}
    </Drawer>
  );
};

RunPrompt.propTypes = {};

export default RunPrompt;
