import { Box, Button, IconButton, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React, { useRef } from "react";
import ValueSelector from "./ValueSelectors/ValueSelector";
import {
  FILTER_INPUT_TYPES,
  FilterColTypes,
  FilterDefaultOperators,
  FilterDefaultValues,
} from "src/utils/constants";
import Iconify from "../iconify";
import { ShowComponent } from "../show";
import SvgColor from "../svg-color";
import FilterRowMenu from "./FilterRowMenu";
import { FormSearchSelectFieldState } from "../FromSearchSelectField";
import { getFilterType } from "./common";

const FilterRow = ({
  index,
  removeFilter,
  addFilter,
  filter,
  updateFilter,
  filterDefinition,
  defaultFilter,
  propertyIdCount,
}) => {
  const parentProperty = filter?._meta?.parentProperty || "";
  const filterRef = useRef(null);
  const childRef = useRef(null);

  const renderDependent = (path) => {
    const property = path[path.length - 1];
    let findArray = filterDefinition;
    const parentPath = path.slice(0, -1);

    for (const parentProperty of parentPath) {
      const foundObject = findArray.find(
        (item) =>
          item.propertyName === parentProperty ||
          item.propertyId === parentProperty,
      );
      if (foundObject) {
        findArray = foundObject.dependents;
      }
    }

    const ogDefinition = findArray.find(
      (item) => item.propertyName === property || item.propertyId === property,
    );
    if (!ogDefinition) return <></>;
    if (!ogDefinition?.dependents?.length) {
      // we will render value selector
      if (ogDefinition?.hideValueSelector) return <></>;
      return (
        <ValueSelector
          definition={ogDefinition}
          filter={filter}
          updateFilter={updateFilter}
        />
      );
    } else {
      const dependents = ogDefinition?.dependents?.filter((def) => {
        if (
          def.maxUsage &&
          propertyIdCount[def.propertyId] >= def.maxUsage &&
          filter.column_id !== def.propertyId
        ) {
          return false;
        }
        return true;
      });

      const currentDependent = dependents.find(
        (d) => d?.propertyId === filter?._meta?.[property],
      );

      return (
        <>
          <Typography
            variant="s1"
            fontWeight={"fontWeightRegular"}
            color="text.primary"
          >
            {ogDefinition?.stringConnector}
          </Typography>
          <FormSearchSelectFieldState
            label={property}
            size="small"
            options={dependents.map((item) => ({
              label: item.propertyName,
              value: item?.propertyId || item?.propertyName,
            }))}
            value={filter?._meta?.[property] || ""}
            sx={{ maxWidth: "280px", width: "280px" }}
            onChange={(e) => {
              const dependentOgDefinition = dependents.find(
                (item) =>
                  item.propertyId === e.target.value ||
                  item.propertyName === e.target.value,
              );

              if (dependentOgDefinition?.propertyId) {
                updateFilter(filter.id, (existingFilter) => ({
                  column_id: dependentOgDefinition.propertyId,
                  filter_config: {
                    filter_type: getFilterType(dependentOgDefinition),
                    filter_op:
                      dependentOgDefinition.defaultFilter ??
                      FilterDefaultOperators[
                        dependentOgDefinition.filterType.type
                      ],
                    filter_value:
                      dependentOgDefinition?.defaultFilterValue ??
                      FilterDefaultValues[
                        dependentOgDefinition.filterType.type
                      ],
                    col_type: FilterColTypes[ogDefinition?.propertyName],
                  },
                  _meta: {
                    ...existingFilter._meta,
                    [property]: e.target.value,
                  },
                }));
              } else {
                updateFilter(filter.id, (existingFilter) => ({
                  ...defaultFilter,
                  filter_config: {
                    ...defaultFilter?.filter_config,
                    col_type: FilterColTypes[ogDefinition?.propertyName],
                  },
                  _meta: {
                    ...existingFilter._meta,
                  },
                }));
              }
            }}
            popoverComponent={(props) => (
              <FilterRowMenu
                {...props}
                data={dependents.map((item) => ({
                  label: item.propertyName,
                  value: item?.propertyId || item?.propertyName,
                }))}
                onSelect={(e) => {
                  const dependentOgDefinition = dependents.find(
                    (item) =>
                      item.propertyId === e.value ||
                      item.propertyName === e.label,
                  );

                  if (dependentOgDefinition?.propertyId) {
                    updateFilter(filter.id, (existingFilter) => ({
                      column_id: dependentOgDefinition.propertyId,
                      filter_config: {
                        filter_type: getFilterType(dependentOgDefinition),
                        filter_op:
                          dependentOgDefinition.defaultFilter ??
                          FilterDefaultOperators[
                            dependentOgDefinition.filterType.type
                          ],
                        filter_value:
                          dependentOgDefinition?.defaultFilterValue ??
                          FilterDefaultValues[
                            dependentOgDefinition.filterType.type
                          ],
                        col_type: FilterColTypes[ogDefinition?.propertyName],
                      },
                      _meta: {
                        ...existingFilter._meta,
                        [property]: e.value,
                      },
                    }));
                  } else {
                    updateFilter(filter.id, (existingFilter) => ({
                      ...defaultFilter,
                      filter_config: {
                        ...defaultFilter?.filter_config,
                        col_type: FilterColTypes[ogDefinition?.propertyName],
                      },
                      _meta: {
                        ...existingFilter._meta,
                      },
                    }));
                  }
                }}
                ref={childRef}
              />
            )}
            anchorRef={childRef}
            ref={childRef}
          />

          {currentDependent?.allowTypeChange && (
            <>
              <Typography
                sx={{
                  whiteSpace: "nowrap",
                }}
                variant="s1"
                color={"text.primary"}
                fontWeight={"fontWeightRegular"}
              >
                is of
              </Typography>
              <FormSearchSelectFieldState
                showClear={false}
                size="small"
                label={"Type"}
                options={FILTER_INPUT_TYPES}
                value={currentDependent?.filterType?.type}
                onChange={(e) => {
                  if (!currentDependent) return;

                  const filterType = {
                    ...currentDependent?.filterType,
                    type: e?.target?.value,
                  };

                  if (e.target.value === "boolean") {
                    filterType["truthLabel"] = "True";
                    filterType["falseLabel"] = "False";
                  }
                  const currentfilterDef = ogDefinition;
                  const filterDefCopy = [...filterDefinition];

                  currentDependent["filterType"] = filterType;

                  const findDependentIndex =
                    currentfilterDef?.dependents?.findIndex(
                      (ogdef) =>
                        ogdef?.propertyId === currentDependent?.propertyId,
                    );

                  if (findDependentIndex === -1) return;
                  currentfilterDef["dependents"][findDependentIndex] =
                    currentDependent;

                  const findFilterDefIndex = filterDefCopy?.findIndex(
                    (fd) => fd?.propertyName === currentfilterDef?.propertyName,
                  );
                  if (findFilterDefIndex === -1) return;
                  filterDefCopy[findFilterDefIndex] = currentfilterDef;
                  // updateFilterDefinition(filterDefCopy);

                  // update filter with new filter type
                  updateFilter(filter.id, (existingFilter) => ({
                    ...existingFilter,
                    filter_config: {
                      ...existingFilter.filter_config,
                      filter_type: e?.target?.value,
                      filter_value: FilterDefaultValues[e?.target?.value],
                      filter_op: FilterDefaultOperators[e?.target?.value],
                    },
                  }));
                }}
              />
            </>
          )}

          {filter?._meta?.[property] ? (
            renderDependent([...path, filter?._meta?.[property]])
          ) : (
            <></>
          )}
        </>
      );
    }
  };

  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
      }}
    >
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: (theme) => theme.spacing(3),
          flex: 1,
        }}
      >
        <FormSearchSelectFieldState
          label="Property"
          size="small"
          options={filterDefinition.map((item) => ({
            label: item.propertyName,
            value: item?.propertyId || item?.propertyName,
          }))}
          value={parentProperty}
          sx={{ maxWidth: "250px", width: "250px" }}
          onChange={(e) => {
            const ogDefinition = filterDefinition.find(
              (item) =>
                item.propertyName === e.target.value ||
                item.propertyId === e.target.value,
            );
            if (ogDefinition?.propertyId) {
              updateFilter(filter.id, {
                column_id: ogDefinition.propertyId,
                filter_config: {
                  filter_type: getFilterType(ogDefinition),
                  filter_op:
                    ogDefinition.defaultFilter ??
                    FilterDefaultOperators[ogDefinition.filterType.type],
                  filter_value:
                    ogDefinition?.defaultFilterValue ??
                    FilterDefaultValues[ogDefinition.filterType.type],
                  col_type: FilterColTypes[ogDefinition?.propertyName],
                },
                _meta: {
                  parentProperty: e.target.value,
                },
              });
            } else {
              updateFilter(filter.id, {
                ...defaultFilter,
                filter_config: {
                  ...defaultFilter?.filter_config,
                  col_type: FilterColTypes[ogDefinition?.propertyName],
                },
                _meta: {
                  parentProperty: e.target.value,
                },
              });
            }
          }}
          popoverComponent={(props) => (
            <FilterRowMenu
              {...props}
              data={filterDefinition.map((item) => ({
                label: item.propertyName,
                value: item?.propertyId || item?.propertyName,
              }))}
              onSelect={(e) => {
                const ogDefinition = filterDefinition.find(
                  (item) =>
                    item.propertyName === e.value ||
                    item.propertyId === e.value,
                );

                if (ogDefinition?.propertyId) {
                  updateFilter(filter.id, {
                    column_id: ogDefinition.propertyId,
                    filter_config: {
                      filter_type: getFilterType(ogDefinition),
                      filter_op:
                        ogDefinition.defaultFilter ??
                        FilterDefaultOperators[ogDefinition.filterType.type],
                      filter_value:
                        FilterDefaultValues[ogDefinition.filterType.type],
                      col_type: FilterColTypes[ogDefinition?.propertyName],
                    },
                    _meta: {
                      parentProperty: e.value,
                    },
                  });
                } else {
                  updateFilter(filter.id, {
                    ...defaultFilter,
                    filter_config: {
                      ...defaultFilter?.filter_config,
                      col_type: FilterColTypes[ogDefinition?.propertyName],
                    },
                    _meta: {
                      parentProperty: e.value,
                    },
                  });
                }
              }}
              ref={filterRef}
            />
          )}
          anchorRef={filterRef}
          ref={filterRef}
        />

        {parentProperty ? renderDependent([parentProperty]) : <></>}
        <IconButton
          size="small"
          onClick={() => removeFilter(filter.id)}
          sx={{ color: "text.disabled", mr: 3 }}
        >
          <SvgColor
            src="/assets/icons/ic_delete.svg"
            sx={{
              width: "20px",
              height: "20px",
            }}
          />
        </IconButton>
      </Box>
      <Box>
        <ShowComponent condition={index === 0}>
          <Button
            onClick={addFilter}
            variant="outlined"
            color="primary"
            startIcon={
              <Iconify icon="ic:round-plus" color="primary" width="20px" />
            }
            sx={{
              // py: 2.3,
              // px: 2.7,
              // fontSize: "14px",
              // fontWeight: 600,
              "& .MuiButton-startIcon": {
                margin: 0,
                paddingRight: 1,
              },
              whiteSpace: "nowrap",
            }}
          >
            Add Filter
          </Button>
        </ShowComponent>
      </Box>
    </Box>
  );
};

FilterRow.propTypes = {
  index: PropTypes.number.isRequired,
  removeFilter: PropTypes.func.isRequired,
  addFilter: PropTypes.func.isRequired,
  filter: PropTypes.shape({
    id: PropTypes.string.isRequired,
    column_id: PropTypes.string,
    filter_config: PropTypes.shape({
      filter_type: PropTypes.string,
      filter_op: PropTypes.string,
      filter_value: PropTypes.oneOfType([
        PropTypes.string,
        PropTypes.array,
        PropTypes.bool,
      ]),
    }),
    _meta: PropTypes.shape({
      parentProperty: PropTypes.string,
    }),
  }).isRequired,
  updateFilter: PropTypes.func.isRequired,
  filterDefinition: PropTypes.arrayOf(
    PropTypes.shape({
      propertyName: PropTypes.string.isRequired,
      propertyId: PropTypes.string,
      filterType: PropTypes.shape({
        type: PropTypes.string.isRequired,
        options: PropTypes.array,
      }),
      maxUsage: PropTypes.number,
      multiSelect: PropTypes.bool,
      stringConnector: PropTypes.string,
      dependents: PropTypes.array,
    }),
  ).isRequired,
  defaultFilter: PropTypes.object.isRequired,
  propertyIdCount: PropTypes.object.isRequired,
};

export default FilterRow;
