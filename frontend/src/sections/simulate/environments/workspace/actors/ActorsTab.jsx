import PropTypes from "prop-types";
import { Box } from "@mui/material";
import CallActorsPanel from "./CallActorsPanel";
import WorldActorsPanel from "./WorldActorsPanel";

// The environment says how the tab shows: the cast edited here (`edit`), or its actors listed read-only (`list`).
export default function ActorsTab({ env, locked = false }) {
  if (env.actorsTab === "edit")
    return <WorldActorsPanel envId={env.id} locked={locked} />;
  if (env.actorsTab === "list")
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
    actorsTab: PropTypes.oneOf(["edit", "list"]),
    callActors: PropTypes.array,
  }).isRequired,
  locked: PropTypes.bool,
};
