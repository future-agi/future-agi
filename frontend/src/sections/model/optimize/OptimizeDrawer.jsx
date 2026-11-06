import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Chip,
  Drawer,
  FormControl,
  FormHelperText,
  IconButton,
  InputLabel,
  MenuItem,
  Select,
  Typography,
  useTheme,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { useFieldArray, useForm } from "react-hook-form";
import { FormSelectField } from "src/components/FormSelectField";
import {
  useGetAllCustomMetrics,
  useGetMetricOptions,
} from "src/api/model/metric";
import { useParams } from "src/routes/hooks";
import { FormDateField } from "src/components/FormDateField";
import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useSnackbar } from "src/components/snackbar";
import { LoadingButton } from "@mui/lab";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import { EnvironmentMapper, EnvironmentNumberMapper } from "src/utils/constant";
import { addDays, endOfDay, startOfDay, subDays } from "date-fns";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";

import { CreateOptimizationValidation } from "./validation";
import NoMetricAdded from "./NoMetricAdded";
import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";

const optimizeTypeOptions = [
  { label: "Prompt Template", value: "PromptTemplate" },
  { label: "AI Output", value: "RightAnswer" },
];

const getDefaultValues = (initialData, viewData) => {
  if (viewData) {
    return viewData;
  }
  if (initialData) {
    return {
      ...initialData,
      name: "",
      optimizeType: "",
      metrics: [],
    };
  }
  return {
    name: "",
    startDate: startOfDay(subDays(new Date(), 1)),
    endDate: endOfDay(addDays(new Date(), 1)),
    environment: "",
    version: "",
    optimizeType: "",
    metrics: [],
  };
};

const OptimizeDrawerForm = ({ onClose, initialData, viewData }) => {
  const { enqueueSnackbar } = useSnackbar();
  const { id } = useParams();
  const queryClient = useQueryClient();

  const { control, handleSubmit, formState, reset, watch } = useForm({
    defaultValues: getDefaultValues(initialData, viewData),
    resolver: zodResolver(CreateOptimizationValidation),
  });

  const { mutate: createOptimization, isPending } = useMutation({
    mutationFn: (d) =>
      axios.post(`${endpoints.optimization.createOptimization}${id}/`, d),
    onSuccess: () => {
      // if (initialData) {
      //   trackEvent(
      //     Events.datasetsPageOptimizeDatasetFormComplete,
      //     trackObject(v),
      //   );
      // } else {
      //   trackEvent(
      //     Events.optimizePageOptimizeDatasetFormComplete,
      //     trackObject(v),
      //   );
      // }
      enqueueSnackbar({
        message: "Optimization created successfully.",
        variant: "success",
      });
      queryClient.invalidateQueries({ queryKey: ["optimization-list", id] });
      queryClient.invalidateQueries({
        queryKey: ["optimization-table-columns", id],
      });
      onClose();
      reset();
    },
    onError: (data) => {
      if (typeof data?.result === "string") {
        enqueueSnackbar({
          message: data?.result,
          variant: "error",
          autoHideDuration: 5000,
        });
      }
    },
  });

  const {
    append: addMetric,
    remove: removeMetric,
    fields,
  } = useFieldArray({
    control,
    name: "metrics",
  });

  const { data: datasetOptions } = useGetMetricOptions(id);

  const selectedEnvironment = watch("environment");

  const environmentOptions = useMemo(() => {
    if (!datasetOptions) {
      return [];
    }

    const set = new Set(datasetOptions.map((o) => o.environment));

    return Array.from(set).map((v) => ({
      label: v,
      value: EnvironmentMapper[v],
    }));
  }, [datasetOptions]);

  const versionOptions = useMemo(() => {
    if (!datasetOptions) {
      return [];
    }
    if (selectedEnvironment) {
      return datasetOptions.reduce((arr, { environment, version }) => {
        if (
          environment === EnvironmentNumberMapper[selectedEnvironment] &&
          !arr.includes(version)
        ) {
          arr.push({ label: version, value: version });
        }
        return arr;
      }, []);
    } else {
      const set = new Set(datasetOptions.map((o) => o.version));
      return Array.from(set).map((v) => ({ label: v, value: v }));
    }
  }, [datasetOptions, selectedEnvironment]);

  const { data: allCustomMetrics } = useGetAllCustomMetrics(id);

  const [selectedMetric, setSelectedMetric] = useState(null);

  const metricOptions = useMemo(
    () =>
      allCustomMetrics?.map(({ id, name }) => ({ label: name, value: id })) ||
      [],
    [allCustomMetrics],
  );

  const theme = useTheme();

  const onFormSubmit = (formValues) => {
    createOptimization({
      name: formValues.name,
      start_date: formValues.startDate,
      end_date: formValues.endDate,
      environment: formValues.environment,
      version: formValues.version,
      optimize_type: formValues.optimizeType,
      metrics: formValues.metrics?.map(({ value }) => value),
      model: id,
    });
  };

  const viewDisabled = Boolean(viewData);

  const renderDatasetSelection = () => {
    if (initialData) {
      return <></>;
    }
    return (
      <>
        <Box sx={{ gap: "6px", display: "flex" }}>
          <Box sx={{ flex: 1 }}>
            <FormSelectField
              label="Environment"
              control={control}
              fieldName="environment"
              fullWidth
              options={environmentOptions || []}
              size="small"
              MenuProps={{
                sx: {
                  maxHeight: "280px",
                },
              }}
              disabled={viewDisabled}
            />
          </Box>
          <CustomTooltip
            show={!selectedEnvironment}
            placement="bottom"
            arrow
            title="Select an environment first"
          >
            <Box sx={{ flex: 1 }}>
              <FormSelectField
                label="Version"
                control={control}
                fieldName="version"
                fullWidth
                options={versionOptions || []}
                size="small"
                MenuProps={{
                  sx: {
                    maxHeight: "280px",
                  },
                }}
                disabled={viewDisabled || !selectedEnvironment}
              />
            </Box>
          </CustomTooltip>
        </Box>
        <Box sx={{ gap: "6px", display: "flex" }}>
          <FormDateField
            control={control}
            fieldName="startDate"
            fullWidth
            slotProps={{
              textField: { size: "small" },
            }}
            disabled={viewDisabled}
            label="Start Date"
          />
          <FormDateField
            control={control}
            fieldName="endDate"
            fullWidth
            slotProps={{
              textField: { size: "small" },
            }}
            disabled={viewDisabled}
            label="End Date"
          />
        </Box>
      </>
    );
  };

  return (
    <Box
      sx={{
        padding: 2,
        flex: 1,
        display: "flex",
        flexDirection: "column",
        zIndex: 21,
        gap: 2,
        paddingTop: 0.5,
        overflow: "hidden",
      }}
    >
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <Typography variant="subtitle1" color="text.disabled">
          Optimize dataset
        </Typography>
        <IconButton onClick={() => onClose()}>
          <Iconify icon="solar:close-linear" />
        </IconButton>
      </Box>
      <FormTextFieldV2
        label="Name"
        sx={{
          "& .MuiInputBase-input": {
            fontSize: "16px",
            fontWeight: 600,
          },
          "& .MuiInputLabel-root": {
            fontSize: "16px",
          },
        }}
        autoFocus
        placeholder="Untitled"
        control={control}
        fieldName="name"
        disabled={viewDisabled}
      />
      <Box sx={{ display: "flex", gap: 0.5, alignItems: "center" }}>
        <Iconify icon="solar:info-circle-bold" color="text.disabled" />
        <Typography variant="caption" color="text.secondary">
          Choose the dataset you want to optimize and choose metrics to optimize
          your dataset
        </Typography>
      </Box>
      <FormSelectField
        label="Optimize"
        control={control}
        fieldName="optimizeType"
        options={optimizeTypeOptions}
        disabled={viewDisabled}
        fullWidth
        size="small"
      />
      {renderDatasetSelection()}
      <Box sx={{ display: "flex", gap: "22px", alignItems: "flex-start" }}>
        <FormControl
          fullWidth
          size="small"
          error={!!formState?.errors?.metrics?.message}
        >
          <InputLabel>Metric</InputLabel>
          <Select
            label="Metric"
            value={selectedMetric?.value || ""}
            onChange={(e) => {
              const metric = metricOptions.find(
                (o) => o.value === e.target.value,
              );
              setSelectedMetric(metric);
            }}
            disabled={viewDisabled}
          >
            {metricOptions.map((option) => {
              const { value, label } = option;
              const disabled = Boolean(fields.find((f) => f.value === value));
              return (
                <MenuItem disabled={disabled} value={value} key={value}>
                  {label}
                </MenuItem>
              );
            })}
          </Select>
          {!!formState?.errors?.metrics?.message && (
            <FormHelperText>
              {formState?.errors?.metrics?.message}
            </FormHelperText>
          )}
        </FormControl>
        <Button
          variant="contained"
          color="primary"
          disabled={!selectedMetric}
          id="optimize-no-metric-add"
          sx={{
            "& .MuiButton-startIcon": {
              margin: 0,
            },
          }}
          onClick={() => {
            addMetric({ ...selectedMetric });
            setSelectedMetric(null);
          }}
          startIcon={<Iconify icon="solar:add-linear" />}
        />
      </Box>
      {/* <Box sx={{ display: "flex", flexDirection: "column", paddingX: 1 }}>
        <FormGroup row>
          <FormControlLabel
            control={<Radio />}
            label="Optimize Prompt Template"
            componentsProps={{ typography: { color: "text.disabled" } }}
            checked={optimizeType.value === "PromptTemplate"}
            onChange={(_, c) =>
              optimizeType.onChange(c ? "PromptTemplate" : null)
            }
            disabled={viewDisabled}
          />
          <FormControlLabel
            control={<Radio />}
            label="Optimize AI Output"
            color="text.disabled"
            componentsProps={{ typography: { color: "text.disabled" } }}
            checked={optimizeType.value === "RightAnswer"}
            onChange={(_, c) => optimizeType.onChange(c ? "RightAnswer" : null)}
            disabled={viewDisabled}
          />
        </FormGroup>
        {!!formState?.errors?.optimizeType?.message && (
          <FormHelperText error>
            {formState?.errors?.optimizeType?.message}
          </FormHelperText>
        )}
      </Box> */}
      <Box
        sx={{
          flex: 1, // This allows the "Add Metric" section to take up remaining space
          display: "flex",
          flexDirection: "column",
          border: `2px solid ${theme.palette.divider}`,
          borderRadius: "8px",
          overflow: "hidden", // Prevents overflow
        }}
      >
        <Box
          sx={{
            padding: "12px 16px",
            fontWeight: 600,
            fontSize: "13px",
            color: theme.palette.text.disabled,
            backgroundColor: theme.palette.divider,
          }}
        >
          Add Metric
        </Box>
        <Box
          sx={{
            padding: 2,
            display: "flex",
            flexDirection: "column",
            gap: 2,
            flex: 1,
            alignItems: "flex-start",
            overflow: "auto", // Enables vertical scrolling
          }}
        >
          {!fields?.length ? <NoMetricAdded /> : <></>}
          {fields.map(({ value, label }, idx) => (
            <Chip
              color="primary"
              variant="soft"
              label={label}
              onDelete={() => removeMetric(idx)}
              key={value}
            />
          ))}
        </Box>
      </Box>
      {!viewDisabled && (
        <Box sx={{ display: "flex", justifyContent: "flex-end" }}>
          <LoadingButton
            onClick={handleSubmit(onFormSubmit)}
            color="primary"
            size="large"
            variant="contained"
            loading={isPending}
          >
            Optimize dataset
          </LoadingButton>
        </Box>
      )}
    </Box>
  );
};

OptimizeDrawerForm.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  initialData: PropTypes.object,
  viewData: PropTypes.object,
};

const OptimizeDrawer = (props) => {
  const { open, onClose } = props;
  const covered = useDrawerCovered(open);

  return (
    <Drawer
      sx={coveredDrawerSx(covered)}
      anchor="right"
      open={open}
      onClose={onClose}
      PaperProps={{
        sx: {
          height: "100vh",
          width: "480px",
          position: "fixed",
          zIndex: 9999,
          borderRadius: "8px",
          backgroundColor: "background.paper",
        },
      }}
      ModalProps={{
        BackdropProps: {
          style: { backgroundColor: "transparent" },
        },
      }}
    >
      <OptimizeDrawerForm {...props} />
    </Drawer>
  );
};

OptimizeDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  initialData: PropTypes.object,
  viewData: PropTypes.object,
};

export default OptimizeDrawer;
