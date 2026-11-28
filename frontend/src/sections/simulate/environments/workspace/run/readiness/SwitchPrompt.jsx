import PropTypes from "prop-types";
import { Button } from "@mui/material";
import { useReadiness } from "src/api/simulate-environments/readiness";
import { named } from "../../agents/agentVersionStatus";
import { DRAFTS_COPY } from "../../workspace.constants";
import RunPlanNote from "../RunPlanNote";
import { RUN_PLAN_COPY as COPY } from "./readiness.constants";

// Offers the version a fix saved over an older pick, with how many scenarios run on it.
export default function SwitchPrompt({
  envId,
  piece,
  picks,
  saved,
  keys,
  trials,
  enabled,
  onSwitch,
  onKeep,
}) {
  const selection = useReadiness(
    envId,
    { ...picks, [piece]: saved.id },
    { keys, trials, enabled },
  ).data?.selection;
  return (
    <RunPlanNote
      data-testid={`run-switch-${piece}`}
      action={
        <>
          <Button size="small" onClick={onSwitch}>
            {COPY.switchVersion(named(saved))}
          </Button>
          <Button size="small" color="inherit" onClick={onKeep}>
            {COPY.keepVersion}
          </Button>
        </>
      }
    >
      {COPY.switchTo(DRAFTS_COPY.pieces[piece], named(saved))}
      {Number.isInteger(selection?.running) &&
        ` ${COPY.switchReady(selection.running, selection.requested)}`}
    </RunPlanNote>
  );
}

SwitchPrompt.propTypes = {
  envId: PropTypes.string.isRequired,
  piece: PropTypes.string.isRequired,
  picks: PropTypes.object.isRequired,
  saved: PropTypes.shape({ id: PropTypes.string }).isRequired,
  keys: PropTypes.arrayOf(PropTypes.string),
  trials: PropTypes.number,
  // False until the selection has settled, as the plan's own read.
  enabled: PropTypes.bool,
  onSwitch: PropTypes.func.isRequired,
  onKeep: PropTypes.func.isRequired,
};
