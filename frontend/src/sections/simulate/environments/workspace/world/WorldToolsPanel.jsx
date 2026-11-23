import PropTypes from "prop-types";
import { Box } from "@mui/material";
import CapabilityGraph from "../overview/CapabilityGraph";
import WorldInternalsSection from "../contract/WorldInternalsSection";
import SourcesPanel from "../sources/SourcesPanel";

export default function WorldToolsPanel({
  env,
  envState,
  patch,
  locked = false,
  graphData,
  backed = false,
}) {
  const hasWorld = !!envState?.agent || (env.tools?.length || 0) > 0;
  return (
    <Box sx={{ p: 2 }}>
      {hasWorld && (
        <Box sx={{ mb: 2 }}>
          <CapabilityGraph
            env={env}
            envState={envState}
            data={graphData}
            defaultOpen
          />
        </Box>
      )}
      {backed && env.versioning?.enabled && (
        <SourcesPanel env={env} locked={locked} />
      )}
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
  env: PropTypes.object.isRequired,
  envState: PropTypes.object.isRequired,
  patch: PropTypes.func,
  locked: PropTypes.bool,
  graphData: PropTypes.object,
  backed: PropTypes.bool,
};
