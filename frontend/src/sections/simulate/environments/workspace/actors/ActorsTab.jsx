import PropTypes from "prop-types";
import { Box } from "@mui/material";
import { VOICE_ONLY_SURFACES } from "../scenarios/scenarioEditor.constants";
import SubActorsPanel from "./SubActorsPanel";
import WorldActorsPanel from "./WorldActorsPanel";

// With versioning the cast is edited here, sub-actors included; without it they are listed read-only.
export default function ActorsTab({ env, locked = false }) {
  if (env.versioning?.enabled && env.id)
    return (
      <WorldActorsPanel
        envId={env.id}
        locked={locked}
        callerSide={
          VOICE_ONLY_SURFACES.includes(env.surface) ||
          env.subActors?.length > 0
        }
      />
    );
  if (env.subActors?.length > 0)
    return (
      <Box sx={{ p: 2 }}>
        <SubActorsPanel subActors={env.subActors} />
      </Box>
    );
  return null;
}

ActorsTab.propTypes = {
  env: PropTypes.shape({
    id: PropTypes.string,
    surface: PropTypes.string,
    versioning: PropTypes.shape({ enabled: PropTypes.bool }),
    subActors: PropTypes.array,
  }).isRequired,
  locked: PropTypes.bool,
};
