import { Box, Checkbox, TextField, Typography, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import React, { useMemo } from "react";
import { useController } from "react-hook-form";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import Iconify from "src/components/iconify";
import { ShowComponent } from "src/components/show";
import CustomTooltip from "src/components/tooltip";

const FieldSelection = ({
  field,
  allColumns,
  jsonSchemas = {},
  control,
  isMultipleColumn,
  check,
  isChecked,
  handleCheckbox,
  placeholder = "",
  fieldName = "",
  dropdownLabel = "Column",
  fullWidth,
  ...rest
}) => {
  const options = useMemo(() => {
    const baseOptions = allColumns.map((col) => ({
      label: col.headerName,
      value: col.field,
    }));

    // Add JSON paths for JSON-type columns
    // Note: dataType can be in col.dataType or col.col.dataType depending on context
    allColumns.forEach((col) => {
      const dataType = col?.dataType || col?.col?.dataType;
      if (jsonSchemas?.[col?.field]?.keys?.length) {
        const schema = jsonSchemas[col?.field];
        schema.keys.forEach((path) => {
          baseOptions.push({
            label: `${col.headerName}.${path}`,
            value: `${col.field}.${path}`,
            isJsonPath: true,
          });
        });
      }

      // Add indexed options for images-type columns (maxImagesCount comes from jsonSchemas)
      const imagesSchema = jsonSchemas?.[col?.field];
      if (dataType === "images" && imagesSchema?.maxImagesCount) {
        for (let idx = 0; idx < imagesSchema.maxImagesCount; idx++) {
          baseOptions.push({
            label: `${col.headerName}[${idx}]`,
            value: `${col.field}[${idx}]`,
            isImagesIndex: true,
          });
        }
      }
    });

    return baseOptions;
  }, [allColumns, jsonSchemas]);

  const theme = useTheme();

  // Get field state for error handling
  const {
    fieldState: { error },
  } = useController({
    name: fieldName ? fieldName : `config.mapping.${field}`,
    control,
  });

  return (
    <Box
      sx={{
        display: "flex",
        ...(check && { marginLeft: "-10px" }),
        ...(fullWidth && { flex: 1, minWidth: 0 }),
      }}
    >
      <Box
        sx={{
          display: "flex",
          gap: 1.5,
          alignItems: "flex-start",
          justifyContent: "space-between",
          width: rest?.module === "task" ? "700px" : "100%",
        }}
      >
        <Box display={"flex"} alignItems={"center"} gap={1.5}>
          {check && (
            <Checkbox
              checked={isChecked}
              onChange={handleCheckbox}
              icon={
                <Iconify
                  icon="system-uicons:checkbox-empty"
                  color="action.selected"
                  width="24px"
                  height="24px"
                />
              }
              checkedIcon={
                <Iconify
                  icon="famicons:checkbox"
                  color="primary.light"
                  width="24px"
                  height="24px"
                />
              }
              sx={{
                padding: 1,
                color: "divider",
              }}
            />
          )}
          <CustomTooltip
            show
            title={field}
            placement="top"
            arrow
            PopperProps={{
              modifiers: [
                {
                  name: "offset",
                  options: {
                    offset: [0, -15],
                  },
                },
              ],
            }}
          >
            <Box
              sx={{
                width: theme.spacing(21.125),
              }}
            >
              <TextField
                value={field}
                InputProps={{
                  readOnly: true,
                  endAdornment: check ? (
                    ""
                  ) : (
                    <Iconify
                      icon="material-symbols:lock-outline"
                      color="text.secondary"
                      sx={{
                        width: theme.spacing(3),
                        height: theme.spacing(3),
                      }}
                    />
                  ),
                }}
                size="small"
                sx={{
                  width: "100%",
                  input: { color: "text.primary", textOverflow: "ellipsis" },
                  "& .MuiOutlinedInput-notchedOutline": {
                    borderColor: isChecked
                      ? theme.palette.text.disabled
                      : theme.palette.divider,
                  },
                  "& .MuiOutlinedInput-root.Mui-focused .MuiOutlinedInput-notchedOutline":
                    {
                      borderColor: isChecked
                        ? theme.palette.text.disabled
                        : theme.palette.divider,
                    },
                  "& .MuiOutlinedInput-root:hover .MuiOutlinedInput-notchedOutline":
                    {
                      borderColor: isChecked
                        ? theme.palette.text.disabled
                        : theme.palette.divider,
                    },
                  ...(isChecked && {
                    boxShadow: `inset 0 0 0 1px ${theme.palette.text.disabled}`,
                    borderRadius: "4px",
                  }),
                  ...(!isChecked && {
                    pointerEvents: "none",
                  }),
                }}
              />
            </Box>
          </CustomTooltip>
          <ShowComponent condition={!rest?.hideFieldColumns}>
            <svg
              width="89"
              height="8"
              viewBox="0 0 89 8"
              fill="none"
              xmlns="http://www.w3.org/2000/svg"
            >
              <path
                d="M0.533333 4C0.533333 5.91459 2.08541 7.46667 4 7.46667C5.91459 7.46667 7.46667 5.91459 7.46667 4C7.46667 2.08541 5.91459 0.533333 4 0.533333C2.08541 0.533333 0.533333 2.08541 0.533333 4ZM89 4L82.5 0.247223V7.75278L89 4ZM4 4V4.65H83.15V4V3.35H4V4Z"
                fill="var(--border-light)"
              />
            </svg>
          </ShowComponent>
        </Box>
        <ShowComponent condition={!rest?.hideFieldColumns}>
          <Box
            sx={{
              flex: 1,
              width: "100%",
            }}
          >
            {check ? (
              <FormTextFieldV2
                defaultValue={""}
                helperText={error?.message || ""}
                error={!!error}
                fieldName={fieldName ? fieldName : `config.mapping.${field}`}
                control={control}
                fullWidth
                size="small"
                label="column"
                placeholder="Enter a column Name"
                sx={{
                  "& .MuiOutlinedInput-root": {
                    ...(isChecked && {
                      boxShadow: `inset 0 0 0 1px ${theme.palette.text.disabled}`,
                      borderRadius: "4px",
                    }),
                  },
                  "& .MuiOutlinedInput-notchedOutline": {
                    borderColor: error
                      ? "error.main"
                      : isChecked
                        ? theme.palette.text.disabled
                        : theme.palette.divider,
                  },
                  "& .MuiOutlinedInput-root.Mui-focused .MuiOutlinedInput-notchedOutline":
                    {
                      borderColor: error
                        ? "error.main"
                        : isChecked
                          ? theme.palette.text.disabled
                          : theme.palette.divider,
                    },
                  "& .MuiOutlinedInput-root:hover .MuiOutlinedInput-notchedOutline":
                    {
                      borderColor: error
                        ? "error.main"
                        : isChecked
                          ? theme.palette.text.disabled
                          : theme.palette.divider,
                    },
                }}
              />
            ) : (
              <FormSearchSelectFieldControl
                label={dropdownLabel}
                fieldName={fieldName ? fieldName : `config.mapping.${field}`}
                control={control}
                error={!!error}
                helperText={error?.message}
                options={(options || []).map((option) => {
                  return {
                    ...option,
                    component: (
                      <CustomTooltip
                        enterDelay={500}
                        enterNextDelay={500}
                        placement="bottom"
                        arrow
                        show
                        title={option.label}
                        sx={{
                          zIndex: 9999,
                        }}
                      >
                        <Box
                          display={"flex"}
                          flexDirection={"row"}
                          alignItems={"center"}
                          gap={"8px"}
                          sx={{
                            width: "100%",
                            padding: (theme) => theme.spacing(0.75, 1),
                          }}
                        >
                          <Typography
                            typography="s1"
                            fontWeight={"fontWeightRegular"}
                            color={"text.primary"}
                          >
                            {option.label}
                          </Typography>
                        </Box>
                      </CustomTooltip>
                    ),
                  };
                })}
                fullWidth
                size="small"
                placeholder={placeholder || "Select a column"}
                multiple={isMultipleColumn}
                allowClear
                {...rest}
              />
            )}
          </Box>
        </ShowComponent>
      </Box>
    </Box>
  );
};

FieldSelection.propTypes = {
  field: PropTypes.string,
  allColumns: PropTypes.array,
  jsonSchemas: PropTypes.object,
  control: PropTypes.object,
  isMultipleColumn: PropTypes.bool,
  check: PropTypes.bool,
  handleCheckbox: PropTypes.func,
  isChecked: PropTypes.bool,
  fieldName: PropTypes.string,
  placeholder: PropTypes.string,
  dropdownLabel: PropTypes.string,
  fullWidth: PropTypes.bool,
};

export default FieldSelection;
