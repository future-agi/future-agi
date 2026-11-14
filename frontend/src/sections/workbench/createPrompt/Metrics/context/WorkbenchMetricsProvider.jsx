import React, { useEffect, useRef, useState } from "react";
import { WorkbenchMetricsContext } from "./WorkbenchMetricsContext";
import PropTypes from "prop-types";
import { useUrlState } from "src/routes/hooks/use-url-state";
import { getDefaultFilter } from "../common";

const WorkbenchMetricsProvider = ({ children }) => {
  const [activeTab, setActiveTab] = useUrlState("activeTab", "Metrics");
  const [searchQuery, setSearchQuery] = useState("");
  const [isFilterDrawerOpen, setIsFilterDrawerOpen] = useState(false);
  const [filters, setFilters] = useUrlState(
    "metricsFilters",
    getDefaultFilter(),
  );
  const [columns, setColumns] = useState([]);

  // Filters belong to one tab's columns; switching tabs starts clean.
  const prevTabRef = useRef(activeTab);
  useEffect(() => {
    if (prevTabRef.current !== activeTab) {
      setFilters(getDefaultFilter());
      prevTabRef.current = activeTab;
    }
  }, [activeTab, setFilters]);

  const value = {
    activeTab,
    setActiveTab,
    searchQuery,
    setSearchQuery,
    isFilterDrawerOpen,
    setIsFilterDrawerOpen,
    filters,
    setFilters,
    columns,
    setColumns,
  };

  return (
    <WorkbenchMetricsContext.Provider value={value}>
      {children}
    </WorkbenchMetricsContext.Provider>
  );
};

WorkbenchMetricsProvider.propTypes = {
  children: PropTypes.node,
};

export default WorkbenchMetricsProvider;
