import React from "react";
import { useState, useRef, useCallback } from "react";
import { ExperimentDetailContext } from "./experiment-context";
import PropTypes from "prop-types";

const ExperimentDetailContextProvider = ({ children }) => {
  const [evaluateOpen, setEvaluateOpen] = useState(false);
  const [chooseWinnerOpen, setChooseWinnerOpen] = useState(false);
  const [diffMode, setDiffMode] = useState(false);
  const [fetchingData, setFetchingData] = useState(false);
  const [showAllColumns, setShowAllColumns] = useState(false);
  const [experimentDetailSearch, setExperimentDetailSearch] = useState("");
  const [viewAllPrompts, setViewAllPrompts] = useState(false);
  const [experimentDetailColumnSize, setExperimentDetailColumnSize] =
    useState("Short");
  const experimentGridRef = useRef(null);

  const setExperimentGridRef = useCallback((ref) => {
    experimentGridRef.current = ref;
  }, []);

  const handleToggleDiffMode = () => {
    setDiffMode((prev) => !prev);
  };

  return (
    <ExperimentDetailContext.Provider
      value={{
        evaluateOpen,
        chooseWinnerOpen,
        setEvaluateOpen,
        setChooseWinnerOpen,
        diffMode,
        handleToggleDiffMode,
        setFetchingData,
        fetchingData,
        experimentDetailSearch,
        setExperimentDetailSearch,
        experimentDetailColumnSize,
        setExperimentDetailColumnSize,
        experimentGridRef,
        setExperimentGridRef,
        showAllColumns,
        setShowAllColumns,
        viewAllPrompts,
        setViewAllPrompts,
      }}
    >
      {children}
    </ExperimentDetailContext.Provider>
  );
};

ExperimentDetailContextProvider.propTypes = {
  children: PropTypes.node,
};

export default ExperimentDetailContextProvider;
