import React from "react";
import PropTypes from "prop-types";
import {
  Box,
  FormControl,
  InputLabel,
  MenuItem,
  Select,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { DesktopDatePicker, LocalizationProvider } from "@mui/x-date-pickers";
import { AdapterDateFns } from "@mui/x-date-pickers/AdapterDateFns";
import logger from "src/utils/logger";

export default function DatasetSelector({
  datasets,
  datasetSelection,
  onDatasetSelectionChange,
  datasetType,
  disabled = false,
}) {
  let borderColor = "";
  let datasetName = "";

  if (datasetType === "primary") {
    // eslint-disable-next-line no-restricted-syntax -- ds: data colour
    borderColor = "#551a8b";
    datasetName = "A";
  } else {
    // eslint-disable-next-line no-restricted-syntax -- ds: data colour
    borderColor = "#03AC13";
    datasetName = "B";
  }

  function handleChange(e, source) {
    logger.error("Dataset selector changed", e);
    if (source === "environment") {
      onDatasetSelectionChange((prevItem) => ({
        ...prevItem,
        ...{ environment: e.target.value },
      }));
    }

    if (source === "version") {
      onDatasetSelectionChange((prevItem) => ({
        ...prevItem,
        ...{ version: e.target.value },
      }));
    }

    if (source === "eventDateStart") {
      onDatasetSelectionChange((prevItem) => ({
        ...prevItem,
        ...{ eventDateStart: e.toISOString().split("T")[0] },
      }));
    }

    if (source === "eventDateEnd") {
      onDatasetSelectionChange((prevItem) => ({
        ...prevItem,
        ...{ eventDateEnd: e.toISOString().split("T")[0] },
      }));
    }
  }

  return (
    <>
      <div style={{ display: "flex", justifyContent: "left" }}>
        <Stack
          direction="row"
          spacing={0}
          justifyContent="flex-start"
          sx={{
            p: 1,
            mr: 1,
            border: "2.5px solid " + borderColor,
            borderRadius: "6px",
            alignItems: "center",
            width: "auto",
          }}
        >
          <Typography variant="h6" component="div" sx={{ mr: 1 }}>
            {datasetName}
          </Typography>
          <FormControl size="small" sx={{ minWidth: 120 }}>
            <InputLabel id="production-label">Environment</InputLabel>
            <Select
              labelId="environment-label"
              id="environment-select"
              value={
                datasetSelection?.environment
                  ? datasetSelection?.environment
                  : "-"
              }
              label="Environment"
              onChange={(e) => handleChange(e, "environment")}
              disabled={disabled}
            >
              {datasets?.environments?.map((row, index) => (
                <MenuItem key={index} value={row?.environment}>
                  {row?.environment}
                </MenuItem>
              ))}
              {/* <MenuItem key="" value="Production">Production</MenuItem> */}
            </Select>
          </FormControl>
          <FormControl size="small" sx={{ minWidth: 120 }}>
            <InputLabel id="version-label">Version</InputLabel>
            <Select
              labelId="version-label"
              id="version-select"
              value={
                datasetSelection?.version ? datasetSelection?.version : "-"
              }
              label="Version"
              onChange={(e) => handleChange(e, "version")}
              disabled={disabled}
            >
              {datasets?.environments
                ?.filter(
                  (x) => x.environment === datasetSelection?.environment,
                )[0]
                ?.modelVersion?.map((row, index) => (
                  <MenuItem key={index} value={row}>
                    {row}
                  </MenuItem>
                ))}
            </Select>
          </FormControl>
          <LocalizationProvider dateAdapter={AdapterDateFns}>
            <DesktopDatePicker
              label="Start Date"
              inputFormat="dd/MM/yyyy"
              slotProps={{ textField: { size: "small" } }}
              value={
                datasetSelection?.eventDateStart
                  ? Date.parse(datasetSelection?.eventDateStart)
                  : new Date()
              }
              onChange={(e) => handleChange(e, "eventDateStart")}
              renderInput={(params) => (
                <TextField
                  {...params}
                  size="small"
                  sx={{ minWidth: 140, mr: 2 }}
                />
              )}
              disabled={disabled}
            />
            <Box sx={{ mx: 1 }}>-</Box>
            <DesktopDatePicker
              label="End Date"
              inputFormat="dd/MM/yyyy"
              slotProps={{ textField: { size: "small" } }}
              value={
                datasetSelection?.eventDateEnd
                  ? Date.parse(datasetSelection?.eventDateEnd)
                  : new Date()
              }
              onChange={(e) => handleChange(e, "eventDateEnd")}
              renderInput={(params) => (
                <TextField {...params} size="small" sx={{ minWidth: 140 }} />
              )}
              disabled={disabled}
            />
          </LocalizationProvider>
        </Stack>
      </div>
    </>
  );
}

DatasetSelector.propTypes = {
  datasets: PropTypes.object,
  datasetSelection: PropTypes.object,
  onDatasetSelectionChange: PropTypes.func,
  datasetType: PropTypes.string,
  disabled: PropTypes.bool,
};
