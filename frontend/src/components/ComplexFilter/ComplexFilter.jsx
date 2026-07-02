import { Box } from "@mui/material";
import PropTypes from "prop-types";
import React, { useCallback, useMemo } from "react";
import { getRandomId } from "src/utils/utils";
import FilterRow from "./FilterRow";
import logger from "../../utils/logger";

/**
 * Complex filter component for handling multiple filter rows
 * @param filters - Array of filter objects representing current filters
 * @param defaultFilter - Default filter object used when adding new filters
 * @param setFilters - State setter function to update filters array
 * @param filterDefinition - Object containing filter configuration and options
 * filter definition is described like this:
 * [
 *  {
 *    "propertyName": "Name of property 1",
 *    "propertyId" : "" // parent property ID for which the filter value in eventually set
 *    "filter_type" : "filter_type" // filter type (options, number, text)
 *    "dependents" : [
 *      {
 *        "stringConnector" : "is",
 *        "propertyName": "Dependent property name 1",
 *        "propertyId" : "" // dependent property ID (on priority) for which the filter value in eventually set
 *        "filter_type" : "filter_type" // filter type (options, number),
 *        "options" : [
 *          {
 *            "value": "value 1",
 *            "label": "label 1"
 *          }
 *        ],
 *        "overrideOperators" : [
 *          {
 *            "value": "value 1",
 *            "label": "label 1"
 *          }
 *        ]
 *
 *      },
 *      {
 *        "stringConnector" : "is",
 *        "propertyName": "Dependent property name 2",
 *        "propertyId" : "" // dependent property ID (on priority) for which the filter value in eventually set
 *      }
 *    ]
 *  },
 *  {
 *    "propertyName": "Name of property 2",
 *  },
 * ]
 */
const ComplexFilter = ({
  filters,
  defaultFilter,
  setFilters,
  filterDefinition,
  onClose,
}) => {
  const addFilter = useCallback(() => {
    setFilters(() => [...filters, { ...defaultFilter, id: getRandomId() }]);
  }, [defaultFilter, filters, setFilters]);
  const propertyIdCount = useMemo(() => {
    logger.debug({ filters });
    return filters.reduce((acc, curr) => {
      if (curr.column_id) {
        acc[curr.column_id] = (acc[curr.column_id] || 0) + 1;
      }
      return acc;
    }, {});
  }, [filters]);

  const removeFilter = useCallback(
    (id) => {
      if (filters.length === 1) {
        onClose();
      }
      setFilters((internalFilters) => {
        if (internalFilters.length === 1) {
          return [{ ...defaultFilter, id: getRandomId() }];
        }
        return internalFilters.filter((filter) => filter.id !== id);
      });
    },
    [defaultFilter, setFilters, filters, onClose],
  );

  const updateFilter = useCallback(
    (id, newFilter) => {
      setFilters((internalFilters) =>
        internalFilters.map((filter) => {
          if (filter.id === id) {
            return typeof newFilter === "function"
              ? { ...newFilter(filter), id }
              : { ...newFilter, id };
          } else {
            return filter;
          }
        }),
      );
    },
    [setFilters],
  );

  // Memoize the filter rendering to prevent unnecessary re-renders
  const filterRows = useMemo(() => {
    return filters.map((filter, index) => {
      const adjustedFilterDefinition = filterDefinition.filter((def) => {
        if (
          def.maxUsage &&
          propertyIdCount[def.propertyId] >= def.maxUsage &&
          filter.column_id !== def.propertyId
        ) {
          return false;
        }
        return true;
      });

      return (
        <FilterRow
          key={filter.id}
          index={index}
          removeFilter={removeFilter}
          addFilter={addFilter}
          updateFilter={updateFilter}
          filter={filter}
          filterDefinition={adjustedFilterDefinition}
          defaultFilter={defaultFilter}
          propertyIdCount={propertyIdCount}
        />
      );
    });
  }, [
    filters,
    filterDefinition,
    propertyIdCount,
    defaultFilter,
    addFilter,
    updateFilter,
    removeFilter,
  ]);

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: (theme) => theme.spacing(0.5),
      }}
    >
      {/* <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <Typography variant='s1' fontWeight={500} fontSize="16px" color="text.disabled">
          Filter
        </Typography>
      </Box> */}
      <Box
        sx={{
          border: "1px solid",
          borderRadius: "8px",
          borderColor: "divider",
          padding: 2,
          gap: "18px",
          display: "flex",
          flexDirection: "column",
        }}
      >
        {filterRows}
      </Box>
    </Box>
  );
};

ComplexFilter.propTypes = {
  filters: PropTypes.array,
  defaultFilter: PropTypes.object,
  setFilters: PropTypes.func,
  filterDefinition: PropTypes.array,
  onClose: PropTypes.func,
};

export default ComplexFilter;
