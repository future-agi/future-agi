import React, { useMemo } from "react";
import { ModalComponent } from "src/components/ModalComponent";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import { useForm } from "react-hook-form";
import { FormSelectField } from "src/components/FormSelectField";
import { useGetMetricOptions } from "../../../../api/model/metric";
import { useParams } from "react-router";
import { zodResolver } from "@hookform/resolvers/zod";
import { ConfigureDefaultDatasetValidation } from "./validation";
import axios, { endpoints } from "src/utils/axios";
import { LoadingButton } from "@mui/lab";
import { useSnackbar } from "src/components/snackbar";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import CustomTooltip from "src/components/tooltip/CustomTooltip";

const ConfigureDefaultDataset = ({ open, onClose, modelDetails }) => {
  const { id } = useParams();
  const { baselineModelEnvironment, baselineModelVersion } = modelDetails;

  const { enqueueSnackbar } = useSnackbar();
  const queryClient = useQueryClient();

  const isDefaultModelConfigured =
    Boolean(baselineModelEnvironment) && Boolean(baselineModelVersion);

  const { mutate: updateDefaultDataset, isPending } = useMutation({
    mutationFn: (body) =>
      axios.post(`${endpoints.model.updateDefaultDataset}${id}/`, body),
    onSuccess: () => {
      enqueueSnackbar("Default dataset configured", {
        variant: "success",
      });
      queryClient.invalidateQueries({ queryKey: ["model", id] });
      onClose();
      // trackEvent(Events.configConfigureDefaultDatasetComplete, trackObject(v));
    },
  });

  const { data: datasetOptions } = useGetMetricOptions(id);

  const { handleSubmit, control, watch, setValue } = useForm({
    resolver: zodResolver(ConfigureDefaultDatasetValidation),
    defaultValues: isDefaultModelConfigured
      ? {
          environment: baselineModelEnvironment,
          modelVersion: baselineModelVersion,
        }
      : {
          environment: null,
          modelVersion: null,
        },
  });

  const selectedEnvironment = watch("environment");

  const environmentOptions = useMemo(() => {
    if (!datasetOptions) return [];

    const set = new Set(datasetOptions.map((o) => o.environment));

    return Array.from(set).map((v) => ({ label: v, value: v }));
  }, [datasetOptions]);

  const versionOptions = useMemo(() => {
    if (!datasetOptions) return [];
    if (selectedEnvironment) {
      return datasetOptions.reduce((arr, { environment, version }) => {
        if (environment === selectedEnvironment && !arr.includes(version)) {
          arr.push({ label: version, value: version });
        }
        return arr;
      }, []);
    } else {
      const set = new Set(datasetOptions.map((o) => o.version));
      return Array.from(set).map((v) => ({ label: v, value: v }));
    }
  }, [datasetOptions, selectedEnvironment]);

  const onSubmit = (formValues) => {
    updateDefaultDataset(formValues);
  };

  return (
    <ModalComponent open={open} onClose={onClose}>
      <form onSubmit={handleSubmit(onSubmit)}>
        <Box
          sx={{
            padding: 3,
            display: "flex",
            flexDirection: "column",
            gap: 2,
          }}
        >
          <Typography variant="h5">Configure Default Dataset</Typography>
          <FormSelectField
            label="Model Environment"
            control={control}
            fieldName="environment"
            options={environmentOptions || []}
            placeholder="Production"
            onChange={() => {
              setValue("modelVersion", "");
            }}
            MenuProps={{
              PaperProps: {
                sx: {
                  maxHeight: 224,
                },
              },
            }}
          />
          <CustomTooltip
            show={!selectedEnvironment}
            placement="bottom"
            arrow
            title="Select an environment first"
          >
            <Box>
              <FormSelectField
                fullWidth
                label="Model Version"
                control={control}
                fieldName="modelVersion"
                options={versionOptions || []}
                placeholder="V1.1"
                MenuProps={{
                  PaperProps: {
                    sx: {
                      maxHeight: 224,
                    },
                  },
                }}
                disabled={!selectedEnvironment}
              />
            </Box>
          </CustomTooltip>

          <Box sx={{ display: "flex", justifyContent: "flex-end" }}>
            <LoadingButton
              variant="contained"
              color="primary"
              type="submit"
              loading={isPending}
            >
              Update Config
            </LoadingButton>
          </Box>
        </Box>
      </form>
    </ModalComponent>
  );
};

ConfigureDefaultDataset.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  modelDetails: PropTypes.object,
};

export default ConfigureDefaultDataset;
