import { Box, Button, Divider, Stack, Typography } from "@mui/material";
import React, { useCallback, useEffect, useMemo } from "react";
import { Controller, useFieldArray, useForm } from "react-hook-form";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import {
  AlertConfigValidationSchema,
  getDefaultAlertConfigValues,
} from "./validation";
import { zodResolver } from "@hookform/resolvers/zod";
import CardWrapper from "./CardWrapper";
import {
  alertTypes,
  directionOfAnomaly,
  intervalOptions,
  notificationOptions,
  thresholdOptions,
  timeOptions,
  alertDefinitionOptions,
  convertFiltersToPayload,
  isSpanAttrFilterValid,
} from "../common";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import NewTaskFilterRow from "src/sections/common/EvalsTasks/NewTaskDrawer/NewTaskFilterRow";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useEvalAttributesPage } from "src/hooks/use-eval-attributes";
import axios, { endpoints } from "src/utils/axios";
import { getRandomId } from "src/utils/utils";
import {
  FilterDefaultOperators,
  FilterDefaultValues,
} from "src/utils/constants";
import Iconify from "src/components/iconify";
import RadioField from "src/components/RadioField/RadioField";
import { ShowComponent } from "src/components/show";
import ChipsInput from "../../../../components/ChipsInput/ChipsInput";
import { enqueueSnackbar } from "notistack";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { LoadingButton } from "@mui/lab";
import { useCreateAlertMutation } from "../useCreateAlertMutation";
import PropTypes from "prop-types";
import { useDebounce } from "src/hooks/use-debounce";
import { useAlertStore } from "../store/useAlertStore";
import { useAlertSheetView } from "../store/useAlertSheetView";

const isValidEmail = (email) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim());

export default function AlertSettingsForm({
  onThresholdTypeChange,
  setThresholdOperator,
  setWarningValue,
  setCriticalValue,
  setFormIsDirty,
  onPayloadChange,
}) {
  const {
    selectedProject: observeId,
    alertType,
    handleChangeAlertType,
    handleCloseCreateAlert,
    openSheetView,
    duplicateAlertName,
    refreshGrid,
    handleCloseSheetView,
    setHasData,
    setConfirmationModalOpen,
  } = useAlertStore();

  const { alertRuleDetails, refreshGrid: refreshIssues } = useAlertSheetView();

  const {
    control,
    getValues,
    watch,
    handleSubmit,
    setValue,
    reset,
    setError,
    trigger,
    formState: { errors, isDirty },
  } = useForm({
    defaultValues: getDefaultAlertConfigValues({
      ...(openSheetView && alertRuleDetails),
      name: openSheetView
        ? duplicateAlertName || alertRuleDetails?.name || ""
        : "",
      ...(alertType &&
        !openSheetView && {
          metricType: alertType,
        }),
    }),
    resolver: zodResolver(AlertConfigValidationSchema),
    mode: "onChange",
    reValidateMode: "onChange",
  });

  const metricType = watch("metric_type");
  const metric = watch("metric");

  const selectedNotificationMethod = watch("notification.method");
  const thresholdType = watch("threshold_type");

  useEffect(() => {
    if (openSheetView) {
      setFormIsDirty(isDirty);
    }
  }, [isDirty, openSheetView, setFormIsDirty]);

  const {
    fields,
    append,
    update,
    remove: removeFilter,
  } = useFieldArray({
    control,
    name: "filters",
  });

  const debouncedName = useDebounce(watch("name"), 300);
  const debouncedWarning = useDebounce(watch("warning_threshold_value"), 300);
  const debouncedCritical = useDebounce(watch("critical_threshold_value"), 300);
  const debouncedOperator = useDebounce(watch("threshold_operator"), 300);
  const debouncedType = useDebounce(watch("threshold_type"), 300);
  const debouncedMetricType = useDebounce(watch("metric_type"), 300);
  const debouncedFrequency = useDebounce(watch("alert_frequency"), 300);
  const debouncedMetric = useDebounce(watch("metric"), 300);
  const debouncedThresHoldMetricValue = useDebounce(
    watch("threshold_metric_value"),
    300,
  );

  const debounceWatchedFilters = useDebounce(watch("filters"), 300);

  const { data: expandedEvaluations } = useQuery({
    queryKey: ["observe-evaluations", observeId],
    queryFn: () =>
      axios.get(endpoints.project.getTraceEvals(), {
        params: {
          project_id: observeId,
        },
      }),
    select: (res) => res?.data?.result,
    enabled: Boolean(observeId && metricType === "evaluation_metrics"),
  });

  const selectedMetricOptions = useMemo(() => {
    if (expandedEvaluations?.length > 0 && metric) {
      const selectedEval = expandedEvaluations.find(
        (evaluation) => evaluation?.id === metric,
      );
      return (
        selectedEval?.choices?.map((choice) => ({
          label: choice,
          value: choice,
        })) ?? []
      );
    }
    return [];
  }, [expandedEvaluations, metric]);

  const queryPayload = useMemo(() => {
    const { observation_type, span_attributes_filters } =
      convertFiltersToPayload(debounceWatchedFilters);
    const payload = {
      name: debouncedName,
      project: observeId,
      metric_type: debouncedMetricType,
      threshold_operator: debouncedOperator,
      threshold_type: debouncedType,
      critical_threshold_value: debouncedCritical,
      warning_threshold_value: debouncedWarning,
      alert_frequency: debouncedFrequency,
      filters: {
        ...(observation_type.length > 0 && { observation_type }),
        ...(!isSpanAttrFilterValid(span_attributes_filters)
          ? {}
          : { span_attributes_filters }),
      },
    };

    if (debouncedMetricType === "evaluation_metrics") {
      payload.metric = debouncedMetric;
      if (debouncedThresHoldMetricValue && selectedMetricOptions?.length > 0) {
        payload.threshold_metric_value = debouncedThresHoldMetricValue;
      }
    }

    return payload;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    observeId,
    debouncedMetricType,
    debouncedOperator,
    debouncedType,
    debouncedCritical,
    debouncedWarning,
    debouncedFrequency,
    debouncedMetric,
    debouncedThresHoldMetricValue,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    JSON.stringify(debounceWatchedFilters),
  ]);

  // Validation logic
  const isQueryEnabled = useMemo(() => {
    const relevantFields = [
      "metric_type",
      "threshold_operator",
      "threshold_type",
      "critical_threshold_value",
      "warning_threshold_value",
      "alert_frequency",
    ];
    const hasErrors = relevantFields.some((field) => errors[field]);

    const requiredFieldsValid =
      observeId &&
      debouncedMetricType &&
      debouncedOperator &&
      debouncedType &&
      debouncedCritical &&
      debouncedWarning &&
      debouncedFrequency &&
      !hasErrors &&
      (debouncedMetricType === "evaluation_metrics" ? debouncedMetric : true);

    const isThresholdValid = (() => {
      if (debouncedOperator === "less_than") {
        return debouncedCritical < debouncedWarning;
      }
      if (debouncedOperator === "greater_than") {
        return debouncedCritical > debouncedWarning;
      }
      return true;
    })();

    return (
      requiredFieldsValid && isThresholdValid && (!openSheetView || isDirty)
    );
  }, [
    observeId,
    debouncedMetricType,
    debouncedOperator,
    debouncedType,
    debouncedCritical,
    debouncedWarning,
    debouncedFrequency,
    debouncedMetric,
    errors,
    openSheetView,
    isDirty,
  ]);

  useEffect(() => {
    onPayloadChange(queryPayload, isQueryEnabled);
  }, [queryPayload, isQueryEnabled, onPayloadChange]);

  useEffect(() => {
    if (queryPayload) {
      setThresholdOperator(queryPayload?.threshold_operator);
      setWarningValue(queryPayload?.warning_threshold_value);
      setCriticalValue(queryPayload?.critical_threshold_value);
    }
  }, [queryPayload, setThresholdOperator, setWarningValue, setCriticalValue]);

  const { items: evalAttributeKeys } = useEvalAttributesPage({
    projectId: observeId,
    enabled: !!observeId,
  });
  const evalAttributes = useMemo(
    () =>
      (evalAttributeKeys || []).map((attr) => ({
        label: attr,
        value: attr,
      })),
    [evalAttributeKeys],
  );

  const addFilter = () => {
    append({
      id: getRandomId(),
      propertyId: "",
      property: "",
      filterConfig: {
        filterType: "text",
        filterOp: FilterDefaultOperators["text"],
        filterValue: FilterDefaultValues["text"],
      },
    });
  };

  const { mutate: createAlert, isPending: isCreating } = useCreateAlertMutation(
    {
      metricType,
      reset: () => reset(getDefaultAlertConfigValues()),
      handleClose: handleCloseCreateAlert,
      onSuccessCallback: () => {
        trackEvent(Events.alertHomepageLoaded, {
          [PropertyName.source]: "redirection_after_alert_confirmation",
        });
        refreshGrid();
        setHasData(true);
        if (duplicateAlertName) {
          handleCloseSheetView();
        }
      },
    },
  );

  const { mutate: updateAlert, isPending: isUpdating } = useMutation({
    mutationFn: (data) => {
      return axios.patch(
        `${endpoints.project.createMonitor}${openSheetView}/`,
        data,
      );
    },
    onSuccess: (data, variables) => {
      enqueueSnackbar("Alert updated successfully", {
        variant: "success",
      });
      trackEvent(Events.createAlertConfirmed, {
        [PropertyName.formFields]: {
          ...variables,
          metricType,
          thresholdType: variables?.threshold_type,
        },
      });
      handleCloseCreateAlert();
      reset(getDefaultAlertConfigValues());
      refreshGrid();
      refreshIssues();
    },
  });

  const handleCreateAlert = (data) => {
    const { observation_type, span_attributes_filters } =
      convertFiltersToPayload(data?.filters);

    const notificationPayload = {};
    if (data?.notification?.method === "email") {
      notificationPayload.notification_emails =
        data?.notification?.emails ?? [];
    }
    if (data?.notification?.method === "slack") {
      notificationPayload.slack_webhook_url =
        data?.notification?.slack?.webhookUrl ?? "";
      notificationPayload.slack_notes = data?.notification?.slack?.notes ?? "";
    }
    if (
      selectedMetricOptions?.length > 0 &&
      data?.metric_type === "evaluation_metrics" &&
      !data?.threshold_metric_value
    ) {
      setError("threshold_metric_value", {
        message: "Choice is required",
      });
      return;
    }

    const payload = {
      name: data?.name,
      metric_type: data?.metric_type,
      project: observeId,
      alert_frequency: data?.alert_frequency,
      filters: {
        ...(observation_type.length > 0 && { observation_type }),
        ...(span_attributes_filters.length > 0 && { span_attributes_filters }),
      },
      threshold_type: data?.threshold_type,
      threshold_operator: data?.threshold_operator,
      ...(data?.threshold_type !== "anomaly_detection" && {
        critical_threshold_value: data?.critical_threshold_value,
        warning_threshold_value: data?.warning_threshold_value,
      }),
      ...(data?.metric_type === "evaluation_metrics" && {
        metric: data?.metric,
        ...(data?.threshold_metric_value && {
          threshold_metric_value: data?.threshold_metric_value,
        }),
      }),
      ...notificationPayload,
      ...(data?.threshold_type === "percentage_change" && {
        auto_threshold_time_window: data?.auto_threshold_time_window,
      }),
    };

    if (openSheetView && !duplicateAlertName) {
      updateAlert(payload);
    } else {
      createAlert(payload);
    }
  };

  const handleThresholdSwapIfNeeded = useCallback(
    (operatorInput) => {
      const warningValue = parseFloat(watch("warning_threshold_value"));
      const criticalValue = parseFloat(watch("critical_threshold_value"));

      if (!isNaN(warningValue) && !isNaN(criticalValue)) {
        let shouldSwap = false;

        if (operatorInput === "less_than") {
          shouldSwap = criticalValue >= warningValue;
        } else if (operatorInput === "greater_than") {
          shouldSwap = criticalValue <= warningValue;
        }

        if (shouldSwap) {
          setValue("warning_threshold_value", criticalValue);
          setValue("critical_threshold_value", warningValue);
          setWarningValue(criticalValue);
          setCriticalValue(warningValue);
        }
      }
    },
    [watch, setValue, setWarningValue, setCriticalValue],
  );

  return (
    <Box
      sx={{
        minHeight: "600px",
      }}
    >
      <Stack>
        <Typography
          color={"text.primary"}
          variant="s1"
          fontWeight={"fontWeightSemiBold"}
        >
          Manage alert settings
        </Typography>
        <Typography
          color={"text.primary"}
          variant="s1"
          fontWeight={"fontWeightRegular"}
        >
          Create alert to get notification
        </Typography>
      </Stack>
      <form
        noValidate
        onSubmit={(e) => {
          e.preventDefault();
          handleSubmit(handleCreateAlert)();
        }}
      >
        <Box
          sx={{
            mt: 3,
            display: "flex",
            flexDirection: "column",
            gap: 3,
          }}
        >
          <FormTextFieldV2
            control={control}
            required
            placeholder="Enter alert name"
            fieldName="name"
            label="Name"
            size="small"
            fullWidth
          />
          <CardWrapper order={0} title="Define Metrics & Interval">
            <Box
              sx={{
                padding: 3,
                display: "flex",
                flexDirection: "row",
                gap: 2,
                alignItems: "center",
              }}
            >
              <FormSearchSelectFieldControl
                control={control}
                fieldName={"metric_type"}
                label="Metric"
                size="small"
                fullWidth
                onChange={(e) => {
                  handleChangeAlertType(e?.target?.value);
                }}
                options={alertTypes.flatMap((group, groupIndex) => [
                  {
                    label: group.category,
                    value: `__group_${group.category}`,
                    isGroup: true,
                  },
                  ...group.options.map((opt) => ({
                    label: opt.label,
                    value: opt.value,
                  })),
                  {
                    isDivider: true,
                    component:
                      groupIndex !== alertTypes.length - 1 ? (
                        <Divider sx={{ my: 0.5 }} />
                      ) : null,
                  },
                ])}
              />
              <ShowComponent condition={metricType === "evaluation_metrics"}>
                <Typography>of</Typography>
                <FormSearchSelectFieldControl
                  control={control}
                  fieldName={"metric"}
                  label="Metric"
                  size="small"
                  options={expandedEvaluations?.map((evaluation) => ({
                    label: evaluation?.name,
                    value: evaluation?.id,
                  }))}
                  fullWidth
                  onChange={() => {
                    setValue("threshold_metric_value", "");
                  }}
                  // sx={{
                  //   flex: 1,
                  //   maxWidth: { sm: "300px", md: "400px", lg: "600px" },
                  //   minWidth: { sm: "400px" },
                  // }}
                />
              </ShowComponent>
              <FormSearchSelectFieldControl
                control={control}
                fieldName={"alert_frequency"}
                label="Interval"
                size="small"
                options={intervalOptions}
                fullWidth
                // sx={{
                //   flex: 1,
                //   maxWidth: { sm: "300px", md: "400px", lg: "600px" },
                //   minWidth: { sm: "400px" },
                // }}
              />
            </Box>
          </CardWrapper>
          <CardWrapper order={1} title="Filter Events">
            <Box
              sx={{
                padding: 3,
                display: "flex",
                flexDirection: "column",
                gap: 2,
              }}
            >
              {fields.map((filter, index) => (
                <NewTaskFilterRow
                  key={filter.id}
                  index={index}
                  removeFilter={removeFilter}
                  control={control}
                  attributes={evalAttributes}
                  update={update}
                  getValues={getValues}
                  compact={false}
                />
              ))}
              <Box>
                <Button
                  startIcon={
                    <Iconify color="text.primary" icon="material-symbols:add" />
                  }
                  onClick={addFilter}
                  variant="text"
                  color="primary"
                  size="small"
                  sx={{
                    fontSize: "12px",
                    color: "text.disabled",
                    border: "1px solid",
                    borderColor: "divider",
                    borderRadius: "8px",
                    width: "126px",
                    height: "30px",
                  }}
                >
                  Add Filter
                </Button>
              </Box>
            </Box>
          </CardWrapper>
          <CardWrapper order={2} title="Define Alert">
            <Stack gap={3}>
              <Box
                sx={{
                  padding: 1.75,
                }}
              >
                <RadioField
                  label={""}
                  control={control}
                  fieldName={"threshold_type"}
                  options={alertDefinitionOptions}
                  optionColor="text.primary"
                  onChange={(e) => {
                    onThresholdTypeChange(e?.target?.value);
                    if (
                      debouncedWarning !== undefined ||
                      debouncedCritical !== undefined ||
                      debouncedOperator ||
                      e?.target?.value
                    ) {
                      trigger([
                        "critical_threshold_value",
                        "warning_threshold_value",
                      ]);
                    }
                  }}
                />
              </Box>

              <Divider flexItem />
              <ShowComponent condition={thresholdType === "percentage_change"}>
                <Stack direction={"row"} gap={2} px={3}>
                  <Typography>
                    Compare percentage change higher or lower to{" "}
                  </Typography>
                  <FormSearchSelectFieldControl
                    required
                    control={control}
                    fieldName={"auto_threshold_time_window"}
                    label="Time"
                    size="small"
                    options={timeOptions}
                    sx={{
                      flex: 1,
                      maxWidth: "400px",
                    }}
                  />
                </Stack>
              </ShowComponent>
              <ShowComponent
                condition={["static", "percentage_change"].includes(
                  thresholdType,
                )}
              >
                <Stack
                  sx={{
                    px: 3,
                    pb: 3,
                    gap: 3,
                  }}
                >
                  <CardWrapper
                    icon={"/assets/icons/ic_critical.svg"}
                    title="Critical"
                    iconColor={"red.500"}
                    bgColor="red.o5"
                  >
                    <Box
                      sx={{
                        padding: 3,
                        display: "flex",
                        alignItems: "flex-start",
                        gap: 2,
                      }}
                    >
                      <ShowComponent
                        condition={
                          metricType === "evaluation_metrics" &&
                          metric &&
                          selectedMetricOptions?.length > 0
                        }
                      >
                        <Stack
                          sx={{
                            flexDirection: "row",
                            gap: 2,
                            alignItems: "center",
                          }}
                        >
                          <FormSearchSelectFieldControl
                            control={control}
                            fieldName={"threshold_metric_value"}
                            label="Choice"
                            size="small"
                            options={selectedMetricOptions}
                            sx={{
                              flex: 1,
                              maxWidth: "400px",
                            }}
                          />
                          <Typography
                            variant="s1"
                            color={"text.primary"}
                            fontWeight={"fontWeightRegular"}
                          >
                            %
                          </Typography>
                          <Typography
                            variant="s1"
                            color={"text.primary"}
                            fontWeight={"fontWeightRegular"}
                          >
                            of
                          </Typography>
                        </Stack>
                      </ShowComponent>
                      <FormSearchSelectFieldControl
                        control={control}
                        fieldName={"threshold_operator"}
                        onChange={(e) => {
                          handleThresholdSwapIfNeeded(e?.target?.value);
                          if (
                            debouncedWarning !== undefined ||
                            debouncedCritical !== undefined ||
                            e?.target?.value ||
                            debouncedType
                          ) {
                            trigger([
                              "critical_threshold_value",
                              "warning_threshold_value",
                            ]);
                          }
                        }}
                        label="Threshold"
                        size="small"
                        options={thresholdOptions}
                        sx={{
                          flex: 1,
                          maxWidth: "400px",
                        }}
                      />
                      <FormTextFieldV2
                        control={control}
                        fieldName="critical_threshold_value"
                        onChange={(e) => {
                          if (
                            debouncedWarning !== undefined ||
                            e?.target?.value !== undefined ||
                            debouncedOperator ||
                            debouncedType
                          ) {
                            trigger([
                              "critical_threshold_value",
                              "warning_threshold_value",
                            ]);
                          }
                        }}
                        label="Value"
                        size="small"
                        fullWidth
                        fieldType="number"
                        sx={{
                          maxWidth: "400px",
                        }}
                      />
                    </Box>
                  </CardWrapper>
                  <CardWrapper
                    icon={"/assets/icons/ic_warning.svg"}
                    title="Warning"
                    iconColor="orange.400"
                    bgColor="orange.o5"
                  >
                    <Box
                      sx={{
                        padding: 3,
                        display: "flex",
                        alignItems: "flex-start",
                        gap: 2,
                      }}
                    >
                      <ShowComponent
                        condition={
                          metricType === "evaluation_metrics" &&
                          metric &&
                          selectedMetricOptions?.length > 0
                        }
                      >
                        <Stack
                          sx={{
                            flexDirection: "row",
                            gap: 2,
                            alignItems: "center",
                          }}
                        >
                          <FormSearchSelectFieldControl
                            control={control}
                            fieldName={"threshold_metric_value"}
                            label="Choice"
                            size="small"
                            options={selectedMetricOptions}
                            sx={{
                              flex: 1,
                              maxWidth: "400px",
                            }}
                          />
                          <Typography
                            variant="s1"
                            color={"text.primary"}
                            fontWeight={"fontWeightRegular"}
                          >
                            %
                          </Typography>
                          <Typography
                            variant="s1"
                            color={"text.primary"}
                            fontWeight={"fontWeightRegular"}
                          >
                            of
                          </Typography>
                        </Stack>
                      </ShowComponent>
                      <FormSearchSelectFieldControl
                        control={control}
                        fieldName={"threshold_operator"}
                        onChange={(e) => {
                          handleThresholdSwapIfNeeded(e?.target?.value);
                          if (
                            debouncedWarning !== undefined ||
                            debouncedCritical !== undefined ||
                            e?.target?.value ||
                            debouncedType
                          ) {
                            trigger([
                              "critical_threshold_value",
                              "warning_threshold_value",
                            ]);
                          }
                        }}
                        label="Threshold"
                        size="small"
                        options={thresholdOptions}
                        showClear={false}
                        sx={{
                          flex: 1,
                          maxWidth: "400px",
                        }}
                      />
                      <FormTextFieldV2
                        control={control}
                        fieldName="warning_threshold_value"
                        onChange={(e) => {
                          if (
                            e?.target?.value !== undefined ||
                            debouncedCritical !== undefined ||
                            debouncedOperator ||
                            debouncedType
                          ) {
                            trigger([
                              "critical_threshold_value",
                              "warning_threshold_value",
                            ]);
                          }
                        }}
                        label="Value"
                        size="small"
                        fullWidth
                        fieldType="number"
                        sx={{
                          maxWidth: "400px",
                        }}
                      />
                    </Box>
                  </CardWrapper>
                </Stack>
              </ShowComponent>
              <ShowComponent condition={thresholdType === "anomaly_detection"}>
                <Stack
                  gap={3}
                  flexDirection={"column"}
                  sx={{
                    p: 3,
                    pt: 0,
                  }}
                >
                  {/* <CardWrapper hideOrder title="Level of responsiveness">
                    <Stack
                      direction={"column"}
                      gap={2}
                      sx={{
                        p: 3,
                      }}
                    >
                      <Typography
                        variant="s1"
                        color={"text.primary"}
                        fontWeight={"fontWeightRegular"}
                      >
                        Choose your level of anomaly responsiveness. Higher
                        thresholds means alerts for most anomalies. Lower
                        thresholds means alerts only for larger ones.
                      </Typography>
                      <FormSearchSelectFieldControl
                        control={control}
                        fieldName={"anomalyLevelThreshold"}
                        label="Threshold"
                        size="small"
                        options={levelOfResponsiveness}
                        fullWidth
                        sx={{
                          flex: 1,
                          maxWidth: { sm: "300px", md: "400px", lg: "600px" },
                          minWidth: { sm: "400px" },
                        }}
                      />
                    </Stack>
                  </CardWrapper> */}
                  <CardWrapper hideOrder title="Direction of anomaly movement ">
                    <Stack
                      direction={"column"}
                      gap={2}
                      sx={{
                        p: 3,
                      }}
                    >
                      <Typography
                        variant="s1"
                        color={"text.primary"}
                        fontWeight={"fontWeightRegular"}
                      >
                        Decide if you want to be alerted to anomalies that are
                        moving above, below, or in both directions in relation
                        to your threshold.
                      </Typography>
                      <Stack
                        sx={{
                          flexDirection: "row",
                          gap: 2,
                          alignItems: "center",
                        }}
                      >
                        <ShowComponent
                          condition={
                            metricType === "evaluation_metrics" &&
                            metric &&
                            selectedMetricOptions?.length > 0
                          }
                        >
                          <Stack
                            sx={{
                              flexDirection: "row",
                              gap: 2,
                              alignItems: "center",
                            }}
                          >
                            <FormSearchSelectFieldControl
                              control={control}
                              fieldName={"threshold_metric_value"}
                              label="Choice"
                              size="small"
                              options={selectedMetricOptions}
                              sx={{
                                flex: 1,
                                maxWidth: "400px",
                              }}
                            />
                            <Typography
                              variant="s1"
                              color={"text.primary"}
                              fontWeight={"fontWeightRegular"}
                            >
                              %
                            </Typography>
                            <Typography
                              variant="s1"
                              color={"text.primary"}
                              fontWeight={"fontWeightRegular"}
                            >
                              of
                            </Typography>
                          </Stack>
                        </ShowComponent>
                        <FormSearchSelectFieldControl
                          control={control}
                          fieldName={"threshold_operator"}
                          label="Threshold"
                          size="small"
                          options={directionOfAnomaly}
                          fullWidth
                          sx={{
                            flex: 1,
                            maxWidth: { sm: "300px", md: "400px", lg: "600px" },
                            minWidth: { sm: "400px" },
                          }}
                        />
                      </Stack>
                    </Stack>
                  </CardWrapper>
                </Stack>
              </ShowComponent>
            </Stack>
          </CardWrapper>

          <CardWrapper order={3} title="Define Notification">
            <Box
              sx={{
                padding: 1.75,
              }}
            >
              <RadioField
                label={""}
                control={control}
                fieldName={"notification.method"}
                options={notificationOptions}
                optionColor="text.primary"
                optionDirection="row"
              />
              <ShowComponent condition={selectedNotificationMethod === "email"}>
                <Box
                  sx={{
                    paddingY: 3,
                    paddingLeft: 2,
                  }}
                >
                  <Controller
                    name="notification.emails"
                    control={control}
                    rules={{
                      required: "At least one email is required",
                      validate: (value) =>
                        value.length > 0 || "Please add at least one email",
                    }}
                    render={({ field, fieldState }) => (
                      <ChipsInput
                        {...field}
                        error={fieldState.error?.message}
                        setError={setError}
                        label={"Emails"}
                        placeholder={
                          "Separate emails by commas. can add upto 5  emails"
                        }
                        limit={5}
                        validateItem={isValidEmail}
                        formatItem={(email) => email.trim().toLowerCase()}
                        getErrorMessage={(type) =>
                          type === "limit"
                            ? "To add more email IDs contact sales"
                            : "Please enter a valid email"
                        }
                      />
                    )}
                  />
                </Box>
              </ShowComponent>
              <ShowComponent condition={selectedNotificationMethod === "slack"}>
                <Stack
                  sx={{
                    padding: 3,
                    gap: 3,
                  }}
                >
                  <FormTextFieldV2
                    control={control}
                    required
                    placeholder="Enter webhook URL"
                    fieldName="notification.slack.webhookUrl"
                    label="Webhook URL"
                    size="small"
                    fullWidth
                  />
                  <FormTextFieldV2
                    control={control}
                    placeholder="eg., name of the slack channel it’s linked to (optional)"
                    fieldName="notification.slack.notes"
                    label="Notes"
                    size="small"
                    fullWidth
                    multiline
                    rows={4}
                  />
                </Stack>
              </ShowComponent>
            </Box>
          </CardWrapper>
          <Stack direction={"row"} gap={1} justifyContent={"flex-end"}>
            <Button
              disabled={isCreating || isUpdating}
              onClick={() => setConfirmationModalOpen(true)}
              type="button"
              sx={{
                minWidth: "191px",
              }}
              variant="outlined"
            >
              Cancel
            </Button>
            <LoadingButton
              loading={isCreating || isUpdating}
              disabled={isCreating || isUpdating}
              type="submit"
              sx={{
                minWidth: "191px",
              }}
              variant="contained"
              color="primary"
            >
              {openSheetView
                ? duplicateAlertName
                  ? "Duplicate"
                  : "Update"
                : "Create"}{" "}
              Alert
            </LoadingButton>
          </Stack>
        </Box>
      </form>
    </Box>
  );
}

AlertSettingsForm.propTypes = {
  onThresholdTypeChange: PropTypes.func,
  setThresholdOperator: PropTypes.func,
  setWarningValue: PropTypes.func,
  setCriticalValue: PropTypes.func,
  setFormIsDirty: PropTypes.func,
  onPayloadChange: PropTypes.func,
};
