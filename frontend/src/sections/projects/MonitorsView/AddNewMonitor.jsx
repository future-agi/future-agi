import React, { useCallback, useEffect, useState } from "react";
import { Box, Grid, useTheme, Tabs, Tab } from "@mui/material";
import PropTypes from "prop-types";
import { useForm, useFormState } from "react-hook-form";
import { useMutation, useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { zodResolver } from "@hookform/resolvers/zod";
import { enqueueSnackbar } from "src/components/snackbar";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { useParams } from "react-router";

import validationSchema from "./validation";
import ManageMonitorSettings from "./ManageMonitorSettings";
import MonitorGraph from "./MonitorGraph";
import UnsavedChangesDialog from "./UnsavedChangesDialog";
import ManageLogs from "./ManageLogs";

const AddNewMonitor = ({
  onBack,
  setIsDataEmpty,
  isDataEmpty,
  alertData,
  isLoadingAlertData,
}) => {
  const { control, handleSubmit, watch, reset } = useForm({
    defaultValues: {
      name: "",
      metric: "span_response_time",
      thresholdType: "greater_than",
      thresholdValue: "",
      notificationEmails: [],
    },
    resolver: zodResolver(validationSchema),
  });
  const { isDirty, isValid } = useFormState({ control });
  const [showUnsavedDialog, setShowUnsavedDialog] = useState(false);
  const [thresholdType, setThresholdType] = useState("auto");
  const [thresholdMetricValue, setThresholdMetricValue] = useState("");
  const [tabValue, setTabValue] = useState(0);

  const handleTabChange = (event, newValue) => {
    setTabValue(newValue);
  };

  const theme = useTheme();
  const metricName = watch("metric");
  const { observeId } = useParams();

  const { data: metricList } = useQuery({
    queryKey: ["monitor-metric-list", observeId],
    queryFn: () =>
      axios.get(endpoints.project.getMonitorMetricOptions, {
        params: { project_id: observeId },
      }),
    select: (data) => data.data.result || [],
  });

  const showMetricName = metricList?.find((metric) => metric.id === metricName);

  const getMonitorPayload = (data) => {
    const metric = metricList?.find((metric) => metric.id === data.metric);
    const isEvalMetric = metric?.metric_type === "evaluation_metrics";

    return {
      name: data.name,
      metric: isEvalMetric ? data.metric : null,
      threshold_operator: data.thresholdType,
      threshold_type:
        thresholdType === "manual" ? "static" : "percentage_change",
      critical_threshold_value: Number(data.thresholdValue),
      notification_emails: data.notificationEmails,
      threshold_metric_value:
        isEvalMetric && thresholdMetricValue ? thresholdMetricValue : null,
      metric_type: metric?.metric_type,
      project: observeId,
    };
  };

  const { mutate: createMonitor, isPending: isCreating } = useMutation({
    mutationFn: (data) => {
      return axios.post(
        endpoints.project.createMonitor,
        getMonitorPayload(data),
      );
    },
    onSuccess: (_response, variables) => {
      enqueueSnackbar("Alert created successfully", {
        variant: "success",
      });
      const metric = metricList?.find(
        (metric) => metric.id === variables.metric,
      );
      trackEvent(Events.monitorSettingsSubmitted, {
        [PropertyName.formFields]: {
          ...variables,
          metricType: metric?.metric_type,
        },
      });
      onBack();
      setIsDataEmpty(false);
    },
  });

  const { mutate: updateMonitor, isPending: isUpdating } = useMutation({
    mutationFn: (data) => {
      return axios.patch(
        endpoints.project.createMonitor + alertData.id + "/",
        getMonitorPayload(data),
      );
    },
    onSuccess: (_response, variables) => {
      enqueueSnackbar("Alert updated successfully", {
        variant: "success",
      });
      const metric = metricList?.find(
        (metric) => metric.id === variables.metric,
      );
      trackEvent(Events.monitorSettingsSubmitted, {
        [PropertyName.formFields]: {
          ...variables,
          metricType: metric?.metric_type,
        },
      });
      onBack();
      setIsDataEmpty(false);
    },
  });

  const onSubmit = (data) => {
    if (alertData?.id) {
      updateMonitor(data);
    } else {
      createMonitor(data);
    }
  };

  // Handler for close/cross button
  const handleCancel = useCallback(() => {
    if (isDirty) {
      setShowUnsavedDialog(true);
    } else {
      onBack();
    }
  }, [isDirty, onBack]);

  // Handler for Escape key
  React.useEffect(() => {
    const handleEsc = (event) => {
      if (event.key === "Escape") {
        handleCancel();
      }
    };
    window.addEventListener("keydown", handleEsc);
    return () => window.removeEventListener("keydown", handleEsc);
  }, [handleCancel, isDirty]);

  useEffect(() => {
    if (alertData) {
      reset({
        name: alertData.name || "",
        metric:
          alertData.metric || alertData.metric_type || "span_response_time",
        thresholdType: alertData.threshold_operator || "greater_than",
        thresholdValue: alertData.critical_threshold_value || "",
        notificationEmails: (alertData.notification_emails || []).map(
          (email) => ({
            value: email,
          }),
        ),
      });
      setThresholdType(
        alertData.threshold_type === "static" ? "manual" : "auto",
      );
      setThresholdMetricValue(alertData.threshold_metric_value || "");
    }
  }, [alertData, reset]);

  return (
    <Box
      sx={{
        height: "100%",
        display: "flex",
        flexDirection: "column",
        gap: theme.spacing(2),
        paddingTop: theme.spacing(2),
      }}
    >
      <Grid container sx={{ flex: 1 }}>
        <Grid item xs={6} sx={{ height: "100%" }}>
          <MonitorGraph
            selectedMetric={showMetricName?.name}
            control={control}
            metricList={metricList}
          />
        </Grid>
        <Grid item xs={6} sx={{ height: "100%" }}>
          <Box
            sx={{
              padding: theme.spacing(2),
              paddingTop: theme.spacing(0),
              borderLeft: "1px solid",
              borderColor: "divider",
              display: "flex",
              flexDirection: "column",
              overflowY: "auto",
              height: "100%",
            }}
          >
            {!alertData ? (
              <ManageMonitorSettings
                control={control}
                watch={watch}
                handleSubmit={handleSubmit}
                onSubmit={onSubmit}
                metricList={metricList}
                handleCancel={handleCancel}
                setIsDataEmpty={setIsDataEmpty}
                isDataEmpty={isDataEmpty}
                setThresholdType={setThresholdType}
                thresholdType={thresholdType}
                alertData={alertData}
                isValid={isValid}
                setThresholdMetricValue={setThresholdMetricValue}
                thresholdMetricValue={thresholdMetricValue}
                isLoadingAlertData={isLoadingAlertData}
                isCreating={isCreating}
                isUpdating={isUpdating}
              />
            ) : (
              <>
                <Box
                  sx={{
                    borderBottom: 1,
                    borderColor: "divider",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    marginBottom: theme.spacing(2),
                  }}
                >
                  <Tabs
                    value={tabValue}
                    onChange={handleTabChange}
                    textColor="primary"
                    TabIndicatorProps={{
                      style: { backgroundColor: theme.palette.primary.main },
                    }}
                    sx={{
                      fontWeight: "fontWeightMedium",
                      "& .MuiTab-root": {
                        color: theme.palette.text.disabled,
                        marginRight: theme.spacing(0) + "!important",
                        typography: "s1",
                        fontWeight: "fontWeightMedium",
                      },
                      "& .Mui-selected": {
                        color: theme.palette.primary.main,
                      },
                    }}
                  >
                    <Tab
                      label="Alert Details"
                      sx={{
                        margin: 0,
                        px: theme.spacing(3),
                      }}
                    />
                    <Tab
                      label="Logs"
                      sx={{
                        margin: 0,
                        px: theme.spacing(3),
                      }}
                    />
                  </Tabs>
                </Box>

                {/* Tab Panels */}
                {tabValue === 0 && (
                  <ManageMonitorSettings
                    control={control}
                    watch={watch}
                    handleSubmit={handleSubmit}
                    onSubmit={onSubmit}
                    metricList={metricList}
                    handleCancel={handleCancel}
                    setIsDataEmpty={setIsDataEmpty}
                    isDataEmpty={isDataEmpty}
                    setThresholdType={setThresholdType}
                    thresholdType={thresholdType}
                    alertData={alertData}
                    isValid={isValid}
                    setThresholdMetricValue={setThresholdMetricValue}
                    thresholdMetricValue={thresholdMetricValue}
                    isLoadingAlertData={isLoadingAlertData}
                    isCreating={isCreating}
                    isUpdating={isUpdating}
                  />
                )}
                {tabValue === 1 && <ManageLogs alertId={alertData.id} />}
              </>
            )}
          </Box>
        </Grid>
      </Grid>
      <UnsavedChangesDialog
        open={showUnsavedDialog}
        onClose={() => setShowUnsavedDialog(false)}
        onConfirm={() => {
          setShowUnsavedDialog(false);
          onBack();
        }}
      />
    </Box>
  );
};

AddNewMonitor.propTypes = {
  onBack: PropTypes.func,
  setIsDataEmpty: PropTypes.func,
  isDataEmpty: PropTypes.bool,
  alertData: PropTypes.object,
  isLoadingAlertData: PropTypes.bool,
};

export default AddNewMonitor;
