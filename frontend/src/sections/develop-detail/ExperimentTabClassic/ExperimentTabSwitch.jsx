import React from "react";
import DsxExperimentTab from "../ExperimentTab/ExperimentTab";
import { useExperimentSurface } from "src/sections/experiment-detail-classic/experimentsBasic";
import ClassicExperimentTab from "./ExperimentTab";

// L3 (the capability contract): the dataset Experiments tab body. Prototype builds keep
// the DSX leaderboard; a non-prototype build shows dev's experiments list
// (experiments/v2/list/) when experimentsBasic is on and nothing when it is
// off (the launch switch hides the tab itself).
const ExperimentTabSwitch = React.forwardRef((props, ref) => {
  const surface = useExperimentSurface();
  if (surface === "hidden") return null;
  if (surface === "classic")
    return <ClassicExperimentTab {...props} ref={ref} />;
  return <DsxExperimentTab {...props} ref={ref} />;
});

ExperimentTabSwitch.displayName = "ExperimentTabSwitch";

export default ExperimentTabSwitch;
