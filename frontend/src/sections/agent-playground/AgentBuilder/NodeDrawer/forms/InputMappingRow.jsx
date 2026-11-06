/* eslint-disable react/prop-types */
import React from "react";
import PropTypes from "prop-types";
import { Box, TextField, Typography } from "@mui/material";
import { useWatch } from "react-hook-form";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";
import CustomTooltip from "src/components/tooltip";

export default function InputMappingRow({
  port,
  index,
  control,
  variableOptions,
}) {
  const fieldName = `inputMappings.${index}.value`;
  const selectedValue = useWatch({ control, name: fieldName });

  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 1,
      }}
    >
      <CustomTooltip
        show
        size="small"
        title={port.display_name}
        placement="top"
        arrow
        PopperProps={{
          modifiers: [
            {
              name: "offset",
              options: { offset: [0, -15] },
            },
          ],
        }}
      >
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <TextField
            value={port.display_name}
            InputProps={{
              readOnly: true,
              endAdornment: (
                <Iconify
                  icon="solar:lock-linear"
                  color="text.secondary"
                  sx={{ width: 16, height: 16 }}
                />
              ),
            }}
            size="small"
            fullWidth
            sx={{
              input: {
                color: "text.primary",
                textOverflow: "ellipsis",
              },
              "& .MuiOutlinedInput-notchedOutline": {
                borderColor: "divider",
              },
              pointerEvents: "none",
            }}
          />
        </Box>
      </CustomTooltip>
      <SvgColor
        src="/assets/icons/ic_mapping_arrow.svg"
        sx={{
          width: 40,
          height: 8,
          flexShrink: 0,
          color: "text.disabled",
        }}
      />
      <CustomTooltip
        show={!!selectedValue}
        size="small"
        title={selectedValue || ""}
        placement="top"
        arrow
        PopperProps={{
          modifiers: [
            {
              name: "offset",
              options: { offset: [0, -15] },
            },
          ],
        }}
      >
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <FormSearchSelectFieldControl
            control={control}
            fieldName={fieldName}
            label="Variable"
            placeholder="Select variable"
            size="small"
            fullWidth
            options={variableOptions.map((option) => ({
              ...option,
              component: (
                <CustomTooltip
                  enterDelay={500}
                  enterNextDelay={500}
                  placement="bottom"
                  arrow
                  show
                  size="small"
                  title={option.label}
                  sx={{ zIndex: 9999 }}
                >
                  <Box
                    sx={{
                      width: "100%",
                      px: 1,
                      py: 0.75,
                    }}
                  >
                    <Typography
                      typography="s1"
                      fontWeight="fontWeightRegular"
                      color="text.primary"
                      noWrap
                    >
                      {option.label}
                    </Typography>
                  </Box>
                </CustomTooltip>
              ),
            }))}
            showClear
          />
        </Box>
      </CustomTooltip>
    </Box>
  );
}

InputMappingRow.propTypes = {
  port: PropTypes.shape({
    id: PropTypes.string.isRequired,
    displayName: PropTypes.string.isRequired,
  }).isRequired,
  index: PropTypes.number.isRequired,
  control: PropTypes.object.isRequired,
  variableOptions: PropTypes.array.isRequired,
};
