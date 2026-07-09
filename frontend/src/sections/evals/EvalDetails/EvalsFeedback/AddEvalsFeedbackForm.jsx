import React from "react";
import {
  alpha,
  Box,
  Button,
  Checkbox,
  Divider,
  FormControl,
  FormControlLabel,
  IconButton,
  Radio,
  RadioGroup,
  Typography,
  useTheme,
} from "@mui/material";
import { LoadingButton } from "@mui/lab";
import PropTypes from "prop-types";
import { Controller } from "react-hook-form";
import Iconify from "src/components/iconify";
import CellMarkdown from "src/sections/common/CellMarkdown";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import RadioField from "src/components/RadioField/RadioField";
import { OUTPUT_TYPES, RADIO_VALUES } from "./feedbackConstant";

const AddEvalsFeedbackForm = ({
  handleClose,
  control,
  explanation,
  outputType,
  feedbackError,
  choices,
  retuneOptions,
  handleSubmitForm,
  handleSubmit,
  disabled,
  loading,
  isEdit = false,
}) => {
  const theme = useTheme();

  const renderFeedbackValueInput = () => {
    switch (outputType) {
      case OUTPUT_TYPES.TEXT:
        return (
          <FormTextFieldV2
            // @ts-ignore
            fieldName="value"
            control={control}
            fullWidth
            multiline
            placeholder="Write a right value here"
            helperText=""
            sx={{
              "& .MuiOutlinedInput-root": {
                backgroundColor: "action.hover",
              },
            }}
            rows={4}
          />
        );

      case OUTPUT_TYPES.BOOL:
        return (
          <Controller
            name="value"
            control={control}
            render={({ field }) => (
              <FormControl fullWidth>
                <RadioGroup
                  {...field}
                  sx={{
                    border: "1px solid",
                    borderColor: "divider",
                    borderRadius: 1,
                    p: 2,
                  }}
                >
                  <FormControlLabel
                    value={RADIO_VALUES.PASSED}
                    control={<Radio />}
                    label="Passed"
                  />
                  <FormControlLabel
                    value={RADIO_VALUES.FAILED}
                    control={<Radio />}
                    label="Failed"
                  />
                </RadioGroup>
              </FormControl>
            )}
          />
        );

      case OUTPUT_TYPES.FLOAT:
      case OUTPUT_TYPES.INT:
        return (
          <FormTextFieldV2
            // @ts-ignore
            fieldName="value"
            control={control}
            fullWidth
            type="number"
            placeholder="Add Number"
            helperText=""
            sx={{
              "& .MuiOutlinedInput-root": {
                backgroundColor: "action.hover",
                height: "38px",
              },
              "& .MuiInputBase-input": {
                height: "100%",
                boxSizing: "border-box",
              },
            }}
            inputProps={{
              step: outputType === OUTPUT_TYPES.FLOAT ? "0.01" : "1",
            }}
          />
        );

      case OUTPUT_TYPES.STR_LIST:
        if (feedbackError) {
          return (
            <Typography color="error" variant="body2">
              Failed to load choices. Please try again.
            </Typography>
          );
        }

        if (!choices || choices?.length === 0) {
          return (
            <Typography variant="body2" sx={{ color: "text.disabled" }}>
              No choices available
            </Typography>
          );
        }

        return (
          <Controller
            name="value"
            control={control}
            render={({ field }) => (
              <FormControl fullWidth>
                <Box
                  sx={{
                    border: "1px solid",
                    borderColor: "divider",
                    borderRadius: 1,
                    p: 2,
                  }}
                >
                  {choices?.map((choice, index) => (
                    <div key={`${choice}-${index}`}>
                      <FormControlLabel
                        control={
                          <Checkbox
                            checked={field.value?.includes(choice) || false}
                            onChange={(e) => {
                              const currentValues = field.value || [];
                              if (e.target.checked) {
                                // Add choice to array
                                field.onChange([...currentValues, choice]);
                              } else {
                                // Remove choice from array
                                field.onChange(
                                  currentValues.filter(
                                    (item) => item !== choice,
                                  ),
                                );
                              }
                            }}
                          />
                        }
                        label={choice}
                      />
                    </div>
                  ))}
                </Box>
              </FormControl>
            )}
          />
        );

      default:
        return null;
    }
  };

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: "16px",
        height: "100vh",
        padding: 2,
      }}
    >
      <Box sx={{ display: "flex", gap: "4px" }}>
        <Box sx={{ display: "flex", flexDirection: "column", gap: "4px" }}>
          <Typography
            typography="m3"
            fontWeight={"fontWeightSemiBold"}
            color="text.primary"
          >
            {isEdit ? "Edit Feedback" : "Feedbacks for Auto Learning"}
          </Typography>
          <Typography
            typography="s1"
            fontWeight={"fontWeightRegular"}
            color="text.primary"
          >
            Help us refined evals. Share any issues and we’ll use your feedback
            to improve it automatically
          </Typography>
        </Box>
        <IconButton onClick={handleClose} size="small" sx={{ height: "32px" }}>
          {/* @ts-ignore */}
          <Iconify icon="akar-icons:cross" sx={{ color: "text.primary" }} />
        </IconButton>
      </Box>
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          flex: 1,
          overflowY: "auto",
          gap: 2,
          height: "100%",
        }}
      >
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            gap: "8px",
            overflowWrap: "break-word",
          }}
        >
          <Typography fontWeight={500} fontSize={14} color="text.primary">
            Explanation
          </Typography>
          <Box
            sx={{
              border: `1px solid ${alpha(theme.palette.text.disabled, 0.2)}`,
              padding: "16px",
              borderRadius: "4px",
              height: "240px",
              overflowY: "auto",
            }}
          >
            <Typography fontWeight={400} fontSize={14} color="text.primary">
              {typeof explanation === "string" ? (
                explanation?.trim() ? (
                  <CellMarkdown spacing={0} text={explanation} />
                ) : (
                  "Unable to fetch Explanation"
                )
              ) : (
                explanation?.map((item, index) => (
                  <CellMarkdown key={index} spacing={0} text={item} />
                ))
              )}
            </Typography>
          </Box>
        </Box>
        <Box>
          <Typography
            typography={"s1"}
            fontWeight={"fontWeightMedium"}
            color="text.primary"
            mb={1}
          >
            {outputType === OUTPUT_TYPES.BOOL ||
            outputType === OUTPUT_TYPES.STR_LIST
              ? "Choose a right value"
              : "Write a right value"}
            <span style={{ color: "red" }}>*</span>
          </Typography>

          {renderFeedbackValueInput()}
        </Box>

        <Box>
          <Typography
            typography={"s1"}
            fontWeight={"fontWeightMedium"}
            color="text.primary"
            mb={1}
          >
            Write the right explanation{" "}
            <span style={{ color: "red" }}>*</span>
          </Typography>
          <FormTextFieldV2
            // @ts-ignore
            fieldName="explanation"
            required
            control={control}
            fullWidth
            multiline
            placeholder="Write the explanation the eval should have given for this result, and why it's correct"
            helperText=""
            sx={{
              "& .MuiOutlinedInput-root": {
                backgroundColor: "action.hover",
              },
            }}
            rows={3}
          />
        </Box>
        <Divider orientation="horizontal" />
        <Box
          sx={{
            gap: 2,
            display: "flex",
            flexDirection: "column",
          }}
        >
          <Typography typography={"s1"} fontWeight={"fontWeightMedium"}>
            Select one of the options <span style={{ color: "red" }}>*</span>
          </Typography>
          <RadioField
            control={control}
            fieldName={"actionType"}
            label={""}
            required
            groupSx={{
              border: "none",
              padding: 0,
              gap: 3,
              marginTop: "2px",
            }}
            optionSx={{
              alignItems: "start",
              "& .MuiRadio-root	": {
                marginTop: "-6px",
              },
            }}
            options={retuneOptions.map((item) => ({
              value: item.value,
              label: (
                <RenderLabel
                  title={item.title || ""}
                  description={item.description || ""}
                />
              ),
            }))}
          />
        </Box>
      </Box>
      <Box display="flex" gap={1} justifyContent={"flex-end"}>
        <Button
          variant="outlined"
          size="small"
          sx={{ width: "200px" }}
          onClick={handleClose}
        >
          Cancel
        </Button>
        <LoadingButton
          onClick={handleSubmit(handleSubmitForm)}
          variant="contained"
          color="primary"
          size="small"
          disabled={disabled}
          loading={loading}
          sx={{ width: "200px" }}
        >
          {"Submit feedback"}
        </LoadingButton>
      </Box>
    </Box>
  );
};

export default AddEvalsFeedbackForm;

AddEvalsFeedbackForm.propTypes = {
  handleClose: PropTypes.func,
  control: PropTypes.any,
  explanation: PropTypes.any,
  outputType: PropTypes.string,
  choices: PropTypes.array,
  feedbackError: PropTypes.bool,
  handleSubmitForm: PropTypes.func,
  handleSubmit: PropTypes.func,
  loading: PropTypes.bool,
  disabled: PropTypes.bool,
  retuneOptions: PropTypes.array,
  isEdit: PropTypes.bool,
};

const RenderLabel = ({ title, description }) => {
  return (
    <Box>
      <Typography
        fontWeight={"fontWeightSemiBold"}
        typography={"s1"}
        color="text.secondary"
      >
        {title}
      </Typography>
      <Typography typography={"s2"} color="text.primary">
        {description}
      </Typography>
    </Box>
  );
};

RenderLabel.propTypes = {
  title: PropTypes.string,
  description: PropTypes.string,
};
