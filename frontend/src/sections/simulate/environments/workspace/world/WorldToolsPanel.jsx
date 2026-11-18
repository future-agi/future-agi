import PropTypes from "prop-types";
import { Box } from "@mui/material";
import CapabilityGraph from "../overview/CapabilityGraph";
import WorldInternalsSection from "../contract/WorldInternalsSection";
import SourcesSection from "../sources/SourcesSection";

/**
 * World & tools: what the world is (the capability graph), the files it is
 * built from, and the tables and tools a run reads from.
 */
export default function WorldToolsPanel({
  env,
  envState,
  patch,
  locked = false,
  graphData,
}) {
  const hasWorld = !!envState?.agent || (env.tools?.length || 0) > 0;
  return (
    <Box sx={{ p: 2 }}>
      {hasWorld && (
        <Box sx={{ mb: 2 }}>
          <CapabilityGraph env={env} envState={envState} data={graphData} />
        </Box>
      )}
      <SourcesSection envId={env.id} readOnly={locked} />
      <WorldInternalsSection
        env={env}
        envState={envState}
        patch={patch}
        locked={locked}
      />
    </Box>
  );
}

WorldToolsPanel.propTypes = {
  env: PropTypes.shape({
    id: PropTypes.string,
    tools: PropTypes.arrayOf(PropTypes.shape({ name: PropTypes.string })),
  }).isRequired,
  envState: PropTypes.shape({
    agent: PropTypes.shape({ name: PropTypes.string }),
  }).isRequired,
  patch: PropTypes.func,
  locked: PropTypes.bool,
  // Real capability-graph data for a backed env; undefined otherwise.
  graphData: PropTypes.object,
};
