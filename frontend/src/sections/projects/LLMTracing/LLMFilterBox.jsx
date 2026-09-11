import { Box, Button } from "@mui/material";
import PropTypes from "prop-types";
import React, { useMemo } from "react";
import { getRandomId } from "src/utils/utils";
import LLMFilterRow from "./LLMFilterRow";
import Iconify from "src/components/iconify";

const LLMFilterBox = ({
  filters,
  defaultFilter,
  setFilters,
  filterDefinition,
  setFilterDefinition,
  resetFiltersAndClose,
}) => {
  const addFilter = () => {
    setFilters([...filters, { ...defaultFilter, id: getRandomId() }]);
  };

  const propertyIdCount = useMemo(() => {
    return filters.reduce((acc, curr) => {
      if (curr.column_id) {
        acc[curr.column_id] = (acc[curr.column_id] || 0) + 1;
      }
      return acc;
    }, {});
  }, [filters]);

  const removeFilter = (id) => {
    if (filters.length === 1) {
      resetFiltersAndClose();
    }
    setFilters((internalFilters) => {
      if (internalFilters.length === 1) {
        return [{ ...defaultFilter, id: getRandomId() }];
      }
      return internalFilters.filter((filter) => filter.id !== id);
    });
  };

  const updateFilter = (id, newFilter) => {
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
  };

  const updateFilterDefinition = (newDefinition) => {
    setFilterDefinition(newDefinition);
  };

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
        <LLMFilterRow
          key={filter.id}
          index={index}
          removeFilter={removeFilter}
          addFilter={addFilter}
          updateFilter={updateFilter}
          filter={filter}
          filterDefinition={adjustedFilterDefinition}
          defaultFilter={defaultFilter}
          propertyIdCount={propertyIdCount}
          updateFilterDefinition={updateFilterDefinition}
          totalFilters={filters.length}
        />
      );
    });
  }, [filters, filterDefinition, propertyIdCount, defaultFilter]);

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: 1,
        width: "100%",
        minWidth: 0,
      }}
    >
      <Box
        sx={{
          paddingY: 1,
          gap: 2,
          display: "flex",
          flexDirection: "column",
          width: "100%",
          minWidth: 0,
          overflow: "visible", // Changed from hidden to visible
        }}
      >
        {filterRows}
      </Box>
      <Button
        size="small"
        onClick={addFilter}
        variant="outlined"
        startIcon={<Iconify icon="ic:round-plus" />}
        sx={{
          "& .MuiButton-startIcon": {
            margin: 0,
            paddingRight: 1,
          },
          whiteSpace: "nowrap",
          flexShrink: 0,
          alignSelf: "flex-start",
        }}
      >
        Add filter
      </Button>
    </Box>
  );
};

LLMFilterBox.propTypes = {
  filters: PropTypes.array,
  defaultFilter: PropTypes.object,
  setFilters: PropTypes.func,
  filterDefinition: PropTypes.array,
  setFilterDefinition: PropTypes.func,
  resetFiltersAndClose: PropTypes.func,
};

export default LLMFilterBox;
