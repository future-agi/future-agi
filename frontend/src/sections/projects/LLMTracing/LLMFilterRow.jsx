import { Box, IconButton, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React, { useRef } from "react";
import ValueSelector from "src/components/ComplexFilter/ValueSelectors/ValueSelector";
import {
  FILTER_INPUT_TYPES,
  FilterColTypes,
  FilterDefaultOperators,
  FilterDefaultValues,
} from "src/utils/constants";
import { ShowComponent } from "src/components/show";
import SvgColor from "src/components/svg-color";
import FilterRowMenu from "src/components/ComplexFilter/FilterRowMenu";
import DropdownWithSearch from "src/sections/common/DropdownWithSearch";
import { FormSearchSelectFieldState } from "src/components/FromSearchSelectField";
import DevelopBooleanFilter from "src/sections/develop-detail/DataTab/DevelopFilters/DevelopBooleanFilter";
import { getFilterType } from "src/components/ComplexFilter/common";
import DevelopTextFilter from "src/sections/develop-detail/DataTab/DevelopFilters/DevelopTextFilter.jsx";

const LLMFilterRow = ({
  removeFilter,
  filter,
  updateFilter,
  filterDefinition,
  defaultFilter,
  propertyIdCount,
  totalFilters,
}) => {
  const filterRef = useRef(null);
  const dropdownRefsMap = useRef({});
  const getDropdownRef = (key) => {
    if (!dropdownRefsMap.current[key]) {
      dropdownRefsMap.current[key] = React.createRef();
    }
    return dropdownRefsMap.current[key];
  };

  const getParentProperty = () => {
    if (filter?._meta?.parentProperty) {
      return filter._meta.parentProperty;
    }
    if (filter?.column_id) {
      // Check if column_id matches a top-level definition
      const matchingDef = filterDefinition.find(
        (def) =>
          def.propertyId === filter.column_id ||
          def.propertyName === filter.column_id,
      );
      if (matchingDef) {
        return matchingDef.propertyName || matchingDef.propertyId;
      }

      for (const def of filterDefinition) {
        if (def.dependents) {
          const nestedMatch = findInDependents(
            def.dependents,
            filter.column_id,
          );
          if (nestedMatch) {
            return def.propertyName || def.propertyId;
          }
        }
      }
    }

    return "";
  };

  const findInDependents = (dependents, column_id) => {
    for (const dep of dependents) {
      if (dep.propertyId === column_id || dep.propertyName === column_id) {
        return dep;
      }
      if (dep.dependents) {
        const nested = findInDependents(dep.dependents, column_id);
        if (nested) return nested;
      }
    }
    return null;
  };

  const parentProperty = getParentProperty();

  // Helper function to render the appropriate value selector based on filter type
  const renderValueSelector = (ogDefinition) => {
    const filterType =
      ogDefinition?.filterType?.type || filter?.filter_config?.filter_type;

    switch (filterType) {
      case "boolean":
        return (
          <DevelopBooleanFilter filter={filter} updateFilter={updateFilter} />
        );
      case "text":
        return (
          <DevelopTextFilter filter={filter} updateFilter={updateFilter} />
        );
      default:
        return (
          <ValueSelector
            definition={ogDefinition}
            filter={filter}
            updateFilter={updateFilter}
          />
        );
    }
  };

  const renderFilterElements = () => {
    const elements = [];

    // Helper function to collect all elements in order
    const collectElements = (path) => {
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
        (item) =>
          item.propertyName === property || item.propertyId === property,
      );

      if (!ogDefinition) return;

      if (!ogDefinition?.dependents?.length) {
        // Skip value selector for nodes with a pre-set defaultFilterValue
        if (!ogDefinition?.defaultFilterValue) {
          elements.push({
            type: "value",
            key: `value-${path.join("-")}`,
            component: renderValueSelector(ogDefinition),
          });
        }
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

        // Add connector text
        if (ogDefinition?.stringConnector) {
          elements.push({
            type: "connector",
            key: `connector-${path.join("-")}`,
            component: (
              <Typography
                component="span"
                variant="s1"
                fontWeight={"fontWeightRegular"}
                color="text.primary"
                sx={{ whiteSpace: "nowrap", flexShrink: 0 }}
              >
                {ogDefinition.stringConnector}
              </Typography>
            ),
          });
        }

        // Each depth level gets its own ref so popovers don't conflict
        const dropdownRef = getDropdownRef(`dropdown-${path.join("-")}`);

        // Add dropdown
        elements.push({
          type: "dropdown",
          key: `dropdown-${path.join("-")}`,
          component: (
            <DropdownWithSearch
              label={property}
              size="small"
              options={dependents.map((item) => ({
                label: item.propertyName,
                value: item?.propertyId || item?.propertyName,
              }))}
              value={filter?._meta?.[property] || ""}
              sx={{
                minWidth: "180px",
                maxWidth: "220px",
                width: "auto",
                flexShrink: 0,
              }}
              useCustomStyle={false}
              onSelect={(e) => {
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
                  ref={dropdownRef}
                />
              )}
              anchorRef={dropdownRef}
              ref={dropdownRef}
            />
          ),
        });

        // Add type selector if allowed
        if (currentDependent?.allowTypeChange) {
          elements.push({
            type: "type-connector",
            key: `type-connector-${path.join("-")}`,
            component: (
              <Typography
                component="span"
                sx={{
                  whiteSpace: "nowrap",
                  flexShrink: 0,
                }}
                variant="s1"
                color={"text.primary"}
                fontWeight={"fontWeightRegular"}
              >
                is of
              </Typography>
            ),
          });

          elements.push({
            type: "type-selector",
            key: `type-selector-${path.join("-")}`,
            component: (
              <FormSearchSelectFieldState
                showClear={false}
                size="small"
                label={"Type"}
                options={FILTER_INPUT_TYPES}
                value={currentDependent?.filterType?.type}
                sx={{
                  minWidth: 100,
                  maxWidth: 130,
                  width: "auto",
                  flexShrink: 0,
                }}
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
            ),
          });
        }

        // Recursively collect next level if there's a selection
        if (filter?._meta?.[property]) {
          collectElements([...path, filter?._meta?.[property]]);
        }
      }
    };

    // Start collection from parent property if it exists
    if (parentProperty) {
      collectElements([parentProperty]);
    }

    return elements;
  };

  const filterElements = renderFilterElements();
  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "row",
        flexWrap: "wrap",
        alignItems: "center",
        gap: 2,
        width: "100%",
        p: 1.5,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
      }}
    >
      {/* Property Dropdown - First element */}
      <DropdownWithSearch
        key={`property-${filter.id}-${parentProperty}`} // Force re-render when value changes
        label="Property"
        size="small"
        options={filterDefinition.map((item) => ({
          label: item.propertyName,
          value: item?.propertyId || item?.propertyName,
        }))}
        value={parentProperty}
        sx={{
          minWidth: "220px",
          maxWidth: "280px",
          width: "auto",
          flexShrink: 0,
        }}
        useCustomStyle={false}
        onSelect={(e) => {
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
                filter_value: FilterDefaultValues[ogDefinition.filterType.type],
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
                  item.propertyName === e.value || item.propertyId === e.value,
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

      {/* All dependent filter elements in sequence */}
      {filterElements.slice(0, -1).map((element) => (
        <React.Fragment key={element.key}>{element.component}</React.Fragment>
      ))}

      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          minWidth: 120,
          gap: 2,
          flexShrink: 0,
        }}
      >
        <React.Fragment>
          {filterElements[filterElements?.length - 1]?.component}
        </React.Fragment>
        {/* Delete Button - Always at the end */}
        <ShowComponent
          condition={filterElements.length > 0 || totalFilters > 1}
        >
          <IconButton
            size="small"
            onClick={() => removeFilter(filter.id)}
            sx={{
              flexShrink: 0,
            }}
          >
            <SvgColor
              src="/assets/icons/ic_delete.svg"
              sx={{
                width: "20px",
                height: "20px",
              }}
            />
          </IconButton>
        </ShowComponent>
      </Box>
    </Box>
  );
};

LLMFilterRow.propTypes = {
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
  updateFilterDefinition: PropTypes.func.isRequired,
  totalFilters: PropTypes.number,
};

export default LLMFilterRow;
