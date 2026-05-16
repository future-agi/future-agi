import React, { useMemo, useState } from "react";
import { ChartsViewContext } from "./ChartsViewContext";
import PropTypes from "prop-types";
import { endOfToday, sub } from "date-fns";
import {
  isDateRangeLessThan90Days,
  isDateRangeMoreThan7Days,
} from "src/utils/dateTimeUtils";
import { convertToISO } from "./common";

const ChartsViewProvider = ({ children }) => {
  const [selectedInterval, setSelectedInterval] = useState("Day");
  const [zoomRange, setZoomRange] = useState([null, null]);
  const [parentDateFilter, setParentDateFilter] = useState(() => {
    const defaultDates = [sub(new Date(), { days: 30 }), endOfToday()];
    return convertToISO(defaultDates);
  });
  const isMoreThan7Days = isDateRangeMoreThan7Days(parentDateFilter);
  const isLessThan90Days = isDateRangeLessThan90Days(parentDateFilter);

  const filters = useMemo(
    () => [
      {
        column_id: "created_at",
        filter_config: {
          filter_type: "datetime",
          filter_op: "between",
          filter_value: convertToISO(parentDateFilter),
        },
      },
    ],
    [parentDateFilter],
  );

  const handleZoomChange = (dates) => {
    setZoomRange(dates);
    setParentDateFilter(dates);
  };

  const value = {
    selectedInterval,
    setSelectedInterval,
    parentDateFilter,
    isMoreThan7Days,
    setParentDateFilter,
    filters,
    zoomRange,
    setZoomRange,
    handleZoomChange,
    isLessThan90Days,
  };

  return (
    <ChartsViewContext.Provider value={value}>
      {children}
    </ChartsViewContext.Provider>
  );
};

ChartsViewProvider.propTypes = {
  children: PropTypes.any,
};

export default ChartsViewProvider;
