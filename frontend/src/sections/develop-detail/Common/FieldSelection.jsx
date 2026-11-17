import { Box, Checkbox, Typography, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import React, { useMemo } from "react";
import { useController, useWatch } from "react-hook-form";
import PathPreviewStrip from "src/sections/common/Values/PathPreviewStrip";
import { kindIcon } from "src/sections/common/Values/kindIcons";
import {
  coveragePercent,
  dominantKind,
  exampleText,
} from "src/sections/common/Values/inferSchema";
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
      // R2 (P6): typed paths (type icon, coverage, example) when the
      // json-schema has them, else its keys.
      if (jsonSchemas?.[col?.field]?.paths?.length) {
        jsonSchemas[col.field].paths
          .filter((p) => dominantKind(p.kinds) !== "null")
          .forEach((p) => {
            baseOptions.push({
              label: p.path,
              value: `${col.field}${p.rel.startsWith("[") ? "" : "."}${p.rel}`,
              isJsonPath: true,
              kind: dominantKind(p.kinds),
              coverage: p.coverage,
              example: p.example,
            });
          });
      } else if (jsonSchemas?.[col?.field]?.keys?.length) {
        const schema = jsonSchemas[col?.field];
        schema.keys.forEach((path) => {
          baseOptions.push({
            label: `${col.headerName}.${path}`,
            value: `${col.field}.${path}`,
            isJsonPath: true,
          });
        });
      }

      // Add indexed options for images-type columns (json-schema sends
      // max_images_count)
      const imagesSchema = jsonSchemas?.[col?.field];
      if (dataType === "images" && imagesSchema?.max_images_count) {
        for (let idx = 0; idx < imagesSchema.max_images_count; idx++) {
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
  const mappedValue = useWatch({
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
          alignItems: "center",
          justifyContent: "space-between",
          width: rest?.module === "task" ? "700px" : "100%",
        }}
      >
        <Box display={"flex"} alignItems={"center"} gap={1.5}>
          {check && (
            <Checkbox
              checked={isChecked}
              onChange={handleCheckbox}
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
            {/* The source column is a name, not an input: plain text on
                the row's line, with a lock when it can't be unticked. */}
            <Box
              sx={{
                width: theme.spacing(21),
                height: 32,
                display: "flex",
                alignItems: "center",
                gap: 0.75,
                minWidth: 0,
                color: check && !isChecked ? "text.disabled" : "text.primary",
              }}
            >
              <Typography
                variant="body2"
                noWrap
                sx={{ fontWeight: 500, color: "inherit", minWidth: 0 }}
              >
                {field}
              </Typography>
              {!check && (
                <Iconify
                  icon="solar:lock-linear"
                  width={14}
                  sx={{ color: "text.disabled", flexShrink: 0 }}
                />
              )}
            </Box>
          </CustomTooltip>
          <ShowComponent condition={!rest?.hideFieldColumns}>
            <Iconify
              icon="solar:arrow-right-linear"
              width={16}
              sx={{ color: "text.disabled", flexShrink: 0 }}
            />
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
                placeholder="Column name"
                sx={{
                  "& .MuiOutlinedInput-notchedOutline": {
                    borderColor: error ? "error.main" : "divider",
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
                          {option.kind ? (
                            <Iconify
                              icon={kindIcon(option.kind)}
                              width={14}
                              sx={{ color: "text.secondary", flexShrink: 0 }}
                            />
                          ) : null}
                          <Box sx={{ minWidth: 0, flex: 1 }}>
                            <Typography
                              typography="s1"
                              fontWeight={"fontWeightRegular"}
                              color={"text.primary"}
                              noWrap
                              sx={
                                option.kind
                                  ? { fontFamily: "monospace", fontSize: 13 }
                                  : undefined
                              }
                            >
                              {option.label}
                            </Typography>
                            {option.coverage && option.example != null ? (
                              <Typography
                                typography="s3"
                                color="text.secondary"
                                noWrap
                              >
                                {`e.g. ${exampleText(option.example)}`}
                              </Typography>
                            ) : null}
                          </Box>
                          {option.coverage ? (
                            <Typography
                              typography="s3"
                              sx={{
                                flexShrink: 0,
                                color:
                                  option.coverage.present ===
                                  option.coverage.total
                                    ? "text.disabled"
                                    : "amber.700",
                              }}
                            >
                              {coveragePercent(option.coverage)}
                            </Typography>
                          ) : null}
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
            <PathPreviewStrip
              value={mappedValue}
              jsonSchemas={jsonSchemas}
              columns={allColumns}
            />
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
