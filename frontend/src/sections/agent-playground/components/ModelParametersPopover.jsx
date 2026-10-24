import React from "react";
import PropTypes from "prop-types";
import {
  Box,
  Typography,
  Popover,
  Divider,
  Switch,
  Stack,
  useTheme,
} from "@mui/material";
import _ from "lodash";
import SliderRowState from "src/sections/common/SliderRow/SliderRowState";
import { FormSearchSelectFieldState } from "src/components/FromSearchSelectField";
import { BOOLEAN_VALUE_OPTIONS } from "src/utils/constants";
import { MODEL_PARAMS_TOOLTIPS } from "../utils/constants";
import SvgColor from "src/components/svg-color";
import CustomTooltip from "src/components/tooltip/CustomTooltip";

const selectFieldSx = {
  width: 150,
  "& .MuiInputBase-input": {
    height: "26px",
    paddingY: "2px",
  },
};

export default function ModelParametersPopover({
  open,
  anchorEl,
  onClose,
  modelParameters,
  onSliderChange,
  onBooleanChange,
  onDropdownChange,
  onReasoningSliderChange,
  onReasoningDropdownChange,
  onShowReasoningProcessChange,
}) {
  const theme = useTheme();
  const reasoning = modelParameters?.reasoning;

  const sliderMarkStyles = {
    "& .MuiSlider-mark": {
      backgroundColor: "action.selected",
      height: theme.spacing(0.5),
      width: theme.spacing(0.25),
    },
  };

  const hasReasoning =
    reasoning?.sliders?.length > 0 || reasoning?.dropdowns?.length > 0;

  const hasParameters =
    modelParameters?.sliders?.length > 0 ||
    modelParameters?.booleans?.length > 0 ||
    modelParameters?.dropdowns?.length > 0 ||
    hasReasoning;

  return (
    <Popover
      open={open}
      anchorEl={anchorEl}
      onClose={onClose}
      anchorOrigin={{
        vertical: "bottom",
        horizontal: "left",
      }}
      transformOrigin={{
        vertical: "top",
        horizontal: "left",
      }}
      slotProps={{
        paper: {
          sx: {
            mt: 1,
            p: 2,
            minWidth: 320,
            maxWidth: 400,
            borderRadius: 1,
            border: "1px solid",
            borderColor: "divider",
          },
        },
      }}
    >
      <Typography
        typography="s2_1"
        fontWeight="fontWeightSemiBold"
        color="text.primary"
        sx={{ mb: 2 }}
      >
        Model Parameters
      </Typography>
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          gap: theme.spacing(1.5),
        }}
      >
        {modelParameters?.sliders?.map((item, index) => (
          <SliderRowState
            key={item?.id}
            tooltipText={MODEL_PARAMS_TOOLTIPS[item.label] || null}
            onChange={(value) => onSliderChange(index, value)}
            label={item?.label}
            value={item?.value}
            min={item?.min}
            max={item?.max}
            step={item?.step}
            sx={sliderMarkStyles}
          />
        ))}
        {modelParameters?.booleans?.map((item, index) => (
          <Box
            key={item?.label}
            sx={{ display: "flex", justifyContent: "space-between" }}
          >
            <Typography
              variant="s3"
              fontWeight="fontWeightMedium"
              color="text.primary"
            >
              {_.startCase(item?.label)}
            </Typography>
            <FormSearchSelectFieldState
              value={item?.value}
              onChange={(e) => onBooleanChange(index, e?.target?.value)}
              size="small"
              placeholder="Select"
              sx={selectFieldSx}
              options={BOOLEAN_VALUE_OPTIONS}
            />
          </Box>
        ))}
        {modelParameters?.dropdowns?.map((item, index) => (
          <Box
            key={item?.label}
            sx={{ display: "flex", justifyContent: "space-between" }}
          >
            <Typography
              variant="s3"
              fontWeight="fontWeightMedium"
              color="text.primary"
            >
              {_.startCase(item?.label)}
            </Typography>
            <FormSearchSelectFieldState
              value={item?.value}
              onChange={(e) => onDropdownChange(index, e?.target?.value)}
              size="small"
              placeholder="Select"
              sx={selectFieldSx}
              options={item?.options?.map((op) => ({
                label: _.startCase(op),
                value: op,
              }))}
            />
          </Box>
        ))}

        {/* Reasoning section — only for models that support it */}
        {hasReasoning && (
          <>
            <Divider sx={{ borderColor: "divider" }} />
            <Typography
              variant="s3"
              fontWeight="fontWeightSemiBold"
              color="text.primary"
            >
              Reasoning
            </Typography>
            {reasoning?.sliders?.map((item, index) => (
              <SliderRowState
                key={item?.id}
                tooltipText={MODEL_PARAMS_TOOLTIPS[item.label] || null}
                onChange={(value) => onReasoningSliderChange(index, value)}
                label={item?.label}
                value={item?.value}
                min={item?.min}
                max={item?.max}
                step={item?.step}
                sx={sliderMarkStyles}
              />
            ))}
            {reasoning?.dropdowns?.map((item, index) => (
              <Box
                key={item?.label}
                sx={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                }}
              >
                <Typography
                  variant="s3"
                  fontWeight="fontWeightMedium"
                  color="text.primary"
                >
                  {_.startCase(item?.label)}
                </Typography>
                <FormSearchSelectFieldState
                  value={item?.value}
                  onChange={(e) =>
                    onReasoningDropdownChange(index, e?.target?.value)
                  }
                  size="small"
                  placeholder="Select"
                  sx={selectFieldSx}
                  options={item?.options?.map((op) => ({
                    label: _.startCase(op),
                    value: op,
                  }))}
                />
              </Box>
            ))}
            <Box
              sx={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
              }}
            >
              <Stack direction="row" alignItems="center" gap={1}>
                <Typography
                  typography="s3"
                  fontWeight="fontWeightMedium"
                  color="text.primary"
                >
                  Show reasoning process
                </Typography>
                <CustomTooltip
                  type="black"
                  size="small"
                  placement="top"
                  arrow
                  title="Reasoning visibility depends on model support. Some models may not display it."
                >
                  <SvgColor
                    sx={{ height: "16px", width: "16px" }}
                    src="/assets/icons/ic_info.svg"
                  />
                </CustomTooltip>
              </Stack>
              <Switch
                checked={reasoning?.showReasoningProcess ?? true}
                onChange={(e) => onShowReasoningProcessChange(e.target.checked)}
              />
            </Box>
          </>
        )}

        {!hasParameters && (
          <Typography
            typography="s2"
            color="text.secondary"
            sx={{ textAlign: "center", py: 2 }}
          >
            No parameters available for this model
          </Typography>
        )}
      </Box>
    </Popover>
  );
}

ModelParametersPopover.propTypes = {
  open: PropTypes.bool.isRequired,
  anchorEl: PropTypes.any,
  onClose: PropTypes.func.isRequired,
  modelParameters: PropTypes.shape({
    sliders: PropTypes.array,
    booleans: PropTypes.array,
    dropdowns: PropTypes.array,
    reasoning: PropTypes.shape({
      sliders: PropTypes.array,
      dropdowns: PropTypes.array,
      showReasoningProcess: PropTypes.bool,
    }),
  }),
  onSliderChange: PropTypes.func.isRequired,
  onBooleanChange: PropTypes.func.isRequired,
  onDropdownChange: PropTypes.func.isRequired,
  onReasoningSliderChange: PropTypes.func,
  onReasoningDropdownChange: PropTypes.func,
  onShowReasoningProcessChange: PropTypes.func,
};
