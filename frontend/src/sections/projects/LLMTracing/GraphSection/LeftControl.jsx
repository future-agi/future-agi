import { Box, Stack, TextField, Typography, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import React, {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useParams } from "react-router";
import { useGetTraceEvals } from "src/api/project/llm-tracing";
import axios, { endpoints } from "src/utils/axios";
import { useDebounce } from "src/hooks/use-debounce";
import {
  FormSearchSelectFieldControl,
  FormSearchSelectFieldState,
} from "src/components/FromSearchSelectField";
import { useForm } from "react-hook-form";
import ThumbsRatingSelectField from "./ThumbsRatingSelectField";
import CategoricalAnnotationSelectField from "./CategoricalAnnotationSelectField";
import { useQuery } from "@tanstack/react-query";
import _ from "lodash";
import { createCachePrimaryFilter, getCachePrimaryFilter } from "../common";
import { useAuthContext } from "src/auth/hooks";
import { AdvanceNumberFilterOperators } from "src/utils/constants";
import { handleNumericInput } from "src/components/ComplexFilter/common";
import { RANGE_FILTER_OPS } from "src/api/contracts/filter-contract.generated";

const RangeOperators = new Set(RANGE_FILTER_OPS);

const LeftControl = ({
  onGraphConfigChange,
  selectedGraphEvals,
  selectedGraphProperty,
  setSelectedGraphEvals,
  setSelectedGraphProperty,
  selectedGraphAttributes,
  setSelectedGraphAttributes,
}) => {
  const { observeId } = useParams();
  const { user } = useAuthContext();
  const theme = useTheme();
  const isUserInteractionRef = useRef(false);
  const isInitializingRef = useRef(true);
  const hasAppliedUrlStateRef = useRef(false); // NEW: Track if URL state has been applied
  const [evalSearch] = useState("");
  const debouncedEvalSearch = useDebounce(evalSearch, 500);
  const { data: traceEvals } = useGetTraceEvals(observeId, debouncedEvalSearch);

  const { data: labelsData } = useQuery({
    queryKey: ["project-annotations-labels", observeId],
    queryFn: () =>
      axios.get(endpoints.project.getAnnotationLabels(), {
        params: { project_id: observeId },
      }),
    select: (data) => data?.data?.results,
  });

  const { data: systemMetricsList } = useQuery({
    queryKey: ["system-metrics-list"],
    queryFn: () => axios.get(endpoints.project.getSystemMetricList),
    staleTime: Infinity,
    select: (data) => {
      return data.data?.result?.map((item) => ({
        label: _.capitalize(item),
        value: item,
      }));
    },
  });
  const { control, setValue, watch } = useForm({
    defaultValues: {
      selectedProperty: selectedGraphProperty || "",
      selectedEvals: selectedGraphEvals?.map((evals) => evals.id) || [],
      annotationValue: null,
      numberOperator: "greater_than",
      numberValue: "",
      numberValue2: "",
    },
  });

  // clear property when observe id changes
  // using use layout effect to prevent flicker
  useLayoutEffect(() => {
    setValue("selectedProperty", "");
  }, [observeId, setValue]);

  const getOutputType = (item) => {
    switch (item.output_type ?? item.outputType) {
      case "score":
        return "float";
      case "choices":
        return "str_list";
      case "Pass/Fail":
        return "bool";
      default:
        return "str_list"; // Fallback
    }
  };

  const propertyOptions = useMemo(() => {
    const options = [];

    if (Array.isArray(systemMetricsList) && systemMetricsList?.length > 0) {
      options.push({
        label: "System metrics",
        value: "system-metrics-header",
        disabled: true,
        isHeader: true,
      });

      systemMetricsList.map((metric) => {
        options.push({
          label: metric.label,
          value: metric.value,
          id: metric.value,
          type: "SYSTEM_METRIC",
        });
      });
    }

    options.push({
      label: "Evals",
      value: "evals-header",
      disabled: true,
      isHeader: true,
    });

    traceEvals?.forEach((evalItem) => {
      options.push({
        label: evalItem.name,
        value: `eval_${evalItem.id}`,
        id: evalItem.id,
        type: "EVAL",
        output_type: getOutputType(evalItem),
        evalData: evalItem,
      });
    });

    // Filter out text annotations since they're not suitable for graphing
    const nonTextAnnotations =
      labelsData?.filter((annotation) => annotation.type !== "text") || [];

    // Annotations section
    if (nonTextAnnotations.length > 0) {
      options.push({
        label: "Annotations",
        value: "annotations-header",
        disabled: true,
        isHeader: true,
      });

      nonTextAnnotations.forEach((annotation) => {
        options.push({
          label: annotation.name,
          value: `annotation_${annotation.id}`,
          id: annotation.id,
          type: "ANNOTATION",
          output_type: getOutputType(annotation),
          annotationData: annotation,
        });
      });
    }

    return options;
  }, [traceEvals, labelsData, systemMetricsList]);

  const selectedProperty = watch("selectedProperty");
  const selectedOption = useMemo(
    () =>
      propertyOptions.find(
        (opt) => opt.value === selectedProperty && !opt.isHeader,
      ),
    [propertyOptions, selectedProperty],
  );

  // Helper function to convert boolean values to display format
  const convertBooleanToDisplay = useCallback((value, outputType) => {
    if (outputType === "bool" && typeof value === "boolean") {
      return value ? "Passed" : "Failed";
    }
    return value;
  }, []);

  // Helper function to convert display format to boolean
  const convertDisplayToBoolean = useCallback((value, outputType) => {
    if (outputType === "bool" && typeof value === "string") {
      return value.toLowerCase() === "passed";
    }
    return value;
  }, []);

  // Effect to initialize form values on component mount and when URL state changes
  useEffect(() => {
    if (
      selectedGraphProperty &&
      propertyOptions.length > 0 &&
      !hasAppliedUrlStateRef.current
    ) {
      const matchingOption = propertyOptions.find(
        (opt) => opt.label === selectedGraphProperty,
      );
      if (matchingOption) {
        isInitializingRef.current = true;
        setValue("selectedProperty", matchingOption.value);

        hasAppliedUrlStateRef.current = true; // Mark that URL state has been applied
      }
    }
  }, [selectedGraphProperty, propertyOptions, setValue]);

  useEffect(() => {
    const isInitAndUrlApplied =
      hasAppliedUrlStateRef.current && isInitializingRef.current;

    if (
      isInitAndUrlApplied &&
      ((selectedOption &&
        selectedGraphAttributes?.[selectedOption.id] !== undefined) ||
        selectedOption?.type === "SYSTEM_METRIC")
    ) {
      const savedValue = selectedGraphAttributes[selectedOption.id];

      if (selectedOption.type === "ANNOTATION") {
        setValue("annotationValue", savedValue);
      } else if (selectedOption.type === "EVAL") {
        const displayValue = convertBooleanToDisplay(
          savedValue,
          selectedOption.output_type,
        );
        setValue("selectedEvals", [displayValue]);
      }

      const config = {
        id: selectedOption.id,
        type: selectedOption.type,
        output_type: selectedOption.output_type,
        value: savedValue,
      };
      onGraphConfigChange(config);

      // Reset initialization flag after a brief delay
      setTimeout(() => {
        isInitializingRef.current = false;
      }, 100);
    }
  }, [
    selectedOption,
    selectedGraphAttributes,
    setValue,
    convertBooleanToDisplay,
    onGraphConfigChange,
  ]);

  useEffect(() => {
    if (!selectedProperty) {
      isUserInteractionRef.current = true;
      setValue("selectedEvals", []);
      setValue("annotationValue", null);
      setValue("numberOperator", "greater_than");
      setValue("numberValue", "");
      setValue("numberValue2", "");
      setSelectedGraphEvals([]);

      // Clear the stored attribute for the previous option if it exists
      if (selectedOption) {
        const newAttributes = { ...selectedGraphAttributes };
        delete newAttributes[selectedOption.id];
        setSelectedGraphAttributes(newAttributes);
      }
    }
  }, [
    selectedProperty,
    setValue,
    setSelectedGraphEvals,
    selectedOption,
    selectedGraphAttributes,
    setSelectedGraphAttributes,
  ]);

  // Common props
  const commonAttributes = useMemo(
    () => ({
      size: "small",
      fullWidth: true,
      sx: {
        "& .MuiOutlinedInput-root": {
          height: "38px",
        },
      },
    }),
    [],
  );

  const getAnnotationOptions = useCallback(() => {
    if (!selectedOption || selectedOption.type !== "ANNOTATION") return [];

    const annotation = selectedOption.annotationData;

    switch (annotation.type) {
      case "categorical":
        return (
          annotation.settings?.options?.map((opt) => ({
            label: opt.label,
            value: opt.label,
          })) || []
        );
      default:
        return [];
    }
  }, [selectedOption]);

  const handlePropertyChange = useCallback(
    (e) => {
      const value = e.target.value;
      // ignore header-clicks
      if (value.includes("-header")) return;

      createCachePrimaryFilter(user.id, observeId, value);

      // mark that this is a user interaction
      isUserInteractionRef.current = true;
      isInitializingRef.current = false;

      // find the metadata for this selection
      const option = propertyOptions.find((opt) => opt.value === value);
      if (!option) {
        // no valid option → clear everything
        setValue("selectedProperty", "");
        setValue("selectedEvals", []);
        setValue("annotationValue", null);
        setSelectedGraphEvals([]);
        onGraphConfigChange(null);
        return;
      }

      // set the chosen property
      setValue("selectedProperty", value);
      setSelectedGraphProperty(option.label);

      // clear any old second-dropdown state
      setValue("selectedEvals", []);
      setValue("annotationValue", null);
      setValue("numberOperator", "greater_than");
      setValue("numberValue", "");
      setValue("numberValue2", "");
      setSelectedGraphEvals([]);
      onGraphConfigChange(null);

      // now special-case float outputs
      if (option.output_type === "float") {
        // tell RHF you did select an eval ID (even though no UI)
        setValue("selectedEvals", [option?.evalData?.id]);

        // update your graph‐data array
        if (option.type === "EVAL") {
          setSelectedGraphEvals([option.evalData]);
        }

        // Fire config change to load graph (without filter values initially)
        onGraphConfigChange({
          id: option.id,
          type: option.type,
          output_type: option.output_type,
        });

        // Update attributes
        setSelectedGraphAttributes({
          ...selectedGraphAttributes,
          [option.id]: null,
        });
      } else if (option?.type === "SYSTEM_METRIC") {
        onGraphConfigChange({
          id: String(option.id).toLowerCase(),
          type: option.type,
        });
      } else {
        setSelectedGraphEvals([]);
        onGraphConfigChange(null);
      }

      // Clear previous option's attributes if exists
      if (selectedOption) {
        const newAttributes = { ...selectedGraphAttributes };
        delete newAttributes[selectedOption.id];
        setSelectedGraphAttributes(newAttributes);
      }
    },
    [
      propertyOptions,
      setValue,
      setSelectedGraphEvals,
      setSelectedGraphProperty,
      setSelectedGraphAttributes,
      selectedOption,
      selectedGraphAttributes,
      onGraphConfigChange,
    ],
  );

  const handleAnnotationValueChange = useCallback(
    (value) => {
      if (
        selectedOption &&
        value !== null &&
        value !== undefined &&
        value !== ""
      ) {
        let parsedValue = value;

        if (selectedOption.output_type === "bool") {
          // Normalize value to boolean
          parsedValue = convertDisplayToBoolean(
            value,
            selectedOption.output_type,
          );
        }

        const newConfig = {
          id: selectedOption.id,
          type: selectedOption.type,
          output_type: selectedOption.output_type,
          value: parsedValue,
        };

        // Update the selectedGraphAttributes with the selected value
        setSelectedGraphAttributes({
          ...selectedGraphAttributes,
          [selectedOption.id]: parsedValue,
        });

        isInitializingRef.current = false; // User interaction, not initialization
        onGraphConfigChange(newConfig);
      }
    },
    [
      selectedOption,
      onGraphConfigChange,
      selectedGraphAttributes,
      setSelectedGraphAttributes,
      convertDisplayToBoolean,
    ],
  );

  const handleSelectAnnotationChange = useCallback(
    (e) => {
      const value = e.target.value;
      handleAnnotationValueChange(value);
    },
    [handleAnnotationValueChange],
  );

  const handleEvalsChange = useCallback(
    (e) => {
      const selectedValue = e.target.value;

      if (
        selectedOption &&
        selectedValue !== null &&
        selectedValue !== undefined &&
        selectedValue !== ""
      ) {
        const configValue = convertDisplayToBoolean(
          selectedValue,
          selectedOption.output_type,
        );

        const config = {
          id: selectedOption.id,
          type: selectedOption.type,
          output_type: selectedOption.output_type,
          value: configValue,
        };

        // Update the selectedGraphAttributes with the boolean value (for storage)
        setSelectedGraphAttributes({
          ...selectedGraphAttributes,
          [selectedOption.id]: configValue,
        });

        // Batch these updates to prevent unnecessary re-renders
        isUserInteractionRef.current = true;
        isInitializingRef.current = false; // User interaction, not initialization
        setValue("selectedEvals", [selectedValue]); // Keep display value in form
        setSelectedGraphEvals([selectedOption.evalData]);

        // Only call config change when user actually selects a value
        onGraphConfigChange(config);
      }
    },
    [
      selectedOption,
      setValue,
      setSelectedGraphEvals,
      onGraphConfigChange,
      selectedGraphAttributes,
      setSelectedGraphAttributes,
      convertDisplayToBoolean,
    ],
  );

  const handleNumberFilterChange = useCallback(
    (operator, value, value2) => {
      if (!selectedOption) return;

      const isBetweenOp = RangeOperators.has(operator);

      // Store values for UI persistence (operator and values separately for form restoration)
      setSelectedGraphAttributes({
        ...selectedGraphAttributes,
        [selectedOption.id]: {
          _numberFilter: true,
          operator,
          value,
          value2,
        },
      });

      isUserInteractionRef.current = true;
      isInitializingRef.current = false;

      // Only trigger API if values are properly defined
      // For between operators: require both values
      // For other operators: require single value
      const hasValue = value || value === 0;
      const hasValue2 = value2 || value2 === 0;
      if (!hasValue) return;
      if (isBetweenOp && !hasValue2) return;

      const filterValue = isBetweenOp ? [value, value2] : value;

      const config = {
        id: selectedOption.id,
        type: selectedOption.type,
        output_type: selectedOption.output_type,
        filterOp: operator,
        filterValue: filterValue,
      };

      if (selectedOption.type === "EVAL") {
        setSelectedGraphEvals([selectedOption.evalData]);
      }

      onGraphConfigChange(config);
    },
    [
      selectedOption,
      onGraphConfigChange,
      selectedGraphAttributes,
      setSelectedGraphAttributes,
      setSelectedGraphEvals,
    ],
  );

  useEffect(() => {
    if (!propertyOptions.length) return;

    const current = getCachePrimaryFilter(user?.id, observeId);

    const latencyOption = propertyOptions.find((item) =>
      current ? item.value === current : item.label === "Latency",
    );
    const targetValue = latencyOption?.value;
    if (targetValue) {
      createCachePrimaryFilter(user.id, observeId, targetValue);
      setValue("selectedProperty", targetValue);
      handlePropertyChange({ target: { value: targetValue } });
    }
  }, [propertyOptions, user?.id, observeId]);

  // Custom option renderer for headers
  const renderOption = useCallback((props, option) => {
    if (option.isHeader) {
      return (
        <li {...props}>
          <Box
            sx={{
              fontWeight: "bold",
              color: "text.primary",
              backgroundColor: "background.default",
              width: "100%",
              px: 2,
              py: 1,
              fontSize: 14,
              cursor: "default",
            }}
          >
            {option.label}
          </Box>
        </li>
      );
    }

    return <li {...props}>{option.label}</li>;
  }, []);

  const renderSecondDropdown = useCallback(() => {
    if (!selectedProperty || !selectedOption) return null;

    if (selectedOption.output_type === "float") {
      const savedConfig = selectedGraphAttributes?.[selectedOption.id];

      const currentOperator =
        watch("numberOperator") || savedConfig?.operator || "greater_than";
      const currentValue = watch("numberValue") ?? savedConfig?.value ?? "";
      const currentValue2 = watch("numberValue2") ?? savedConfig?.value2 ?? "";

      return (
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 1,
          }}
        >
          <FormSearchSelectFieldState
            onChange={(e) => {
              setValue("numberOperator", e.target.value);
              handleNumberFilterChange(
                e.target.value,
                currentValue,
                currentValue2,
              );
            }}
            label="Operator"
            value={currentOperator}
            size="small"
            showClear={false}
            options={AdvanceNumberFilterOperators.map(({ label, value }) => ({
              label,
              value,
            }))}
            sx={{ minWidth: 150 }}
          />
          <TextField
            sx={{ width: 100 }}
            type="text"
            label="Value"
            placeholder="Value"
            size="small"
            value={currentValue}
            onChange={(e) => {
              const value = handleNumericInput(e.target.value);
              setValue("numberValue", value);
              handleNumberFilterChange(currentOperator, value, currentValue2);
            }}
          />
          {RangeOperators.has(currentOperator) && (
            <>
              <Typography
                typography="s2"
                fontWeight={"fontWeightRegular"}
                color="text.primary"
              >
                and
              </Typography>
              <TextField
                sx={{ width: 100 }}
                type="text"
                label="Value"
                placeholder="Value"
                size="small"
                value={currentValue2}
                onChange={(e) => {
                  const value = handleNumericInput(e.target.value);
                  setValue("numberValue2", value);
                  handleNumberFilterChange(
                    currentOperator,
                    currentValue,
                    value,
                  );
                }}
              />
            </>
          )}
        </Box>
      );
    }

    if (selectedOption?.type === "ANNOTATION") {
      const annotation = selectedOption.annotationData;

      if (annotation.type === "thumbs_up_down") {
        // Get current form value first, then fall back to saved value during initialization
        const currentFormValue = watch("annotationValue");
        const savedValue = selectedGraphAttributes?.[selectedOption.id];
        const displayValue =
          currentFormValue ??
          (isInitializingRef.current
            ? convertBooleanToDisplay(savedValue, selectedOption.output_type)
            : null);
        return (
          <ThumbsRatingSelectField
            control={control}
            fieldName="annotationValue"
            onChange={handleSelectAnnotationChange}
            value={displayValue}
            defaultValue={displayValue}
            {...commonAttributes}
          />
        );
      }

      const annotationOptions = getAnnotationOptions();
      if (annotation.type === "categorical") {
        const currentFormValue = watch("annotationValue");
        const savedValue = selectedGraphAttributes?.[selectedOption.id];
        const valueToUse =
          currentFormValue ||
          (isInitializingRef.current ? savedValue : undefined);

        return (
          <CategoricalAnnotationSelectField
            {...commonAttributes}
            control={control}
            fieldName="annotationValue"
            options={annotationOptions}
            onChange={handleSelectAnnotationChange}
            value={valueToUse}
            defaultValue={valueToUse}
          />
        );
      }

      return (
        <FormSearchSelectFieldControl
          {...commonAttributes}
          control={control}
          fieldName="annotationValue"
          options={annotationOptions}
          label="Annotation Value"
          placeholder="Select value"
          onChange={handleSelectAnnotationChange}
          searchable={true}
          defaultValue={
            isInitializingRef.current
              ? selectedGraphAttributes?.[selectedOption.id]
              : undefined
          }
        />
      );
    }

    if (selectedOption?.type === "EVAL") {
      const evalChoices = selectedOption.evalData?.choices || [];

      if (!Array.isArray(evalChoices) || evalChoices.length === 0) {
        return null; // don't show anything if no choices
      }

      const evalChoiceOptions = evalChoices.map((choice) => ({
        label: choice,
        value: choice,
      }));

      // Get the current form value first, then fall back to saved value during initialization
      const currentFormValue = watch("selectedEvals");
      const savedValue = selectedGraphAttributes?.[selectedOption.id];
      const displayValue = convertBooleanToDisplay(
        savedValue,
        selectedOption.output_type,
      );

      // Use current form value if it exists, otherwise use saved value during initialization
      const valueToUse =
        currentFormValue && currentFormValue.length > 0
          ? currentFormValue
          : isInitializingRef.current && displayValue
            ? [displayValue]
            : [];

      return (
        <FormSearchSelectFieldControl
          {...commonAttributes}
          control={control}
          fieldName="selectedEvals"
          options={evalChoiceOptions}
          label="Class"
          placeholder="Choose Class"
          onChange={handleEvalsChange}
          multiple={false}
          searchable={true}
          value={valueToUse}
        />
      );
    }

    return null;
  }, [
    selectedProperty,
    selectedOption,
    control,
    getAnnotationOptions,
    commonAttributes,
    handleSelectAnnotationChange,
    handleEvalsChange,
    selectedGraphAttributes,
    convertBooleanToDisplay,
    handleNumberFilterChange,
    setValue,
    watch,
  ]);

  const shouldRenderSecondDropdown = useMemo(() => {
    if (!selectedProperty || !selectedOption) return false;

    // MODIFIED: Only auto-show based on saved attributes during initialization
    if (
      isInitializingRef.current &&
      selectedGraphAttributes?.[selectedOption.id] !== undefined
    )
      return true;

    // Show number filter for float types
    if (selectedOption.output_type === "float") return true;

    if (selectedOption.type === "ANNOTATION") {
      const annotation = selectedOption.annotationData;
      return Boolean(annotation);
    }

    if (selectedOption.type === "EVAL") {
      const evalChoices = selectedOption.evalData?.choices || [];
      return evalChoices.length > 0;
    }

    return false;
  }, [selectedProperty, selectedOption, selectedGraphAttributes]);

  return (
    <Stack sx={{ gap: theme.spacing(2) }}>
      <Box sx={{ width: 275 }}>
        <FormSearchSelectFieldControl
          {...commonAttributes}
          control={control}
          fieldName="selectedProperty"
          options={propertyOptions}
          label="Property"
          placeholder="Choose Property"
          onChange={handlePropertyChange}
          searchable={true}
          renderOption={renderOption}
          isOptionDisabled={(option) => option.disabled}
          sx={{
            "& .MuiOutlinedInput-root": {
              height: "38px",
            },
            "& .MuiAutocomplete-paper li[aria-disabled='true'][value$='-header']":
              {
                color: "text.primary !important",
                fontWeight: "bold !important",
                backgroundColor: "background.default !important",
                opacity: "1 !important",
                cursor: "default !important",
                pointerEvents: "none",
                paddingTop: "8px",
                paddingBottom: "8px",
              },
            "& .MuiAutocomplete-paper li[aria-disabled='true'][value$='-header']:hover":
              {
                backgroundColor: "background.default !important",
              },
          }}
        />
      </Box>

      {shouldRenderSecondDropdown && (
        <Stack direction={"row"} alignItems={"center"} gap={2}>
          <Box>{renderSecondDropdown()}</Box>
        </Stack>
      )}
    </Stack>
  );
};

LeftControl.propTypes = {
  onGraphConfigChange: PropTypes.func,
  selectedGraphEvals: PropTypes.array,
  selectedGraphProperty: PropTypes.string,
  setSelectedGraphEvals: PropTypes.func,
  setSelectedGraphProperty: PropTypes.func,
  selectedGraphAttributes: PropTypes.string,
  setSelectedGraphAttributes: PropTypes.func,
};

export default LeftControl;
