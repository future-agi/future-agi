import React, { useState } from "react";
import { Controller, useController, useWatch } from "react-hook-form";
import PropTypes from "prop-types";
import { Box, TextField, IconButton } from "@mui/material";
import DateTimeRangePicker from "src/sections/projects/DateTimeRangePicker";
import NewTaskSlider from "./NewTaskSlider";
import Iconify from "src/components/iconify";
import { red } from "src/theme/palette";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";

const RunTypes = [
  {
    label: "Run on historic data",
    value: "historical",
    tooltip:
      "Applies evaluators to specified time range of already-collected traces. Essential for post-deployment audits.",
  },
  {
    label: "Run continuously on new incoming data",
    value: "continuous",
    tooltip: "Applies evaluators as new traces are generated.",
  },
];

const ScheduledRuns = ({ control, dayLimit, isEdit = false }) => {
  const runType = useWatch({ control, name: "runType" });
  const [dateOption, setDateOption] = useState(dayLimit);

  const { field: startDateField } = useController({
    control,
    name: "startDate",
  });
  const { field: endDateField } = useController({ control, name: "endDate" });

  const dateFilter = [startDateField.value, endDateField.value];

  const handleDateFilterChange = (newDateFilter) => {
    if (newDateFilter && newDateFilter.length === 2) {
      startDateField.onChange(newDateFilter[0]);
      endDateField.onChange(newDateFilter[1]);
    }
  };

  return (
    <Box sx={{ display: "flex", gap: 1, flexDirection: "column" }}>
      <Box
        sx={{
          marginBottom: 1,
        }}
      >
        <FormSearchSelectFieldControl
          control={control}
          fieldName="runType"
          size="small"
          fullWidth
          label="Run on"
          options={RunTypes}
          required
        />
      </Box>
      {runType === "historical" && (
        <>
          <DateTimeRangePicker
            setParentDateFilter={handleDateFilterChange}
            dateOption={dateOption}
            setDateOption={setDateOption}
            dateFilter={dateFilter}
            isEdit={isEdit}
          />
          <Box>
            {/* MaxSpans TextField with increase/decrease buttons */}
            <Box my={2}>
              <Controller
                name="spansLimit"
                control={control}
                rules={{
                  required: "Max spans is required",
                  min: { value: 1, message: "Minimum value is 1" },
                  max: { value: 1000000, message: "Maximum value is 1000000" },
                }}
                render={({ field, fieldState: { error } }) => {
                  const handleIncrement = () => {
                    const currentValue = Number(field.value) || 0;
                    field.onChange(currentValue + 1);
                  };

                  const handleDecrement = () => {
                    const currentValue = Number(field.value) || 0;
                    if (currentValue > 1) {
                      field.onChange(currentValue - 1);
                    }
                  };

                  return (
                    <Box sx={{ position: "relative" }}>
                      <TextField
                        {...field}
                        label="Max Spans"
                        type="number"
                        size="small"
                        variant="outlined"
                        placeholder="Enter number of spans"
                        fullWidth
                        error={!!error}
                        helperText={error?.message}
                        inputProps={{ min: 1, max: 1000000, step: 1 }}
                        sx={{
                          "& .MuiInputBase-root": { paddingRight: "60px" },
                          "& .MuiFormLabel-asterisk": { color: red[500] },
                        }}
                        required={true}
                        value={field.value || ""}
                      />
                      <Box
                        sx={{
                          position: "absolute",
                          right: "10px",
                          top: "50%",
                          transform: "translateY(-50%)",
                          display: "flex",
                          flexDirection: "column",
                          gap: 0,
                        }}
                      >
                        <IconButton
                          size="small"
                          onClick={handleIncrement}
                          sx={{
                            p: 0.2,
                            minWidth: "10px",
                            lineHeight: 0,
                          }}
                        >
                          <Iconify
                            icon="solar:alt-arrow-up-linear"
                            width={20}
                            color="text.primary"
                          />
                        </IconButton>
                        <IconButton
                          size="small"
                          onClick={handleDecrement}
                          sx={{
                            p: 0.2,
                            minWidth: "10px",
                            lineHeight: 0,
                          }}
                        >
                          <Iconify
                            icon="solar:alt-arrow-down-linear"
                            width={20}
                            color="text.primary"
                          />
                        </IconButton>
                      </Box>
                    </Box>
                  );
                }}
              />
            </Box>

            {/* SamplingRate Slider */}
            <Box sx={{ flex: 1 }}>
              <Controller
                name="samplingRate"
                control={control}
                rules={{
                  required: "SamplingRate is required",
                  min: { value: 1, message: "Minimum value is 1" },
                  max: { value: 100, message: "Maximum value is 100" },
                }}
                render={({ field, fieldState: { error } }) => (
                  <>
                    <NewTaskSlider
                      {...field}
                      label="Sampling rate"
                      control={control}
                      fieldName="samplingRate"
                      min={1}
                      max={100}
                      step={1}
                      value={field.value}
                      onChange={field.onChange}
                      onBlur={field.onBlur}
                    />
                    {error && (
                      <Box
                        sx={{
                          color: "error.main",
                          fontSize: "0.75rem",
                          mt: 0.5,
                        }}
                      >
                        {error.message}
                      </Box>
                    )}
                  </>
                )}
              />
            </Box>
          </Box>
        </>
      )}
      {runType === "continuous" && (
        <Box sx={{ flex: 1 }}>
          <Controller
            name="samplingRate"
            control={control}
            rules={{
              required: "SamplingRate is required",
              min: { value: 1, message: "Minimum value is 1" },
              max: { value: 100, message: "Maximum value is 100" },
            }}
            render={({ field, fieldState: { error } }) => (
              <>
                <NewTaskSlider
                  {...field}
                  label="Sampling rate"
                  control={control}
                  fieldName="samplingRate"
                  min={1}
                  max={100}
                  step={1}
                  value={field.value}
                  onChange={field.onChange}
                  onBlur={field.onBlur}
                />
                {error && (
                  <Box
                    sx={{
                      color: "error.main",
                      fontSize: "0.75rem",
                      mt: 0.5,
                    }}
                  >
                    {error.message}
                  </Box>
                )}
              </>
            )}
          />
        </Box>
      )}
    </Box>
  );
};

ScheduledRuns.propTypes = {
  control: PropTypes.object.isRequired,
  dayLimit: PropTypes.string.isRequired,
  isEdit: PropTypes.bool,
};

export default ScheduledRuns;
