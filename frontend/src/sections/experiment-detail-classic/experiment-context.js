import { createContext, useContext } from "react";

export const ExperimentDetailContext = createContext({
  evaluateOpen: false,
  chooseWinnerOpen: false,
  setEvaluateOpen: (_value) => {},
  setChooseWinnerOpen: (_value) => {},
  diffMode: false,
  handleToggleDiffMode: (_value) => {},
  fetchingData: false,
  experimentDetailSearch: "",
  setExperimentDetailSearch: (_val) => {},
  experimentDetailColumnSize: "Short",
  setExperimentDetailColumnSize: (_val) => {},
  setFetchingData: (_val) => {},
  experimentGridRef: null,
  setExperimentGridRef: (_val) => {},
  showAllColumns: false,
  setShowAllColumns: (_val) => {},
  viewAllPrompts: false,
  setViewAllPrompts: (_val) => {},
});

export const useExperimentDetailContext = () => {
  return useContext(ExperimentDetailContext);
};
