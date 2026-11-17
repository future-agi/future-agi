import React from "react";
import DsxExperimentTabHeader from "../ExperimentTab/ExperimentTabHeader";
import { useExperimentSurface } from "src/sections/experiment-detail-classic/experimentsBasic";
import ClassicExperimentTabHeader from "./ExperimentTabHeader";

// L3 (the capability contract): the dataset Experiments tab header; see ExperimentTabSwitch.
const ExperimentTabHeaderSwitch = (props) => {
  const surface = useExperimentSurface();
  if (surface === "hidden") return null;
  if (surface === "classic") return <ClassicExperimentTabHeader {...props} />;
  return <DsxExperimentTabHeader {...props} />;
};

export default ExperimentTabHeaderSwitch;
