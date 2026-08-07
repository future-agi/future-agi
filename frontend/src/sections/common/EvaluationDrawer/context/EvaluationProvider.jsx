import React, { useState } from "react";
import PropTypes from "prop-types";
import { EvaluationContext } from "./EvaluationContext";

const EvaluationProvider = ({
  children,
  actionButtonConfig: initialActionButtonConfig,
}) => {
  const [module, setModule] = useState("dataset");
  const [isDirty, setIsDirty] = useState(false);
  const [selectedEval, setSelectedEval] = useState({});
  const [visibleSection, setVisibleSection] = useState("list");
  const [selectedColumn, setSelectedColumn] = useState("");
  const [actionButtonConfig, setActionButtonConfig] = useState(
    initialActionButtonConfig || {
      id: "",
      showTest: true,
      showAdd: true,
      testLabel: "Test",
      runLabel: "Add & Run",
      handleRun: (_data, _onSuccess) => {},
      handleTest: (_data) => {},
      saveLabel: "Add",
    },
  );
  const [playgroundEvaluation, setPlaygroundEvaluation] = useState(null);
  const [viewEvalsDetails, setViewEvalsDetails] = useState(null);
  const [formValues, setFormValues] = useState({});
  const [currentTab, setCurrentTab] = useState("evals");
  const [selectedGroup, setSelectedGroup] = useState(null);
  const [openEditForSavedEval, setOpenEditForSavedEval] = useState(null);
  const registerOpenEditForSavedEval = (fn) =>
    setOpenEditForSavedEval(() => fn);

  // const endpoint = useMemo(()=>{
  //   switch (module) {
  //     case 'dataset':
  //       return endpoints.develop.eval.addEval(actionButtonConfig.id);
  //     case 'task':

  //     default:
  //       break;
  //   }
  // },[module]);

  return (
    <EvaluationContext.Provider
      value={{
        module,
        isDirty,
        selectedEval,
        visibleSection,
        selectedColumn,
        actionButtonConfig,
        formValues,
        setModule,
        setIsDirty,
        setSelectedEval,
        setVisibleSection,
        setSelectedColumn,
        setActionButtonConfig,
        setPlaygroundEvaluation,
        playgroundEvaluation,
        setFormValues,
        viewEvalsDetails,
        setViewEvalsDetails,
        currentTab,
        setCurrentTab,
        selectedGroup,
        setSelectedGroup,
        openEditForSavedEval,
        registerOpenEditForSavedEval,
      }}
    >
      {children}
    </EvaluationContext.Provider>
  );
};

EvaluationProvider.propTypes = {
  children: PropTypes.node,
  actionButtonConfig: PropTypes.object,
};

export default EvaluationProvider;
