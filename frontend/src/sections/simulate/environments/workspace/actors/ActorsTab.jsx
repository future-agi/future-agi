import PropTypes from "prop-types";
import { Box } from "@mui/material";
import CallActorsPanel from "./CallActorsPanel";
import WorldActorsPanel from "./WorldActorsPanel";

// With versioning the cast is edited here, actors included; without it they are listed read-only.
export default function ActorsTab({ env, locked = false }) {
  if (env.versioning?.enabled && env.id)
    return <WorldActorsPanel envId={env.id} locked={locked} />;
  if (env.callActors?.length > 0)
    return (
      <Box sx={{ p: 2 }}>
        <CallActorsPanel callActors={env.callActors} />
      </Box>
    );
  return null;
}

ActorsTab.propTypes = {
  env: PropTypes.shape({
    id: PropTypes.string,
    surface: PropTypes.string,
    versioning: PropTypes.shape({ enabled: PropTypes.bool }),
    callActors: PropTypes.array,
  }).isRequired,
  locked: PropTypes.bool,
};
