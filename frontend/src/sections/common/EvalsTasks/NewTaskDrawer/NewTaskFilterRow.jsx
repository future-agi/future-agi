import { Box, IconButton, Stack, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React, { useCallback, useMemo } from "react";
import { useWatch } from "react-hook-form";
import Iconify from "src/components/iconify";
import { ShowComponent } from "src/components/show";
import { AllowedEvalSpanTypes } from "src/utils/constant";
import FilterDependents from "./FilterDependents";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import { debounce } from "lodash";
import CustomTooltip from "src/components/tooltip";

const FilterTypes = [
  { label: "Span Type", value: "observation_type" },
  { label: "Attributes", value: "attributes" },
];

const NewTaskFilterRow = ({
  removeFilter,
  index,
  control,
  attributes,
  update,
  getValues,
  compact = true,
}) => {
  const filterData =
    useWatch({ control, name: "filters", defaultValue: [] })?.[index] ?? {};
  const { property, propertyId } = filterData;

  const currentFilter = useMemo(() => {
    return getValues(`filters.${index}`) ?? {};
  }, [getValues, index]);

  const allowedProperties = FilterTypes.map((filter) => ({
    ...filter,
    disabled: false,
  }));

  const debouncedUpdateFilter = useMemo(
    () =>
      debounce((newFilter) => {
        update(index, {
          ...currentFilter,
          ...newFilter,
        });
      }, 300),
    [index, currentFilter, update],
  );

  const updateFilter = useCallback(
    (newFilter) => {
      debouncedUpdateFilter(newFilter);
    },
    [debouncedUpdateFilter],
  );

  return (
    <Box
      sx={{
        display: "flex",
        justifyContent: "space-between",
        flexDirection: "column",
      }}
    >
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 1.5,
          flexDirection: compact
            ? property === "attributes" && propertyId
              ? "column"
              : "row"
            : "row",
        }}
      >
        <Stack
          direction={"row"}
          sx={{
            width: property && compact ? "100%" : "auto",
          }}
          alignItems="center"
          gap={1.5}
        >
          <FormSearchSelectFieldControl
            fieldName={`filters.${index}.property`}
            options={allowedProperties}
            control={control}
            size="small"
            label="Property"
            showClear={false}
            fullWidth={!compact ? false : Boolean(property !== "")}
          />
          <ShowComponent condition={property === "observation_type"}>
            <Typography
              variant="s2"
              fontWeight={"fontWeightRegular"}
              color="text.primary"
            >
              is
            </Typography>
            <FormSearchSelectFieldControl
              fieldName={`filters.${index}.filterConfig.filterValue`}
              options={AllowedEvalSpanTypes}
              control={control}
              size="small"
              label="Observation Type"
              fullWidth={compact ? true : false}
              showClear={false}
            />
          </ShowComponent>
          <ShowComponent condition={property === "attributes"}>
            <Typography
              variant="s2"
              fontWeight={"fontWeightRegular"}
              color="text.primary"
            >
              is
            </Typography>
            {/* <Box sx={{ flex: 1 }}> */}
            <FormSearchSelectFieldControl
              fieldName={`filters.${index}.propertyId`}
              options={(attributes || []).map((option) => {
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
              control={control}
              size="small"
              label="Attributes"
              fullWidth={compact}
              showClear={false}
              placeholder="Select Attribute"
            />
            {/* </Box> */}
            {propertyId && (
              <Typography
                variant="s2"
                fontWeight={"fontWeightRegular"}
                color="text.primary"
                sx={{ whiteSpace: "nowrap", pl: 1 }}
              >
                is of
              </Typography>
            )}
          </ShowComponent>
        </Stack>

        <Stack
          direction={"row"}
          sx={{
            width: property && propertyId ? "100%" : "auto",
            // ml: !(propertyId) ? 'auto' : 0,
          }}
          alignItems="center"
          gap={1.5}
        >
          <ShowComponent condition={property === "attributes" && propertyId}>
            <FilterDependents
              filter={currentFilter}
              index={index}
              update={updateFilter}
              fieldPrefix={`filters.${index}.filterConfig`}
              control={control}
            />
          </ShowComponent>
          <IconButton
            size="small"
            onClick={() => removeFilter(index)}
            sx={{ color: "text.disabled" }}
          >
            <Iconify
              icon="hugeicons:delete-01"
              sx={{ color: "text.disabled" }}
            />
          </IconButton>
        </Stack>
      </Box>
    </Box>
  );
};

NewTaskFilterRow.propTypes = {
  index: PropTypes.number,
  removeFilter: PropTypes.func,
  control: PropTypes.object,
  attributes: PropTypes.array,
  update: PropTypes.func,
  getValues: PropTypes.func,
  compact: PropTypes,
};

export default NewTaskFilterRow;
