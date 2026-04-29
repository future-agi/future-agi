import { Box, useTheme } from "@mui/material";
import React from "react";
import SidebarOption from "./SidebarOption";
import DatasetCard from "./DatasetCard";
import FilterCard from "./FilterCard/FilterCard";
import PropTypes from "prop-types";
import { getRandomId } from "src/utils/utils";
import BreakdownCard from "./BreakdownCard";

const PerformanceSidebar = ({
  selectedDatasets,
  setSelectedDatasets,
  selectedFilters,
  setSelectedFilters,
  selectedBreakdown,
  setSelectedBreakdown,
}) => {
  const theme = useTheme();

  const setDataset = (idx, dataset) => {
    const newDatasets = [...selectedDatasets];
    let newDataset = dataset;
    if (typeof dataset === "function") {
      newDataset = dataset(newDatasets[idx]);
    }
    newDatasets[idx] = newDataset;
    setSelectedDatasets(newDatasets);
  };

  const cloneDataset = (idx) => {
    setSelectedDatasets([...selectedDatasets, selectedDatasets[idx]]);
  };

  const removeDataset = (idx) => {
    setSelectedDatasets(selectedDatasets.filter((_, i) => i !== idx));
  };

  const addDataset = () => {
    setSelectedDatasets([
      ...selectedDatasets,
      {
        id: getRandomId(),
        metricId: "",
        environment: "",
        version: "",
        filters: [],
      },
    ]);
  };

  const addFilter = () => {
    setSelectedFilters([
      ...selectedFilters,
      {
        id: getRandomId(),
        operator: "equal",
        values: [],
        type: "",
        datatype: "string",
        key: "",
        keyId: "",
      },
    ]);
  };

  const removeFilter = (idx) => {
    setSelectedFilters(selectedFilters.filter((_, i) => i !== idx));
  };

  const cloneFilter = (idx) => {
    setSelectedFilters([...selectedFilters, selectedFilters[idx]]);
  };

  const setFilter = (idx, update) => {
    const newFilters = [...selectedFilters];
    if (typeof update === "function") {
      newFilters[idx] = update(newFilters[idx]);
    } else {
      newFilters[idx] = update;
    }
    setSelectedFilters(newFilters);
  };

  const addBreakdown = () => {
    setSelectedBreakdown([
      ...selectedBreakdown,
      { id: getRandomId(), key: "", keyId: "" },
    ]);
  };

  const removeBreakdown = (idx) => {
    setSelectedBreakdown(selectedBreakdown.filter((_, i) => i !== idx));
  };

  const setBreakdown = (idx, update) => {
    const newBreakdowns = [...selectedBreakdown];
    if (typeof update === "function") {
      newBreakdowns[idx] = update(newBreakdowns[idx]);
    } else {
      newBreakdowns[idx] = update;
    }
    setSelectedBreakdown(newBreakdowns);
  };

  const cloneBreakdown = (idx) => {
    setSelectedBreakdown([...selectedBreakdown, selectedBreakdown[idx]]);
  };

  return (
    <Box
      sx={{
        backgroundColor: "action.hover",
        paddingX: "16px",
        overflow: "auto",
        borderLeft: `1px solid ${theme.palette.divider}`,
      }}
    >
      <SidebarOption title="Performance" onAddClick={addDataset}>
        {selectedDatasets.map((dataset, idx) => (
          <DatasetCard
            key={dataset.id}
            index={idx}
            setDataset={setDataset}
            dataset={dataset}
            cloneDataset={cloneDataset}
            removeDataset={removeDataset}
          />
        ))}
      </SidebarOption>
      <SidebarOption title="Filters" onAddClick={addFilter}>
        {selectedFilters.map((filter, idx) => (
          <FilterCard
            key={filter.id}
            index={idx}
            removeFilter={removeFilter}
            cloneFilter={cloneFilter}
            setFilter={(update) => setFilter(idx, update)}
            filter={filter}
          />
        ))}
      </SidebarOption>
      <SidebarOption title="Breakdown" onAddClick={addBreakdown}>
        {selectedBreakdown.map((breakdown, idx) => (
          <BreakdownCard
            key={breakdown.id}
            index={idx}
            setBreakdown={setBreakdown}
            breakdown={breakdown}
            removeBreakdown={removeBreakdown}
            cloneBreakdown={cloneBreakdown}
          />
        ))}
      </SidebarOption>
    </Box>
  );
};

PerformanceSidebar.propTypes = {
  selectedDatasets: PropTypes.array,
  setSelectedDatasets: PropTypes.func,
  selectedFilters: PropTypes.array,
  setSelectedFilters: PropTypes.func,
  selectedBreakdown: PropTypes.array,
  setSelectedBreakdown: PropTypes.func,
};

export default PerformanceSidebar;
