import React from "react";
import { useState } from "react";
import PropTypes from "prop-types";

export const DatasetContext = React.createContext({
  datasetSelectMode: false,
  setDatasetSelectMode: (_opt) => {},
});

const DatasetContextProvider = ({ children }) => {
  const [datasetSelectMode, setDatasetSelectMode] = useState(false);

  return (
    <DatasetContext.Provider
      value={{ datasetSelectMode, setDatasetSelectMode }}
    >
      {children}
    </DatasetContext.Provider>
  );
};

DatasetContextProvider.propTypes = {
  children: PropTypes.any,
};

export { DatasetContextProvider };
