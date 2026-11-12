import { Box, Button, Divider, Link, Stack, Typography } from "@mui/material";
import React, { useCallback, useEffect, useMemo, useRef } from "react";
import { Controller, useForm } from "react-hook-form";
import { Link as RouterLink } from "react-router-dom";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import {
  AlertConfigValidationSchema,
  getDefaultAlertConfigValues,
  getThresholdValueDefaults,
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
  earlierRuleNote,
  providerThresholdHelp,
  evalUsesChoiceThreshold,
  isSpanAttrFilterValid,
} from "../common";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import AlertFilterBar from "./AlertFilterBar";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
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
import { useOrganization } from "src/contexts/OrganizationContext";

// The fields whose new value moves a saved alert to the current rule, as the
// server's monitor serializer lists them.
const THRESHOLD_RULE_FIELDS = [
  "metric_type",
  "metric",
  "threshold_metric_value",
  "threshold_type",
  "threshold_operator",
  "critical_threshold_value",
  "warning_threshold_value",
  "alert_frequency",
  "auto_threshold_time_window",
];

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
    selectedProject,
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
  const { currentOrganizationId } = useOrganization();
  const queryClient = useQueryClient();
  const observeId = selectedProject || alertRuleDetails?.project || null;

  const buildFormValues = useCallback(
    () =>
      getDefaultAlertConfigValues({
        ...(openSheetView && alertRuleDetails),
        name: openSheetView
          ? duplicateAlertName || alertRuleDetails?.name || ""
          : "",
        ...(alertType &&
          !openSheetView && {
            metricType: alertType,
          }),
      }),
    [openSheetView, alertRuleDetails, duplicateAlertName, alertType],
  );

  const {
    control,
    watch,
    handleSubmit,
    setValue,
    getValues,
    getFieldState,
    reset,
    setError,
    trigger,
    formState: { errors, isDirty, dirtyFields },
  } = useForm({
    defaultValues: buildFormValues(),
    resolver: zodResolver(AlertConfigValidationSchema),
    mode: "onChange",
    reValidateMode: "onChange",
  });

  // defaultValues is mount-only, so an alert that arrives later has to be pushed in.
  const hydratedKeyRef = useRef(null);

  useEffect(() => {
    if (!openSheetView) {
      hydratedKeyRef.current = null;
      return;
    }
    if (!alertRuleDetails?.id) return;
    const key = `${alertRuleDetails.id}|${duplicateAlertName ?? ""}`;
    if (hydratedKeyRef.current === key) return;
    hydratedKeyRef.current = key;
    reset(buildFormValues());
  }, [
    openSheetView,
    alertRuleDetails,
    duplicateAlertName,
    buildFormValues,
    reset,
  ]);

  const metricType = watch("metric_type");
  const metric = watch("metric");

  const selectedNotificationMethod = watch("notification.method");
  const thresholdType = watch("threshold_type");
  const thresholdOperator = watch("threshold_operator");

  useEffect(() => {
    if (openSheetView) {
      setFormIsDirty(isDirty);
    }
  }, [isDirty, openSheetView, setFormIsDirty]);

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

  const {
    data: expandedEvaluations,
    isLoading: isEvaluationsLoading,
    isError: isEvaluationsError,
  } = useQuery({
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

  // get_eval_names lists only evals that have already produced results, so a
  // project with no eval task, or with one that has not run yet, gets [].
  // Only a resolved empty list says that; loading and errors say their own.
  const hasNoEvalsWithResults =
    Array.isArray(expandedEvaluations) && expandedEvaluations.length === 0;
  const evalPickerEmptyText = isEvaluationsLoading
    ? "Loading evals..."
    : isEvaluationsError
      ? "Could not load evals"
      : "No evals with results yet";

  // An alert on an eval with no results yet is not in the eval list above,
  // and the picker would show the eval's raw id. The saved alert carries the eval's
  // name (the server appends " (<choice>)" for a choice eval, which has its
  // own field), so offer it under that name. Display only: selectedEval still
  // comes from the list.
  const evalOptions = useMemo(() => {
    const options =
      expandedEvaluations?.map((evaluation) => ({
        label: evaluation?.name,
        value: evaluation?.id,
      })) ?? [];
    const savedEvalId = alertRuleDetails?.metric;
    const savedEvalName = alertRuleDetails?.metricName;
    if (
      !openSheetView ||
      alertRuleDetails?.metricType !== "evaluation_metrics" ||
      !savedEvalId ||
      !savedEvalName ||
      options.some((option) => option.value === savedEvalId)
    ) {
      return options;
    }
    const choiceSuffix = alertRuleDetails?.thresholdMetricValue
      ? ` (${alertRuleDetails.thresholdMetricValue})`
      : "";
    const label =
      choiceSuffix && savedEvalName.endsWith(choiceSuffix)
        ? savedEvalName.slice(0, -choiceSuffix.length)
        : savedEvalName;
    return [...options, { label, value: savedEvalId }];
  }, [expandedEvaluations, openSheetView, alertRuleDetails]);

  const selectedEval = useMemo(
    () => expandedEvaluations?.find((evaluation) => evaluation?.id === metric),
    [expandedEvaluations, metric],
  );
  const selectedEvalOutputType = selectedEval?.output_type ?? null;
  // The eval the preview payload actually names. The payload carries the
  // debounced metric, so for 300ms after a switch it still points at the old
  // eval; gating on the live one in that window previews a choice eval with no
  // label, and the backend rejects it.
  const debouncedSelectedEval = useMemo(
    () =>
      expandedEvaluations?.find(
        (evaluation) => evaluation?.id === debouncedMetric,
      ),
    [expandedEvaluations, debouncedMetric],
  );
  // Choice and Pass/Fail evals aggregate to a rate between 0 and 1; score
  // evals are avg(output_float) with no upper bound, so only the bounded
  // kinds get the fraction label. percentage_change divides this same field
  // by 100 for every metric type (backend: 0-100 scale), so that mode is
  // labelled as a percent instead.
  const isEvalFractionScale =
    metricType === "evaluation_metrics" &&
    thresholdType === "static" &&
    ["choices", "Pass/Fail"].includes(selectedEvalOutputType);
  const isPercentScale = thresholdType === "percentage_change";
  // Which share a provider error rate threshold is on, beside the number. A
  // saved row still on the earlier rule reads the share with no errors.
  const providerHelp = providerThresholdHelp({
    metricType,
    thresholdType,
    // Saving a new value in any of these moves the row to the current rule
    // (the server's THRESHOLD_RULE_FIELDS), so the words follow the edit.
    earlier:
      Boolean(openSheetView) &&
      !duplicateAlertName &&
      alertRuleDetails?.thresholdRule === "earlier" &&
      !THRESHOLD_RULE_FIELDS.some((name) => dirtyFields?.[name]),
  });
  // The saved alert's own rule. A duplicate is saved as a new alert, which
  // the server judges by the current rule, so it gets no line.
  const earlierNote =
    openSheetView && !duplicateAlertName
      ? earlierRuleNote(alertRuleDetails)
      : "";

  const selectedMetricOptions = useMemo(() => {
    if (expandedEvaluations?.length > 0 && metric) {
      if (!evalUsesChoiceThreshold(selectedEval)) return [];
      return (
        selectedEval?.choices?.map((choice) => ({
          label: choice,
          value: choice,
        })) ?? []
      );
    }
    return [];
  }, [expandedEvaluations, metric, selectedEval]);

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
      // An alert saved without a warning keeps it empty: send none.
      ...(debouncedWarning !== "" && {
        warning_threshold_value: debouncedWarning,
      }),
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
      if (
        debouncedThresHoldMetricValue &&
        evalUsesChoiceThreshold(debouncedSelectedEval)
      ) {
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
    debouncedSelectedEval,
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
      // Presence, not truthiness — 0 is a valid threshold and would
      // otherwise disable the preview graph for the whole form.
      debouncedCritical !== undefined &&
      debouncedWarning !== undefined &&
      debouncedFrequency &&
      !hasErrors &&
      (debouncedMetricType === "evaluation_metrics"
        ? debouncedMetric &&
          // Wait for the debounced metric to catch up with the picker, so the
          // preview never runs for the eval the user just left.
          debouncedMetric === metric &&
          // Until the eval list resolves, a Pass/Fail eval looks like a score
          // one and would be previewed without the label it needs.
          debouncedSelectedEval &&
          // A choice-thresholded eval's graph requires the chosen label; firing
          // the preview before it is set 400s. Score evals need no choice.
          (!evalUsesChoiceThreshold(debouncedSelectedEval) ||
            debouncedThresHoldMetricValue)
        : true);

    const isThresholdValid = (() => {
      // No warning to compare with (an alert saved without one).
      if (debouncedWarning === "") return true;
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
    metric,
    debouncedThresHoldMetricValue,
    debouncedSelectedEval,
    errors,
    openSheetView,
    isDirty,
  ]);

  // A choice-thresholded eval with no label yet has nothing to preview; the
  // chart says so rather than keep drawing the eval the user just left.
  const previewAwaitingLabel = Boolean(
    debouncedMetricType === "evaluation_metrics" &&
      debouncedMetric === metric &&
      evalUsesChoiceThreshold(debouncedSelectedEval) &&
      !debouncedThresHoldMetricValue,
  );

  useEffect(() => {
    onPayloadChange(queryPayload, isQueryEnabled, previewAwaitingLabel);
  }, [queryPayload, isQueryEnabled, previewAwaitingLabel, onPayloadChange]);

  useEffect(() => {
    if (queryPayload) {
      setThresholdOperator(queryPayload?.threshold_operator);
      setWarningValue(queryPayload?.warning_threshold_value);
      setCriticalValue(queryPayload?.critical_threshold_value);
    }
  }, [queryPayload, setThresholdOperator, setWarningValue, setCriticalValue]);

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
      refreshGrid();
      refreshIssues();
      // The details-page graph is keyed only on the alert id + date window, so
      // a threshold_type or config change leaves its cache stale until the 10s
      // poll happens to refetch. Invalidate it so the saved graph updates now.
      queryClient.invalidateQueries({ queryKey: ["alert-graph"] });
    },
  });

  const handleCreateAlert = (data) => {
    const { observation_type, span_attributes_filters } =
      convertFiltersToPayload(data?.filters);

    const isSlack = data?.notification?.method === "slack";
    const notificationPayload = {
      notification_emails: isSlack ? [] : data?.notification?.emails ?? [],
      slack_webhook_url: isSlack
        ? data?.notification?.slack?.webhookUrl ?? ""
        : "",
      slack_notes: isSlack ? data?.notification?.slack?.notes ?? "" : "",
    };
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

    const isUpdate = Boolean(openSheetView && !duplicateAlertName);
    // Send the choice only for an eval known to take one. While the eval list
    // is unresolved an update leaves it out: the server keeps the stored choice
    // for a Pass/Fail eval and drops it for a score eval, where resending it
    // would 400 with no visible field to clear. A new alert has no stored
    // choice to fall back on, so it keeps sending what the form holds.
    const sendChoice = selectedEval
      ? evalUsesChoiceThreshold(selectedEval)
      : !isUpdate;

    const payload = {
      name: data?.name,
      metric_type: data?.metric_type,
      project: observeId,
      ...(currentOrganizationId && { organization: currentOrganizationId }),
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
        ...(data?.threshold_metric_value &&
          sendChoice && {
            threshold_metric_value: data?.threshold_metric_value,
          }),
      }),
      // PATCH must clear saved evaluation fields when switching to a system metric.
      ...(data?.metric_type !== "evaluation_metrics" && {
        metric: null,
        threshold_metric_value: null,
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

  // A new alert whose two values are still the defaults for the direction
  // being left gets the defaults for the new one, so an untouched Count of
  // errors alert switched to Below reads critical 300 and warning 400, not a
  // reason under values nobody typed. Typed values and a saved alert's values
  // never change. isDirty is the "typed" signal: typing and the spinner both
  // go through the field's own onChange, which compares with the values the
  // form opened with, while setValue (here and in the metric_type and
  // threshold_type handlers) leaves it alone. isTouched would not do:
  // tabbing through a field marks it without changing anything.
  const applyDirectionDefaultsIfUntouched = (nextOperator) => {
    if (openSheetView || !nextOperator) return;
    const valueFields = ["critical_threshold_value", "warning_threshold_value"];
    if (valueFields.some((name) => getFieldState(name).isDirty)) return;
    const previous = getThresholdValueDefaults(
      metricType,
      thresholdType,
      thresholdOperator,
    );
    if (
      getValues("critical_threshold_value") !== previous.critical ||
      getValues("warning_threshold_value") !== previous.warning
    ) {
      return;
    }
    const next = getThresholdValueDefaults(
      metricType,
      thresholdType,
      nextOperator,
    );
    // Both before the caller's trigger(), as in the metric_type handler.
    setValue("critical_threshold_value", next.critical);
    setValue("warning_threshold_value", next.warning);
  };

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
      <form noValidate onSubmit={handleSubmit(handleCreateAlert)}>
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
            inputProps={{ "data-alert-field": "name" }}
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
                inputProps={{ "data-alert-field": "metric-type" }}
                onChange={(e) => {
                  handleChangeAlertType(e?.target?.value);
                  // FormSearchSelectFieldControl calls this onChange BEFORE
                  // react-hook-form's own field.onChange, so metric_type in
                  // form state is still the old value at this point. Set it
                  // here so the zod re-validation below (and the scale
                  // lookup) both see the metric the user just picked, not
                  // the one they're leaving.
                  setValue("metric_type", e?.target?.value);
                  // Reset the thresholds only when the switch actually moves
                  // them onto a different scale. Overwriting unconditionally
                  // would destroy a saved value (e.g. a stored 250) on a
                  // switch between two metrics that read the field the same
                  // way. The new pair keeps the alert's direction, so a
                  // Below alert does not get Above values it can't save.
                  const previous = getThresholdValueDefaults(
                    metricType,
                    thresholdType,
                    thresholdOperator,
                  );
                  const next = getThresholdValueDefaults(
                    e?.target?.value,
                    thresholdType,
                    thresholdOperator,
                  );
                  if (previous.critical !== next.critical) {
                    // Set both before validating — the cross-field
                    // (critical > warning) check in the zod schema needs both
                    // new values in place together, or validating right after
                    // the first setValue checks it against the other field's
                    // stale value and raises a spurious error.
                    setValue("critical_threshold_value", next.critical);
                    setValue("warning_threshold_value", next.warning);
                  }
                  trigger([
                    "metric",
                    "critical_threshold_value",
                    "warning_threshold_value",
                  ]);
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
                  required
                  size="small"
                  options={evalOptions}
                  noOptions={evalPickerEmptyText}
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
                inputProps={{ "data-alert-field": "interval" }}
                // sx={{
                //   flex: 1,
                //   maxWidth: { sm: "300px", md: "400px", lg: "600px" },
                //   minWidth: { sm: "400px" },
                // }}
              />
            </Box>
            <ShowComponent
              condition={
                metricType === "evaluation_metrics" && hasNoEvalsWithResults
              }
            >
              <Typography
                component="p"
                variant="s1"
                color="text.secondary"
                sx={{ px: 3, pb: 3, mt: -1.5 }}
              >
                No evals have results in this project yet.{" "}
                <Link
                  component={RouterLink}
                  to={`/dashboard/tasks/create?project=${observeId}`}
                >
                  Set up an eval task
                </Link>
                , and its evals will show up here after the first run.
              </Typography>
            </ShowComponent>
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
              <AlertFilterBar
                control={control}
                setValue={setValue}
                projectId={observeId}
              />
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
                    // Same scale-guarded re-derivation as the metric-type
                    // switch above — static and percentage_change read this
                    // field on different scales (fraction vs percent) for
                    // eval metrics, but a switch that keeps the same scale
                    // must leave a saved value alone.
                    const previous = getThresholdValueDefaults(
                      metricType,
                      thresholdType,
                      thresholdOperator,
                    );
                    const next = getThresholdValueDefaults(
                      metricType,
                      e?.target?.value,
                      thresholdOperator,
                    );
                    if (previous.critical !== next.critical) {
                      // Set both before validating — see the same note on the
                      // metric_type handler above.
                      setValue("critical_threshold_value", next.critical);
                      setValue("warning_threshold_value", next.warning);
                    }
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
              <ShowComponent condition={isPercentScale && Boolean(earlierNote)}>
                <Typography
                  component="p"
                  variant="s1"
                  color="text.secondary"
                  data-alert-earlier-rule=""
                  sx={{ px: 3 }}
                >
                  {earlierNote}
                </Typography>
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
                    iconColor={"status.danger.fg"}
                    bgColor="status.danger.bg"
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
                            of
                          </Typography>
                        </Stack>
                      </ShowComponent>
                      <FormSearchSelectFieldControl
                        control={control}
                        fieldName={"threshold_operator"}
                        onChange={(e) => {
                          // Never touch the values the person typed: a pair
                          // that does not fit the new direction shows why
                          // under both fields, and the form will not save.
                          // FormSearchSelectFieldControl calls this onChange
                          // BEFORE react-hook-form's own field.onChange, so
                          // set the direction first, or the check below
                          // compares the values with the one being left.
                          // Only a new alert's untouched defaults follow it.
                          setValue("threshold_operator", e?.target?.value);
                          applyDirectionDefaultsIfUntouched(e?.target?.value);
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
                        inputProps={{
                          "data-alert-field": "critical-threshold-operator",
                        }}
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
                        label={
                          isEvalFractionScale
                            ? "Value (0-1)"
                            : isPercentScale
                              ? "Percentage"
                              : "Value"
                        }
                        helperText={
                          isEvalFractionScale
                            ? "Fraction between 0 and 1, e.g. 0.095 for 9.5%"
                            : providerHelp
                        }
                        size="small"
                        fullWidth
                        fieldType="number"
                        inputProps={{
                          "data-alert-field": "critical-threshold-value",
                        }}
                        sx={{
                          maxWidth: "400px",
                        }}
                      />
                    </Box>
                  </CardWrapper>
                  <CardWrapper
                    icon={"/assets/icons/ic_warning.svg"}
                    title="Warning"
                    iconColor="status.warning.fg"
                    bgColor="status.warning.bg"
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
                            of
                          </Typography>
                        </Stack>
                      </ShowComponent>
                      <FormSearchSelectFieldControl
                        control={control}
                        fieldName={"threshold_operator"}
                        onChange={(e) => {
                          // Same as the critical picker above: keep the typed
                          // values and check them against the new direction.
                          setValue("threshold_operator", e?.target?.value);
                          applyDirectionDefaultsIfUntouched(e?.target?.value);
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
                        inputProps={{
                          "data-alert-field": "warning-threshold-operator",
                        }}
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
                        label={
                          isEvalFractionScale
                            ? "Value (0-1)"
                            : isPercentScale
                              ? "Percentage"
                              : "Value"
                        }
                        helperText={
                          isEvalFractionScale
                            ? "Fraction between 0 and 1, e.g. 0.095 for 9.5%"
                            : providerHelp
                        }
                        size="small"
                        fullWidth
                        fieldType="number"
                        inputProps={{
                          "data-alert-field": "warning-threshold-value",
                        }}
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
              data-alert-form-submit={
                openSheetView
                  ? duplicateAlertName
                    ? "duplicate"
                    : "update"
                  : "create"
              }
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
