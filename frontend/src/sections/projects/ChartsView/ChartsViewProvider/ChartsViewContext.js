import React from "react";

const chartsDefaultValue = {
  selectedInterval: "Day",
  setSelectedInterval: (_option) => {},
  parentDateFilter: null,
  setParentDateFilter: (_dates) => {},
  isMoreThan7Days: true,
  extraFilters: [],
  setExtraFilters: (_filters) => {},
  filters: [],
  zoomRange: [null, null],
  setZoomRange: (_dates) => {},
  handleZoomChange: (_dates) => {},
  isLessThan90Days: false,
};

export const ChartsViewContext = React.createContext({
  ...chartsDefaultValue,
});

export const useChartsViewContext = () => {
  return React.useContext(ChartsViewContext);
};
