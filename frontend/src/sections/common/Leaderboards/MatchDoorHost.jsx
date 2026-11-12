// HO results R9 (W5 B-H5, door half): holds W5 B7's match-by-key dialog for
// the leaderboard's row menus (matchDoor.js). Mounted once with the tab, so
// a live refresh of the grid's cells never closes the dialog or loses what
// was picked in it. Renders nothing without the match endpoint.
import React, { useEffect } from "react";
import MatchByKeyDialog from "src/sections/common/CompareRuns/MatchByKeyDialog";
import { hasW5 } from "src/sections/common/Analysis/workbench/w5Api";
import { closeMatchDoor, registerMatchHost, useMatchDoor } from "./matchDoor";

export default function MatchDoorHost() {
  useEffect(() => registerMatchHost(), []);
  const door = useMatchDoor();
  if (!door.open || !hasW5("resultsMatch")) return null;
  return (
    <MatchByKeyDialog
      open
      onClose={closeMatchDoor}
      runs={door.runs}
      defaultRunId={door.defaultRunId}
    />
  );
}
