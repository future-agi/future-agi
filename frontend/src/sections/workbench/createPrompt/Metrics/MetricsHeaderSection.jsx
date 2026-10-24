import { Box, useTheme } from "@mui/material";
import React, { useMemo } from "react";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import { useWorkbenchMetrics } from "./context/WorkbenchMetricsContext";
import { FilterButton, useComplexFilterBinding } from "src/components/filter";
import { buildFilterDefinitions, defaultFilterBase } from "./common";

const MetricsHeaderSection = () => {
  const theme = useTheme();
  const {
    activeTab,
    searchQuery,
    setSearchQuery,
    isFilterDrawerOpen,
    setIsFilterDrawerOpen,
    columns,
    filters,
    setFilters,
  } = useWorkbenchMetrics();

  const searchBarStyle = useMemo(
    () => ({
      display: "flex",
      justifyContent: "space-between",
      alignItems: "center",
      marginBottom: theme.spacing(2),
    }),
    [theme],
  );
  const searchFieldStyle = useMemo(
    () => ({
      minWidth: "400px",
    }),
    [],
  );
  const handleSearchChange = (e) => {
    setSearchQuery(e.target.value);
  };

  const filterDefinition = useMemo(
    () => buildFilterDefinitions(columns || [], activeTab === "Metrics"),
    [columns, activeTab],
  );
  const filterBinding = useComplexFilterBinding({
    filters,
    setFilters,
    filterDefinition,
    defaultFilter: defaultFilterBase,
  });

  return (
    <Box sx={searchBarStyle}>
      <FormSearchField
        defaultValue=""
        size="small"
        placeholder="Search"
        sx={{
          ...searchFieldStyle,
          visibility: activeTab === "Metrics" ? "hidden" : "visible",
        }}
        value={searchQuery}
        onChange={handleSearchChange}
      />
      <FilterButton
        fields={filterBinding.fields}
        value={filterBinding.value}
        onChange={filterBinding.onChange}
        open={isFilterDrawerOpen}
        onOpenChange={setIsFilterDrawerOpen}
      />
    </Box>
  );
};

export default MetricsHeaderSection;
