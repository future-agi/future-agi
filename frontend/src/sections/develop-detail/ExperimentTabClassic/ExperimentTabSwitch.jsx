import React from "react";
import { useParams } from "react-router";
import DsxExperimentTab from "../ExperimentTab/ExperimentTab";
import { useExperimentSurface } from "src/sections/experiment-detail-classic/experimentsBasic";
import { isPrototypeMode } from "src/utils/prototype";
import ClassicExperimentTab from "./ExperimentTab";
import OtherExperimentsGroup from "./OtherExperimentsGroup";
import { useOtherExperiments } from "./otherExperiments";
import { OtherExperimentsContext } from "./otherExperimentsContext";

// L3 (the capability contract): the dataset Experiments tab body. Prototype builds keep
// the DSX leaderboard; a non-prototype build shows dev's experiments list
// (experiments/v2/list/) when experimentsBasic is on and nothing when it is
// off (the launch switch hides the tab itself).
//
// A build that shows the runs leaderboard to customers also lists, under it,
// the dataset's experiments the runs view cannot show in full (the list's own
// opens_in_full_results marker), so none of them leaves the tab. Prototype
// builds read the mock backend, which serves no experiments list and no such
// marker.
const ExperimentTabSwitch = React.forwardRef((props, ref) => {
  const surface = useExperimentSurface();
  if (surface === "hidden") return null;
  if (surface === "classic")
    return <ClassicExperimentTab {...props} ref={ref} />;
  if (isPrototypeMode()) return <DsxExperimentTab {...props} ref={ref} />;
  return <RunsWithOtherExperiments {...props} ref={ref} />;
});

ExperimentTabSwitch.displayName = "ExperimentTabSwitch";

const RunsWithOtherExperiments = React.forwardRef((props, ref) => {
  const { dataset } = useParams();
  const other = useOtherExperiments(dataset);
  const value = {
    count: other.count,
    pending: other.isPending,
    group: <OtherExperimentsGroup other={other} />,
    byId: Object.fromEntries(other.rows.map((row) => [String(row.id), row])),
  };
  return (
    <OtherExperimentsContext.Provider value={value}>
      <DsxExperimentTab {...props} ref={ref} />
    </OtherExperimentsContext.Provider>
  );
});

RunsWithOtherExperiments.displayName = "RunsWithOtherExperiments";

export default ExperimentTabSwitch;
