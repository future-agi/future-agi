import { localProviderLogo } from "src/utils/localProviderLogo";
import {
  Box,
  ButtonBase,
  IconButton,
  Typography,
  Stack,
  Divider,
  Skeleton,
} from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import PropTypes from "prop-types";
import { useEffect, useMemo, useRef, useState } from "react";
import { useWatch } from "react-hook-form";
import Image from "src/components/image";
import SvgColor from "src/components/svg-color";
import axios, { endpoints } from "src/utils/axios";
import { MODEL_TYPES } from "../../RunPrompt/common";
import _ from "lodash";
import ModelParamsPopover from "../ModelParamsPopover";
import { ShowComponent } from "src/components/show";
import { useVoiceOptions } from "src/api/develop/develop-detail";
import { getLabelFrom, getModelParamsDefaultValue } from "../common";
import CustomAudioDialog from "../../CustomAudioDialog";
import VoiceSelectorPopover from "../VoiceSelectorPopover";
import CustomModelDropdown from "src/components/custom-model-dropdown/CustomModelDropdown";
import Iconify from "src/components/iconify";
import { getRandomId } from "src/utils/utils";
import { isUUID } from "src/utils/utils";
import { inheritedParams } from "src/sections/common/RunSettings/modelRowParams";
import { offersModelParams } from "src/sections/common/RunSettings/modelCapabilities";
import { formModelParams } from "../modelParamNames";
/** IT-R5 G1 S5: `temp 0.2 · max 1,024 tokens` (null when neither is set). */
export function paramsSummary(p) {
  if (!p || typeof p !== "object") return null;
  const t = p.temperature;
  const m =
    p.maxTokens ?? p.max_tokens ?? p.maxCompletionTokens ?? p.maxOutputTokens;
  const tp = p.topP ?? p.top_p;
  const fp = p.frequencyPenalty ?? p.frequency_penalty;
  const pp = p.presencePenalty ?? p.presence_penalty;
  const rf = p.responseFormat ?? p.response_format;
  const has = (v) =>
    v !== null && v !== undefined && v !== "" && Number.isFinite(Number(v));
  const rfName =
    typeof rf === "string"
      ? rf
      : rf && typeof rf === "object"
        ? rf.name ?? rf.type ?? null
        : null;
  const bits = [
    has(t) ? `temp ${t}` : null,
    has(m) && Number(m)
      ? `max ${Number(m).toLocaleString("en-US")} token${Number(m) === 1 ? "" : "s"}`
      : null,
    // options-truth L1 G5: top_p shows once it is set below 1.
    has(tp) && Number(tp) < 1 ? `top_p ${tp}` : null,
    // options-truth L2 G8: penalties and a non-text response format show
    // once they leave their defaults, so every committed param is visible.
    has(fp) && Number(fp) !== 0 ? `freq_penalty ${fp}` : null,
    has(pp) && Number(pp) !== 0 ? `presence_penalty ${pp}` : null,
    rfName && rfName !== "text" ? `format ${rfName}` : null,
    p.reasoning != null ? `reasoning ${JSON.stringify(p.reasoning)}` : null,
  ].filter(Boolean);
  return bits.length ? bits.join(" · ") : null;
}

/**
 * The popover starts from what the card shows (options-truth L1 G5): the
 * prompt version's temperature / max tokens / top_p fill the model's own
 * parameter keys when the model has them.
 */
export function preloadParams(defaults, fallback) {
  const out = { ...(defaults ?? {}) };
  if (!fallback || typeof fallback !== "object") return out;
  const pairs = [
    ["temperature", fallback.temperature],
    ["maxTokens", fallback.maxTokens ?? fallback.max_tokens],
    ["topP", fallback.topP ?? fallback.top_p],
    ["presencePenalty", fallback.presencePenalty ?? fallback.presence_penalty],
    [
      "frequencyPenalty",
      fallback.frequencyPenalty ?? fallback.frequency_penalty,
    ],
    ["responseFormat", fallback.responseFormat ?? fallback.response_format],
    ["reasoning", fallback.reasoning],
  ];
  pairs.forEach(([k, v]) => {
    if (v === undefined || v === null || v === "") return;
    out[k] = v;
  });
  return out;
}

/**
 * v2.1 g4 (knob:prompt:version, knob:prompt:models): the params a new model
 * row gets once the prompt version's own params arrive after the model's
 * defaults were filled (the version loads later): the version's temperature
 * and max tokens replace values still at the model's defaults. A row the
 * user changed, or a kept run's row, keeps its values. Null = no change.
 * @param {object|null} current  the row's params now
 * @param {object|null} defaults  the model's defaults (same keys)
 * @param {object|null} version  the version's params (temperature,
 *   max_tokens / maxTokens)
 */
export function versionParamsUpdate(current, defaults, version) {
  if (!current || !version || typeof version !== "object") return null;
  const atDefault = (k) =>
    current[k] === undefined ||
    defaults?.[k] === undefined ||
    String(current[k]) === String(defaults[k]);
  if (!atDefault("temperature") || !atDefault("maxTokens")) return null;
  const next = preloadParams(current, version);
  return String(next.temperature) !== String(current.temperature) ||
    String(next.maxTokens) !== String(current.maxTokens)
    ? next
    : null;
}

const NewModelRenderWithParamsTool = ({
  selectedModel,
  modelIndex: _modelIndex,
  index,
  control,
  setValue,
  // options-truth L2 G8 (P1): callers pass `watch` here; `watch({control,
  // name})` returns the WHOLE form, which the effect below then wrote into
  // modelParams (form -> modelParams -> form ..., 1.9 MB POST, frozen page).
  // The real hook is used instead.
  useWatch: _useWatchProp,
  voiceFieldName = null,
  modelParamsFieldName,
  modelType,
  onRemove,
  showTool: _showTool = false,
  getValues: _getValues,
  deleteIcon = null,
  customBgColor = "background.paper",
  leading = null,
  fallbackParams = null,
  fallbackVersionId = null,
  fallbackParamsNote = null,
}) => {
  const [modelAnchorEl, setModelAnchorEl] = useState(null);
  const [voiceAnchorEl, setVoiceAnchorEl] = useState(null);
  const [customAudioOpen, setCustomAudioOpen] = useState(false);
  const { data: voiceOptions, isLoading: loadingVoices } = useVoiceOptions({
    model: selectedModel?.value,
    enabled: modelType === MODEL_TYPES.TTS,
  });
  const [open, setOpen] = useState(false);
  const modelContainerRef = useRef(null);
  // v2.1 g4: set when this row filled its own params (see versionKey).
  const filledFrom = useRef(null);
  const paramsQueryEnabled = !!(
    selectedModel?.value &&
    selectedModel?.providers &&
    modelType
  );
  const {
    data: modelParams,
    isSuccess,
    isLoading: _isLoadingModelParams,
  } = useQuery({
    queryKey: ["model-params", selectedModel?.value],
    queryFn: () =>
      axios.get(endpoints.develop.modelParams, {
        params: {
          model: selectedModel?.value,
          provider: selectedModel?.providers,
          model_type: modelType,
        },
      }),
    enabled: paramsQueryEnabled,
    select: (d) => d.data?.result,
  });
  // L5 G4: a model whose parameter list cannot be read (e.g. a custom model
  // without a provider) starts from the first model row's parameters, so
  // every row carries the same default set.
  const parentField = modelParamsFieldName
    ? modelParamsFieldName.slice(0, modelParamsFieldName.lastIndexOf("."))
    : null;
  const siblingParams = useWatch({ control, name: parentField ?? "" });
  const firstRowParams = useMemo(() => {
    if (!parentField || !siblingParams || typeof siblingParams !== "object")
      return null;
    return (
      Object.values(siblingParams).find(
        (p) => p && typeof p === "object" && Object.keys(p).length > 0,
      ) ?? null
    );
  }, [parentField, siblingParams]);
  const currentModelParams = useWatch({
    control,
    name: modelParamsFieldName,
  });
  const editableParams = Boolean(modelParams && offersModelParams(modelParams));
  // Never display version-only values that are absent from the submitted form.
  const paramsText = paramsSummary(currentModelParams);

  useEffect(() => {
    // Effective settings returned by the backend are canonical snake_case.
    // Put them in the editor's keys before opening an existing run's popover.
    if (currentModelParams) {
      const formParams = formModelParams(currentModelParams);
      if (!_.isEqual(formParams, currentModelParams)) {
        setValue(modelParamsFieldName, formParams, { shouldDirty: false });
        return;
      }
    }
    if (filledFrom.current?.field !== modelParamsFieldName)
      filledFrom.current = null;
    const previous = filledFrom.current;
    const version = preloadParams({}, fallbackParams);
    const versionId = fallbackVersionId ?? JSON.stringify(version);
    const current = currentModelParams ?? {};
    // Persisted effective values are the starting state, not session edits.
    // Keep them when opening a run, but re-seed untouched keys on version change.
    if (!previous && Object.keys(current).length) {
      filledFrom.current = {
        field: modelParamsFieldName,
        automatic: _.cloneDeep(current),
        last: _.cloneDeep(current),
        edited: new Set(),
        versionId,
        version,
        persisted: true,
      };
      return;
    }
    const edited = previous?.edited ?? new Set();
    if (previous) {
      new Set([...Object.keys(previous.last), ...Object.keys(current)]).forEach(
        (key) => {
          if (!_.isEqual(current[key], previous.last[key])) edited.add(key);
        },
      );
      if (previous.persisted && previous.versionId === versionId) {
        previous.last = _.cloneDeep(current);
        return;
      }
    }
    const defaults =
      isSuccess && modelParams
        ? getModelParamsDefaultValue(modelParams, {})
        : {};
    // An added row inherits the first row once, not its own watched values on
    // every render. A subsequent version selection uses the new version.
    const inherited = !previous
      ? inheritedParams(firstRowParams, fallbackParams)
      : previous.versionId === versionId
        ? previous.inherited
        : null;
    const automatic = preloadParams(
      preloadParams(defaults, version),
      inherited,
    );
    if (!previous && !Object.keys(automatic).length) return;
    const next = { ...(currentModelParams ?? {}) };
    new Set([
      ...Object.keys(previous?.automatic ?? {}),
      ...Object.keys(automatic),
    ]).forEach((key) => {
      // A user's edit stays an override across every subsequent version switch.
      if (!edited.has(key)) {
        if (key in automatic) next[key] = automatic[key];
        else delete next[key];
      }
    });
    filledFrom.current = {
      field: modelParamsFieldName,
      automatic,
      versionId,
      last: _.cloneDeep(next),
      edited,
      inherited,
    };
    if (!_.isEqual(next, currentModelParams))
      setValue(modelParamsFieldName, next, {
        shouldDirty: true,
        shouldValidate: true,
      });
  }, [
    modelParams,
    modelParamsFieldName,
    setValue,
    isSuccess,
    currentModelParams,
    fallbackParams,
    fallbackVersionId,
    firstRowParams,
  ]);
  const addNewVoice = (newVoice) => {
    const existing = selectedModel?.voices || [];
    setValue(
      voiceFieldName,
      [...existing, { voice: newVoice, promptConfigId: null }],
      { shouldValidate: true, shouldDirty: true },
    );
  };
  const handleSuccessCustomAudio = (res) => {
    const customId = res?.data?.result?.id;
    if (!customId) return;
    addNewVoice(customId);
  };
  const voicesString = selectedModel?.voices
    ?.map((voiceObj) => {
      const voice = typeof voiceObj === "string" ? voiceObj : voiceObj.voice;
      if (isUUID(voice)) {
        return getLabelFrom(voiceOptions, voice) || voice;
      } else {
        return _.startCase(voice);
      }
    })
    ?.join(", ");
  const handleModelChange = (e) => {
    const selectedModels = e.target.value;

    // Handle both single and multiple selections
    const modelsArray = Array.isArray(selectedModels)
      ? selectedModels
      : [selectedModels];

    // L7b G2: a model the card already ran keeps its run (adding a model
    // never marks the kept runs Removed).
    const before =
      (typeof _getValues === "function"
        ? _getValues(`promptConfig.${index}.model`)
        : null) ?? [];
    // Map each selected model to form structure
    const formattedModels = modelsArray.map((model) => {
      const value = model?.model_name || model?.value;
      const had = before.find((m) => m?.runId && m?.value === value);
      return {
        id: getRandomId(),
        value,
        ...model,
        ...(had ? { runId: had.runId, ranWith: had.ranWith } : {}),
      };
    });

    setValue(`promptConfig.${index}.model`, formattedModels);
  };
  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: 1,
        border: "1px solid",
        backgroundColor: customBgColor,
        borderColor: "divider",
        padding: 1.5,
        py: 1,
        borderRadius: "4px",
      }}
      ref={modelContainerRef}
    >
      {!selectedModel?.value ? (
        <CustomModelDropdown
          isModalContainer={true}
          openSelectModel={open}
          setOpenSelectModel={setOpen}
          hoverPlacement="bottom-start"
          buttonTitle="Select Model"
          buttonIcon={
            <Iconify
              icon="solar:graph-linear"
              width="16px"
              height="16px"
              sx={{
                cursor: "pointer",
                color: "text.primary",
              }}
            />
          }
          multiple={true}
          showButtons={true}
          value={selectedModel?.value || null}
          modelDetail={selectedModel?.modelDetail}
          onChange={handleModelChange}
          modelContainerRef={modelContainerRef}
          extraParams={{ model_type: "llm" }}
        />
      ) : (
        <Box
          sx={{
            display: "flex",
            flexDirection: "row",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <Box
            sx={{
              display: "flex",
              flexDirection: "row",
              alignItems: "center",
              gap: 1,
            }}
          >
            {leading}
            <Image
              ratio="1/1"
              src={localProviderLogo(selectedModel)}
              alt={selectedModel?.model_name}
              flexShrink={0}
              style={{
                width: "16px",
                height: "16px",
              }}
            />
            <Typography typography="s1" fontWeight="fontWeightMedium">
              {selectedModel?.value}
            </Typography>
          </Box>
          <Box
            sx={{
              display: "flex",
              flexDirection: "row",
              alignItems: "center",
              gap: 1,
            }}
          >
            {currentModelParams && offersModelParams(modelParams) && (
              <IconButton
                size="small"
                aria-label="Model parameters"
                onClick={(e) => setModelAnchorEl(e.currentTarget)}
                sx={{
                  width: 22,
                  backgroundColor: "background.default",
                  border: "1px solid",
                  borderColor: "divider",
                }}
              >
                <SvgColor
                  sx={{
                    height: "20px",
                    width: "20px",
                    color: "text.primary",
                  }}
                  src="/assets/prompt/slider-options.svg"
                />
              </IconButton>
            )}

            {onRemove && (
              <IconButton
                size="small"
                onClick={onRemove}
                aria-label={`Remove ${selectedModel?.label ?? selectedModel?.value ?? "model"}`}
                sx={{
                  width: 22,
                  backgroundColor: "background.default",
                  border: "1px solid",
                  borderColor: "divider",
                }}
              >
                <SvgColor
                  sx={{
                    height: "20px",
                    width: "20px",
                    color: deleteIcon ? "text.primary" : "error.main",
                  }}
                  src={deleteIcon ?? "/assets/icons/ic_delete.svg"}
                />
              </IconButton>
            )}
          </Box>
        </Box>
      )}

      <ShowComponent condition={modelType === MODEL_TYPES.TTS}>
        <Divider />
        <Typography typography={"s2"} fontWeight={"fontWeightMedium"}>
          Selected voice
        </Typography>

        <Stack direction={"row"} gap={0.75} alignItems={"center"}>
          {loadingVoices ? (
            <Skeleton
              sx={{
                bgcolor: "background.paper",
                borderRadius: "4px",
              }}
              variant="rounded"
              width="100%"
              height={16}
            />
          ) : (
            <Box
              sx={{
                border: "1px solid",
                display: "flex",
                flexDirection: "row",
                alignItems: "center",
                justifyContent: "space-between",
                borderColor: "divider",
                backgroundColor: "background.paper",
                padding: "4px 6px",
                borderRadius: "4px",
                width: "100%",
              }}
            >
              <Typography
                typography={"s2_1"}
                fontWeight={"fontWeightRegular"}
                sx={{
                  flex: 1,
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                }}
              >
                {voicesString}
              </Typography>

              <ShowComponent condition={!loadingVoices}>
                <IconButton
                  size="small"
                  onClick={(e) => setVoiceAnchorEl(e.currentTarget)}
                  sx={{
                    color: "text.primary",
                    alignItems: "flex-end",
                  }}
                  disabled={loadingVoices}
                >
                  <SvgColor
                    sx={{
                      height: "16px",
                      width: "16px",
                    }}
                    src={"/assets/icons/custom/lucide--chevron-down.svg"}
                  />
                </IconButton>
              </ShowComponent>
            </Box>
          )}
        </Stack>
      </ShowComponent>
      {/* IT-R5 G1 S5: the parameters at a glance; click to edit. */}
      {modelType === "llm" && paramsText ? (
        <Box sx={{ pl: 4.5, mt: -0.25 }}>
          <ButtonBase
            disabled={!editableParams}
            onClick={(e) => setModelAnchorEl(e.currentTarget)}
            data-testid="model-params-summary"
            title={
              editableParams
                ? "Edit the model's parameters"
                : fallbackParamsNote ?? "From the prompt version"
            }
            sx={{
              typography: "s3",
              color: "text.secondary",
              borderRadius: 0.5,
              px: 0.5,
              "&:hover": { color: "text.primary", bgcolor: "action.hover" },
            }}
          >
            {paramsText}
          </ButtonBase>
        </Box>
      ) : null}

      {Boolean(modelAnchorEl) && (
        <ModelParamsPopover
          anchorEl={modelAnchorEl}
          setAnchorEl={setModelAnchorEl}
          control={control}
          modelParams={modelParams}
          fieldName={modelParamsFieldName}
        />
      )}
      {/* Custom Audio Dialog */}
      <CustomAudioDialog
        open={customAudioOpen}
        onClose={() => {
          setCustomAudioOpen(false);
        }}
        selectedModel={{
          value: selectedModel?.value,
          providers: selectedModel?.providers,
        }}
        onSuccess={handleSuccessCustomAudio}
      />

      {/* Voice Selector Popover */}
      {Boolean(voiceAnchorEl) && (
        <VoiceSelectorPopover
          anchorEl={voiceAnchorEl}
          setAnchorEl={setVoiceAnchorEl}
          control={control}
          isCustomAudio={voiceOptions?.isCustomAudio}
          fieldName={voiceFieldName}
          openModal={() => {
            if (customAudioOpen) {
              return;
            }
            setVoiceAnchorEl(null);
            setCustomAudioOpen(true);
          }}
          options={voiceOptions?.voices}
          setValue={setValue}
          currentValue={selectedModel?.voices || []}
        />
      )}
    </Box>
  );
};

NewModelRenderWithParamsTool.propTypes = {
  /** Additive (IT-R5 G1 S5): the version's own params, when the model's
   * parameter list is not loaded (summary only). */
  fallbackParams: PropTypes.object,
  fallbackVersionId: PropTypes.string,
  fallbackParamsNote: PropTypes.string,
  selectedModel: PropTypes.object,
  modelIndex: PropTypes.number,
  index: PropTypes.number,
  control: PropTypes.object,
  setValue: PropTypes.func,
  useWatch: PropTypes.func,
  voiceFieldName: PropTypes.string,
  modelParamsFieldName: PropTypes.string,
  modelType: PropTypes.string,
  onRemove: PropTypes.func,
  showTool: PropTypes.bool,
  deleteIcon: PropTypes.string,
  getValues: PropTypes.func,
  customBgColor: PropTypes.string,
  leading: PropTypes.node,
};

export default NewModelRenderWithParamsTool;
