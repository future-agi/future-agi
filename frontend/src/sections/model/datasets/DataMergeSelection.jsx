import { Box, Divider, Typography } from "@mui/material";
import React, { useMemo } from "react";
import { useWatch } from "react-hook-form";
import { FormSelectField } from "src/components/FormSelectField";
import Iconify from "src/components/iconify";
import { EnvironmentOptions } from "src/utils/constant";
import PropTypes from "prop-types";
import { useGetMetricOptions } from "src/api/model/metric";
import { useParams } from "react-router";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";

const DataMergeSelection = ({
  control,
  environmentField,
  versionField,
  versionSelect,
}) => {
  const { id } = useParams();

  const { data: datasetOptions } = useGetMetricOptions(id);

  const selectedEnvironment = useWatch({ control, name: "environment" });

  const versionOptions = useMemo(() => {
    if (!datasetOptions || !selectedEnvironment) {
      return [];
    }
    if (selectedEnvironment) {
      return datasetOptions.reduce((arr, { environment, version }) => {
        if (environment === selectedEnvironment && !arr.includes(version)) {
          arr.push({ label: version, value: version });
        }
        return arr;
      }, []);
    }
  }, [datasetOptions, selectedEnvironment]);

  const environmentOptions = useMemo(() => {
    if (!datasetOptions) {
      return [];
    }

    const set = new Set(datasetOptions.map((o) => o.environment));

    return Array.from(set).map((e) => ({ label: e, value: e }));
  }, [datasetOptions]);

  return (
    <>
      <Box
        sx={{
          padding: "20px",
          display: "flex",
          flexDirection: "column",
          gap: "20px",
        }}
      >
        <Box sx={{ display: "flex", gap: 1, flexDirection: "column" }}>
          <Typography fontWeight={600} fontSize="13px">
            Enter environment and version name
          </Typography>
          <Box sx={{ display: "flex", gap: 1 }}>
            <Box sx={{ display: "flex", gap: 1, alignItems: "flex-start" }}>
              <Iconify
                icon="solar:info-circle-bold"
                color="text.secondary"
                sx={{ flexShrink: 0 }}
              />
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ width: "30ch" }}
              >
                Enter environment and version name to create a new dataset{" "}
              </Typography>
            </Box>
            <FormSelectField
              control={control}
              fieldName={environmentField}
              fullWidth
              options={versionSelect ? environmentOptions : EnvironmentOptions}
              label="Environment"
            />
            {versionSelect ? (
              <CustomTooltip
                show={!selectedEnvironment}
                placement="bottom"
                arrow
                title="Select an environment first"
              >
                <Box sx={{ width: "100%" }}>
                  <FormSelectField
                    control={control}
                    fieldName={versionField}
                    fullWidth
                    options={versionOptions}
                    label="Version"
                    disabled={!selectedEnvironment}
                  />
                </Box>
              </CustomTooltip>
            ) : (
              <FormTextFieldV2
                label="Version"
                placeholder="Enter version"
                fullWidth
                control={control}
                fieldName={versionField}
              />
            )}
          </Box>
        </Box>
        <Box sx={{ display: "flex", gap: 1, flexDirection: "column" }}>
          <Typography fontWeight={600} fontSize="13px">
            Choose from existing datasets
          </Typography>
          <Box sx={{ display: "flex", gap: 1, alignItems: "center" }}>
            <Iconify icon="solar:info-circle-bold" color="text.secondary" />
            <Typography variant="caption" color="text.secondary">
              Choose datasets from the list below to add to the new dataset
            </Typography>
          </Box>
        </Box>
      </Box>
      <Divider />
    </>
  );
};

DataMergeSelection.propTypes = {
  control: PropTypes.any,
  environmentField: PropTypes.string,
  versionField: PropTypes.string,
  versionSelect: PropTypes.bool,
};

export default DataMergeSelection;
