import {
  Box,
  Button,
  FormControl,
  InputLabel,
  MenuItem,
  Select,
  Skeleton,
  TextField,
  Typography,
  useTheme,
} from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import PropTypes from "prop-types";
import React, { useEffect, useState } from "react";
import { useController, useFieldArray } from "react-hook-form";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import {
  FormSearchSelectFieldControl,
  FormSearchSelectFieldState,
} from "src/components/FromSearchSelectField";
import Iconify from "src/components/iconify";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
} from "src/sections/develop-detail/AccordianElements";
import axios, { endpoints } from "src/utils/axios";
import { z } from "zod";
import { LoadingButton } from "@mui/lab";

const ThresholdTypeOptions = [
  { label: "greater than", value: "greater_than" },
  { label: "less than", value: "less_than" },
  { label: "greater than or equal to", value: "greater_or_equal_to" },
  { label: "less than or equal to", value: "less_than_or_equal_to" },
  { label: "equal to", value: "equal" },
];

const ThresholdOptions = [
  { label: "Auto", value: "auto" },
  { label: "Manual", value: "manual" },
];

const metricOptionsConfig = {
  "Pass/Fail": {
    label: "Value",
    options: [
      { value: "pass", label: "Pass" },
      { value: "fail", label: "Fail" },
    ],
    suffixText: "rate is",
  },
};

const AlertSettingSkeleton = () => {
  const theme = useTheme();

  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "flex-start",
        gap: theme.spacing(2),
        py: theme.spacing(2),
      }}
    >
      <Box
        display="flex"
        flexDirection="column"
        gap={theme.spacing(2.5)}
        flex={1}
      >
        <Skeleton variant="rectangular" height={35} width="100%" />
        <Skeleton variant="rectangular" height={25} width="100%" />
        <Skeleton variant="rectangular" height={100} width="100%" />
        <Skeleton variant="rectangular" height={175} width="100%" />
        <Skeleton variant="rectangular" height={140} width="100%" />
      </Box>
    </Box>
  );
};

const ManageMonitorSettings = ({
  control,
  watch,
  handleSubmit,
  onSubmit,
  metricList,
  handleCancel,
  setIsDataEmpty,
  isDataEmpty,
  setThresholdType,
  thresholdType,
  alertData,
  isValid,
  setThresholdMetricValue,
  thresholdMetricValue,
  isLoadingAlertData,
  isCreating,
  isUpdating,
}) => {
  const selectedMetricId = watch("metric");
  const [emailInput, setEmailInput] = useState("");
  const [emailError, setEmailError] = useState(null);
  const theme = useTheme();
  const updateForm = Boolean(alertData?.id);

  const selectedMetric = metricList?.find(
    (metric) => metric.id === selectedMetricId,
  );

  const { data: choices } = useQuery({
    queryKey: ["get-all-choices", selectedMetricId],
    queryFn: () =>
      axios.get(
        endpoints.project.createEvalTaskConfig() +
          (alertData ? selectedMetric?.id : selectedMetricId) +
          "/",
      ),
    enabled: !!selectedMetricId && selectedMetric?.outputType === "choices",
    select: (data) => data?.data?.config?.choices,
  });

  const {
    fields: emailFields,
    append,
    remove,
  } = useFieldArray({
    control,
    name: "notificationEmails",
  });

  const metricOptions =
    metricList?.map((metric) => ({
      label: metric.name,
      value: metric.id,
    })) || [];

  const getMetricMessage = (metric) => {
    if (!metric) {
      return "will detect anomalies when evaluation";
    }
    if (metric.metricType === "system_metric") {
      return "will detect anomalies that are";
    }
    if (metric.outputType === "score") {
      return "will detect anomalies when metric is";
    }
    if (metric.outputType === "choices") {
      return "will detect anomalies when values of";
    }
    if (metric.outputType === "Pass/Fail") {
      return "will detect anomalies when";
    }
    return "will detect anomalies that are";
  };

  const { fieldState } = useController({ control, name: "notificationEmails" });

  const emailsErrorMessage = fieldState.error?.message;
  const emailSchema = z.string().email();

  const isEmailValid = emailSchema.safeParse(emailInput).success;
  const isAddDisabled = !emailInput || !isEmailValid || emailFields.length >= 5;

  const handleAddEmail = () => {
    if (emailFields.length >= 5) {
      return;
    }

    const isValid = emailSchema.safeParse(emailInput);
    if (isValid.success) {
      append({ value: emailInput });
      setEmailInput("");
      setEmailError(null);
    } else {
      setEmailError("Please enter a valid email");
    }
  };

  const handleClose = () => {
    if (isDataEmpty) {
      handleCancel();
    } else {
      setIsDataEmpty(false);
      handleCancel();
    }
  };

  useEffect(() => {
    if (selectedMetric?.outputType === "Pass/Fail" && !thresholdMetricValue) {
      setThresholdMetricValue(
        metricOptionsConfig["Pass/Fail"].options[0].value,
      );
    }

    if (
      selectedMetric?.outputType === "choices" &&
      choices?.length > 0 &&
      !thresholdMetricValue
    ) {
      setThresholdMetricValue(choices[0]);
    }
  }, [selectedMetric, choices, selectedMetricId]);

  if (isLoadingAlertData && alertData) {
    return <AlertSettingSkeleton />;
  }

  return (
    <>
      <Typography
        fontWeight={"fontWeightSemiBold"}
        color={"text.primary"}
        variant="s1"
      >
        {alertData ? "Edit alert settings" : "Manage alert settings"}
      </Typography>
      <Typography
        variant="s2"
        color="text.secondary"
        fontWeight={"fontWeightRegular"}
      >
        Create alerts to get notifications
      </Typography>
      <form
        onSubmit={handleSubmit(onSubmit)}
        onKeyDown={(e) => {
          const target = e.target;
          if (
            e.key === "Enter" &&
            target instanceof HTMLInputElement &&
            target.type !== "submit"
          ) {
            e.preventDefault();
          }
        }}
        style={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          paddingTop: theme.spacing(3),
          justifyContent: "space-between",
        }}
      >
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            gap: theme.spacing(2.5),
            flex: 1,
          }}
        >
          <FormTextFieldV2
            size="small"
            autoFocus
            control={control}
            fieldName="name"
            label="Name"
            placeholder="Untitled"
            fullWidth
            required
            defaultValue=""
            helperText=""
            isSpinnerField={false}
            onBlur={() => {}}
          />
          <Accordion
            defaultExpanded
            sx={{ borderColor: "divider", borderWidth: 1 }}
          >
            <AccordionSummary>
              <Typography
                variant="s2"
                color="text.primary"
                fontWeight={"fontWeightSemiBold"}
              >
                Choose Metric
              </Typography>
            </AccordionSummary>
            <AccordionDetails>
              <FormSearchSelectFieldControl
                size="small"
                label="For"
                placeholder="Choose Metric"
                options={metricOptions}
                control={control}
                fieldName="metric"
                fullWidth
                required
              />
            </AccordionDetails>
          </Accordion>

          {/* alert */}
          <Accordion
            defaultExpanded
            sx={{ borderColor: "divider", borderWidth: 1 }}
          >
            <AccordionSummary>
              <Typography
                variant="s2"
                color="text.primary"
                fontWeight={"fontWeightSemiBold"}
              >
                Define the alert
              </Typography>
            </AccordionSummary>
            <AccordionDetails>
              <Box
                sx={{
                  display: "flex",
                  // flexDirection: "column",
                  alignItems: "center",
                  gap: theme.spacing(1.5),
                  rowGap: theme.spacing(2),
                  flexWrap: "wrap",
                }}
              >
                <Box
                  sx={{
                    display: "flex",
                    gap: theme.spacing(1.5),
                    alignItems: "center",
                    width: "100%",
                  }}
                >
                  <Typography
                    variant="s1"
                    fontWeight={"fontWeightRegular"}
                    sx={{ color: "text.primary" }}
                  >
                    The
                  </Typography>
                  <FormSearchSelectFieldState
                    label="Threshold"
                    placeholder="Select Threshold"
                    onChange={(e) => setThresholdType(e.target.value)}
                    value={thresholdType}
                    defaultValue={thresholdType}
                    options={ThresholdOptions}
                    size="small"
                    sx={{ maxWidth: "350px", flex: 1 }}
                  />
                  <Typography
                    variant="s1"
                    fontWeight={"fontWeightRegular"}
                    sx={{ color: "text.primary", flex: 1, display: "contents" }}
                  >
                    {getMetricMessage(selectedMetric)}
                  </Typography>
                  {selectedMetric?.outputType === "Pass/Fail" && (
                    <>
                      <FormControl size="small" sx={{ flex: 1 }}>
                        <InputLabel>
                          {metricOptionsConfig[selectedMetric.outputType].label}
                        </InputLabel>
                        <Select
                          label={
                            metricOptionsConfig[selectedMetric.outputType].label
                          }
                          value={thresholdMetricValue}
                          onChange={(e) =>
                            setThresholdMetricValue(e.target.value)
                          }
                        >
                          {metricOptionsConfig[
                            selectedMetric.outputType
                          ].options.map((option) => (
                            <MenuItem key={option.value} value={option.value}>
                              {option.label}
                            </MenuItem>
                          ))}
                        </Select>
                      </FormControl>

                      <Typography
                        variant="s1"
                        fontWeight={"fontWeightRegular"}
                        sx={{ color: "text.primary" }}
                      >
                        {
                          metricOptionsConfig[selectedMetric.outputType]
                            .suffixText
                        }
                      </Typography>
                    </>
                  )}
                  {selectedMetric?.outputType === "choices" && (
                    <FormSearchSelectFieldState
                      label="Choices"
                      placeholder="Select Choice"
                      onChange={(e) => setThresholdMetricValue(e.target.value)}
                      value={thresholdMetricValue}
                      defaultValue={thresholdMetricValue}
                      options={choices?.map((choice) => ({
                        label: choice,
                        value: choice,
                      }))}
                      size="small"
                      sx={{ flex: 1 }}
                    />
                  )}
                </Box>

                <Box
                  sx={{
                    display: "flex",
                    gap: theme.spacing(1.5),
                    alignItems: "center",
                    width: "100%",
                  }}
                >
                  {selectedMetric?.outputType === "choices" && (
                    <Typography
                      variant="s1"
                      fontWeight={"fontWeightRegular"}
                      sx={{ color: "text.primary" }}
                    >
                      is
                    </Typography>
                  )}
                  <FormSearchSelectFieldControl
                    label="Select"
                    size="small"
                    control={control}
                    fieldName="thresholdType"
                    options={ThresholdTypeOptions}
                    sx={{ minWidth: "162px", flex: 1 }}
                  />
                  {thresholdType === "manual" && (
                    <Typography
                      variant="s1"
                      fontWeight={"fontWeightRegular"}
                      sx={{
                        color: "text.primary",
                        flex: 1,
                        display: "contents",
                      }}
                    >
                      user defined
                    </Typography>
                  )}

                  <FormTextFieldV2
                    size="small"
                    label={
                      selectedMetric?.metricType === "system_metric"
                        ? "Value"
                        : "Percentage"
                    }
                    fieldName="thresholdValue"
                    placeholder={
                      selectedMetric?.metricType === "system_metric"
                        ? "Enter value"
                        : "Enter percentage"
                    }
                    control={control}
                    sx={{ minWidth: "162px", flex: 1 }}
                    fieldType="number"
                    required
                    helperText=""
                    defaultValue=""
                    isSpinnerField={true}
                    onBlur={() => {}}
                  />
                  {thresholdType === "auto" && (
                    <Typography
                      variant="s1"
                      fontWeight={"fontWeightRegular"}
                      sx={{
                        color: "text.primary",
                        flex: 1,
                        display: "contents",
                      }}
                    >
                      deviation from the predicted data
                    </Typography>
                  )}
                </Box>
              </Box>
            </AccordionDetails>
          </Accordion>

          {/* notification */}
          <Accordion
            defaultExpanded
            sx={{ borderColor: "divider", borderWidth: 1 }}
          >
            <AccordionSummary>
              <Typography
                variant="s2"
                color="text.primary"
                fontWeight={"fontWeightSemiBold"}
              >
                Define the notification
              </Typography>
            </AccordionSummary>
            <AccordionDetails>
              <Box display={"flex"} gap={theme.spacing(1.5)}>
                <TextField
                  size="small"
                  label={
                    <div>
                      Send notification to
                      <span
                        style={{
                          color: theme.palette.red[500],
                          marginLeft: theme.spacing(0.25),
                        }}
                      >
                        *
                      </span>
                    </div>
                  }
                  placeholder="Enter email address"
                  fullWidth
                  helperText={
                    emailsErrorMessage ? (
                      emailsErrorMessage
                    ) : emailError ? (
                      emailError
                    ) : (
                      <Typography
                        variant="s2"
                        color="text.primary"
                        fontWeight={"fontWeightRegular"}
                        sx={{ marginLeft: theme.spacing(-1) }}
                      >
                        Can add upto {5 - emailFields.length} email
                        {5 - emailFields.length !== 1 ? "s" : ""}
                      </Typography>
                    )
                  }
                  value={emailInput}
                  onChange={(e) => {
                    setEmailInput(e.target.value);
                    setEmailError(null);
                  }}
                  error={emailError || emailsErrorMessage}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      handleAddEmail();
                    }
                  }}
                />
                <Button
                  onClick={handleAddEmail}
                  variant="contained"
                  color="primary"
                  sx={{
                    height: "40px",
                    width: "90px",
                    "&:disabled": {
                      color: "common.white",
                      backgroundColor: "action.hover",
                    },
                  }}
                  disabled={isAddDisabled}
                >
                  Add
                </Button>
              </Box>

              <Box sx={{ marginTop: theme.spacing(2) }}>
                <Box
                  sx={{
                    display: "flex",
                    flexWrap: "wrap",
                    gap: theme.spacing(1),
                  }}
                >
                  {emailFields?.map((email, index) => (
                    <Box
                      key={email.id}
                      paddingY={theme.spacing(0.5)}
                      paddingX={theme.spacing(1)}
                      sx={{
                        display: "flex",
                        alignItems: "center",
                        backgroundColor: "primary.lighter",
                        borderRadius: theme.spacing(0.5),
                        width: "fit-content",
                        gap: theme.spacing(1),
                      }}
                    >
                      <Typography
                        variant="s3"
                        fontWeight={"fontWeightRegular"}
                        color={"text.primary"}
                      >
                        {email.value}
                      </Typography>
                      <Iconify
                        icon="mdi:close"
                        onClick={() => remove(index)}
                        sx={{ cursor: "pointer", color: "text.primary" }}
                        width={16}
                        height={16}
                      />
                    </Box>
                  ))}
                </Box>
              </Box>
            </AccordionDetails>
          </Accordion>
        </Box>
        <Box
          display="flex"
          gap={theme.spacing(1)}
          sx={{ paddingBottom: theme.spacing(1), paddingTop: theme.spacing(2) }}
        >
          <Button
            fullWidth
            variant="outlined"
            color="inherit"
            onClick={handleClose}
          >
            <Typography
              variant="s1"
              color="text.primary"
              fontWeight={"fontWeightMedium"}
            >
              Cancel
            </Typography>
          </Button>
          <LoadingButton
            type="submit"
            fullWidth
            loading={isCreating || isUpdating}
            variant="contained"
            color="primary"
            disabled={!isValid}
            sx={{
              "&:disabled": {
                color: "common.white",
                backgroundColor: "action.hover",
              },
            }}
          >
            <Typography
              variant="s1"
              color="background.paper"
              fontWeight={"fontWeightMedium"}
            >
              {updateForm ? "Update" : "Create"} Alert
            </Typography>
          </LoadingButton>
        </Box>
      </form>
    </>
  );
};

ManageMonitorSettings.propTypes = {
  control: PropTypes.object.isRequired,
  watch: PropTypes.func.isRequired,
  handleSubmit: PropTypes.func.isRequired,
  onSubmit: PropTypes.func.isRequired,
  metricList: PropTypes.array.isRequired,
  handleCancel: PropTypes.func.isRequired,
  setIsDataEmpty: PropTypes.func,
  isDataEmpty: PropTypes.bool,
  setThresholdType: PropTypes.func,
  thresholdType: PropTypes.string,
  alertData: PropTypes.object,
  isValid: PropTypes.bool,
  setThresholdMetricValue: PropTypes.func,
  thresholdMetricValue: PropTypes.string,
  isLoadingAlertData: PropTypes.bool,
  isCreating: PropTypes.bool,
  isUpdating: PropTypes.bool,
};

export default ManageMonitorSettings;
