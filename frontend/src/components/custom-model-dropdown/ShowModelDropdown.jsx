import {
  Box,
  Button,
  Checkbox,
  Divider,
  Grid,
  MenuItem,
  Popover,
  Skeleton,
  Stack,
  Typography,
} from "@mui/material";
import _ from "lodash";
import PropTypes from "prop-types";
import React, {
  forwardRef,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import Image from "src/components/image";
import ScrollingWrapper from "./ScrollingWrapper";
import FormSearchField from "../FormSearchField/FormSearchField";
import { getRandomId } from "src/utils/utils";
import Iconify from "../iconify";
import { ShowComponent } from "../show";
import CustomModalAvatar from "./CustomModalAvatar";
import EachCustomVoiceModel from "./EachCustomVoiceModel";
import { MODEL_TYPES } from "src/sections/develop-detail/RunPrompt/common";
import ChipSelector from "../chip-selector";
import { LOGO_WITH_BLACK_BACKGROUND } from "./common";
import {
  MODEL_DROPDOWN_STEPS,
  MODEL_DROPDOWN_MESSAGES,
  MODEL_DROPDOWN_MODES,
} from "./constants";

const MODEL_TYPE_OPTIONS = [
  { label: "All", value: "all" },
  { label: "Text", value: "llm" },
  { label: "Voice", value: "tts" },
  { label: "Image", value: "image" },
];

const ShowModelDropdown = forwardRef(
  (
    {
      open,
      onClose,
      id,
      searchDropdown,
      setIsFocus,
      searchQuery,
      setSearchQuery,
      onChange,
      options,
      value,
      isLoadingModelList,
      fetchNextPage,
      isFetchingNextPage,
      onConfigOpen,
      setSelectedModels,
      onCustomModelClick,
      multiple,
      disableClickOutside,
      openKeyConfig,
      inputRef,
      showButtons = false,
      requireUserApiKey = true,
      allowSelectingVoices = false,
      onModelTypeChange,
      modelType,
      ...rest
    },
    ref,
  ) => {
    const popperRef = useRef(null);
    const [mode, setMode] = useState(MODEL_DROPDOWN_MODES.BOTTOM);
    // Local state for temporary selections when showButtons is true
    const [localValue, setLocalValue] = useState(value);
    const [currentStep, setCurrentStep] = useState(MODEL_DROPDOWN_STEPS.MODELS);
    const [customAudioDialogOpen, setCustomAudioDialogOpen] = useState(false);
    const [validationError, setValidationError] = useState("");
    const voicesRef = useRef([]);
    // Sync localValue with value when dropdown opens or value changes externally
    useEffect(() => {
      if (showButtons && open) {
        setLocalValue(value);
      }
    }, [open, value, showButtons]);

    useEffect(() => {
      if (!ref?.current) return;
      const { bottom } = ref.current.getBoundingClientRect();
      const spaceBelow = window.innerHeight - bottom;
      setMode(
        spaceBelow < 380
          ? MODEL_DROPDOWN_MODES.TOP
          : MODEL_DROPDOWN_MODES.BOTTOM,
      );
    }, [open, ref, options.length]);

    const position = {
      width: 400,
      height: 250,
    };

    const fieldWidth = useMemo(() => {
      return ref?.current?.offsetWidth
        ? ref?.current?.offsetWidth
        : position?.width;
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [open, ref, position]);

    useEffect(() => {
      function handleClickOutside(event) {
        if (popperRef?.current && !popperRef?.current?.contains(event.target)) {
          onClose();
        }
      }
      // Disable click-outside detection when custom audio dialog is open or when disableClickOutside is true
      if (!disableClickOutside && !customAudioDialogOpen) {
        document.addEventListener("mousedown", handleClickOutside);
      }
      return () => {
        document.removeEventListener("mousedown", handleClickOutside);
      };
    }, [popperRef, open, disableClickOutside, onClose, customAudioDialogOpen]);

    const handleOnClick = useCallback(
      (e, option) => {
        if (!option.isAvailable && requireUserApiKey) {
          onConfigOpen?.(option);
          if (multiple) {
            onClose();
          }
          return;
        }

        if (multiple && requireUserApiKey) {
          inputRef?.current?.focus();

          // Use localValue if showButtons is true, otherwise use value
          const currentValue = showButtons ? localValue : value;
          const newValue = new Set(currentValue?.map((v) => v?.value));

          if (newValue.has(option.model_name)) {
            newValue.delete(option.model_name);
          } else {
            newValue.add(option.model_name);
          }

          // Build final list with correct logoUrl
          const finalValues = Array.from(newValue).map((modelName) => {
            const matched = options?.find(
              (opt) => opt?.model_name === modelName,
            );
            const existingItem = currentValue?.find(
              (item) => item?.value === modelName,
            );

            return {
              id: existingItem?.id ?? getRandomId(),
              value: modelName,
              logoUrl: existingItem?.logoUrl ?? matched?.logoUrl ?? "",
              providers: existingItem?.providers ?? matched?.providers ?? "",
              ...(allowSelectingVoices && {
                voices: existingItem?.voices ?? matched?.voices ?? [],
              }),
            };
          });

          if (showButtons) {
            // Only update local state when showButtons is true
            setLocalValue(finalValues);
          } else {
            // Immediately call onChange when showButtons is false
            const event = {
              ...e,
              target: {
                ...e.target,
                value: finalValues,
              },
            };
            if (
              allowSelectingVoices &&
              currentStep === MODEL_DROPDOWN_STEPS.MODELS
            ) {
              setCurrentStep(MODEL_DROPDOWN_STEPS.VOICES);
              return;
            }
            onChange?.(event);
          }
          return;
        } else {
          const event = {
            ...e,
            target: { ...e.target, value: option.model_name, option },
          };
          onChange?.(event);
          setIsFocus?.(false);
          setSearchQuery?.("");
          onClose();
        }
      },
      [
        onChange,
        showButtons,
        localValue,
        value,
        options,
        multiple,
        inputRef,
        onConfigOpen,
        onClose,
        setIsFocus,
        setSearchQuery,
        allowSelectingVoices,
        currentStep,
        requireUserApiKey,
      ],
    );

    const handleAdd = () => {
      if (multiple && showButtons) {
        const event = {
          target: {
            value: localValue,
          },
        };
        if (allowSelectingVoices) {
          if (localValue?.length === 0) {
            setValidationError(
              MODEL_DROPDOWN_MESSAGES.MIN_MODEL_SELECTION_ERROR,
            );
            setTimeout(() => setValidationError(""), 4000);
            return;
          }
          setValidationError("");
          setCurrentStep(MODEL_DROPDOWN_STEPS.VOICES);
          return;
        }
        onChange?.(event);
      }
      onClose();
      setIsFocus?.(false);
      setSearchQuery?.("");
      inputRef?.current?.blur();
    };

    const handleClear = () => {
      if (showButtons) {
        // Clear local selection only
        if (multiple) {
          setLocalValue([]);
        } else {
          setLocalValue({
            modelName: "",
            providers: "",
            isAvailable: false,
            logoUrl: "",
          });
        }
      } else {
        // Immediately clear via onChange when showButtons is false
        if (multiple) {
          onChange({ target: { value: [] } });
        } else {
          onChange({
            target: {
              value: {
                modelName: "",
                providers: "",
                isAvailable: false,
                logoUrl: "",
              },
            },
          });
        }
      }
      setCurrentStep(MODEL_DROPDOWN_STEPS.MODELS);
    };

    // Determine which value to display in checkboxes
    const displayValue = showButtons && multiple ? localValue : value;
    const handleAddClick = () => {
      if (currentStep === MODEL_DROPDOWN_STEPS.VOICES) {
        const updated = Array.isArray(localValue)
          ? localValue.map((model, idx) => {
              const ref = voicesRef.current[idx];
              const voices =
                ref && ref.getSelectedVoices
                  ? ref.getSelectedVoices()
                  : model.voices || [];
              return { ...model, voices };
            })
          : localValue;
        setLocalValue(updated);
        const event = { target: { value: updated } };
        onChange?.(event);
        onClose();
        setCurrentStep(MODEL_DROPDOWN_STEPS.MODELS);
        setIsFocus?.(false);
        setSearchQuery?.("");
        inputRef?.current?.blur();
      } else {
        handleAdd();
      }
    };
    const handleClearClick = () => {
      if (currentStep === MODEL_DROPDOWN_STEPS.VOICES) {
        setCurrentStep(MODEL_DROPDOWN_STEPS.MODELS);
        return;
      } else {
        handleClear();
      }
    };
    const selectedValues = useMemo(
      () =>
        Array.isArray(displayValue) ? displayValue.map((v) => v?.value) : [],
      [displayValue],
    );

    return (
      <Popover
        id={id}
        anchorEl={ref?.current}
        open={open}
        ref={popperRef}
        onClose={() => {
          !(disableClickOutside || customAudioDialogOpen) && onClose();
          setCurrentStep(MODEL_DROPDOWN_STEPS.MODELS);
          setIsFocus?.(false);
        }}
        onClick={(e) => e.stopPropagation()}
        anchorOrigin={{
          vertical: searchDropdown && rest?.error ? 40 : "bottom",
          horizontal: "left",
        }}
        disableRestoreFocus
        disableEnforceFocus
        disableAutoFocus
        sx={{
          ...(openKeyConfig && { zIndex: 1000 }),
          mt: mode === "top" ? "-40px" : 0,
          "& .MuiPaper-root": {
            bgcolor: "background.paper",
            border: "1px solid",
            borderColor: "divider",
            p: "12px",
            marginTop: "-2px",
            borderRadius: "0px 0px 4px 4px !important",
          },
        }}
        transformOrigin={{
          vertical: mode === "bottom" ? "top" : "bottom",
          horizontal: "left",
        }}
      >
        <ShowComponent condition={onModelTypeChange}>
          <ChipSelector
            options={MODEL_TYPE_OPTIONS}
            value={modelType}
            onChange={onModelTypeChange}
            sx={{ mb: 1.5 }}
          />
        </ShowComponent>
        <Box
          sx={{
            width: fieldWidth - 26,
            minHeight: 300,
            display: "flex",
            flexDirection: "column",
          }}
        >
          <ShowComponent
            condition={currentStep === MODEL_DROPDOWN_STEPS.MODELS}
          >
            {!searchDropdown && (
              <FormSearchField
                {...rest}
                autoComplete="off"
                autoFocus
                placeholder={MODEL_DROPDOWN_MESSAGES.SEARCH_PLACEHOLDER}
                type="text"
                size="small"
                hiddenLabel
                fullWidth
                onChange={(e) => setSearchQuery(e.target.value)}
                searchQuery={searchQuery}
                aria-describedby={id}
                sx={{
                  input: { color: "text.secondary" },
                  position: "sticky",
                  marginBottom: 1,
                  top: 0,
                  zIndex: 10,
                  backgroundColor: "background.paper",
                  "& .MuiOutlinedInput-root:hover .MuiOutlinedInput-notchedOutline":
                    {
                      borderColor: "action.hover",
                    },
                  "& .MuiOutlinedInput-root.Mui-focused .MuiOutlinedInput-notchedOutline":
                    {
                      borderColor: "action.hover",
                      borderWidth: "2px",
                    },
                }}
              />
            )}
            {!rest?.hideCreateLabel && (
              <MenuItem
                onClick={() => {
                  onClose();
                  onCustomModelClick?.();
                }}
                sx={{
                  px: "6px",
                  py: "6px",
                  borderRadius: "6px",
                  display: "flex",
                  alignItems: "center",
                  gap: 1,
                  minWidth: "350px",
                }}
              >
                <Iconify icon="solar:add-linear" width={16} />
                <Typography
                  variant="s1"
                  fontWeight="fontWeightMedium"
                  color="primary.main"
                >
                  Add custom model
                </Typography>
              </MenuItem>
            )}
            <ScrollingWrapper
              position={position}
              width={fieldWidth - 26}
              scrollFunction={() => {
                if (isFetchingNextPage || isLoadingModelList) return;
                fetchNextPage();
              }}
              dependancies={[isFetchingNextPage, isLoadingModelList]}
              sx={{ flex: 1 }}
            >
              <>
                {isLoadingModelList && (
                  <Box>
                    <Skeleton variant="text" height={34} width={360} />
                    <Skeleton variant="text" height={34} width={360} />
                    <Skeleton variant="text" height={34} width={360} />
                  </Box>
                )}
                {!isLoadingModelList &&
                  options?.map((option, index) => {
                    const {
                      modelName,
                      providers,
                      isAvailable,
                      logoUrl,
                      disabled,
                      type,
                      ...restOption
                    } = option;

                    if (option?.value === "no") {
                      return (
                        <MenuItem
                          key={index}
                          disabled={disabled}
                          {...restOption}
                          sx={{ minWidth: "350px" }}
                        >
                          <Typography
                            variant="s1"
                            fontWeight={"fontWeightRegular"}
                            color={"text.disabled"}
                            noWrap
                          >
                            {option.label}
                          </Typography>
                        </MenuItem>
                      );
                    }

                    return (
                      <MenuItem
                        key={modelName + index}
                        value={modelName}
                        selected={modelName === value}
                        disabled={disabled}
                        onClick={(e) => {
                          if (!disabled) {
                            handleOnClick(e, option);
                          }
                        }}
                        {...restOption}
                        sx={{
                          padding: "4px 6px",
                          minWidth: "350px",
                          width: "100%",
                          pointerEvents: "all",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          display: "flex",
                          alignItems: "center",
                          gap: 1.25,
                          "&.Mui-disabled": {
                            pointerEvents: "auto",
                            cursor: "pointer",
                            opacity: 1,
                            // eslint-disable-next-line no-restricted-syntax -- ds: error state of unavailable option
                            color: "status.danger.fg",
                          },
                        }}
                      >
                        {multiple && (
                          <Checkbox
                            size="small"
                            checked={selectedValues.includes(modelName)}
                            onClick={(e) => e.stopPropagation()}
                            onChange={(e) => {
                              e.stopPropagation();
                              handleOnClick(e, option);
                            }}
                            sx={{
                              p: 0,
                              alignSelf: "center",
                              color: "text.disabled",
                            }}
                          />
                        )}
                        <ShowComponent condition={logoUrl}>
                          <Image
                            src={logoUrl}
                            alt=""
                            width="16px"
                            disableThemeFilter={
                              !LOGO_WITH_BLACK_BACKGROUND.includes(
                                providers?.toLowerCase(),
                              )
                            }
                            sx={{ verticalAlign: "middle" }}
                            flexShrink={0}
                          />
                        </ShowComponent>
                        <ShowComponent condition={!logoUrl}>
                          <CustomModalAvatar
                            text={modelName}
                            width={16}
                            height={16}
                            fontSize={12}
                          />
                        </ShowComponent>
                        <Box
                          sx={{
                            display: "flex",
                            alignItems: "center",
                            flexWrap: "wrap",
                            gap: 0.5,
                          }}
                        >
                          <Typography
                            variant="s1"
                            fontWeight={"fontWeightRegular"}
                            color="text.primary"
                            sx={{
                              whiteSpace: "normal",
                            }}
                          >
                            {modelName}
                          </Typography>
                          {requireUserApiKey && !isAvailable && (
                            <Typography
                              sx={{
                                lineHeight: "14px",
                                fontSize: "12px",
                                color: "status.danger.fg",
                                fontWeight: 400,
                              }}
                            >
                              (Configure an api key for {providers} ai)
                            </Typography>
                          )}
                        </Box>
                      </MenuItem>
                    );
                  })}
                {isFetchingNextPage && (
                  <Box>
                    <Skeleton variant="text" height={34} width={360} />
                    <Skeleton variant="text" height={34} width={360} />
                    <Skeleton variant="text" height={34} width={360} />
                  </Box>
                )}
              </>
            </ScrollingWrapper>
          </ShowComponent>

          <ShowComponent
            condition={
              currentStep === MODEL_DROPDOWN_STEPS.VOICES &&
              Array.isArray(localValue) &&
              localValue?.length > 0
            }
          >
            <Box
              sx={{ width: "100%", maxHeight: 350, overflowY: "auto", flex: 1 }}
            >
              <Grid
                container
                spacing={2}
                columns={3}
                sx={{ width: "100%", flex: 1 }}
              >
                {Array.isArray(localValue) &&
                  localValue.map((model, index) => (
                    <Grid item xs={1} key={model.value}>
                      <EachCustomVoiceModel
                        ref={(el) => (voicesRef.current[index] = el)}
                        selectedModel={model}
                        modelType={MODEL_TYPES.TTS}
                        onCustomAudioDialogOpen={(isOpen) =>
                          setCustomAudioDialogOpen(isOpen)
                        }
                      />
                    </Grid>
                  ))}
              </Grid>
            </Box>
          </ShowComponent>
          {validationError && (
            <Typography sx={{ color: "error.main", mt: 1 }} variant="s2">
              {validationError}
            </Typography>
          )}
          {showButtons && (
            <Stack marginY={"auto"}>
              <Divider sx={{ my: 0 }} />
              <Stack
                direction="row"
                spacing={1}
                sx={{
                  paddingX: 1.5,
                  paddingTop: 1.5,
                  bgcolor: "background.paper",
                  position: "sticky",
                  bottom: 0,
                  zIndex: 10,
                  justifyContent: "flex-end",
                }}
              >
                <Button
                  variant="outlined"
                  size="small"
                  onClick={handleClearClick}
                >
                  <Typography
                    variant="s2"
                    fontWeight="fontWeightMedium"
                    color="text.primary"
                  >
                    {currentStep === MODEL_DROPDOWN_STEPS.VOICES
                      ? "Back"
                      : "Clear"}
                  </Typography>
                </Button>
                <Button
                  variant="contained"
                  size="small"
                  color="primary"
                  onClick={handleAddClick}
                >
                  <Typography variant="s2" fontWeight="fontWeightMedium">
                    {allowSelectingVoices &&
                    currentStep === MODEL_DROPDOWN_STEPS.MODELS
                      ? "Next"
                      : "Add"}
                  </Typography>
                </Button>
              </Stack>
            </Stack>
          )}
        </Box>
      </Popover>
    );
  },
);

export default ShowModelDropdown;

ShowModelDropdown.displayName = "ShowModelDropdown";

ShowModelDropdown.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  id: PropTypes.string,
  searchDropdown: PropTypes.bool,
  setIsFocus: PropTypes.func,
  searchQuery: PropTypes.string,
  setSearchQuery: PropTypes.func,
  onChange: PropTypes.func,
  options: PropTypes.array,
  value: PropTypes.string,
  isLoadingModelList: PropTypes.bool,
  fetchNextPage: PropTypes.func,
  isFetchingNextPage: PropTypes.bool,
  onConfigOpen: PropTypes.func,
  setSelectedModels: PropTypes.func,
  onCustomModelClick: PropTypes.func,
  multiple: PropTypes.bool,
  inputRef: PropTypes.object,
  disableClickOutside: PropTypes.bool,
  showIcon: PropTypes.bool,
  openKeyConfig: PropTypes.object,
  showButtons: PropTypes.bool,
  requireUserApiKey: PropTypes.bool,
  allowSelectingVoices: PropTypes.bool,
  onModelTypeChange: PropTypes.func,
  modelType: PropTypes.string,
};
